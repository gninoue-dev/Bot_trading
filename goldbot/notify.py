"""Notifications Telegram (optionnelles) : entrées, sorties, alertes de risque, erreurs."""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

log = logging.getLogger("goldbot")


class Notifier:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.token = cfg.get("telegram_token", "")
        self.chat = str(cfg.get("telegram_chat_id", ""))
        self.sent: list[str] = []

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat)

    def send(self, text: str) -> None:
        self.sent.append(text)
        if not self.enabled:
            return
        try:
            data = urllib.parse.urlencode({"chat_id": self.chat, "text": text}).encode()
            req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/sendMessage", data=data)
            with urllib.request.urlopen(req, timeout=10) as r:
                json.load(r)
        except Exception as e:  # noqa: BLE001 - une notification ratée ne doit pas arrêter le bot
            log.warning("Telegram : envoi impossible (%s)", e)
