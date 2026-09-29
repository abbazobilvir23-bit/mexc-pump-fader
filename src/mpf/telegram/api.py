"""Тонкий клиент Telegram Bot API на stdlib (urllib). Без внешних зависимостей."""

from __future__ import annotations

import json
import urllib.request
from typing import Any


class TelegramAPI:
    def __init__(self, token: str, timeout: float = 30.0) -> None:
        self.token = token
        self.base = f"https://api.telegram.org/bot{token}"
        self.timeout = timeout

    def call(self, method: str, **payload: Any) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base}/{method}",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_updates(self, offset: int | None = None, timeout: int = 25) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        # HTTP-таймаут должен быть больше long-poll таймаута
        self.timeout = timeout + 15
        result = self.call("getUpdates", **payload)
        self.timeout = 30.0
        return list(result.get("result", []))

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.call("sendMessage", **payload)

    def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.call("editMessageText", **payload)

    def answer_callback_query(self, callback_id: str, text: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        return self.call("answerCallbackQuery", **payload)

    def set_my_commands(self, commands: list[tuple[str, str]]) -> dict[str, Any]:
        return self.call(
            "setMyCommands",
            commands=[{"command": c, "description": d} for c, d in commands],
        )