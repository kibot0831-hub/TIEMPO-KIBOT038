import requests
import time
import logging
import threading
from typing import List, Dict, Optional
import config

logger = logging.getLogger(__name__)

class BingXClient:
    def __init__(self):
        self.base = config.BINGX_BASE_URL
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "MapleStax-CBC-Bot/1.0"
        })
        self._rl_lock = threading.Lock()
        self._next_slot = 0.0
        self._min_gap = 1.0 / max(1, getattr(config, "MAX_REQUESTS_PER_SECOND", 8))

    def _throttle(self):
        """Limita peticiones/segundo entre todos los hilos."""
        with self._rl_lock:
            now = time.monotonic()
            wait = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + self._min_gap
        if wait > 0:
            time.sleep(wait)

    def _get(self, path: str, params: dict = None) -> dict:
        url = f"{self.base}{path}"
        for attempt in range(3):
            try:
                self._throttle()
                r = self.session.get(url, params=params, timeout=12)
                if r.status_code == 429:
                    time.sleep(2 * (attempt + 1))
                    continue
                r.raise_for_status()
                data = r.json()
                if data.get("code") != 0:
                    logger.warning(f"BingX error {data.get('code')}: {data.get('msg')}")
                    return {}
                return data.get("data", {})
            except Exception as e:
                logger.error(f"Request error {path} (intento {attempt + 1}): {e}")
                time.sleep(1)
        return {}

    def get_all_perpetual_symbols(self) -> List[str]:
        """Devuelve lista de símbolos USDT-M perpetuos (formato BTC-USDT)"""
        data = self._get("/openApi/swap/v2/quote/contracts")
        if not data:
            return []
        
        symbols = []
        for item in data:
            # Solo contratos online y USDT
            if (item.get("status") == 1 and 
                item.get("currency") == "USDT" and
                item.get("symbol", "").endswith("-USDT")):
                symbols.append(item["symbol"])
        return symbols

    def get_24h_tickers(self) -> Dict[str, float]:
        """Devuelve {symbol: quoteVolume} para filtrar por liquidez"""
        data = self._get("/openApi/swap/v2/quote/ticker")
        if not data:
            return {}
        
        volumes = {}
        if isinstance(data, list):
            for t in data:
                sym = t.get("symbol")
                vol = float(t.get("quoteVolume", 0) or 0)
                if sym:
                    volumes[sym] = vol
        return volumes

    def get_klines(self, symbol: str, interval: str = "5m", limit: int = 120) -> List[dict]:
        """
        Devuelve lista de velas:
        [{open_time, open, high, low, close, volume}, ...]
        ordenadas de más antigua a más reciente
        """
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
        data = self._get("/openApi/swap/v3/quote/klines", params)
        
        if not data:
            return []
        
        # BingX a veces devuelve lista directa, a veces dentro de otra estructura
        klines_raw = data if isinstance(data, list) else data.get("klines", data)
        
        result = []
        for k in klines_raw:
            try:
                # Formato típico BingX: [open_time, open, high, low, close, volume, close_time, ...]
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
        
        # Asegurar orden cronológico (más antigua primero)
        result.sort(key=lambda x: x["open_time"])
        return result

    def get_top_liquid_symbols(self, max_symbols: int = 0, min_volume: float = 0) -> List[str]:
        """Combina contratos + volumen 24h y devuelve los más líquidos"""
        symbols = self.get_all_perpetual_symbols()
        volumes = self.get_24h_tickers()
        
        scored = []
        for s in symbols:
            vol = volumes.get(s, 0)
            if vol >= min_volume:
                scored.append((s, vol))
        
        scored.sort(key=lambda x: x[1], reverse=True)
        # max_symbols = 0 / None -> todas las monedas
        limit = max_symbols if max_symbols else len(scored)
        top = [s for s, _ in scored[:limit]]
        logger.info(f"Símbolos líquidos seleccionados: {len(top)}")
        return top