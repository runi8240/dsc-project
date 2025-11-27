import base64
import requests

CLIENT_ID = "7c263b10c6be4b09a8981b66c442e73c"
CLIENT_SECRET = "eb91f356b3f0470f967d4cb84851c249"

# Get token
auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
token = requests.post(
    "https://accounts.spotify.com/api/token",
    headers={"Authorization": f"Basic {auth}"},
    data={"grant_type": "client_credentials"}
).json()["access_token"]

# Test bare minimum
res = requests.get(
    "https://api.spotify.com/v1/recommendations",
    headers={"Authorization": f"Bearer {token}"},
    params={
        "seed_tracks": "4uLU6hMCjMI75M1A2tKUQC",  # random valid track (Michael Jackson - Beat It)
        "limit": 1
    }
)

print("\nStatus:", res.status_code)
print("Body:", res.text)
