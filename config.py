import os
from dotenv import load_dotenv

load_dotenv()

# ===== TELEGRAM =====
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")   # can be group or user id

# ===== BINGX =====
BINGX_BASE_URL = "https://open-api.bingx.com"

# ===== STRATEGY SETTINGS (igual que el Pine) =====
TIMEFRAME = "5m"
LOOKBACK_BARS = 120          # velas necesarias para calcular indicadores

# Filtros
ER_MAX = 0.45
RVOL_MIN = 1.20
ATR_MULT_REG = 1.35
CMF_THRESH = 0.05
ATR_SL_MULT = 1.4
TP1_R = 1.2
TP2_R = 2.2

# Ranking
TOP_N = 5                    # cuántas mejores señales enviar
MIN_SCORE = 6                # score mínimo para considerar la señal

# Runtime
SCAN_INTERVAL_SECONDS = 300  # 5 minutos
MAX_SYMBOLS_TO_SCAN = 80     # límite de seguridad (los más líquidos)
MIN_24H_VOLUME_USDT = 500000 # filtrar monedas con poco volumen

# Logging
LOG_LEVEL = "INFO"