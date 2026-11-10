"""Pruebas offline (sin red) de la estrategia PULLBACK:

  CBC verde  + vela roja  cerrada -> LONG
  CBC rojo   + vela verde cerrada -> SHORT

Ejecutar:  python -m unittest discover -s tests -t .
"""
from __future__ import annotations

import os
import random
import unittest
from dataclasses import replace
from unittest import mock

from backtest import backtest_symbol
from config import load_config
from messages import format_event, format_signal, format_startup
from models import Candle, SymbolMeta
from strategy import MIN_BARS, build_series, cbc_against, evaluate
from tracker import Tracker

DAY = 86_400_000
LTF = 900_000  # 15m
T0 = (1_700_000_000_000 // DAY) * DAY  # inicio de un día UTC


def mk_cfg(**over):
    with mock.patch.dict(os.environ, {}, clear=True):
        cfg = load_config()
    # Para probar solo el DISPARADOR: sin filtros de confluencia ni espacio, sin guardar estado
    base = dict(min_score=0, min_room_r=0.0, state_file="", dry_run=True)
    base.update(over)
    return replace(cfg, **base)


def candle(k, o, c, wick=0.1):
    return Candle(T0 + k * LTF, o, max(o, c) + wick, min(o, c) - wick, c, 1000.0)


def uptrend(n=300):
    """Velas verdes seguidas; cada cierre rompe el máximo anterior -> CBC verde. ATR ≈ 1."""
    rows = []
    for k in range(n):
        o = 100 + 0.5 * k
        rows.append(candle(k, o, o + 0.8))
    return rows


def downtrend(n=300):
    rows = []
    for k in range(n):
        o = 300 - 0.5 * k
        rows.append(candle(k, o, o - 0.8))
    return rows


def evaluate_last(rows, cfg):
    S = build_series(rows, cfg)
    return evaluate(S, len(rows) - 1, cfg, "TEST-USDT"), S


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = mk_cfg()
        self.assertEqual(cfg.strategy, "pullback")
        self.assertEqual(cfg.ltf, "15m")
        self.assertTrue(cfg.exit_on_cbc_flip)
        self.assertFalse(cfg.pullback_require_htf)

    def test_invalid_strategy(self):
        with mock.patch.dict(os.environ, {"STRATEGY": "otra"}, clear=True):
            with self.assertRaises(ValueError):
                load_config()


class TestPullbackTrigger(unittest.TestCase):
    def setUp(self):
        self.cfg = mk_cfg()

    def test_cbc_is_green_in_uptrend(self):
        S = build_series(uptrend(), self.cfg)
        self.assertTrue(all(S.cbc[5:]))

    def test_long_on_red_candle_in_green_cbc(self):
        rows = uptrend()
        last = rows[-1]
        # vela ROJA que NO pierde el mínimo anterior -> el CBC sigue verde
        rows.append(candle(len(rows), last.c, last.c - 0.3))
        (sig, why), S = evaluate_last(rows, self.cfg)
        self.assertTrue(S.cbc[-1] and S.cbc[-2])
        self.assertEqual(why, "ok")
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "LONG")
        self.assertLess(sig.sl, sig.entry)
        self.assertGreater(sig.tps[0], sig.entry)
        # SL = por debajo del mínimo de la vela de señal (nivel de flip del CBC)
        self.assertLess(sig.sl, rows[-1].l)
        self.assertIn("vela roja", sig.trigger)

    def test_no_long_on_green_candle_in_green_cbc(self):
        (sig, why), _ = evaluate_last(uptrend(), self.cfg)  # la última vela es verde
        self.assertIsNone(sig)
        self.assertEqual(why, "no_setup")

    def test_no_long_if_red_candle_breaks_previous_low(self):
        rows = uptrend()
        last = rows[-1]
        # vela roja que cierra POR DEBAJO del mínimo anterior -> el CBC pasa a rojo (flip)
        rows.append(candle(len(rows), last.c, last.l - 0.5))
        (sig, why), S = evaluate_last(rows, self.cfg)
        self.assertFalse(S.cbc[-1])
        self.assertIsNone(sig)  # tampoco SHORT: la vela es roja, no verde
        self.assertEqual(why, "no_setup")

    def test_short_on_green_candle_in_red_cbc(self):
        rows = downtrend()
        last = rows[-1]
        rows.append(candle(len(rows), last.c, last.c + 0.3))  # vela VERDE sin romper el máximo anterior
        (sig, why), S = evaluate_last(rows, self.cfg)
        self.assertFalse(S.cbc[-1] or S.cbc[-2])
        self.assertEqual(why, "ok")
        self.assertEqual(sig.side, "SHORT")
        self.assertGreater(sig.sl, sig.entry)
        self.assertLess(sig.tps[0], sig.entry)
        self.assertGreater(sig.sl, rows[-1].h)

    def test_no_short_on_red_candle_in_red_cbc(self):
        (sig, why), _ = evaluate_last(downtrend(), self.cfg)
        self.assertIsNone(sig)
        self.assertEqual(why, "no_setup")

    def test_wide_stop_is_skipped(self):
        rows = uptrend()
        last = rows[-1]
        # vela ROJA con mecha inferior larguísima (low muy lejos), pero que cierra sin perder el
        # mínimo anterior -> CBC sigue verde y el stop natural queda > MAX_SL_ATR x ATR
        rows.append(Candle(T0 + len(rows) * LTF, last.c, last.c + 0.05, last.c - 5.0, last.c - 0.3, 1000.0))
        (sig, why), S = evaluate_last(rows, self.cfg)
        self.assertTrue(S.cbc[-1])
        self.assertIsNone(sig)
        self.assertEqual(why, "sl_wide")
        # con SKIP_WIDE_SL=false la señal se acota a MAX_SL_ATR
        cfg = replace(self.cfg, skip_wide_sl=False)
        (sig, why), _ = evaluate_last(rows, cfg)
        self.assertIsNotNone(sig)
        self.assertAlmostEqual(sig.risk, cfg.max_sl_atr * sig.atr)

    def test_min_score_filter(self):
        rows = uptrend()
        last = rows[-1]
        rows.append(candle(len(rows), last.c, last.c - 0.3))
        cfg = replace(self.cfg, min_score=5)  # imposible (máx. 4)
        (sig, why), _ = evaluate_last(rows, cfg)
        self.assertIsNone(sig)
        self.assertEqual(why, "score")

    def test_require_htf(self):
        # tendencia bajista larga + un rebote verde: el CBC HTF (1h) es rojo
        rows = downtrend()
        last = rows[-1]
        rows.append(candle(len(rows), last.c, last.c + 0.3))
        cfg = replace(self.cfg, pullback_require_htf=True)
        (sig, why), _ = evaluate_last(rows, cfg)
        self.assertIsNotNone(sig)  # CBC 15m rojo y CBC 1h rojo: coinciden
        self.assertEqual(sig.side, "SHORT")

    def test_cbc_against(self):
        self.assertTrue(cbc_against("LONG", False))
        self.assertFalse(cbc_against("LONG", True))
        self.assertTrue(cbc_against("SHORT", True))
        self.assertFalse(cbc_against("SHORT", False))

    def test_original_flip_strategy_still_works(self):
        cfg = mk_cfg(strategy="flip")
        # bajista y de golpe sube fuerte: nace el acuerdo CBC alcista
        rows = downtrend(260)
        n = len(rows)
        px = rows[-1].c
        for j in range(30):
            o = px + 1.5 * j
            rows.append(candle(n + j, o, o + 1.5))
        S = build_series(rows, cfg)
        sigs = [evaluate(S, i, cfg, "T")[0] for i in range(n, len(rows))]
        sigs = [s for s in sigs if s is not None]
        self.assertTrue(sigs)
        self.assertEqual(sigs[0].side, "LONG")
        self.assertIn("Flip", sigs[0].trigger)


class TestTrackerFlipExit(unittest.TestCase):
    def _open_long(self, cfg):
        rows = uptrend()
        last = rows[-1]
        rows.append(candle(len(rows), last.c, last.c - 0.3))
        (sig, _), _ = evaluate_last(rows, cfg)
        self.assertIsNotNone(sig)
        tr = Tracker(cfg)
        tr.register(sig, 2)
        return tr, rows, sig

    def test_flip_closes_the_trade_with_FLIP(self):
        cfg = mk_cfg()
        tr, rows, sig = self._open_long(cfg)
        prev_low = rows[-1].l
        # siguiente vela: cierra apenas por debajo del mínimo anterior, sin tocar el SL
        c = prev_low - 0.02
        rows.append(Candle(T0 + len(rows) * LTF, rows[-1].c, rows[-1].c, c - 0.01, c, 1000.0))
        self.assertGreater(c - 0.01, sig.sl)  # el SL no se toca
        S = build_series(rows, cfg)
        self.assertFalse(S.cbc[-1])
        evs = tr.update("TEST-USDT", rows, S.cbc)
        self.assertEqual([e.kind for _, e in evs], ["FLIP"])
        self.assertTrue(evs[0][1].final)
        self.assertLess(evs[0][1].total_r, 0)
        self.assertIsNone(tr.has_open("TEST-USDT"))
        self.assertEqual(tr.stats["n"], 1)
        txt = format_event(evs[0][0], evs[0][1], cfg, tr.stats_line())
        self.assertIn("CBC cambió a ROJO", txt)

    def test_no_flip_exit_when_disabled(self):
        cfg = mk_cfg(exit_on_cbc_flip=False)
        tr, rows, sig = self._open_long(cfg)
        c = rows[-1].l - 0.02
        rows.append(Candle(T0 + len(rows) * LTF, rows[-1].c, rows[-1].c, c - 0.01, c, 1000.0))
        S = build_series(rows, cfg)
        self.assertEqual(tr.update("TEST-USDT", rows, S.cbc), [])
        self.assertIsNotNone(tr.has_open("TEST-USDT"))

    def test_take_profit_then_flip(self):
        cfg = mk_cfg()
        tr, rows, sig = self._open_long(cfg)
        # vela que alcanza TP1 (verde, el CBC sigue verde)
        rows.append(Candle(T0 + len(rows) * LTF, rows[-1].c, sig.tps[0] + 0.1, rows[-1].c - 0.01, sig.tps[0] + 0.05, 1000.0))
        S = build_series(rows, cfg)
        evs = tr.update("TEST-USDT", rows, S.cbc)
        self.assertEqual([e.kind for _, e in evs], ["TP"])
        self.assertAlmostEqual(tr.has_open("TEST-USDT").sl, sig.entry)  # SL a breakeven


class TestBacktestInvariants(unittest.TestCase):
    def test_random_walk(self):
        rnd = random.Random(7)
        rows, px = [], 100.0
        for k in range(1500):
            o = px
            c = o * (1 + rnd.gauss(0, 0.003))
            h = max(o, c) * (1 + abs(rnd.gauss(0, 0.001)))
            l = min(o, c) * (1 - abs(rnd.gauss(0, 0.001)))
            rows.append(Candle(T0 + k * LTF, o, h, l, c, rnd.uniform(500, 1500)))
            px = c
        cfg = mk_cfg(min_score=2, min_room_r=0.0)
        trades = backtest_symbol(rows, cfg, "RW-USDT", 0.0005, 0)
        self.assertGreater(len(trades), 20)
        kinds = {t["kind"] for t in trades}
        self.assertTrue(kinds <= {"TP", "SL", "BE", "FLIP", "OPPOSITE", "TIMEOUT"}, kinds)
        self.assertIn("FLIP", kinds)
        for t in trades:
            self.assertLess(t["r"], t["gross_r"] + 1e-12)  # la comisión siempre resta

        # Invariante de la regla: toda señal LONG = CBC verde (antes y después) + vela roja
        S = build_series(rows, cfg)
        n_sig = 0
        for i in range(MIN_BARS, len(rows)):
            sig, _ = evaluate(S, i, cfg, "RW-USDT")
            if sig is None:
                continue
            n_sig += 1
            red, green = rows[i].c < rows[i].o, rows[i].c > rows[i].o
            if sig.side == "LONG":
                self.assertTrue(S.cbc[i] and S.cbc[i - 1] and red)
            else:
                self.assertTrue((not S.cbc[i]) and (not S.cbc[i - 1]) and green)
        self.assertGreater(n_sig, 20)

    def test_no_lookahead(self):
        """La señal en la vela i no cambia si se añaden velas futuras."""
        rnd = random.Random(11)
        rows, px = [], 100.0
        for k in range(700):
            o = px
            c = o * (1 + rnd.gauss(0, 0.003))
            rows.append(Candle(T0 + k * LTF, o, max(o, c) * 1.001, min(o, c) * 0.999, c, 1000.0))
            px = c
        cfg = mk_cfg(min_score=0)
        S_full = build_series(rows, cfg)
        for i in range(300, 600, 7):
            S_cut = build_series(rows[: i + 1], cfg)
            a, _ = evaluate(S_full, i, cfg, "X")
            b, _ = evaluate(S_cut, i, cfg, "X")
            self.assertEqual((a is None), (b is None), f"i={i}")
            if a is not None:
                self.assertEqual((a.side, round(a.sl, 9), round(a.entry, 9)), (b.side, round(b.sl, 9), round(b.entry, 9)))


class TestMessages(unittest.TestCase):
    def test_signal_text_long_and_short(self):
        cfg = mk_cfg(min_score=0)
        meta = SymbolMeta("TEST-USDT", price_prec=2)
        rows = uptrend()
        rows.append(candle(len(rows), rows[-1].c, rows[-1].c - 0.3))
        (sig, _), _ = evaluate_last(rows, cfg)
        txt = format_signal(sig, meta, cfg)
        self.assertIn("LONG · TEST-USDT", txt)
        self.assertIn("CBC 15m VERDE", txt)
        self.assertIn("cambia a ROJO", txt)

        rows = downtrend()
        rows.append(candle(len(rows), rows[-1].c, rows[-1].c + 0.3))
        (sig, _), _ = evaluate_last(rows, cfg)
        txt = format_signal(sig, meta, cfg)
        self.assertIn("SHORT · TEST-USDT", txt)
        self.assertIn("CBC 15m ROJO", txt)
        self.assertIn("cambia a VERDE", txt)

    def test_startup(self):
        txt = format_startup(mk_cfg(), 100, [], 0)
        self.assertIn("compra velas rojas", txt)


if __name__ == "__main__":
    unittest.main()
