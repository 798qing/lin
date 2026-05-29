from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

from analysis.db import DEFAULT_DB
from notify.telegram_commands import handle_command


TELEGRAM_API = "https://api.telegram.org"


def run_polling(
    token: Optional[str] = None,
    allowed_chat_id: Optional[str] = None,
    db_path: Path = DEFAULT_DB,
    once: bool = False,
    poll_seconds: int = 2,
) -> None:
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    allowed_chat_id = allowed_chat_id or os.environ.get("TELEGRAM_ALLOWED_CHAT_ID")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required.")
    offset: Optional[int] = None
    while True:
        updates = get_updates(token, offset=offset, timeout=poll_seconds)
        for update in updates:
            offset = int(update["update_id"]) + 1
            message = update.get("message") or update.get("edited_message") or {}
            chat = message.get("chat") or {}
            chat_id = str(chat.get("id", ""))
            if allowed_chat_id and chat_id != str(allowed_chat_id):
                continue
            text = message.get("text")
            if not text:
                continue
            result = handle_command(text, db_path)
            send_message(token, chat_id, result["response"])
        if once:
            break


def get_updates(token: str, offset: Optional[int] = None, timeout: int = 2) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    response = _request(token, "getUpdates", params)
    if not response.get("ok"):
        raise RuntimeError(f"Telegram getUpdates failed: {response}")
    return list(response.get("result", []))


def send_message(token: str, chat_id: str, text: str) -> dict[str, Any]:
    response = _request(token, "sendMessage", {"chat_id": chat_id, "text": text[:3900]})
    if not response.get("ok"):
        raise RuntimeError(f"Telegram sendMessage failed: {response}")
    return response


def _request(token: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    data = urllib.parse.urlencode(params).encode("utf-8")
    request = urllib.request.Request(
        f"{TELEGRAM_API}/bot{token}/{method}",
        data=data,
        headers={"User-Agent": "openclaw-perp-analyst-v0.2"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def sleep_forever() -> None:
    while True:
        time.sleep(3600)
