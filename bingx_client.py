import requests
import time
import logging
from typing import List, Dict
import config

logger = logging.getLogger(__name__)

class BingXClient:
    def __init__(self):
        self.base = config.BINGX_BASE_URL
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "MapleStax-CBC-Bot/1.1"
        })

    def _get(self, path: str, params: dict = None) -> dict:
        url = f"{self.base}{path}"
        try:
            r = self.session.get(url, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
            if data.get("code") not in (0, "0", None):
                logger.warning(f"BingX code {data.get('code')}: {data.get('msg')}")
                return {}
            return data.get("data", data)
        except Exception as e:
            logger.error(f"Request error {path}: {e}")
            return {}

    def get_all_perpetual_symbols(self) -> List[str]:
        data = self._get("/openApi/swap/v2/quote/contracts")
        if not data:
            return []
        
        symbols = []
        items = data if isinstance(data, list) else data.get("contracts", [])
        for item in items:
            try:
                symbol = item.get("symbol", "")
                status = item.get("status", 0)
                currency = item.get("currency", "")
                if status == 1 and currency == "USDT" and symbol.endswith("-USDT"):
                    symbols.append(symbol)
            except Exception:
                continue
        return symbols

    def get_24h_tickers(self) -> Dict[str, float]:
        data = self._get("/openApi/swap/v2/quote/ticker")
        volumes = {}
        if not data:
            return volumes
        
        items = data if isinstance(data, list) else []
        for t in items:
            try:
                sym = t.get("symbol")
                vol = float(t.get("quoteVolume", 0) or 0)
                if sym:
                    volumes[sym] = vol
            except Exception:
                continue
        return volumes

    def get_klines(self, symbol: str, interval: str = "5m", limit: int = 120) -> List[dict]:
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
        data = self._get("/openApi/swap/v3/quote/klines", params)
        
        if not data:
            return []
        
        # BingX puede devolver lista directa o dentro de una clave
        if isinstance(data, list):
            klines_raw = data
        elif isinstance(data, dict):
            klines_raw = data.get("klines") or data.get("data") or []
        else:
            return []
        
        result = []
        for k in klines_raw:
            try:
                if isinstance(k, list) and len(k) >= 6:
                    result.append({
                        "open_time": int(k[0]),
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                    })
                elif isinstance(k, dict):
                    result.append({
                        "open_time": int(k.get("openTime") or k.get("time") or 0),
                        "open": float(k.get("open", 0)),
                        "high": float(k.get("high", 0)),
                        "low": float(k.get("low", 0)),
                        "close": float(k.get("close", 0)),
                        "volume": float(k.get("volume", 0)),
                    })
            except Exception:
                continue
        
        result.sort(key=lambda x: x["open_time"])
        return result

    def get_top_liquid_symbols(self, max_symbols: int = 60, min_volume: float = 800000) -> List[str]:
        symbols = self.get_all_perpetual_symbols()
        volumes = self.get_24h_tickers()
        
        scored = []
        for s in symbols:
            vol = volumes.get(s, 0)
            if vol >= min_volume:
                scored.append((s, vol))
        
        scored.sort(key=lambda x: x[1], reverse=True)
        top = [s for s, _ in scored[:max_symbols]]
        logger.info(f"Símbolos líquidos seleccionados: {len(top)}")
        return top