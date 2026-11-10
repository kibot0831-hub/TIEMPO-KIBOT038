"""Prueba de integración offline del bucle en vivo (BingX y Telegram simulados)."""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from models import Candle, SymbolMeta
from scanner import Scanner
from tracker import Tracker
from test_pullback import LTF, mk_cfg

NOW0 = 1_800_000_000  # segundos (instante arbitrario)


class FakeBingX:
    def __init__(self):
        self.candles = {}

    async def klines(self, sym, interval, limit=500, **kw):
        return list(self.candles[sym])[-limit:]


class FakeTG:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)
        return True


def series_ending_at(boundary_ms, n=300):
    """Tendencia alcista con n velas cerradas; la última cierra en `boundary_ms`."""
    rows = []
    for k in range(n):
        t = boundary_ms - (n - k) * LTF
        o = 100 + 0.5 * k
        c = o + 0.8
        rows.append(Candle(t, o, c + 0.1, o - 0.1, c, 1000.0))
    return rows


class TestLiveLoop(unittest.TestCase):
    def test_signal_then_flip_exit(self):
        cfg = mk_cfg()
        boundary = (NOW0 * 1000 // LTF) * LTF  # cierre de la última vela
        rows = series_ending_at(boundary)
        last = rows[-1]
        # última vela cerrada: ROJA que no pierde el mínimo anterior -> señal LONG
        rows[-1] = Candle(last.t, last.o + 0.8 + 0.0, last.o + 0.8 + 0.1, last.o + 0.5 - 0.1, last.o + 0.5, 1000.0)

        bingx, tg = FakeBingX(), FakeTG()
        bingx.candles["TEST-USDT"] = rows
        tracker = Tracker(cfg)
        sc = Scanner(cfg, bingx, tg, tracker)
        sc.universe = {"TEST-USDT": SymbolMeta("TEST-USDT", price_prec=2)}
        sc._universe_ts = 1e18

        with mock.patch("time.time", return_value=boundary / 1000 + 5):
            asyncio.run(sc.scan_once())
        self.assertEqual(len(tg.sent), 1, tg.sent)
        self.assertIn("LONG · TEST-USDT", tg.sent[0])
        self.assertIsNotNone(tracker.has_open("TEST-USDT"))
        sl = tracker.has_open("TEST-USDT").sl

        # Siguiente vela: cierra por debajo del mínimo anterior sin tocar el SL -> CBC pasa a rojo
        prev = rows[-1]
        c = prev.l - 0.02
        assert c - 0.01 > sl
        nxt = Candle(prev.t + LTF, prev.c, prev.c, c - 0.01, c, 1000.0)
        bingx.candles["TEST-USDT"] = rows + [nxt]
        with mock.patch("time.time", return_value=(boundary + LTF) / 1000 + 5):
            asyncio.run(sc.scan_once())
        self.assertEqual(len(tg.sent), 2, tg.sent)
        self.assertIn("CBC cambió a ROJO", tg.sent[1])
        self.assertIsNone(tracker.has_open("TEST-USDT"))
        self.assertEqual(tracker.stats["n"], 1)

    def test_no_signal_on_green_candle(self):
        cfg = mk_cfg()
        boundary = (NOW0 * 1000 // LTF) * LTF
        bingx, tg = FakeBingX(), FakeTG()
        bingx.candles["TEST-USDT"] = series_ending_at(boundary)  # última vela VERDE
        sc = Scanner(cfg, bingx, tg, Tracker(cfg))
        sc.universe = {"TEST-USDT": SymbolMeta("TEST-USDT", price_prec=2)}
        sc._universe_ts = 1e18
        with mock.patch("time.time", return_value=boundary / 1000 + 5):
            asyncio.run(sc.scan_once())
        self.assertEqual(tg.sent, [])


if __name__ == "__main__":
    unittest.main()
