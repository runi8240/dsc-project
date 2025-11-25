import asyncio
from bleak import BleakClient

HR_CHAR = "00002a37-0000-1000-8000-00805f9b34fb"   # Heart Rate Measurement

def hr_handler(sender, data):
    # Heart rate value is usually in byte index 1
    hr = data[1]
    print("Heart Rate:", hr)

async def main(address):
    async with BleakClient(address) as client:
        print("Connected to:", address)
        await client.start_notify(HR_CHAR, hr_handler)
        print("Listening for HR...")
        await asyncio.sleep(300)

# Replace with your Garmin device MAC
asyncio.run(main("F3FD4758-51E9-1BD3-36D9-80DF3F6C6B79"))
