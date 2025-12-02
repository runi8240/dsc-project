import asyncio
import threading
import matplotlib.pyplot as plt
from bleak import BleakClient
from matplotlib.animation import FuncAnimation

HR_CHAR = "00002a37-0000-1000-8000-00805f9b34fb"
GARMIN_ID = "F3FD4758-51E9-1BD3-36D9-80DF3F6C6B79"  # your UUID

heart_rates = []
timestamps = []

def hr_handler(sender, data):
    hr = data[1]
    print("Heart Rate:", hr)
    heart_rates.append(hr)
    timestamps.append(len(timestamps))

async def stream_hr():
    print("Connecting to Garmin...")
    async with BleakClient(GARMIN_ID) as client:
        print("Connected to Garmin!")
        await client.start_notify(HR_CHAR, hr_handler)

        # keep BLE running forever
        while True:
            await asyncio.sleep(0.1)

def run_ble_loop():
    asyncio.run(stream_hr())

# ----------------- PLOT -----------------
plt.style.use("ggplot")
fig, ax = plt.subplots()
line, = ax.plot([], [], lw=2)
ax.set_title("Live Garmin Heart Rate (BLE)")
ax.set_xlabel("Time (samples)")
ax.set_ylabel("Heart Rate (bpm)")

def update_plot(frame):
    if len(heart_rates) > 1:
        line.set_data(timestamps, heart_rates)
        ax.set_xlim(max(0, len(timestamps)-50), len(timestamps))
        ax.set_ylim(min(heart_rates)-5, max(heart_rates)+5)
    return line,

ani = FuncAnimation(fig, update_plot, interval=500)

# ----------------- MAIN -----------------
# Start BLE thread
ble_thread = threading.Thread(target=run_ble_loop, daemon=True)
ble_thread.start()

# Start plot in main thread
plt.show()
