import asyncio
import threading
from typing import Optional

from bleak import BleakClient
from flask import Flask, render_template
from flask_socketio import SocketIO

# BLE constants
HR_CHAR = "00002a37-0000-1000-8000-00805f9b34fb"
GARMIN_ID = "1A4EDA26-AA5E-0D73-27F1-211B33814D3C"

latest_hr: Optional[int] = None

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")


def hr_handler(_sender, data: bytearray):
    """Handle heart-rate notification packets."""
    global latest_hr
    if len(data) < 2:
        return

    latest_hr = data[1]
    print("HR:", latest_hr)
    socketio.emit("hr", {"hr": latest_hr})


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


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    ble_thread = threading.Thread(target=run_ble, daemon=True)
    ble_thread.start()
    socketio.run(app, host="0.0.0.0", port=5959)
