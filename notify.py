"""Telegram + estado en disco (volumen Railway /data)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

import config

log = logging.getLogger("notify")


class Telegram:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._c = client
        self.enabled = bool(config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID)
        if not self.enabled:
            log.warning("Telegram sin configurar — solo logs")

    async def send(self, text: str) -> bool:
        if not self.enabled:
            log.info("[tg-off] %s", text[:200])
            return False
        url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        for attempt in range(2):
            try:
                r = await self._c.post(url, json=payload, timeout=15)
                if r.status_code == 429 and attempt == 0:
                    try:
                        wait = float(r.json().get("parameters", {}).get("retry_after", 3))
                    except Exception:  # noqa: BLE001
                        wait = 3.0
                    await asyncio.sleep(min(wait, 30.0) + 0.5)
                    continue
                ok = r.status_code == 200 and r.json().get("ok") is True
                if not ok:
                    log.error("Telegram error: %s %s", r.status_code, r.text[:200])
                return ok
            except Exception as exc:  # noqa: BLE001
                log.error("Telegram falló: %s", exc)
                return False
        return False


class State:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {
            "closed": 0,
            "wins": 0,
            "losses": 0,
            "day": "",
            "day_trades": 0,
            "day_losses": 0,
            "day_signals": 0,
            "open": {},
            "last_signal": {},
            "cooldown": {},
        }
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                self.data.update(json.loads(self.path.read_text()))
        except Exception as exc:  # noqa: BLE001
            log.warning("State load: %s", exc)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=2))
        except Exception as exc:  # noqa: BLE001
            log.warning("State save: %s", exc)

    def roll_day(self) -> None:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if self.data.get("day") != today:
            self.data["day"] = today
            self.data["day_trades"] = 0
            self.data["day_losses"] = 0
            self.data["day_signals"] = 0
            # limpia cooldowns caducados y señales viejas (con cientos de pares crece)
            now = time.time()
            self.data["cooldown"] = {
                k: v for k, v in self.data.get("cooldown", {}).items() if v > now
            }
            self.data["last_signal"] = {
                k: v for k, v in self.data.get("last_signal", {}).items() if now - v < 86400
            }
            self.save()

    # ── señales: solo cooldown por símbolo (en SIGNAL no hay topes diarios) ──
    def can_signal(self, symbol: str) -> bool:
        return time.time() >= self.data.get("cooldown", {}).get(symbol, 0)

    def mark_signal(self, symbol: str) -> None:
        cd = config.COOLDOWN_BARS * config.TF_SEC
        self.data.setdefault("last_signal", {})[symbol] = time.time()
        self.data.setdefault("cooldown", {})[symbol] = time.time() + cd
        self.data["day_signals"] = int(self.data.get("day_signals", 0)) + 1
        self.save()

    # ── ejecución real: topes diarios y posiciones abiertas ──
    def can_trade_live(self, symbol: str) -> bool:
        self.roll_day()
        if self.data["day_trades"] >= config.MAX_TRADES_DAY:
            return False
        if self.data["day_losses"] >= config.MAX_LOSSES_DAY:
            return False
        opens = self.data.get("open", {})
        if symbol in opens or len(opens) >= config.MAX_OPEN:
            return False
        return True

    def mark_trade(self, symbol: str, info: dict) -> None:
        self.data["day_trades"] = int(self.data.get("day_trades", 0)) + 1
        self.data.setdefault("open", {})[symbol] = info
        self.save()

    def close_trade(self, symbol: str, won: bool) -> None:
        self.data.get("open", {}).pop(symbol, None)
        self.data["closed"] = int(self.data.get("closed", 0)) + 1
        if won:
            self.data["wins"] = int(self.data.get("wins", 0)) + 1
        else:
            self.data["losses"] = int(self.data.get("losses", 0)) + 1
            self.data["day_losses"] = int(self.data.get("day_losses", 0)) + 1
        self.save()
