"""Confirmación en 5m: rechazo / cierre de vuelta."""
from __future__ import annotations

from typing import Optional


def confirm_5m(
    ohlcv_5m: list[list],
    side: str,
    level: float,
    mode: str = "reject",
) -> bool:
    if not ohlcv_5m or len(ohlcv_5m) < 3:
        return False
    o, h, l, c = (float(ohlcv_5m[-1][i]) for i in range(1, 5))
    body = abs(c - o)
    rng = max(h - l, 1e-12)
    body_pct = 100.0 * body / rng

    if side == "LONG":
        # rechazo: mecha abajo, cierre arriba del nivel
        wick = min(o, c) - l
        if mode == "reject":
            return l <= level * 1.001 and c > level and c > o and body_pct >= 30
        return c > level
    else:
        wick = h - max(o, c)
        if mode == "reject":
            return h >= level * 0.999 and c < level and c < o and body_pct >= 30
        return c < level
