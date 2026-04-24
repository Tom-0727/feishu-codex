"""Runtime-local Codex thread persistence."""

import json
from pathlib import Path


class SessionStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def get_thread_id(self) -> str | None:
        data = self._load()
        value = data.get("thread_id")
        return value if isinstance(value, str) and value else None

    def save_thread_id(self, thread_id: str) -> None:
        self._write({"thread_id": thread_id})

    def clear(self) -> None:
        self._write({})

    def _load(self) -> dict[str, object]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, object]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
