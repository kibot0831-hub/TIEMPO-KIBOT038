"""
IPDA BingX Bot v3 — escanea TODOS los perps USDT de BingX al cierre de cada vela.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from collections import Counter
from typing import Optional

import httpx
import numpy as np

import config
from bingx import BingX, BingXError
from notify import State, Telegram
from tracker import Tracker
from strategy import (
    Signal,
    analyze,
    apply_cross_sectional,
    btc_regime_from_rows,
    BtcRegime,
    minutes_to_funding,
)

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ipda")
STOP = asyncio.Event()

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _handle_sig(*_):
    log.info("Parada")
    STOP.set()


def round_sig(bx: BingX, s: Signal) -> None:
    """Redondea entrada/SL/TP a la precisión del contrato (antes salían con 15 decimales)."""
    for f in ("entry", "sl", "tp1", "tp2"):
        setattr(s, f, bx.round_price(s.symbol, getattr(s, f)))


def fmt_sig(s: Signal, mode: str, trading: bool | None = None) -> str:
    arrow = "🟢 LONG" if s.side == "LONG" else "🔴 SHORT"
    if trading is None:
        trading = mode == "LIVE" and s.tier == "A"
    live = "LIVE" if trading else "SOLO SEÑAL"
    abs_t = " · ABSORCIÓN" if s.absorb else ""
    tier = " · 🟡 TIER B (filtro relajado)" if s.tier == "B" else ""
    btc = {"alineado": "alineado ✓", "contra": "EN CONTRA ✗", "líder": "es el líder"}.get(
        s.btc_state, "sin dato"
    )
    xs = f"p{s.xsec_pct:.0f}" if s.xsec_pct > 0 else "n/a"
    return (
        f"<b>IPDA v3 · {arrow}</b> · {live}{abs_t}{tier}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Par: <b>{s.symbol}</b>\n"
        f"Origen: {s.src} · Score <b>{s.score}/100</b>\n"
        f"RVOL universo: <b>{xs}</b> · Funding en {s.funding_min:.0f}m\n"
        f"BTC: {btc} · Tendencia: {s.trend}\n"
        f"\n"
        f"📍 Entrada: <code>{s.entry}</code>\n"
        f"🛑 SL: <code>{s.sl}</code> ({s.risk_pct:.2f}%)\n"
        f"🎯 TP1: <code>{s.tp1}</code> · TP2: <code>{s.tp2}</code>\n"
        f"\n"
        f"RVOL {s.rvol:.2f}x · Dist {s.dist_atr:.2f} ATR\n"
        f"{s.reason}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"TF {config.TIMEFRAME} · MODE={mode}"
    )


# ── universo ──────────────────────────────────────────────────────────
async def build_universe(bx: BingX) -> Optional[list[str]]:
    """Todos los contratos USDT activos (o SYMBOLS si se fija). None si falla."""
    try:
        all_syms = await bx.symbols()   # también carga precisiones
    except Exception as exc:  # noqa: BLE001
        log.error("simbolos: %s", exc)
        return None
    if not all_syms:
        return None

    if config.SYMBOLS:
        wanted = set(config.SYMBOLS)
        syms = [s for s in all_syms if s in wanted]
    else:
        try:
            vols = await bx.tickers_24h()
        except Exception as exc:  # noqa: BLE001
            log.warning("tickers: %s", exc)
            vols = {}
        syms = all_syms
        if vols and any(v > 0 for v in vols.values()):
            ranked = sorted(
                (s for s in all_syms if vols.get(s, 0) >= config.MIN_QUOTE_VOL_24H),
                key=lambda s: vols.get(s, 0),
                reverse=True,
            )
            if ranked:
                syms = ranked
        if config.MAX_SYMBOLS > 0:
            syms = syms[: config.MAX_SYMBOLS]

    btc = config.BTC_SYMBOL
    if config.USE_BTC_LEAD and btc in all_syms:
        syms = [btc] + [s for s in syms if s != btc]
    return syms


async def fetch_all(bx: BingX, symbols: list[str]) -> dict[str, list[dict]]:
    """Velas de todos los símbolos en paralelo (el limitador de bx marca el ritmo)."""
    sem = asyncio.Semaphore(max(1, config.CONCURRENCY))

    async def one(sym: str):
        async with sem:
            if STOP.is_set():
                return sym, None
            try:
                return sym, await bx.klines(sym, config.TIMEFRAME, config.KLINE_LIMIT)
            except Exception as exc:  # noqa: BLE001
                log.debug("klines %s: %s", sym, exc)
                return sym, None

    res = await asyncio.gather(*(one(s) for s in symbols))
    return {s: r for s, r in res if r}


# ── ejecución real ────────────────────────────────────────────────────
async def try_live_order(bx: BingX, sig: Signal, equity: float) -> tuple[Optional[str], str]:
    """Devuelve (client_id | None, nota). La nota explica por qué no se operó."""
    if equity <= 0 or abs(sig.entry - sig.sl) <= 0:
        return None, "equity/riesgo inválido"

    # La señal es del CIERRE de la vela anterior y el escaneo tarda (cientos de
    # peticiones): al enviar la orden el precio ya se movió. SL/TP son absolutos,
    # así que si el precio se desplazó mucho el R:R real deja de ser el calculado.
    risk0 = abs(sig.entry - sig.sl)
    ref_px = sig.entry
    try:
        rows = await bx.klines(sig.symbol, config.TIMEFRAME, 2)
        if rows:
            ref_px = float(rows[-1]["close"])
    except Exception as exc:  # noqa: BLE001
        log.warning("%s precio actual: %s (uso la entrada de la señal)", sig.symbol, exc)
    drift_r = abs(ref_px - sig.entry) / risk0
    if drift_r > config.MAX_ENTRY_DRIFT_R:
        log.info("%s entrada desfasada %.2fR (> %.2fR): no se opera", sig.symbol, drift_r,
                 config.MAX_ENTRY_DRIFT_R)
        return None, f"entrada desfasada {drift_r:.2f}R"

    # el riesgo real es hasta el SL desde donde se llena, no desde el cierre de la vela
    risk_px = abs(ref_px - sig.sl)
    if risk_px <= 0:
        return None, "precio en el SL"
    qty = (equity * config.RISK_PCT / 100.0) / risk_px
    qty = min(qty, (equity * config.MAX_LEVERAGE) / ref_px)
    qty = bx.round_qty(sig.symbol, qty)
    if qty <= 0 or qty < bx.min_qty(sig.symbol):
        log.warning("%s qty baja %.8f", sig.symbol, qty)
        return None, "cantidad bajo el mínimo del contrato"

    side = "BUY" if sig.side == "LONG" else "SELL"
    pos_side = "LONG" if sig.side == "LONG" else "SHORT"
    sl = bx.round_price(sig.symbol, sig.sl)
    tp = bx.round_price(sig.symbol, sig.tp2 if config.LIVE_TP == "TP2" else sig.tp1)
    cid = f"ipda{int(time.time() * 1000)}"

    # Un fallo aquí (p. ej. ya estaba en ISOLATED) NO debe tumbar el ciclo entero
    # ni dejar sin procesar las demás señales.
    try:
        await bx.set_margin_mode(sig.symbol, "ISOLATED")
    except Exception as exc:  # noqa: BLE001
        log.warning("%s margin mode: %s", sig.symbol, exc)
    try:
        await bx.set_leverage(sig.symbol, pos_side, config.LEVERAGE)
    except Exception as exc:  # noqa: BLE001
        log.warning("%s leverage: %s", sig.symbol, exc)

    try:
        # SL y TP viajan en la MISMA orden: sin ventana de posición desprotegida.
        await bx.market_order(sig.symbol, side, qty, sl, tp, cid)
        return cid, f"desfase {drift_r:.2f}R"
    except (BingXError, httpx.HTTPError) as exc:
        log.error("LIVE %s: %s", sig.symbol, exc)
        # la respuesta pudo perderse con la orden ya ejecutada
        try:
            if await bx.order_exists(sig.symbol, cid):
                return cid, "confirmada tras error de red"
        except Exception:  # noqa: BLE001
            pass
        return None, "rechazada por BingX"


def _hit_outcome(info: dict, rows: list[dict]) -> Optional[bool]:
    """
    True = tocó TP primero, False = tocó SL primero, None = ninguno visible.
    Si SL y TP caen en la misma vela cuenta SL (no se conoce el orden).
    """
    long = info.get("side") == "LONG"
    sl, tp = info.get("sl"), info.get("tp")
    if not sl or not tp:
        return None
    t0 = float(info.get("ts", 0)) * 1000.0
    tf_ms = config.TF_SEC * 1000
    for r in sorted(rows, key=lambda x: x["time"]):
        if r["time"] + tf_ms <= t0:          # vela cerrada antes de la entrada
            continue
        sl_hit = r["low"] <= sl if long else r["high"] >= sl
        tp_hit = r["high"] >= tp if long else r["low"] <= tp
        if sl_hit:
            return False
        if tp_hit:
            return True
    return None


def open_side_count(state: State, side: str) -> int:
    return sum(1 for v in state.data.get("open", {}).values() if v.get("side") == side)


async def reconcile(bx: BingX, tg: Telegram, state: State) -> None:
    """
    Quita de state['open'] lo que ya no está abierto en BingX y cuenta W/L.

    El resultado se decide por QUÉ nivel (SL o TP) tocó el precio desde la
    entrada. Antes se comparaba «precio actual vs entrada»: una operación parada
    por SL contaba como ganada si el precio rebotaba después (y al revés), y
    MAX_LOSSES_DAY depende de estas cuentas.
    """
    opens = state.data.get("open", {})
    if not opens:
        return
    try:
        pos = await bx.open_positions()
    except Exception as exc:  # noqa: BLE001
        log.warning("posiciones: %s", exc)
        return
    live = {
        str(p.get("symbol"))
        for p in pos
        if float(p.get("positionAmt", 0) or 0) != 0
    }
    for sym, info in list(opens.items()):
        if sym in live or time.time() - float(info.get("ts", 0)) < 90:
            continue
        won, exact = False, False
        try:
            bars_since = int((time.time() - float(info.get("ts", 0))) / config.TF_SEC) + 3
            rows = await bx.klines(sym, config.TIMEFRAME, max(3, min(config.KLINE_LIMIT, bars_since)))
            hit = _hit_outcome(info, rows)
            if hit is not None:
                won, exact = hit, True
            else:  # ni SL ni TP visibles (cierre manual, datos cortos): precio vs entrada
                px = rows[-1]["close"]
                won = px > info["entry"] if info["side"] == "LONG" else px < info["entry"]
        except Exception:  # noqa: BLE001
            pass
        state.close_trade(sym, won)
        how = "por SL/TP" if exact else "resultado aprox."
        await tg.send(
            f"{'✅' if won else '❌'} <b>{sym}</b> {info.get('side')} cerrada "
            f"({how}) · W/L {state.data['wins']}/{state.data['losses']}"
        )


# ── ciclo de escaneo ──────────────────────────────────────────────────
async def scan_once(
    bx: BingX, tg: Telegram, state: State, trk: Tracker, symbols: list[str], mode: str
) -> dict:
    t0 = time.time()
    state.roll_day()
    live = mode == "LIVE" and config.LIVE_CONFIRMED

    equity = 0.0
    if live:
        await reconcile(bx, tg, state)
        try:
            equity = (await bx.cuenta()).get("equity", 0.0) or await bx.balance_usdt()
        except Exception as exc:  # noqa: BLE001
            log.warning("balance: %s", exc)

    fmap = await bx.funding_info()
    data = await fetch_all(bx, symbols)
    trk.resolve(data, time.time())

    regime = BtcRegime()
    if config.USE_BTC_LEAD and config.BTC_SYMBOL in data:
        regime = btc_regime_from_rows(data[config.BTC_SYMBOL])

    now = time.time()
    universe: list[float] = []
    candidates: list[Signal] = []
    funnel: Counter = Counter()
    stale = 0

    for sym, rows in data.items():
        if STOP.is_set():
            break
        # par parado/deslistado: la última vela es vieja
        if now * 1000 - rows[-1]["time"] > 2 * config.TF_SEC * 1000:
            stale += 1
            continue
        rate, nxt = fmap.get(sym, (0.0, 0.0))
        mins = (nxt - now) / 60.0 if nxt > now else minutes_to_funding(now)
        reg = None if sym == config.BTC_SYMBOL else regime
        sig, metric = analyze(sym, rows, rate, mins, reg, funnel=funnel)
        if metric is not None:
            universe.append(metric)
        if sig:
            if state.can_signal(sym):
                candidates.append(sig)
            else:
                funnel["en_cooldown"] += 1

    ranked = apply_cross_sectional(candidates, universe)
    final_a = [s for s in ranked if s.tier == "A"][: config.MAX_SIGNALS_CYCLE]
    final_b = [s for s in ranked if s.tier == "B"][: config.MAX_B_CYCLE]
    final = final_a + final_b

    p50, p75, p90 = np.percentile(universe, [50, 75, 90]) if universe else (0.0, 0.0, 0.0)
    log.info(
        "Embudo %s | RVOL universo p50/p75/p90 = %.2f/%.2f/%.2f | "
        "tras filtro universo %d/%d (A=%d B=%d)",
        dict(funnel), p50, p75, p90, len(ranked), len(candidates), len(final_a), len(final_b),
    )

    # LIVE solo si el edge medido en papel (tier A, TP elegido) está validado
    live_ok, edge_txt = True, ""
    if live and config.LIVE_REQUIRE_EDGE and config.TRACK_ENABLED:
        n_e, m_e, ci_e = trk.edge("A", config.LIVE_TP, config.LIVE_EDGE_WINDOW)
        live_ok = n_e >= config.LIVE_MIN_SAMPLES and m_e > config.LIVE_MIN_EDGE_R
        edge_txt = (f"A·{config.LIVE_TP} n={n_e}/{config.LIVE_MIN_SAMPLES} "
                    f"E{m_e:+.2f}±{ci_e:.2f}R")
        if not live_ok:
            log.info("LIVE en pausa: edge sin validar (%s; E mín %+.2f)", edge_txt,
                     config.LIVE_MIN_EDGE_R)

    for sig in final:
        log.info(
            "SENAL[%s] %s %s score=%s xsec=p%.0f %s",
            sig.tier, sig.side, sig.symbol, sig.score, sig.xsec_pct, sig.src,
        )
        round_sig(bx, sig)

        will_trade = live and equity > 0 and sig.tier == "A"
        block = ""
        if will_trade:
            if not live_ok:
                block = f"LIVE en pausa: edge en papel sin validar ({edge_txt})"
            elif not state.can_trade_live(sig.symbol):
                block = "límite diario/posiciones"
            elif (config.MAX_SAME_SIDE > 0
                  and open_side_count(state, sig.side) >= config.MAX_SAME_SIDE):
                block = f"ya hay {config.MAX_SAME_SIDE} {sig.side} abiertas (correlación)"
            if block:
                will_trade = False

        text = fmt_sig(sig, mode, will_trade)
        if block:
            text += f"\n⏸ {block} · solo señal"
        await tg.send(text)
        state.mark_signal(sig.symbol)
        trk.add(sig, live=will_trade)

        if will_trade:
            oid, note = await try_live_order(bx, sig, equity)
            if oid:
                state.mark_trade(
                    sig.symbol,
                    {
                        "side": sig.side,
                        "entry": sig.entry,
                        "sl": sig.sl,
                        "tp": sig.tp2 if config.LIVE_TP == "TP2" else sig.tp1,
                        "ts": time.time(),
                        "oid": oid,
                    },
                )
                await tg.send(f"OK Orden <b>{sig.symbol}</b> {sig.side} · {note}")
            else:
                await tg.send(f"WARN {sig.symbol} señal OK, sin orden: {note}")
        await asyncio.sleep(0.4)  # respeta el límite de Telegram

    elapsed = time.time() - t0
    log.info(
        "Ciclo: %d/%d pares con datos (%d parados) | %d candidatos → %d señales | "
        "BTC=%s | %.0fs",
        len(data), len(symbols), stale, len(candidates), len(final),
        regime.side or "neutral", elapsed,
    )
    if candidates and not final:
        for s in sorted(candidates, key=lambda x: x.score, reverse=True)[:3]:
            log.info("  cerca: %s %s score=%s rvol=%.2f %s", s.side, s.symbol, s.score, s.rvol, s.src)
    return {"pairs": len(data), "cands": len(candidates), "sigs": len(final), "sec": elapsed}


def seconds_to_next_bar() -> float:
    now = time.time()
    return config.TF_SEC - (now % config.TF_SEC) + config.BAR_DELAY_SEC


async def main() -> None:
    mode = config.MODE
    if mode == "LIVE" and not config.LIVE_CONFIRMED:
        log.warning("LIVE sin LIVE_CONFIRMED -> SIGNAL")
        mode = "SIGNAL"
    if mode == "LIVE" and not (config.BINGX_API_KEY and config.BINGX_API_SECRET):
        log.warning("LIVE sin claves API -> SIGNAL")
        mode = "SIGNAL"

    # add_signal_handler despierta el bucle al instante; signal.signal + Event.set()
    # no es seguro entre hilos/bucle y Railway podía acabar matando el proceso
    # sin parada limpia mientras esperaba la siguiente vela.
    loop = asyncio.get_running_loop()
    for sg in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sg, _handle_sig)
        except NotImplementedError:      # p. ej. Windows
            signal.signal(sg, _handle_sig)

    state = State(config.STATE_PATH)
    trk = Tracker(config.TRACK_PATH)
    async with httpx.AsyncClient() as client:
        bx = BingX(client)
        tg = Telegram(client)

        symbols = await build_universe(bx) or ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
        await tg.send(
            f"IPDA v3 arrancado\n"
            f"Modo: <b>{mode}</b> · TF {config.TIMEFRAME}\n"
            f"Pares: <b>{len(symbols)}</b> "
            f"(vol24h ≥ {config.MIN_QUOTE_VOL_24H:,.0f} USDT)\n"
            f"Escaneo al cierre de cada vela de {config.TIMEFRAME}\n"
            f"Edges: RVOL universo A p≥{config.XSEC_RVOL_PCT:.0f} / "
            f"B p≥{config.XSEC_B_PCT:.0f} ({'ON' if config.SEND_TIER_B else 'off'}) · "
            f"BTC-lead={'ON' if config.USE_BTC_LEAD else 'off'} · "
            f"Funding={'ON' if config.USE_FUNDING_EDGE else 'off'}\n"
            f"Máx {config.MAX_SIGNALS_CYCLE} señales/ciclo · score≥{config.MIN_SCORE}"
            + (
                f"\nLIVE exige edge en papel: n≥{config.LIVE_MIN_SAMPLES} señales A "
                f"con E mín {config.LIVE_MIN_EDGE_R:+.2f}R · "
                f"máx {config.MAX_SAME_SIDE} abiertas por sentido"
                if mode == "LIVE" and config.LIVE_REQUIRE_EDGE else ""
            )
        )

        last_refresh = last_hb = time.time()
        stats: dict = {}
        first = True
        while not STOP.is_set():
            if not first:
                try:
                    await asyncio.wait_for(STOP.wait(), timeout=seconds_to_next_bar())
                except asyncio.TimeoutError:
                    pass
                if STOP.is_set():
                    break
            first = False

            if time.time() - last_refresh >= config.SYMBOL_REFRESH_MIN * 60:
                new = await build_universe(bx)
                if new:
                    symbols = new
                last_refresh = time.time()

            try:
                stats = await scan_once(bx, tg, state, trk, symbols, mode)
            except Exception as exc:  # noqa: BLE001
                log.exception("scan: %s", exc)

            if time.time() - last_hb >= config.HEARTBEAT_MIN * 60:
                last_hb = time.time()
                d = state.data
                await tg.send(
                    f"Heartbeat · {len(symbols)} pares · último ciclo {stats.get('sec', 0):.0f}s\n"
                    f"Señales hoy {d.get('day_signals', 0)} · "
                    f"trades hoy {d.get('day_trades', 0)} · W/L {d.get('wins', 0)}/{d.get('losses', 0)}\n\n"
                    f"{trk.summary()}"
                )
        await tg.send("IPDA v3 detenido")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
