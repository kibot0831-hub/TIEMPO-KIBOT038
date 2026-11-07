"""
IPDA GRAB Bot v1.0 — BingX perps · Telegram · filtros papel (solo GRAB score≥85)
MODE=SIGNAL por defecto (no opera).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bingx import BingX
from config import settings
from confirm import confirm_5m
from notify import format_heartbeat, format_signal, send_telegram
from strategy import Signal, btc_aligned, detect_grab

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")


def _utc_hour() -> int:
    return datetime.now(timezone.utc).hour


def _in_session() -> bool:
    h = _utc_hour()
    return settings.session_start <= h < settings.session_end


def _load_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save_json(path: str, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)


def _day_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class State:
    def __init__(self) -> None:
        self.data = _load_json(settings.state_path)
        self._roll_day()

    def _roll_day(self) -> None:
        d = _day_key()
        if self.data.get("day") != d:
            self.data = {
                "day": d,
                "signals_today": 0,
                "trades_today": 0,
                "wins": 0,
                "losses": 0,
                "loss_streak": 0,
                "seen": {},  # symbol+side+hour -> ts
            }
            self.save()

    def save(self) -> None:
        _save_json(settings.state_path, self.data)

    def seen_key(self, symbol: str, side: str) -> str:
        return f"{symbol}:{side}:{_day_key()}:{_utc_hour()}"

    def already_sent(self, symbol: str, side: str) -> bool:
        return self.seen_key(symbol, side) in self.data.get("seen", {})

    def mark_sent(self, symbol: str, side: str) -> None:
        self.data.setdefault("seen", {})[self.seen_key(symbol, side)] = time.time()
        self.data["signals_today"] = int(self.data.get("signals_today", 0)) + 1
        self.save()


async def pick_symbols(bx: BingX) -> list[str]:
    tickers = await bx.swap_tickers()
    quote = settings.quote
    rows = []
    for t in tickers:
        sym = (t.get("symbol") or "").upper()
        if not sym.endswith(f"-{quote}"):
            continue
        try:
            vol = float(t.get("quoteVolume") or t.get("volume") or 0)
        except (TypeError, ValueError):
            vol = 0.0
        rows.append((sym, vol))
    rows.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in rows[: settings.max_symbols]]


async def scan_symbol(
    bx: BingX,
    symbol: str,
    btc_kl: list[list],
    sem: asyncio.Semaphore,
) -> Signal | None:
    async with sem:
        try:
            kl = await bx.klines(symbol, settings.tf_main, limit=200)
        except Exception as e:
            log.debug("%s klines: %s", symbol, e)
            return None
    if not settings.allow_grab:
        return None

    side_btc_long = btc_aligned(btc_kl, "LONG")
    side_btc_short = btc_aligned(btc_kl, "SHORT")

    # probar short y long
    for side, allow, btc_ok in (
        ("SHORT", settings.allow_short, side_btc_short if settings.require_btc_align else True),
        ("LONG", settings.allow_long, side_btc_long if settings.require_btc_align else True),
    ):
        if not allow:
            continue
        if settings.require_btc_align and not btc_ok:
            continue
        sig = detect_grab(
            symbol,
            kl,
            lookback=min(120, max(40, len(kl) - 5)),
            near_atr_max=settings.near_atr_max,
            require_near=settings.require_near_level,
            sl_atr_buf=settings.sl_atr_buf,
            tp1_r=settings.tp1_r,
            tp2_r=settings.tp2_r,
            min_rr=settings.min_rr,
            allow_long=side == "LONG",
            allow_short=side == "SHORT",
            btc_ok=btc_ok,
        )
        if not sig:
            continue
        need = settings.short_min_score if sig.side == "SHORT" else settings.min_score
        if sig.score < need:
            continue
        # confirmación 5m
        if settings.confirm_required:
            try:
                async with sem:
                    kl5 = await bx.klines(symbol, settings.tf_confirm, limit=30)
                if not confirm_5m(kl5, sig.side, sig.level, settings.confirm_mode):
                    continue
            except Exception:
                continue
        return sig
    return None


async def run_cycle(bx: BingX, state: State) -> dict[str, Any]:
    t0 = time.time()
    if not _in_session():
        return {"n_sym": 0, "cycle_s": 0, "skipped": "session"}

    if state.data.get("trades_today", 0) >= settings.max_trades_day:
        return {"n_sym": 0, "cycle_s": 0, "skipped": "max_trades"}

    if state.data.get("loss_streak", 0) >= settings.max_loss_streak:
        return {"n_sym": 0, "cycle_s": 0, "skipped": "loss_streak"}

    symbols = await pick_symbols(bx)
    try:
        btc_kl = await bx.klines(f"BTC-{settings.quote}", settings.tf_main, limit=120)
    except Exception:
        btc_kl = []

    sem = asyncio.Semaphore(settings.workers)
    tasks = [scan_symbol(bx, s, btc_kl, sem) for s in symbols]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    signals: list[Signal] = []
    for r in results:
        if isinstance(r, Signal):
            signals.append(r)

    # ranking por score
    signals.sort(key=lambda x: x.score, reverse=True)

    for sig in signals:
        if state.already_sent(sig.symbol, sig.side):
            continue
        if state.data.get("signals_today", 0) >= settings.max_trades_day * 3:
            break
        need = settings.short_min_score if sig.side == "SHORT" else settings.min_score
        if sig.score < need:
            continue

        payload = {
            "symbol": sig.symbol,
            "side": sig.side,
            "src": sig.src,
            "score": sig.score,
            "entry": sig.entry,
            "sl": sig.sl,
            "tp1": sig.tp1,
            "tp2": sig.tp2,
            "level": sig.level,
            "level_name": sig.level_name,
            "rvol": sig.rvol,
            "dist_atr": sig.dist_atr,
            "btc_ok": sig.btc_ok,
            "tf": settings.tf_main,
            "mode": settings.mode,
        }
        await send_telegram(settings.telegram_token, settings.telegram_chat_id, format_signal(payload))
        state.mark_sent(sig.symbol, sig.side)
        log.info("SIGNAL %s %s score=%s", sig.side, sig.symbol, sig.score)

        if settings.mode == "LIVE" and settings.bingx_api_key:
            log.warning("LIVE order path — implementa tamaño y reduce-only según tu cuenta")
            # No envía órdenes reales por defecto sin qty segura

    return {
        "n_sym": len(symbols),
        "cycle_s": time.time() - t0,
        "signals_today": state.data.get("signals_today", 0),
        "trades_today": state.data.get("trades_today", 0),
        "wins": state.data.get("wins", 0),
        "losses": state.data.get("losses", 0),
        "mode": settings.mode,
        "session": f"{settings.session_start}-{settings.session_end}",
    }


async def main() -> None:
    log.info(
        "IPDA GRAB bot arranque MODE=%s MIN_SCORE=%s SHORT_MIN=%s CONT=%s",
        settings.mode,
        settings.min_score,
        settings.short_min_score,
        settings.allow_cont,
    )
    bx = BingX(settings.bingx_api_key, settings.bingx_api_secret)
    state = State()
    hb_every = max(1, int(1800 / max(settings.scan_sec, 30)))  # ~30 min
    n = 0
    try:
        while True:
            state._roll_day()
            try:
                info = await run_cycle(bx, state)
                log.info("ciclo %s", info)
                n += 1
                if n % hb_every == 0:
                    await send_telegram(
                        settings.telegram_token,
                        settings.telegram_chat_id,
                        format_heartbeat(info),
                    )
            except Exception:
                log.exception("ciclo error")
            await asyncio.sleep(settings.scan_sec)
    finally:
        await bx.close()


if __name__ == "__main__":
    asyncio.run(main())
