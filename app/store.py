from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable, List, Optional

from app.models import GaslessIntent


class IntentStore:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self.file_path.write_text("[]\n")

    def list(self) -> List[GaslessIntent]:
        raw = json.loads(self.file_path.read_text())
        return [GaslessIntent.model_validate(item) for item in raw]

    def get(self, intent_id: str) -> Optional[GaslessIntent]:
        for intent in self.list():
            if intent.id == intent_id:
                return intent
        return None

    def create(self, intent: GaslessIntent) -> None:
        with self._lock:
            items = self.list()
            items.append(intent)
            self._write(items)

    def update(self, intent_id: str, updater: Callable[[GaslessIntent], GaslessIntent]) -> Optional[GaslessIntent]:
        updated: Optional[GaslessIntent] = None
        with self._lock:
            items = self.list()
            next_items = []
            for item in items:
                if item.id != intent_id:
                    next_items.append(item)
                    continue
                updated = updater(item)
                next_items.append(updated)
            self._write(next_items)
        return updated

    def _write(self, items: List[GaslessIntent]) -> None:
        self.file_path.write_text(json.dumps([item.model_dump() for item in items], indent=2) + "\n")
