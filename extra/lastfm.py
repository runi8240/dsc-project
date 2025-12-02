import requests

API_KEY = "fcbd5279d285d4cd5af9d8e21db8e4f4"

def get_top_tracks(tag, limit=10):
    url = "http://ws.audioscrobbler.com/2.0/"

    params = {
        "method": "tag.getTopTracks",
        "tag": tag,
        "api_key": API_KEY,
        "format": "json",
        "limit": limit
    }

    r = requests.get(url, params=params)
    r.raise_for_status()
    return r.json()

if __name__ == "__main__":
    tag = "edm"  # try "workout", "running", "dance"
    data = get_top_tracks(tag, limit=10)

    print("\n🎧 TOP TRACKS FOR TAG:", tag.upper())
    for i, track in enumerate(data["tracks"]["track"], 1):
        print(f"{i}. {track['name']} — {track['artist']['name']}")
        print("   Last.fm:", track['url'])
