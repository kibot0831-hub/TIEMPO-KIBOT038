"""Detección GRAB sobre niveles de liquidez (high/low lookback) + score."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class Signal:
    symbol: str
    side: str  # LONG | SHORT
    src: str
    score: int
    entry: float
    sl: float
    tp1: float
    tp2: float
    level: float
    level_name: str
    rvol: float
    dist_atr: float
    atr: float
    btc_ok: bool
    tf: str = "15m"


def _atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14) -> float:
    if len(c) < n + 1:
        return 0.0
    prev_c = c[:-1]
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - prev_c), np.abs(l[1:] - prev_c)))
    if len(tr) < n:
        return float(np.mean(tr)) if len(tr) else 0.0
    return float(np.mean(tr[-n:]))


def _rvol(v: np.ndarray, n: int = 20) -> float:
    if len(v) < n + 1 or np.mean(v[-n - 1 : -1]) <= 0:
        return 0.0
    return float(v[-1] / np.mean(v[-n - 1 : -1]))


def detect_grab(
    symbol: str,
    ohlcv: list[list],
    *,
    lookback: int = 80,
    min_wick_atr: float = 0.30,
    max_depth_atr: float = 1.5,
    rvol_min: float = 1.1,
    near_atr_max: float = 0.6,
    require_near: bool = True,
    sl_atr_buf: float = 0.35,
    tp1_r: float = 1.0,
    tp2_r: float = 2.0,
    min_rr: float = 1.5,
    allow_long: bool = True,
    allow_short: bool = True,
    btc_ok: bool = True,
) -> Optional[Signal]:
    """Barrido de máximo/mínimo de lookback + cierre de vuelta (GRAB)."""
    if len(ohlcv) < lookback + 5:
        return None
    arr = np.array(ohlcv[-lookback - 5 :], dtype=float)
    h, l, c, v = arr[:, 2], arr[:, 3], arr[:, 4], arr[:, 5]
    atr = _atr(h, l, c)
    if atr <= 0:
        return None
    rvol = _rvol(v)
    if rvol < rvol_min:
        return None

    # nivel: high/low de la ventana excluyendo la última vela
    window_h = h[:-1]
    window_l = l[:-1]
    lvl_hi = float(np.max(window_h[-lookback:]))
    lvl_lo = float(np.min(window_l[-lookback:]))

    last_h, last_l, last_c, last_o = float(h[-1]), float(l[-1]), float(c[-1]), float(arr[-1, 1])
    wick_up = last_h - max(last_o, last_c)
    wick_dn = min(last_o, last_c) - last_l

    # SHORT GRAB: barre high y cierra abajo
    if allow_short and last_h > lvl_hi:
        depth = last_h - lvl_hi
        if depth <= max_depth_atr * atr and wick_up >= min_wick_atr * atr and last_c < lvl_hi:
            dist = abs(last_c - lvl_hi) / atr
            if require_near and dist > near_atr_max:
                return None
            entry = last_c
            sl = last_h + sl_atr_buf * atr
            risk = sl - entry
            if risk <= 0 or risk / entry > 0.05:
                return None
            tp1 = entry - tp1_r * risk
            tp2 = entry - tp2_r * risk
            score = _score(rvol, depth / atr, dist, wick_up / atr, btc_ok)
            return Signal(
                symbol=symbol,
                side="SHORT",
                src="GRAB",
                score=score,
                entry=entry,
                sl=sl,
                tp1=tp1,
                tp2=tp2,
                level=lvl_hi,
                level_name="SWING_H",
                rvol=rvol,
                dist_atr=dist,
                atr=atr,
                btc_ok=btc_ok,
            )

    # LONG GRAB: barre low y cierra arriba
    if allow_long and last_l < lvl_lo:
        depth = lvl_lo - last_l
        if depth <= max_depth_atr * atr and wick_dn >= min_wick_atr * atr and last_c > lvl_lo:
            dist = abs(last_c - lvl_lo) / atr
            if require_near and dist > near_atr_max:
                return None
            entry = last_c
            sl = last_l - sl_atr_buf * atr
            risk = entry - sl
            if risk <= 0 or risk / entry > 0.05:
                return None
            tp1 = entry + tp1_r * risk
            tp2 = entry + tp2_r * risk
            score = _score(rvol, depth / atr, dist, wick_dn / atr, btc_ok)
            return Signal(
                symbol=symbol,
                side="LONG",
                src="GRAB",
                score=score,
                entry=entry,
                sl=sl,
                tp1=tp1,
                tp2=tp2,
                level=lvl_lo,
                level_name="SWING_L",
                rvol=rvol,
                dist_atr=dist,
                atr=atr,
                btc_ok=btc_ok,
            )
    return None


def _score(rvol: float, depth_atr: float, dist_atr: float, wick_atr: float, btc_ok: bool) -> int:
    s = 50
    s += min(20, int(rvol * 8))
    s += min(10, int(wick_atr * 8))
    s += 5 if 0.1 <= depth_atr <= 1.0 else 0
    s += 10 if dist_atr <= 0.4 else (5 if dist_atr <= 0.6 else 0)
    s += 10 if btc_ok else 0
    return int(max(0, min(100, s)))


def btc_aligned(btc_ohlcv: list[list], side: str, ema_len: int = 50) -> bool:
    if not btc_ohlcv or len(btc_ohlcv) < ema_len + 2:
        return True  # sin dato → no bloquea si require está off a nivel config
    c = np.array([x[4] for x in btc_ohlcv], dtype=float)
    ema = _ema(c, ema_len)
    if ema is None:
        return True
    if side == "LONG":
        return c[-1] > ema
    return c[-1] < ema


def _ema(x: np.ndarray, n: int) -> Optional[float]:
    if len(x) < n:
        return None
    k = 2 / (n + 1)
    e = x[0]
    for v in x[1:]:
        e = v * k + e * (1 - k)
    return float(e)
