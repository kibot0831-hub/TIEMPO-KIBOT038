"""Backtest con el MISMO motor que el bot en vivo (sin mirar al futuro).

Por defecto prueba la estrategia PULLBACK en 15m (CBC verde -> compra velas rojas,
CBC rojo -> vende velas verdes). Con STRATEGY=flip prueba la estrategia original.

Ejemplos:
  python backtest.py --symbols BTC-USDT,ETH-USDT,SOL-USDT --days 14
  python backtest.py --top 30 --days 10 --fee-bps 5
  python backtest.py --symbols NCCO1OILWTI2USD-USDT --days 20
  STRATEGY=flip LTF=5m HTF=15m python backtest.py --top 30 --days 14     # estrategia original
  EXIT_ON_CBC_FLIP=false python backtest.py --top 30 --days 14           # sin salida por flip

Las variables de entorno (LTF, HTF, MIN_SCORE, TP_R, MAX_SL_ATR...) se respetan,
así puedes probar distintas configuraciones antes de usar el bot.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import replace
from typing import Dict, List

import aiohttp

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from bingx import BingXClient
from config import Config, load_config
from models import Candle
from strategy import MIN_BARS, build_series, cbc_against, evaluate
from tracker import close_trade_at, new_trade, step_trade
from universe import build_universe


async def fetch_history(bingx: BingXClient, symbol: str, interval: str, ltf_ms: int, days: float) -> List[Candle]:
    """Descarga `days` días paginando hacia atrás (máx. 1440 velas por petición)."""
    now_ms = int(time.time() * 1000)
    target = now_ms - int(days * 86_400_000) - 500 * ltf_ms  # + calentamiento de indicadores
    end = now_ms
    out: Dict[int, Candle] = {}
    for _ in range(60):
        chunk = await bingx.klines(symbol, interval, 1440, end_time=end)
        if not chunk:
            break
        for c in chunk:
            out[c.t] = c
        first = chunk[0].t
        if first <= target or first >= end:
            break
        end = first - 1
    candles = [out[k] for k in sorted(out)]
    return [c for c in candles if c.t + ltf_ms <= now_ms]  # solo velas cerradas


def backtest_symbol(candles: List[Candle], cfg: Config, symbol: str, fee_rate: float,
                    eval_from_ms: int) -> List[dict]:
    S = build_series(candles, cfg)
    ltf_ms = cfg.ltf_sec * 1000
    trades: List[dict] = []
    active = None

    def record(tr, ev):
        fee_r = 2.0 * fee_rate * tr.entry / tr.risk  # comisión ida y vuelta en R
        trades.append({"symbol": symbol, "side": tr.side, "t": tr.t_signal, "kind": ev.kind,
                       "r": ev.total_r - fee_r, "gross_r": ev.total_r})

    for i in range(max(MIN_BARS, 3), len(candles)):
        c = candles[i]
        if active is not None:
            final = None
            for ev in step_trade(active, c, cfg):
                if ev.final:
                    final = ev
            if final is None and cfg.max_trade_hours > 0 and \
                    (c.t + ltf_ms - active.opened_ms) >= cfg.max_trade_hours * 3_600_000:
                final = close_trade_at(active, c.c, "TIMEOUT")
            if final is None and cfg.strategy == "pullback" and cfg.exit_on_cbc_flip \
                    and cbc_against(active.side, S.cbc[i]):
                final = close_trade_at(active, c.c, "FLIP")  # el CBC cambió de color en contra
            if final is not None:
                record(active, final)
                active = None
        if c.t < eval_from_ms:
            continue
        sig, _ = evaluate(S, i, cfg, symbol)
        if sig is None:
            continue
        if active is not None:
            if active.side != sig.side or not cfg.exit_on_opposite:
                continue
            record(active, close_trade_at(active, sig.entry, "OPPOSITE"))
            active = None
        active = new_trade(sig, ltf_ms)
    return trades


def summarize(trades: List[dict]) -> None:
    if not trades:
        print("\nSin operaciones.")
        return
    rs = [t["r"] for t in trades]
    wins = [r for r in rs if r > 0.05]
    losses = [r for r in rs if r < -0.05]
    gp, gl = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    eq = peak = dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    print("\n================ RESULTADO (neto de comisiones) ================")
    print(f"Operaciones:        {len(rs)}   (LONG {sum(t['side']=='LONG' for t in trades)} / SHORT {sum(t['side']=='SHORT' for t in trades)})")
    print(f"Ganadoras/Perdedoras: {len(wins)} / {len(losses)}   Winrate: {100*len(wins)/len(rs):.1f}%")
    print(f"Expectativa:        {sum(rs)/len(rs):+.3f} R por operación")
    print(f"Total:              {sum(rs):+.2f} R")
    print(f"Profit factor:      {gp/gl:.2f}" if gl > 0 else "Profit factor:      n/a")
    print(f"Máx. drawdown:      {dd:.2f} R")
    kinds: Dict[str, int] = {}
    for t in trades:
        kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
    print("Cierres por tipo:   " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    by_sym: Dict[str, float] = {}
    for t in trades:
        by_sym[t["symbol"]] = by_sym.get(t["symbol"], 0.0) + t["r"]
    best = sorted(by_sym.items(), key=lambda x: x[1], reverse=True)
    print("Mejores símbolos:   " + ", ".join(f"{s} {r:+.1f}R" for s, r in best[:5]))
    print("Peores símbolos:    " + ", ".join(f"{s} {r:+.1f}R" for s, r in best[-5:]))
    print("================================================================")
    print("Aviso: el pasado no garantiza resultados futuros. Si en una vela se tocan SL y TP,")
    print("se asume SL primero (conservador). No incluye slippage ni funding.")


async def amain(args: argparse.Namespace) -> None:
    cfg: Config = load_config()
    cfg = replace(cfg, dry_run=True)
    logging.basicConfig(level=logging.WARNING, stream=sys.stdout)
    ltf_ms = cfg.ltf_sec * 1000
    async with aiohttp.ClientSession(headers={"User-Agent": "bingx-cbc-bot/2.0"}) as session:
        bingx = BingXClient(session, cfg.requests_per_second)
        if args.symbols:
            symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        else:
            uni = build_universe(await bingx.contracts(), await bingx.tickers(), cfg)
            symbols = list(uni)[: args.top]
        print(f"Backtest {len(symbols)} símbolos · {args.days} días · estrategia {cfg.strategy} · "
              f"LTF {cfg.ltf} / HTF {cfg.htf} · MIN_SCORE {cfg.min_score} · comisión {args.fee_bps} bps/lado")
        eval_from = int(time.time() * 1000) - int(args.days * 86_400_000)
        all_trades: List[dict] = []
        for n, sym in enumerate(symbols, 1):
            try:
                candles = await fetch_history(bingx, sym, cfg.ltf, ltf_ms, args.days)
            except Exception as e:
                print(f"[{n}/{len(symbols)}] {sym}: error {e}")
                continue
            if len(candles) < MIN_BARS + 50:
                print(f"[{n}/{len(symbols)}] {sym}: pocas velas ({len(candles)})")
                continue
            tr = backtest_symbol(candles, cfg, sym, args.fee_bps / 10_000.0, eval_from)
            all_trades.extend(tr)
            print(f"[{n}/{len(symbols)}] {sym}: {len(candles)} velas, {len(tr)} operaciones, {sum(t['r'] for t in tr):+.1f}R")
    summarize(all_trades)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Backtest de la estrategia CBC en BingX")
    ap.add_argument("--symbols", default="", help="lista separada por comas, p.ej. BTC-USDT,ETH-USDT")
    ap.add_argument("--top", type=int, default=20, help="si no hay --symbols: top N por volumen")
    ap.add_argument("--days", type=float, default=14.0)
    ap.add_argument("--fee-bps", type=float, default=5.0, help="comisión por lado en puntos básicos (5 = 0.05%)")
    asyncio.run(amain(ap.parse_args()))
