import csv
import time
from pathlib import Path
from typing import Dict, Optional
import json

import base64
import os
from typing import Tuple

import requests
from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO

from services.feedback_service import FeedbackLogger
from services.recommender_service import RecommenderService
from db import get_conn, init_db

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR.mkdir(exist_ok=True)
CONFIG_DIR.mkdir(exist_ok=True)

app = Flask(__name__, template_folder="templates")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

DB_PATH = DATA_DIR / "app.db"
init_db(DB_PATH)
telemetry_path = DATA_DIR / "telemetry.csv"  # legacy, no longer used
recommendations_path = DATA_DIR / "recommendations.csv"  # legacy, no longer used
recommender = RecommenderService(data_path=DATA_DIR / "data.csv")
feedback_logger = FeedbackLogger(output_path=DATA_DIR / "feedback.jsonl")

latest_hr: Optional[int] = None
latest_track: Optional[Dict[str, str]] = None
last_track_change: Optional[float] = None
MIN_TRACK_DURATION = 30  # seconds a recommended track should play before switching

# Secrets are loaded from environment or a local text file (not committed).
# File format (lines): SPOTIFY_CLIENT_ID=..., SPOTIFY_CLIENT_SECRET=..., SPOTIFY_REFRESH_TOKEN=...
SPOTIFY_CREDENTIALS_FILE = os.getenv(
    "SPOTIFY_CREDENTIALS_FILE",
    str(CONFIG_DIR / "spotify_credentials.txt"),
)


def _load_from_file() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    try:
        with open(SPOTIFY_CREDENTIALS_FILE, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return None, None, None

    values = {}
    for line in lines:
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        values[key.strip()] = val.strip()

    return (
        values.get("SPOTIFY_CLIENT_ID"),
        values.get("SPOTIFY_CLIENT_SECRET"),
        values.get("SPOTIFY_REFRESH_TOKEN"),
    )


file_client_id, file_client_secret, file_refresh_token = _load_from_file()
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", file_client_id)
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", file_client_secret)
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN", file_refresh_token)
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_SCOPES = "user-read-email user-read-private streaming user-modify-playback-state user-read-playback-state"


def append_telemetry_row(timestamp: float, hr: int) -> None:
    with get_conn(DB_PATH) as conn:
        conn.execute("INSERT INTO telemetry (timestamp, hr) VALUES (?, ?)", (timestamp, hr))
        conn.commit()


def append_recommendation_row(timestamp: float, track: Dict[str, str], hr: Optional[int]) -> None:
    with get_conn(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO recommendations (timestamp, track_id, track_name, artists, energy, hr)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                track.get("track_id"),
                track.get("track_name"),
                track.get("artists"),
                track.get("energy"),
                hr,
            ),
        )
        conn.commit()


def maybe_emit_recommendation() -> Optional[Dict[str, str]]:
    global latest_track, last_track_change
    track = recommender.recommend()
    if not track:
        return None

    now = time.time()
    should_switch = False

    if latest_track is None:
        should_switch = True
    elif track["track_id"] != latest_track.get("track_id"):
        if last_track_change is None or now - last_track_change >= MIN_TRACK_DURATION:
            should_switch = True

    if should_switch:
        latest_track = track
        last_track_change = now
        append_recommendation_row(now, track, latest_hr)
        socketio.emit("track", track)

    return track


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/telemetry", methods=["POST"])
def telemetry():
    global latest_hr
    payload = request.get_json(silent=True) or {}
    hr_raw = payload.get("hr")
    if hr_raw is None:
        return jsonify({"error": "hr is required"}), 400

    try:
        hr = int(hr_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "hr must be an integer"}), 400

    timestamp = float(payload.get("timestamp") or time.time())

    latest_hr = hr
    recommender.observe_hr(hr)
    append_telemetry_row(timestamp, hr)
    socketio.emit("hr", {"hr": hr})

    track = maybe_emit_recommendation()
    return jsonify({"status": "ok", "hr": hr, "track": track}), 200


@app.route("/recommendation", methods=["GET"])
def recommendation():
    track = recommender.recommend()
    if not track:
        return jsonify({"error": "No track available"}), 404
    return jsonify(track)


@app.route("/feedback", methods=["POST"])
def feedback():
    payload = request.get_json(silent=True) or {}
    event_type = payload.get("event_type")
    if not event_type:
        return jsonify({"error": "event_type is required"}), 400
    track_id = payload.get("track_id")
    metadata = payload.get("metadata") or {}
    event = feedback_logger.log(event_type=event_type, track_id=track_id, metadata=metadata)
    with get_conn(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO feedback (timestamp, event_type, track_id, metadata) VALUES (?, ?, ?, ?)",
            (event.timestamp, event.event_type, event.track_id, json.dumps(event.metadata)),
        )
        conn.commit()
    socketio.emit("feedback", {"event_type": event_type, "track_id": track_id})
    return jsonify({"status": "ok", "event": event.__dict__})


def _spotify_auth_header() -> Dict[str, str]:
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return {}
    creds = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode("utf-8")
    return {"Authorization": f"Basic {base64.b64encode(creds).decode('ascii')}"}


@app.route("/spotify/token", methods=["GET"])
def spotify_token():
    """Mint an access token from refresh token so frontend never sees the secret."""
    if not SPOTIFY_REFRESH_TOKEN:
        return jsonify({"error": "SPOTIFY_REFRESH_TOKEN not set"}), 400
    headers = _spotify_auth_header()
    if not headers:
        return jsonify({"error": "SPOTIFY_CLIENT_ID/SECRET not set"}), 400
    data = {
        "grant_type": "refresh_token",
        "refresh_token": SPOTIFY_REFRESH_TOKEN,
        "scope": SPOTIFY_SCOPES,
    }
    resp = requests.post(SPOTIFY_TOKEN_URL, data=data, headers=headers, timeout=10)
    if not resp.ok:
        return jsonify({"error": "Failed to refresh token", "details": resp.text}), 502
    token_payload = resp.json()
    return jsonify(
        {
            "access_token": token_payload.get("access_token"),
            "expires_in": token_payload.get("expires_in"),
            "token_type": token_payload.get("token_type"),
            "scope": token_payload.get("scope"),
        }
    )


@socketio.on("connect")
def send_last_value():
    if latest_hr is not None:
        socketio.emit("hr", {"hr": latest_hr})
    if latest_track is not None:
        socketio.emit("track", latest_track)


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5001)
