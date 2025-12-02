import ast
import csv
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Optional

HR_MIN = 55
HR_MAX = 180
TREND_THRESHOLD = 5  # bpm difference needed before nudging target energy


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def parse_artists(value: str) -> str:
    """Convert the artists column (a stringified list) into readable text."""
    try:
        parsed = ast.literal_eval(value)
        if isinstance(parsed, list):
            return ", ".join(str(item) for item in parsed)
        return str(parsed)
    except (ValueError, SyntaxError):
        return value.strip("[]'\" ")


def load_tracks(data_path: Path) -> List[Dict[str, str]]:
    """Load available tracks and their energy from the local CSV."""
    tracks: List[Dict[str, str]] = []
    if not data_path.exists():
        print(f"No {data_path.name} found; automatic track selection disabled.")
        return tracks

    with data_path.open(newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            track_id = row.get("id")
            energy = row.get("energy")
            if not track_id or energy is None:
                continue
            try:
                energy_val = float(energy)
            except (TypeError, ValueError):
                continue

            artists_raw = row.get("artists", "")
            artists_text = parse_artists(artists_raw)
            tracks.append(
                {
                    "id": track_id,
                    "name": row.get("name", "Unknown track"),
                    "artists": artists_text,
                    "energy": energy_val,
                }
            )
    return tracks


class RecommenderService:
    """Maps heart-rate signals to the next track choice."""

    def __init__(self, data_path: Path | None = None, history_seconds: int = 60):
        self.data_path = data_path or Path(__file__).with_name("data.csv")
        self.tracks = load_tracks(self.data_path)
        self.hr_history: Deque[int] = deque(maxlen=history_seconds)

    def observe_hr(self, hr: int) -> None:
        self.hr_history.append(hr)

    def _trend(self) -> float:
        if len(self.hr_history) <= 1:
            return 0.0
        previous = list(self.hr_history)[:-1]
        avg_previous = sum(previous) / len(previous)
        return self.hr_history[-1] - avg_previous

    def recommend(self) -> Optional[Dict[str, str]]:
        """Pick the next track whose energy best matches the heart-rate trend."""
        if not self.hr_history or not self.tracks:
            return None

        current_hr = self.hr_history[-1]
        trend = self._trend()

        normalized = clamp((current_hr - HR_MIN) / (HR_MAX - HR_MIN), 0.0, 1.0)
        if trend > TREND_THRESHOLD:
            normalized = clamp(normalized + 0.1, 0.0, 1.0)
        elif trend < -TREND_THRESHOLD:
            normalized = clamp(normalized - 0.1, 0.0, 1.0)

        best = min(self.tracks, key=lambda track: abs(track["energy"] - normalized))
        return {
            "track_id": best["id"],
            "track_name": best.get("name", ""),
            "artists": best.get("artists", ""),
            "energy": best.get("energy", ""),
        }
