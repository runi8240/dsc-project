import base64
import requests

CLIENT_ID = "7c263b10c6be4b09a8981b66c442e73c"
CLIENT_SECRET = "eb91f356b3f0470f967d4cb84851c249"

def get_token():
    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        headers={"Authorization": f"Basic {auth}"},
        data={"grant_type": "client_credentials"}
    )
    return r.json()["access_token"]

def search_one_song(token, query):
    url = "https://api.spotify.com/v1/search"
    params = {
        "q": query,
        "type": "track",
        "limit": 1
    }
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params)
    r.raise_for_status()
    return r.json()

if __name__ == "__main__":
    token = get_token()
    print("Token OK.")

    query = "edm workout"
    result = search_one_song(token, query)

    track = result["tracks"]["items"][0]
    print("\n🎵 One Song Result:")
    print("Name:", track["name"])
    print("Artist:", track["artists"][0]["name"])
    print("URL:", track["external_urls"]["spotify"])
