import ast
import csv
import math
import random
from collections import defaultdict, deque
from pathlib import Path
from typing import Deque, Dict, List, Optional, Sequence, Tuple, Set

TREND_THRESHOLD = 5  # bpm difference needed before nudging target energy


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def parse_artists(value: str) -> Tuple[List[str], str]:
    """Convert the artists column (a stringified list) into list + readable text."""
    try:
        parsed = ast.literal_eval(value)
        if isinstance(parsed, list):
            artists = [str(item).strip() for item in parsed if str(item).strip()]
            return artists, ", ".join(artists)
        parsed_str = str(parsed).strip().strip("[]")
        return [parsed_str] if parsed_str else [], parsed_str
    except (ValueError, SyntaxError):
        cleaned = value.strip("[]'\" ")
        return ([cleaned] if cleaned else [], cleaned)


def _float(value: Optional[str], default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def load_tracks(data_path: Path) -> List[Dict[str, object]]:
    """Load available tracks and their features from the local CSV."""
    tracks: List[Dict[str, object]] = []
    if not data_path.exists():
        print(f"No {data_path.name} found; automatic track selection disabled.")
        return tracks

    with data_path.open(newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            track_id = row.get("id")
            if not track_id:
                continue
            energy = _float(row.get("energy"), 0.5)
            danceability = _float(row.get("danceability"), 0.5)
            tempo = _float(row.get("tempo"), 120.0)
            valence = _float(row.get("valence"), 0.5)
            artists_raw = row.get("artists", "")
            artist_list, artists_text = parse_artists(artists_raw)
            feature_vector = [danceability, energy, tempo / 200.0, valence]
            tracks.append(
                {
                    "id": track_id,
                    "name": row.get("name", "Unknown track"),
                    "artists": artists_text,
                    "artist_list": artist_list,
                    "artist_set": set(artist_list),
                    "energy": energy,
                    "danceability": danceability,
                    "tempo": tempo,
                    "valence": valence,
                    "feature_vector": feature_vector,
                }
            )
    return tracks


def _euclidean_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    distance = math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(vec_a, vec_b)))
    return 1 / (1 + distance)


class RecommenderService:
    """Maps heart-rate signals to the next track choice."""

    def __init__(self, data_path: Path | None = None, history_seconds: int = 60):
        default_path = Path(__file__).resolve().parents[2] / "data" / "data.csv"
        self.data_path = data_path or default_path
        self.tracks = load_tracks(self.data_path)
        self.track_index = {track["id"]: track for track in self.tracks}
        self.hr_history: Dict[str, Deque[int]] = defaultdict(lambda: deque(maxlen=history_seconds))

    def observe_hr(self, user_id: str, hr: int) -> None:
        self.hr_history[user_id].append(hr)

    def _trend(self, user_id: str) -> float:
        history = self.hr_history.get(user_id)
        if not history or len(history) <= 1:
            return 0.0
        previous = list(history)[:-1]
        avg_previous = sum(previous) / len(previous)
        return history[-1] - avg_previous

    def get_track(self, track_id: Optional[str]) -> Optional[Dict[str, object]]:
        if not track_id:
            return None
        return self.track_index.get(track_id)

    def recommend(
        self,
        *,
        user_profile: Dict[str, float],
        user_id: str,
        latest_track: Optional[Dict[str, object]],
        blacklist: List[str] | set[str],
        preference_vector: Optional[List[float]] = None,
        exclude_track_ids: Optional[Set[str]] = None,
    ) -> Optional[Dict[str, object]]:
        """Pick the next track using liked-song preferences or energy-based randomness."""
        history = self.hr_history.get(user_id)
        if not history or not self.tracks:
            return None

        rest_hr = float(user_profile.get("rest_hr", 60))
        max_hr = float(user_profile.get("max_hr", 190))
        current_hr = history[-1]
        trend = self._trend(user_id)

        hrr = max(max_hr - rest_hr, 1.0)
        intensity = clamp((current_hr - rest_hr) / hrr, 0.0, 1.0)
        if trend > TREND_THRESHOLD:
            intensity = clamp(intensity + 0.05, 0.0, 1.0)
        elif trend < -TREND_THRESHOLD:
            intensity = clamp(intensity - 0.05, 0.0, 1.0)

        _ = latest_track  # Selection now ignores the currently playing track.
        blacklist_set = set(blacklist)
        excluded = exclude_track_ids or set()
        best_track: Optional[Dict[str, object]] = None

        if preference_vector:
            best_score = -1.0
            for track in self.tracks:
                if track["id"] in blacklist_set or track["id"] in excluded:
                    continue
                preference_similarity = _euclidean_similarity(track["feature_vector"], preference_vector)
                if preference_similarity > best_score:
                    best_score = preference_similarity
                    best_track = track
        else:
            candidates: List[Tuple[Dict[str, object], float]] = []
            for track in self.tracks:
                if track["id"] in blacklist_set or track["id"] in excluded:
                    continue
                energy_alignment = clamp(1 - abs(float(track["energy"]) - intensity), 0.0, 1.0)
                candidates.append((track, energy_alignment))
            if not candidates:
                return None
            weights = [weight for _, weight in candidates]
            if not any(weights):
                weights = None
            best_track = random.choices([track for track, _ in candidates], weights=weights, k=1)[0]

        if best_track is None:
            return None

        return {
            "track_id": best_track["id"],
            "track_name": best_track.get("name", ""),
            "artists": best_track.get("artists", ""),
            "energy": best_track.get("energy", ""),
            "danceability": best_track.get("danceability", ""),
            "tempo": best_track.get("tempo", ""),
            "valence": best_track.get("valence", ""),
            "artist_set": best_track.get("artist_set", set()),
        }
