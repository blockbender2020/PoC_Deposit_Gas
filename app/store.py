from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Callable, List, Optional

from app.models import GaslessIntent


class IntentStore:
    def __init__(self, database_path: Path, *, legacy_file_path: Optional[Path] = None) -> None:
        self.database_path = database_path
        self.legacy_file_path = legacy_file_path
        self._lock = threading.RLock()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=NORMAL")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS intents (
                        id TEXT PRIMARY KEY,
                        source_tx_hash TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        status TEXT NOT NULL,
                        payload TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_intents_created_at ON intents(created_at, id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_intents_source_tx_hash ON intents(source_tx_hash)"
                )
                self._import_legacy_file_if_needed(connection)

    def list(self) -> List[GaslessIntent]:
        with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT payload FROM intents ORDER BY created_at ASC, id ASC"
                ).fetchall()
        return [self._decode_payload(row["payload"]) for row in rows]

    def get(self, intent_id: str) -> Optional[GaslessIntent]:
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload FROM intents WHERE id = ?",
                    (intent_id,),
                ).fetchone()
        return None if row is None else self._decode_payload(row["payload"])

    def create(self, intent: GaslessIntent) -> None:
        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO intents (id, source_tx_hash, created_at, updated_at, status, payload)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    self._encode_intent(intent),
                )

    def update(self, intent_id: str, updater: Callable[[GaslessIntent], GaslessIntent]) -> Optional[GaslessIntent]:
        updated: Optional[GaslessIntent] = None
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload FROM intents WHERE id = ?",
                    (intent_id,),
                ).fetchone()
                if row is None:
                    return None

                current = self._decode_payload(row["payload"])
                updated = updater(current)
                connection.execute(
                    """
                    UPDATE intents
                    SET source_tx_hash = ?, updated_at = ?, status = ?, payload = ?
                    WHERE id = ?
                    """,
                    (
                        updated.sourceTxId,
                        updated.updatedAt,
                        updated.status,
                        self._encode_payload(updated),
                        intent_id,
                    ),
                )
        return updated

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _import_legacy_file_if_needed(self, connection: sqlite3.Connection) -> None:
        if not self.legacy_file_path or not self.legacy_file_path.exists():
            return

        row = connection.execute("SELECT COUNT(*) AS count FROM intents").fetchone()
        if row is None or int(row["count"]) != 0:
            return

        raw_text = self.legacy_file_path.read_text().strip()
        if not raw_text:
            return

        try:
            raw_items = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Legacy intent store file is corrupted: {self.legacy_file_path} ({exc})"
            ) from exc

        intents = [GaslessIntent.model_validate(item) for item in raw_items]
        connection.executemany(
            """
            INSERT INTO intents (id, source_tx_hash, created_at, updated_at, status, payload)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [self._encode_intent(intent) for intent in intents],
        )

    @staticmethod
    def _decode_payload(payload: str) -> GaslessIntent:
        try:
            return GaslessIntent.model_validate_json(payload)
        except Exception as exc:
            raise RuntimeError(f"Intent payload in database is corrupted ({exc})") from exc

    @staticmethod
    def _encode_payload(intent: GaslessIntent) -> str:
        return intent.model_dump_json()

    def _encode_intent(self, intent: GaslessIntent) -> tuple[str, Optional[str], str, str, str, str]:
        return (
            intent.id,
            intent.sourceTxId,
            intent.createdAt,
            intent.updatedAt,
            intent.status,
            self._encode_payload(intent),
        )
