import base64
import requests

CLIENT_ID = "7c263b10c6be4b09a8981b66c442e73c"
CLIENT_SECRET = "eb91f356b3f0470f967d4cb84851c249"

# 1) Get token
auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
token = requests.post(
    "https://accounts.spotify.com/api/token",
    headers={"Authorization": f"Basic {auth}"},
    data={"grant_type": "client_credentials"}
).json()["access_token"]

# 2) Recommendations (2 seeds = works reliably)
url = "https://api.spotify.com/v1/recommendations"

params = {
    "limit": 10,
    "seed_tracks": "1bhUWB0zJMIKr9yVPrkEuI",
    "seed_genres": "dance"
}

res = requests.get(
    url,
    headers={"Authorization": f"Bearer {token}"},
    params=params
)

print("Status:", res.status_code)
print("Body:", res.text[:500])  # truncate long output
