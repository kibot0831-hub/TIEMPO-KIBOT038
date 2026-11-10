"""Bucle principal: escanea todos los símbolos al cierre de cada vela LTF."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from typing import Dict, List, Optional, Tuple

from bingx import BingXClient
from config import Config
from messages import format_event, format_signal
from models import Candle, Signal, SymbolMeta
from strategy import MIN_BARS, build_series, evaluate
from telegram import Telegram
from tracker import Tracker
from universe import build_universe

log = logging.getLogger("scanner")


class Scanner:
    def __init__(self, cfg: Config, bingx: BingXClient, tg: Telegram, tracker: Tracker):
        self.cfg = cfg
        self.bingx = bingx
        self.tg = tg
        self.tracker = tracker
        self.universe: Dict[str, SymbolMeta] = {}
        self._universe_ts = 0.0
        self.ltf_ms = cfg.ltf_sec * 1000
        # Velas necesarias: ~2.3 días (para PDH/PDL) y mínimo 500, máx. 1440 (límite BingX)
        self.limit = min(1440, max(500, int(2.3 * 86400 / cfg.ltf_sec) + 40))
        self._sem = asyncio.Semaphore(cfg.concurrency)

    # ---- universo ------------------------------------------------------------------------
    async def refresh_universe(self) -> None:
        contracts = await self.bingx.contracts()
        try:
            volumes = await self.bingx.tickers()
        except Exception as e:
            log.warning("No se pudo leer el ticker 24h (%s): sin filtro de volumen", e)
            volumes = {}  # sin datos de volumen: build_universe omite el filtro
        uni = build_universe(contracts, volumes, self.cfg)
        if not uni:
            raise RuntimeError("Universo vacío: revisa la conexión con BingX / filtros")
        self.universe = uni
        self._universe_ts = time.time()
        n_c = sum(1 for m in uni.values() if m.kind == "crypto")
        oil = [m.symbol for m in uni.values() if m.kind == "oil"]
        log.info("Universo: %d criptos, petróleo=%s, otros=%d", n_c, oil or "NO ENCONTRADO",
                 len(uni) - n_c - len(oil))
        if self.cfg.include_oil and not oil:
            log.warning("No se encontró ningún contrato de petróleo. Define EXTRA_SYMBOLS con el símbolo exacto de BingX.")

    def universe_summary(self) -> Tuple[int, List[str], int]:
        n_c = sum(1 for m in self.universe.values() if m.kind == "crypto")
        oil = [m.symbol for m in self.universe.values() if m.kind == "oil"]
        return n_c, oil, len(self.universe) - n_c - len(oil)

    # ---- datos ---------------------------------------------------------------------------
    async def _load(self, sym: str) -> Optional[List[Candle]]:
        async with self._sem:
            try:
                return await self.bingx.klines(sym, self.cfg.ltf, self.limit)
            except Exception as e:
                log.debug("klines %s falló: %s", sym, e)
                return None

    # ---- un ciclo de análisis --------------------------------------------------------------
    async def scan_once(self, once: bool = False) -> None:
        cfg = self.cfg
        t0 = time.time()
        if time.time() - self._universe_ts > cfg.universe_refresh_min * 60:
            try:
                await self.refresh_universe()
            except Exception as e:
                log.warning("No se pudo refrescar el universo: %s", e)

        # Símbolos con operación abierta que ya no están en el universo (bajó el volumen, etc.)
        # se siguen igualmente hasta cerrar la operación; solo NO generan señales nuevas.
        syms = list(self.universe) + [s for s in self.tracker.trades if s not in self.universe]
        results = await asyncio.gather(*(self._load(s) for s in syms))
        now_ms = int(time.time() * 1000)

        failures = 0
        rejects: Counter = Counter()
        candidates: List[Signal] = []
        notifications: List[str] = []

        for sym, candles in zip(syms, results):
            if not candles:
                failures += 1
                continue
            closed = [c for c in candles if c.t + self.ltf_ms <= now_ms]  # descarta la vela en formación
            if len(closed) < MIN_BARS:
                rejects["pocas_velas"] += 1
                continue
            last_close_ms = closed[-1].t + self.ltf_ms
            stale = (not once) and now_ms - last_close_ms > 2 * self.ltf_ms
            tracked = self.tracker.has_open(sym) is not None
            if stale and not tracked:
                rejects["sin_datos_recientes"] += 1  # mercado parado / contrato ilíquido
                continue

            S = build_series(closed, cfg)
            # 1) seguimiento de operaciones abiertas (TP / SL / CBC cambia de color / tiempo máx.)
            for tr, ev in self.tracker.update(sym, closed, S.cbc, now_ms):
                notifications.append(format_event(tr, ev, cfg, self.tracker.stats_line()))
            if sym not in self.universe:
                continue  # solo seguimiento: fuera del universo no se abren operaciones nuevas
            if stale:
                rejects["sin_datos_recientes"] += 1
                continue

            # 2) señal nueva en la última vela cerrada
            sig, why = evaluate(S, len(closed) - 1, cfg, sym)
            if sig is None:
                rejects[why] += 1
                continue
            if self.tracker.already_alerted(sym, sig.side, sig.t) and not once:
                rejects["duplicada"] += 1
                continue
            open_tr = self.tracker.has_open(sym)
            if open_tr is not None:
                if open_tr.side != sig.side and cfg.exit_on_opposite:
                    res = self.tracker.close_opposite(sym, sig.entry)
                    if res:
                        notifications.append(format_event(res[0], res[1], cfg, self.tracker.stats_line()))
                else:
                    rejects["ya_en_operacion"] += 1
                    continue
            candidates.append(sig)

        # Mejores primero: más confluencia, más espacio libre, más volumen relativo
        candidates.sort(key=lambda s: (s.score, s.room_r if s.room_r is not None else 99.0, s.rvol), reverse=True)
        if len(candidates) > cfg.max_signals_per_scan:
            rejects["recortadas_por_limite"] += len(candidates) - cfg.max_signals_per_scan
            candidates = candidates[:cfg.max_signals_per_scan]

        for text in notifications:
            await self.tg.send(text)
        sent = 0
        for sig in candidates:
            meta = self.universe[sig.symbol]
            if await self.tg.send(format_signal(sig, meta, cfg)):
                self.tracker.register(sig, meta.price_prec)
                sent += 1
                log.info("SEÑAL %s %s entrada=%s sl=%s score=%d/%d", sig.side, sig.symbol,
                         sig.entry, sig.sl, sig.score, sig.max_score)
        self.tracker.save()

        log.info("Scan: %d símbolos en %.1fs · señales=%d · avisos=%d · fallos=%d · descartes=%s",
                 len(syms), time.time() - t0, sent, len(notifications), failures, dict(rejects))
        if syms and failures > len(syms) * 0.5:
            log.warning("Más del 50%% de peticiones fallaron: ¿límite de BingX o red? Baja REQUESTS_PER_SECOND.")

    # ---- bucle -------------------------------------------------------------------------------
    async def run(self) -> None:
        cfg = self.cfg
        while True:
            now = time.time()
            nxt = (int(now // cfg.ltf_sec) + 1) * cfg.ltf_sec + cfg.scan_delay_sec
            await asyncio.sleep(max(0.0, nxt - now))
            try:
                await self.scan_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Error en el ciclo de análisis (se reintenta en la próxima vela)")
