import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Optional
import time


@dataclass
class FeedbackEvent:
    event_type: str
    track_id: Optional[str]
    timestamp: float
    metadata: Dict[str, Any]


class FeedbackLogger:
    """Persist user feedback for future training."""

    def __init__(self, output_path: Path | None = None):
        default_path = Path(__file__).resolve().parents[2] / "data" / "feedback.jsonl"
        self.output_path = output_path or default_path
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event_type: str, track_id: str | None, metadata: Dict[str, Any] | None = None) -> FeedbackEvent:
        event = FeedbackEvent(
            event_type=event_type,
            track_id=track_id,
            timestamp=time.time(),
            metadata=metadata or {},
        )
        self._append_event(event)
        return event

    def _append_event(self, event: FeedbackEvent) -> None:
        line = json.dumps(asdict(event), ensure_ascii=True)
        with self.output_path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
