"""Persist chat_id -> codex thread_id mappings to disk."""

import json
from pathlib import Path

_STORE = Path.home() / ".feishu-codex" / "sessions.json"


def _load() -> dict[str, str]:
    if not _STORE.exists():
        return {}
    try:
        return json.loads(_STORE.read_text())
    except Exception:
        return {}


def get(chat_id: str) -> str | None:
    return _load().get(chat_id)


def save(chat_id: str, thread_id: str) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    data = _load()
    data[chat_id] = thread_id
    _STORE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def clear(chat_id: str) -> None:
    data = _load()
    data.pop(chat_id, None)
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    _STORE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
