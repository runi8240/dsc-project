import base64
import requests

CLIENT_ID = "7c263b10c6be4b09a8981b66c442e73c"
CLIENT_SECRET = "eb91f356b3f0470f967d4cb84851c249"

def get_token():
    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    res = requests.post(
        "https://accounts.spotify.com/api/token",
        headers={"Authorization": f"Basic {auth}"},
        data={"grant_type": "client_credentials"}
    )
    return res.json()["access_token"]

def get_songs():
    token = get_token()

    url = "https://api.spotify.com/v1/recommendations"

    params = {
        "limit": 10,
        "seed_genres": "edm",
        "min_tempo": "150",
        "max_tempo": "165",
        "target_energy": "0.8"
    }

    r = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params
    )

    if r.status_code != 200:
        print("Error:", r.text)
        return

    data = r.json()

    for i, track in enumerate(data["tracks"], 1):
        print(f"{i}. {track['name']} — {track['artists'][0]['name']}")

get_songs()
