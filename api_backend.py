import csv
import time
from pathlib import Path
from typing import Dict, Optional

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO

from feedback_service import FeedbackLogger
from recommender_service import RecommenderService

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

telemetry_path = Path(__file__).with_name("telemetry.csv")
recommendations_path = Path(__file__).with_name("recommendations.csv")
recommender = RecommenderService()
feedback_logger = FeedbackLogger()

latest_hr: Optional[int] = None
latest_track: Optional[Dict[str, str]] = None
last_track_change: Optional[float] = None
MIN_TRACK_DURATION = 30  # seconds a recommended track should play before switching


def append_telemetry_row(timestamp: float, hr: int) -> None:
    is_new = not telemetry_path.exists()
    with telemetry_path.open("a", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=["timestamp", "hr"])
        if is_new:
            writer.writeheader()
        writer.writerow({"timestamp": timestamp, "hr": hr})


def append_recommendation_row(timestamp: float, track: Dict[str, str], hr: Optional[int]) -> None:
    is_new = not recommendations_path.exists()
    with recommendations_path.open("a", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=["timestamp", "track_id", "track_name", "artists", "energy", "hr"],
        )
        if is_new:
            writer.writeheader()
        writer.writerow(
            {
                "timestamp": timestamp,
                "track_id": track.get("track_id"),
                "track_name": track.get("track_name"),
                "artists": track.get("artists"),
                "energy": track.get("energy"),
                "hr": hr,
            }
        )


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
    socketio.emit("feedback", {"event_type": event_type, "track_id": track_id})
    return jsonify({"status": "ok", "event": event.__dict__})


@socketio.on("connect")
def send_last_value():
    if latest_hr is not None:
        socketio.emit("hr", {"hr": latest_hr})
    if latest_track is not None:
        socketio.emit("track", latest_track)


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5001)
