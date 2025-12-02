import base64
import os
from urllib.parse import urlencode, urlparse, parse_qs

from flask import Flask, request
import requests

CLIENT_ID = "38bc0f2b123d4a53815f7e3a2be492ce"
CLIENT_SECRET = "60f8bd16a09a45d3a1ba6e18b4d5c498"
REDIRECT_URI = "http://127.0.0.1:8888/callback"

SCOPES = "user-read-email user-read-private streaming user-modify-playback-state user-read-playback-state"

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"

app = Flask(__name__)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "No code provided!"

    print("Authorization code:", code)

    # Exchange the code for refresh token
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }

    r = requests.post(TOKEN_URL, data=payload)
    print("\nToken response:", r.json())

    refresh_token = r.json().get("refresh_token")
    access_token = r.json().get("access_token")

    return f"""
        <h1>Success!</h1>
        <p><strong>Copy your REFRESH TOKEN:</strong></p>
        <pre>{refresh_token}</pre>
        <p><strong>Copy your ACCESS TOKEN:</strong></p>
        <pre>{access_token}</pre>
        <p>You may close this window.</p>
    """

@app.route("/")
def index():
    params = urlencode({
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES
    })
    return f'<a href="{AUTH_URL}?{params}">Authorize with Spotify</a>'

if __name__ == "__main__":
    print("Listening on http://127.0.0.1:8888 ...")
    app.run(host="127.0.0.1", port=8888, debug=False)
