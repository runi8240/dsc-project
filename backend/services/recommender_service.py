import ast
import csv
import math
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
        """Pick the next track using a multi-factor score."""
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

        target_valence = float(latest_track.get("valence", 0.5)) if latest_track else 0.5
        target_dance = float(latest_track.get("danceability", 0.5)) if latest_track else 0.5
        target_tempo = float(latest_track.get("tempo", 120.0)) if latest_track else 120.0
        target_vector = [target_dance, intensity, target_tempo / 200.0, target_valence]
        last_artist_set = set(latest_track.get("artist_set", [])) if latest_track else set()
        blacklist_set = set(blacklist)
        excluded = exclude_track_ids or set()
        preference_similarity_default = 0.5

        best_track: Optional[Dict[str, object]] = None
        best_score = -1.0
        for track in self.tracks:
            if track["id"] in blacklist_set or track["id"] in excluded:
                continue
            energy_alignment = 1 - abs(float(track["energy"]) - intensity)
            valence_similarity = 1 - min(1.0, abs(float(track["valence"]) - target_valence))
            dance_similarity = 1 - min(1.0, abs(float(track["danceability"]) - target_dance))
            artist_set = track.get("artist_set", set())
            artist_similarity = self._artist_similarity(last_artist_set, artist_set)
            feature_similarity = _euclidean_similarity(track["feature_vector"], target_vector)
            preference_similarity = (
                _euclidean_similarity(track["feature_vector"], preference_vector)
                if preference_vector
                else preference_similarity_default
            )
            combined_score = (
                0.35 * energy_alignment
                + 0.2 * artist_similarity
                + 0.15 * preference_similarity
                + 0.1 * valence_similarity
                + 0.1 * dance_similarity
                + 0.1 * feature_similarity
            )
            if combined_score > best_score:
                best_score = combined_score
                best_track = track

        if not best_track:
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

    @staticmethod
    def _artist_similarity(previous: set[str], current: set[str]) -> float:
        if not previous or not current:
            return 0.5
        intersection = len(previous & current)
        union = len(previous | current)
        if union == 0:
            return 0.0
        return intersection / union
