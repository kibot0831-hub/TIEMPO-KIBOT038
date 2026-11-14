import os
from dotenv import load_dotenv

load_dotenv()

# ===== TELEGRAM =====
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ===== BINGX =====
BINGX_BASE_URL = "https://open-api.bingx.com"

# ===== STRATEGY SETTINGS =====
TIMEFRAME = "5m"
LOOKBACK_BARS = 120

# Filtros (se pueden sobreescribir desde Railway)
ER_MAX = float(os.getenv("ER_MAX", "0.45"))
RVOL_MIN = float(os.getenv("RVOL_MIN", "1.20"))
ATR_MULT_REG = float(os.getenv("ATR_MULT_REG", "1.35"))
CMF_THRESH = float(os.getenv("CMF_THRESH", "0.05"))
ATR_SL_MULT = float(os.getenv("ATR_SL_MULT", "1.4"))
TP1_R = float(os.getenv("TP1_R", "1.2"))
TP2_R = float(os.getenv("TP2_R", "2.2"))

# Ranking
TOP_N = int(os.getenv("TOP_N", "5"))
MIN_SCORE = int(os.getenv("MIN_SCORE", "6"))

# Runtime
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))
MAX_SYMBOLS_TO_SCAN = int(os.getenv("MAX_SYMBOLS_TO_SCAN", "60"))
MIN_24H_VOLUME_USDT = float(os.getenv("MIN_24H_VOLUME_USDT", "800000"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")