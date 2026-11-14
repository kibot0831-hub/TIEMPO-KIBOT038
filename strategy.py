import numpy as np
import pandas as pd
from typing import Optional, Dict, Any
import config

def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()

def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(length).mean()

def kaufman_efficiency_ratio(close: pd.Series, length: int = 10) -> float:
    if len(close) < length + 1:
        return 1.0
    change = abs(close.iloc[-1] - close.iloc[-length-1])
    volatility = close.diff().abs().iloc[-length:].sum()
    if volatility == 0:
        return 0.0
    return change / volatility

def calculate_cmf(df: pd.DataFrame, length: int = 20) -> float:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    volume = df["volume"]
    
    range_ = high - low
    mfm = np.where(range_ != 0, ((close - low) - (high - close)) / range_, 0.0)
    mfv = mfm * volume
    if len(mfv) < length:
        return 0.0
    return mfv[-length:].sum() / volume[-length:].sum()

def calculate_obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff()).fillna(0)
    return (direction * df["volume"]).cumsum()

def analyze_symbol(klines: list, symbol: str) -> Optional[Dict[str, Any]]:
    """
    Analiza un símbolo con la lógica MapleStax CBC 5m.
    Devuelve dict con señal + score + datos, o None si no hay setup.
    """
    if len(klines) < 60:
        return None
    
    df = pd.DataFrame(klines)
    
    # Asegurar tipos
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    
    df = df.dropna().reset_index(drop=True)
    if len(df) < 60:
        return None
    
    close = df["close"]
    open_ = df["open"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]
    
    # === Indicadores ===
    er = kaufman_efficiency_ratio(close, 10)
    er_ok = er <= config.ER_MAX
    
    vol_sma = volume.rolling(20).mean()
    rvol = volume.iloc[-1] / vol_sma.iloc[-1] if vol_sma.iloc[-1] > 0 else 1.0
    rvol_ok = rvol >= config.RVOL_MIN
    
    # ATR regime
    atr_val = atr(df, 14).iloc[-1]
    atr_sma = atr(df, 14).rolling(50).mean().iloc[-1]
    atr_ok = atr_val < atr_sma * config.ATR_MULT_REG if atr_sma > 0 else True
    
    # CVD simple (body position weighted)
    bar_range = high - low
    body_pos = np.where(bar_range > 0, (close - low) / bar_range, 0.5)
    delta = volume * (2 * body_pos - 1)
    cvd = delta.cumsum()
    cvd_ma = cvd.ewm(span=14, adjust=False).mean()
    cvd_slope = cvd.iloc[-1] - cvd.iloc[-4] if len(cvd) > 4 else 0
    cvd_bull = cvd.iloc[-1] > cvd_ma.iloc[-1] and cvd_slope > 0
    cvd_bear = cvd.iloc[-1] < cvd_ma.iloc[-1] and cvd_slope < 0
    
    # OBV
    obv = calculate_obv(df)
    obv_ma = obv.ewm(span=14, adjust=False).mean()
    obv_bull = obv.iloc[-1] > obv_ma.iloc[-1]
    obv_bear = obv.iloc[-1] < obv_ma.iloc[-1]
    
    # CMF
    cmf = calculate_cmf(df, 20)
    
    # VWAP session aproximado (últimas 24h de 5m = 288 velas, usamos lo que haya)
    typical = (high + low + close) / 3
    vwap = (typical * volume).cumsum() / volume.cumsum()
    vwap_last = vwap.iloc[-1]
    
    # Última vela
    last_close = close.iloc[-1]
    last_open = open_.iloc[-1]
    red_close = last_close < last_open
    green_close = last_close > last_open
    
    vol_flip_long = red_close and volume.iloc[-1] > volume.iloc[-2]
    vol_flip_short = green_close and volume.iloc[-1] > volume.iloc[-2]
    
    # HTF bias (15m aproximado usando 3 velas de 5m)
    # Simplificación: usamos EMA 20 de 5m como proxy de bias
    ema20 = ema(close, 20)
    htf_bull = last_close > ema20.iloc[-1]
    htf_bear = last_close < ema20.iloc[-1]
    
    # === Condiciones de señal ===
    long_cond = (
        red_close and
        er_ok and
        rvol_ok and
        atr_ok and
        (cvd_bull or cvd_slope >= 0) and
        obv_bull and
        cmf > -config.CMF_THRESH and
        last_close > vwap_last and
        vol_flip_long and
        htf_bull
    )
    
    short_cond = (
        green_close and
        er_ok and
        rvol_ok and
        atr_ok and
        (cvd_bear or cvd_slope <= 0) and
        obv_bear and
        cmf < config.CMF_THRESH and
        last_close < vwap_last and
        vol_flip_short and
        htf_bear
    )
    
    if not (long_cond or short_cond):
        return None
    
    direction = "LONG" if long_cond else "SHORT"
    
    # Score (cuántos filtros fuertes pasaron + calidad)
    score = 0
    score += 2 if er_ok else 0
    score += 2 if rvol_ok else 0
    score += 1 if atr_ok else 0
    score += 1 if (cvd_bull if long_cond else cvd_bear) else 0
    score += 1 if (obv_bull if long_cond else obv_bear) else 0
    score += 1 if abs(cmf) > 0.05 else 0
    score += 1 if rvol > 1.5 else 0
    score += 1 if er < 0.30 else 0   # mejor mean-reversion
    
    if score < config.MIN_SCORE:
        return None
    
    # Precios de entrada / SL / TP
    entry = last_close
    risk = atr_val * config.ATR_SL_MULT
    
    if direction == "LONG":
        sl = entry - risk
        tp1 = entry + risk * config.TP1_R
        tp2 = entry + risk * config.TP2_R
    else:
        sl = entry + risk
        tp1 = entry - risk * config.TP1_R
        tp2 = entry - risk * config.TP2_R
    
    return {
        "symbol": symbol,
        "direction": direction,
        "entry": round(entry, 8),
        "sl": round(sl, 8),
        "tp1": round(tp1, 8),
        "tp2": round(tp2, 8),
        "score": score,
        "er": round(er, 3),
        "rvol": round(rvol, 2),
        "cmf": round(cmf, 4),
        "atr": round(atr_val, 8),
        "price": round(entry, 8),
        "timeframe": "5m"
    }