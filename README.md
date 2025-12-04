# DSC Project – Local Run Guide

## Prereqs
- Python 3.11+
- Docker + docker-compose (for backend + Redis)
- Spotify Premium credentials in `config/spotify_credentials.txt`
- Garmin BLE device for ingestion (runs on host)

## Setup
1) Install deps for host tools (ingestion, optional backend):
   ```bash
   pip install -r requirements.txt
   ```
2) Set secrets and auth:
   ```bash
   export SPOTIFY_CREDENTIALS_FILE=$(pwd)/config/spotify_credentials.txt
   export SECRET_KEY="change-me"           # use a random string
   export AUTH_ENABLED=true                # default is true
   export DEFAULT_USER_ID=demo-user        # used for ingestion/offline telemetry
   export DEFAULT_REST_HR=60               # defaults for users who skip HR input
   export DEFAULT_MAX_HR=190
   # Optional env user (can also sign up via UI/API):
   # export AUTH_USERNAME=demo
   # export AUTH_PASSWORD=demo
   # export AUTH_REST_HR=58
   # export AUTH_MAX_HR=188
   ```

`config/spotify_credentials.txt` format:
```
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
SPOTIFY_REFRESH_TOKEN=...
```

## Run backend + Redis + Storage (Docker)
```bash
docker-compose up --build
```
- Backend: http://localhost:5001
- Redis: exposed on 6379
- MinIO object storage console: http://localhost:9001 (login `minio`/`minio123`)
- Volumes: `./data`, `./config`, and `./storage` are mounted into the container.
- The storage bucket (`dsc-artifacts`) is auto-created on first write; you can inspect content via the MinIO console.

## Run ingestion (host)
Ingestion is kept on the host to access BLE reliably.
```bash
export REDIS_URL=redis://localhost:6379/0   # use Redis stream path
# or omit REDIS_URL to fall back to HTTP POST /telemetry
python ingestion/ingestion_service.py
```
Heart-rate samples are ingested globally; the backend associates them with the logged-in user when generating recommendations or storing feedback.
Optional telemetry mirroring into object storage:
```bash
export STORAGE_ENABLED=true
export STORAGE_ENDPOINT=http://localhost:9000
export STORAGE_ACCESS_KEY=minio
export STORAGE_SECRET_KEY=minio123
export STORAGE_BUCKET=dsc-artifacts
# export INGESTION_SESSION_ID=my-local-session   # auto-generated if omitted
python ingestion/ingestion_service.py
```
Flow: Garmin BLE → ingestion → Redis stream (`telemetry`) → backend → DB + Socket.IO.

## Auth
- Web login at `/login`, signup at `/signup`. Sessions use `SECRET_KEY`.
- Signup now captures resting and max heart-rate (defaults to 60/190 bpm if omitted).
- API login/signup:
  - `POST /auth/signup { "username": "...", "password": "...", "rest_hr": 58, "max_hr": 188 }`
  - `POST /auth/login { "username": "...", "password": "..." }`

## Frontend
- Served from backend at http://localhost:5001
- Spotify Web Playback SDK: click “Connect to Spotify” once; recommendations auto-play via SDK device.
- Feedback buttons send like/dislike/neutral; dislike triggers immediate next recommendation.

## Data & Storage
- SQLite: `data/app.db` (tables: users, telemetry, recommendations, feedback)
- Telemetry, recommendations, feedback, and user preference tables (`user_likes`, `user_blacklist`) capture `user_id` for personalized ML.
- “Like” feedback snapshots track features of each track; the recommender keeps a running average to steer future picks toward similar songs near the current heart-rate intensity.
- A rolling per-session history (default 5 tracks) prevents the same song from being recommended twice in one listening session; override via `SESSION_HISTORY_LIMIT`.
- Disliked songs are tracked in `user_blacklist` so they are never re-recommended.
- Local CSV: `data/data.csv` (seed tracks) is mirrored into object storage when the backend boots. If missing locally, it is downloaded from storage.
- Raw telemetry snapshots are uploaded to MinIO under `raw-telemetry/` (not user-specific) and recommendations/feedback are stored by user ID.
- Legacy CSV/JSONL files remain in `data/` for reference.
- Use `POST /storage/upload` (multipart form) to push arbitrary artifacts to the bucket when testing Cloud Storage-style workflows.
- Inspect or download artifacts via the MinIO console or `mc` CLI (e.g., `mc alias set dsc http://localhost:9000 minio minio123`).

## Redis debugging
```bash
redis-cli -u redis://localhost:6379/0 XLEN telemetry
redis-cli -u redis://localhost:6379/0 XRANGE telemetry - + COUNT 5
docker-compose logs -f backend   # shows “[redis] consumed telemetry ...”
```

## Environment summary
- `REDIS_URL` (default `redis://localhost:6379/0`)
- `REDIS_STREAM_KEY` (default `telemetry`)
- `SPOTIFY_CREDENTIALS_FILE` (default `config/spotify_credentials.txt`)
- `SECRET_KEY` (required for auth sessions)
- `AUTH_ENABLED` (default `true`; set `false` to bypass login)
- `AUTH_USERNAME`/`AUTH_PASSWORD`/`AUTH_USER_ID` (optional env user)
- User profile defaults: `DEFAULT_USER_ID`, `DEFAULT_REST_HR`, `DEFAULT_MAX_HR`, `AUTH_REST_HR`, `AUTH_MAX_HR`
- Recommendation session control: `SESSION_HISTORY_LIMIT` (default 5)
- Storage: `STORAGE_ENABLED` (default `true` in Docker), `STORAGE_ENDPOINT`, `STORAGE_ACCESS_KEY`, `STORAGE_SECRET_KEY`, `STORAGE_BUCKET`, `STORAGE_SECURE`, `STORAGE_TRACKS_KEY`, `STORAGE_RECOMMENDATION_PREFIX`, `STORAGE_FEEDBACK_PREFIX`, `STORAGE_UPLOAD_PREFIX`
- Ingestion-specific: `GARMIN_DEVICE_ID`, `BACKEND_TELEMETRY_URL` (used if no Redis), `STORAGE_TELEMETRY_PREFIX`, `STORAGE_BATCH_SIZE`, `STORAGE_FLUSH_SECONDS`, `INGESTION_SESSION_ID`
