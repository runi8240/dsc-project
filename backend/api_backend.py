import csv
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import json

import base64
import os

import requests
from flask import Flask, jsonify, render_template, request, redirect, url_for, session
from flask_socketio import SocketIO
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from services.feedback_service import FeedbackLogger
from services.recommender_service import RecommenderService
from services.storage_service import StorageClient
from db import get_conn, init_db
from redis_consumer import RedisStreamConsumer

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR.mkdir(exist_ok=True)
CONFIG_DIR.mkdir(exist_ok=True)

app = Flask(__name__, template_folder="templates")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")

DB_PATH = DATA_DIR / "app.db"
init_db(DB_PATH)
telemetry_path = DATA_DIR / "telemetry.csv"  # legacy, no longer used
recommendations_path = DATA_DIR / "recommendations.csv"  # legacy, no longer used
feedback_logger = FeedbackLogger(output_path=DATA_DIR / "feedback.jsonl")
STORAGE_ENABLED = os.getenv("STORAGE_ENABLED", "false").lower() == "true"
STORAGE_ENDPOINT = os.getenv("STORAGE_ENDPOINT", "http://localhost:9000")
STORAGE_ACCESS_KEY = os.getenv("STORAGE_ACCESS_KEY", "minio")
STORAGE_SECRET_KEY = os.getenv("STORAGE_SECRET_KEY", "minio123")
STORAGE_BUCKET = os.getenv("STORAGE_BUCKET", "dsc-artifacts")
STORAGE_SECURE = os.getenv("STORAGE_SECURE", "false").lower() == "true"
STORAGE_TRACKS_KEY = os.getenv("STORAGE_TRACKS_KEY", "seed-data/data.csv")
STORAGE_RECOMMENDATION_PREFIX = os.getenv("STORAGE_RECOMMENDATION_PREFIX", "recommendations")
STORAGE_FEEDBACK_PREFIX = os.getenv("STORAGE_FEEDBACK_PREFIX", "feedback")
STORAGE_UPLOAD_PREFIX = os.getenv("STORAGE_UPLOAD_PREFIX", "uploads")
storage_client = StorageClient(
    enabled=STORAGE_ENABLED,
    endpoint=STORAGE_ENDPOINT,
    access_key=STORAGE_ACCESS_KEY,
    secret_key=STORAGE_SECRET_KEY,
    bucket=STORAGE_BUCKET,
    secure=STORAGE_SECURE,
    base_path=DATA_DIR,
)


def _ensure_seed_tracks():
    if not storage_client.enabled:
        return
    local_tracks = DATA_DIR / "data.csv"
    if local_tracks.exists():
        storage_client.upload_file(local_tracks, STORAGE_TRACKS_KEY, content_type="text/csv")
        return
    downloaded = storage_client.download_file(STORAGE_TRACKS_KEY, local_tracks)
    if downloaded:
        print("[storage] pulled seed tracks from bucket")


_ensure_seed_tracks()
recommender = RecommenderService(data_path=DATA_DIR / "data.csv")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_STREAM_KEY = os.getenv("REDIS_STREAM_KEY", "telemetry")
AUTH_USERNAME = os.getenv("AUTH_USERNAME")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD")
AUTH_USER_ID = os.getenv("AUTH_USER_ID", "demo-user")
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() == "true"
DEFAULT_REST_HR = int(os.getenv("DEFAULT_REST_HR", "60"))
DEFAULT_MAX_HR = int(os.getenv("DEFAULT_MAX_HR", "190"))
AUTH_REST_HR = int(os.getenv("AUTH_REST_HR", str(DEFAULT_REST_HR)))
AUTH_MAX_HR = int(os.getenv("AUTH_MAX_HR", str(DEFAULT_MAX_HR)))
DEFAULT_USER_ID = os.getenv("DEFAULT_USER_ID") or AUTH_USER_ID or "demo-user"

latest_global_hr: Optional[int] = None
latest_global_hr_timestamp: Optional[float] = None
user_hr_versions: Dict[str, float] = {}
user_latest_hr: Dict[str, Optional[int]] = {}
latest_track: Dict[str, Dict[str, object]] = {}
last_track_change: Dict[str, float] = {}
active_users: Set[str] = set()
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


def _normalize_user_id(user_id: Optional[str]) -> str:
    resolved = user_id or DEFAULT_USER_ID
    return str(resolved)


def _user_profile_from_db(user_id: str) -> Optional[Dict[str, float]]:
    try:
        user_int_id = int(user_id)
    except (TypeError, ValueError):
        return None
    with get_conn(DB_PATH) as conn:
        row = conn.execute("SELECT rest_hr, max_hr FROM users WHERE id = ?", (user_int_id,)).fetchone()
    if not row:
        return None
    return {"user_id": str(user_id), "rest_hr": row["rest_hr"] or DEFAULT_REST_HR, "max_hr": row["max_hr"] or DEFAULT_MAX_HR}


def get_user_profile(user_id: Optional[str]) -> Dict[str, float]:
    resolved = _normalize_user_id(user_id)
    if AUTH_USERNAME and resolved == AUTH_USER_ID:
        return {"user_id": resolved, "rest_hr": AUTH_REST_HR, "max_hr": AUTH_MAX_HR}
    profile = _user_profile_from_db(resolved)
    if profile:
        return profile
    return {"user_id": resolved, "rest_hr": DEFAULT_REST_HR, "max_hr": DEFAULT_MAX_HR}


def parse_hr_value(value, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _ensure_user_hr(user_id: str) -> None:
    if latest_global_hr is None or latest_global_hr_timestamp is None:
        return
    last_version = user_hr_versions.get(user_id)
    if last_version == latest_global_hr_timestamp:
        return
    recommender.observe_hr(user_id, latest_global_hr)
    user_hr_versions[user_id] = latest_global_hr_timestamp
    user_latest_hr[user_id] = latest_global_hr


def append_telemetry_row(timestamp: float, hr: int, user_id: Optional[str]) -> None:
    with get_conn(DB_PATH) as conn:
        conn.execute("INSERT INTO telemetry (timestamp, hr, user_id) VALUES (?, ?, ?)", (timestamp, hr, user_id))
        conn.commit()


def append_recommendation_row(timestamp: float, track: Dict[str, str], hr: Optional[int], user_id: str) -> None:
    with get_conn(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO recommendations (timestamp, track_id, track_name, artists, energy, hr, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                track.get("track_id"),
                track.get("track_name"),
                track.get("artists"),
                track.get("energy"),
                hr,
                user_id,
            ),
        )
        conn.commit()
    _persist_recommendation_artifact(timestamp, track, hr, user_id)


def _serialize_track(track: Dict[str, object]) -> Dict[str, object]:
    payload = {}
    for key, value in track.items():
        if isinstance(value, set):
            payload[key] = list(value)
        else:
            payload[key] = value
    return payload


def _persist_recommendation_artifact(timestamp: float, track: Dict[str, str], hr: Optional[int], user_id: str) -> None:
    if not storage_client.enabled:
        return
    key = f"{STORAGE_RECOMMENDATION_PREFIX}/{user_id}/{int(timestamp * 1000)}_{track.get('track_id', 'unknown')}.json"
    storage_client.upload_json(
        key,
        {
            "timestamp": timestamp,
            "track": _serialize_track(track),
            "hr": hr,
            "user_id": user_id,
        },
    )


def _persist_feedback_artifact(event) -> None:
    if not storage_client.enabled:
        return
    key = f"{STORAGE_FEEDBACK_PREFIX}/{event.user_id or 'unknown'}/{int(event.timestamp * 1000)}_{event.event_type}.json"
    storage_client.upload_json(
        key,
        {
            "event_type": event.event_type,
            "track_id": event.track_id,
            "timestamp": event.timestamp,
            "metadata": event.metadata,
            "user_id": event.user_id,
        },
    )


def get_blacklist(user_id: str) -> List[str]:
    if not user_id:
        return []
    with get_conn(DB_PATH) as conn:
        rows = conn.execute("SELECT track_id FROM user_blacklist WHERE user_id = ?", (user_id,)).fetchall()
    return [row["track_id"] for row in rows if row["track_id"]]


def add_to_blacklist(user_id: str, track_id: Optional[str]) -> None:
    if not track_id:
        return
    with get_conn(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_blacklist (user_id, track_id) VALUES (?, ?)",
            (user_id, track_id),
        )
        conn.commit()


def maybe_emit_recommendation(user_profile: Dict[str, float], force_switch: bool = False) -> Optional[Dict[str, str]]:
    user_id = user_profile["user_id"]
    _ensure_user_hr(user_id)
    current_track = latest_track.get(user_id)
    track = recommender.recommend(
        user_profile=user_profile,
        user_id=user_id,
        latest_track=current_track,
        blacklist=get_blacklist(user_id),
    )
    if not track:
        return None

    now = time.time()
    should_switch = force_switch or current_track is None
    last_change = last_track_change.get(user_id)

    if not should_switch and current_track:
        if track["track_id"] != current_track.get("track_id"):
            if force_switch or last_change is None or now - last_change >= MIN_TRACK_DURATION:
                should_switch = True

    if should_switch:
        latest_track[user_id] = track
        last_track_change[user_id] = now
        append_recommendation_row(now, track, user_latest_hr.get(user_id), user_id)
        socketio.emit("track", {"user_id": user_id, **_serialize_track(track)})

    return track


def handle_telemetry(hr: int, timestamp: float):
    global latest_global_hr, latest_global_hr_timestamp
    latest_global_hr = hr
    latest_global_hr_timestamp = timestamp
    append_telemetry_row(timestamp, hr, None)
    socketio.emit("hr", {"hr": hr})
    target_users = active_users or {DEFAULT_USER_ID}
    for uid in target_users:
        profile = get_user_profile(uid)
        maybe_emit_recommendation(profile)


def _redis_handler(data: Dict):
    # Redis returns bytes; decode fields
    try:
        hr_bytes = data.get(b"hr")
        ts_bytes = data.get(b"timestamp")
        if hr_bytes is None:
            return
        hr = int(hr_bytes)
        ts = float(ts_bytes) if ts_bytes is not None else time.time()
        handle_telemetry(hr, ts)
        print(f"[redis] consumed telemetry hr={hr} ts={ts}")
    except Exception as exc:
        print(f"Error handling redis message: {exc}")


@app.route("/")
def index():
    if AUTH_ENABLED and not session.get("user_id"):
        return redirect(url_for("login"))
    return render_template("index.html")


@app.route("/telemetry", methods=["POST"])
def telemetry():
    payload = request.get_json(silent=True) or {}
    hr_raw = payload.get("hr")
    if hr_raw is None:
        return jsonify({"error": "hr is required"}), 400

    try:
        hr = int(hr_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "hr must be an integer"}), 400

    timestamp = float(payload.get("timestamp") or time.time())
    handle_telemetry(hr, timestamp)
    return jsonify({"status": "ok", "hr": hr}), 200


@app.route("/recommendation", methods=["GET"])
def recommendation():
    requested_user = request.args.get("user_id") or session.get("user_id")
    profile = get_user_profile(requested_user)
    user_id = profile["user_id"]
    _ensure_user_hr(user_id)
    track = recommender.recommend(
        user_profile=profile,
        user_id=user_id,
        latest_track=latest_track.get(user_id),
        blacklist=get_blacklist(user_id),
    )
    if not track:
        return jsonify({"error": "No track available"}), 404
    return jsonify(_serialize_track(track) | {"user_id": user_id})


@app.route("/feedback", methods=["POST"])
def feedback():
    payload = request.get_json(silent=True) or {}
    event_type = payload.get("event_type")
    if not event_type:
        return jsonify({"error": "event_type is required"}), 400
    track_id = payload.get("track_id")
    metadata = payload.get("metadata") or {}
    requested_user = payload.get("user_id") or session.get("user_id")
    profile = get_user_profile(requested_user)
    resolved_user_id = profile["user_id"]
    metadata = {**metadata, "user_id": resolved_user_id}
    event = feedback_logger.log(event_type=event_type, track_id=track_id, metadata=metadata, user_id=resolved_user_id)
    with get_conn(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO feedback (timestamp, event_type, track_id, metadata, user_id) VALUES (?, ?, ?, ?, ?)",
            (event.timestamp, event.event_type, event.track_id, json.dumps(event.metadata), resolved_user_id),
        )
        conn.commit()
    _persist_feedback_artifact(event)
    socketio.emit("feedback", {"event_type": event_type, "track_id": track_id, "user_id": resolved_user_id})
    new_track = None
    if event_type == "dislike":
        add_to_blacklist(resolved_user_id, track_id)
        new_track = maybe_emit_recommendation(profile, force_switch=True)
    serialized_track = _serialize_track(new_track) if new_track else None
    return jsonify({"status": "ok", "event": event.__dict__, "track": serialized_track})


@app.route("/storage/upload", methods=["POST"])
def storage_upload():
    if not storage_client.enabled:
        return jsonify({"error": "storage disabled"}), 400
    if AUTH_ENABLED and not session.get("user_id"):
        return jsonify({"error": "auth required"}), 401
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"error": "file is required"}), 400
    key_override = (request.form.get("key") or "").lstrip("/")
    safe_name = secure_filename(upload.filename)
    timestamp_prefix = int(time.time() * 1000)
    owner = _normalize_user_id(session.get("user_id"))
    storage_key = key_override or f"{STORAGE_UPLOAD_PREFIX}/{owner}/{timestamp_prefix}_{safe_name}"
    if not storage_key:
        return jsonify({"error": "invalid key"}), 400
    contents = upload.read()
    if not contents:
        return jsonify({"error": "file is empty"}), 400
    ok = storage_client.upload_bytes(storage_key, contents, upload.mimetype or "application/octet-stream")
    if not ok:
        return jsonify({"error": "failed to store file"}), 500
    return jsonify({"status": "uploaded", "key": storage_key, "user_id": owner})


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


@app.route("/auth/login", methods=["POST"])
def auth_login():
    if not AUTH_ENABLED:
        return jsonify({"error": "auth disabled"}), 400
    payload = request.get_json(silent=True) or {}
    username = payload.get("username")
    password = payload.get("password")
    user = _verify_credentials(username, password)
    if not user:
        return jsonify({"error": "invalid credentials"}), 401
    session["user_id"] = str(user["id"])
    session["username"] = user["username"]
    return jsonify({"status": "ok", "user_id": str(user["id"]), "username": user["username"]})


@app.route("/auth/signup", methods=["POST"])
def auth_signup():
    if not AUTH_ENABLED:
        return jsonify({"error": "auth disabled"}), 400
    payload = request.get_json(silent=True) or {}
    username = payload.get("username")
    password = payload.get("password")
    if not username or not password:
        return jsonify({"error": "username and password required"}), 400
    rest_hr = parse_hr_value(payload.get("rest_hr"), DEFAULT_REST_HR)
    max_hr = parse_hr_value(payload.get("max_hr"), DEFAULT_MAX_HR)
    user_id = _create_user(username, password, rest_hr=rest_hr, max_hr=max_hr)
    if not user_id:
        return jsonify({"error": "username already exists"}), 409
    session["user_id"] = str(user_id)
    session["username"] = username
    return jsonify({"status": "ok", "user_id": str(user_id), "username": username})


@app.route("/login", methods=["GET", "POST"])
def login():
    if not AUTH_ENABLED:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        user = _verify_credentials(username, password)
        if user:
            session["user_id"] = str(user["id"])
            session["username"] = user["username"]
            return redirect(url_for("index"))
        return render_template("login.html", error="Invalid credentials")
    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login" if AUTH_ENABLED else "index"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if not AUTH_ENABLED:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        rest_hr = parse_hr_value(request.form.get("rest_hr"), DEFAULT_REST_HR)
        max_hr = parse_hr_value(request.form.get("max_hr"), DEFAULT_MAX_HR)
        if not username or not password:
            return render_template("signup.html", error="Username and password required")
        user_id = _create_user(username, password, rest_hr=rest_hr, max_hr=max_hr)
        if not user_id:
            return render_template("signup.html", error="Username already exists")
        session["user_id"] = str(user_id)
        session["username"] = username
        return redirect(url_for("index"))
    return render_template("signup.html", error=None)


def _verify_credentials(username: str, password: str):
    # Env-based user (optional)
    if AUTH_USERNAME and AUTH_PASSWORD and username == AUTH_USERNAME and password == AUTH_PASSWORD:
        return {"id": AUTH_USER_ID, "username": AUTH_USERNAME}
    # DB user lookup
    with get_conn(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, rest_hr, max_hr FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    if not row:
        return None
    if check_password_hash(row["password_hash"], password):
        return {
            "id": str(row["id"]),
            "username": row["username"],
            "rest_hr": row["rest_hr"],
            "max_hr": row["max_hr"],
        }
    return None


def _create_user(username: str, password: str, *, rest_hr: int, max_hr: int):
    pwd_hash = generate_password_hash(password)
    with get_conn(DB_PATH) as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, rest_hr, max_hr) VALUES (?, ?, ?, ?)",
                (username, pwd_hash, rest_hr, max_hr),
            )
            conn.commit()
            return cur.lastrowid
        except Exception:
            return None


@socketio.on("connect")
def send_last_value():
    if AUTH_ENABLED and not session.get("user_id"):
        return False
    user_id = _normalize_user_id(session.get("user_id"))
    active_users.add(user_id)
    if latest_global_hr is not None:
        socketio.emit("hr", {"hr": latest_global_hr}, to=request.sid)
    last_track = latest_track.get(user_id)
    if last_track is not None:
        socketio.emit("track", {"user_id": user_id, **_serialize_track(last_track)}, to=request.sid)


@socketio.on("disconnect")
def handle_disconnect():
    user_id = _normalize_user_id(session.get("user_id"))
    active_users.discard(user_id)


if __name__ == "__main__":
    redis_consumer = RedisStreamConsumer(
        redis_url=REDIS_URL,
        stream_key=REDIS_STREAM_KEY,
        group="backend",
        handler=_redis_handler,
    )
    redis_consumer.start()
    socketio.run(app, host="0.0.0.0", port=5001, allow_unsafe_werkzeug=True)
