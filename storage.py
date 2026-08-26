from __future__ import annotations

import json
import os
import threading
from typing import Any

from config import settings

_lock = threading.Lock()


def _empty_state() -> dict[str, Any]:
    return {
        "chat_ids": [],          # кто подписан на уведомления об изменениях
        "group_id": None,        # найденный id группы (кэш, чтобы не искать каждый раз)
        "group_label": None,     # как группа называется на сайте (для проверки)
        "lessons": {},           # snapshot: key -> lesson dict
    }


def load() -> dict[str, Any]:
    with _lock:
        if not os.path.exists(settings.state_file):
            return _empty_state()
        try:
            with open(settings.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in _empty_state().items():
                    data.setdefault(k, v)
                return data
        except (json.JSONDecodeError, OSError):
            return _empty_state()


def save(state: dict[str, Any]) -> None:
    with _lock:
        tmp = settings.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, settings.state_file)


def add_subscriber(chat_id: int) -> bool:
    state = load()
    if chat_id in state["chat_ids"]:
        return False
    state["chat_ids"].append(chat_id)
    save(state)
    return True


def remove_subscriber(chat_id: int) -> bool:
    state = load()
    if chat_id not in state["chat_ids"]:
        return False
    state["chat_ids"].remove(chat_id)
    save(state)
    return True
