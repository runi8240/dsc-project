import asyncio
import ast
import csv
import threading
import time
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional

from bleak import BleakClient
from flask import Flask, render_template
from flask_socketio import SocketIO

# BLE constants
HR_CHAR = "00002a37-0000-1000-8000-00805f9b34fb"
GARMIN_ID = "1A4EDA26-AA5E-0D73-27F1-211B33814D3C"

HR_MIN = 55
HR_MAX = 180
TREND_THRESHOLD = 5  # bpm difference needed before nudging target energy
MIN_TRACK_DURATION = 90  # seconds a recommended track should play before switching

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

latest_hr: Optional[int] = None
hr_history: deque[int] = deque(maxlen=60)
latest_track: Optional[Dict[str, str]] = None
last_track_change: Optional[float] = None


def load_tracks() -> List[Dict[str, str]]:
    """Load available tracks and their energy from the local CSV."""
    data_path = Path(__file__).with_name("data.csv")
    tracks: List[Dict[str, str]] = []
    if not data_path.exists():
        print("No data.csv found; automatic track selection disabled.")
        return tracks

    with data_path.open(newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            track_id = row.get("id")
            energy = row.get("energy")
            if not track_id or energy is None:
                continue
            try:
                energy_val = float(energy)
            except (TypeError, ValueError):
                continue

            artists_raw = row.get("artists", "")
            artists_text = parse_artists(artists_raw)
            tracks.append(
                {
                    "id": track_id,
                    "name": row.get("name", "Unknown track"),
                    "artists": artists_text,
                    "energy": energy_val,
                }
            )
    return tracks


def parse_artists(value: str) -> str:
    """Convert the artists column (a stringified list) into readable text."""
    try:
        parsed = ast.literal_eval(value)
        if isinstance(parsed, list):
            return ", ".join(str(item) for item in parsed)
        return str(parsed)
    except (ValueError, SyntaxError):
        return value.strip("[]'\" ")


TRACKS = load_tracks()


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def select_track() -> Optional[Dict[str, str]]:
    """Pick the next track whose energy best matches the heart-rate trend."""
    if not hr_history or not TRACKS:
        return None

    current_hr = hr_history[-1]
    if len(hr_history) > 1:
        previous = list(hr_history)[:-1]
        avg_previous = sum(previous) / len(previous)
        trend = current_hr - avg_previous
    else:
        trend = 0

    normalized = clamp((current_hr - HR_MIN) / (HR_MAX - HR_MIN), 0.0, 1.0)
    if trend > TREND_THRESHOLD:
        normalized = clamp(normalized + 0.1, 0.0, 1.0)
    elif trend < -TREND_THRESHOLD:
        normalized = clamp(normalized - 0.1, 0.0, 1.0)

    best = min(TRACKS, key=lambda track: abs(track["energy"] - normalized))
    return best


def track_payload(track: Dict[str, str]) -> Dict[str, str]:
    return {
        "track_id": track["id"],
        "track_name": track.get("name", ""),
        "artists": track.get("artists", ""),
        "energy": track.get("energy", ""),
    }


def hr_handler(_sender, data: bytearray):
    """Handle heart-rate notification packets."""
    global latest_hr, latest_track, last_track_change
    if len(data) < 2:
        return

    latest_hr = data[1]
    hr_history.append(latest_hr)
    print("HR:", latest_hr)
    socketio.emit("hr", {"hr": latest_hr})

    track = select_track()
    if not track:
        return

    now = time.time()
    should_switch = False

    if latest_track is None:
        should_switch = True
    elif track["id"] != latest_track["id"]:
        if last_track_change is None or now - last_track_change >= MIN_TRACK_DURATION:
            should_switch = True

    if should_switch:
        latest_track = track
        last_track_change = now
        socketio.emit("track", track_payload(track))


async def stream_hr():
    print("Connecting to Garmin…")
    async with BleakClient(GARMIN_ID) as client:
        print("Connected!")
        await client.start_notify(HR_CHAR, hr_handler)

        while True:
            await asyncio.sleep(0.1)


def run_ble():
    # Run the BLE event loop in a background thread.
    asyncio.run(stream_hr())


@socketio.on("connect")
def send_last_value():
    if latest_hr is not None:
        socketio.emit("hr", {"hr": latest_hr})
    if latest_track is not None:
        socketio.emit("track", track_payload(latest_track))


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    ble_thread = threading.Thread(target=run_ble, daemon=True)
    ble_thread.start()
    socketio.run(app, host="0.0.0.0", port=5959)
