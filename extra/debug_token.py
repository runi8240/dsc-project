import base64
import requests

CLIENT_ID = "7c263b10c6be4b09a8981b66c442e73c"
CLIENT_SECRET = "eb91f356b3f0470f967d4cb84851c249"

auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()

token_res = requests.post(
    "https://accounts.spotify.com/api/token",
    headers={"Authorization": f"Basic {auth}"},
    data={"grant_type": "client_credentials"}
)

print("Status:", token_res.status_code)
print("Body:", token_res.text)
