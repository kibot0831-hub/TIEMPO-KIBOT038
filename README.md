# MapleStax CBC 5m Bot – BingX + Telegram (Reparated)

Bot que analiza las monedas más líquidas de **BingX Futures**, aplica la estrategia **MapleStax CBC 5m** y envía las **Top 5 mejores señales** a Telegram cada 5 minutos.

## Características

- Escanea las monedas más líquidas de BingX Futures (USDT-M)
- Lógica completa: ER, RVOL, CVD, OBV, CMF, VWAP, ATR regime, Volume Flip, HTF bias
- Ranking por score de calidad
- Envía solo las Top 5 mejores setups
- Diseñado para correr 24/7 en **Railway**
- Manejo robusto de errores

## Estructura

```
maplestax-cbc-bot-repaired/
├── main.py
├── strategy.py
├── bingx_client.py
├── telegram_sender.py
├── config.py
├── requirements.txt
├── Procfile
├── runtime.txt
├── .env.example
└── README.md
```

## 1. Configurar Telegram

1. Habla con [@BotFather](https://t.me/BotFather) → `/newbot` → copia el **token**
2. Habla con [@userinfobot](https://t.me/userinfobot) → copia tu **Chat ID**

## 2. Variables de entorno (Railway)

Copia esto en **Variables** de Railway:

```env
TELEGRAM_BOT_TOKEN=tu_token_aqui
TELEGRAM_CHAT_ID=tu_chat_id_aqui

SCAN_INTERVAL_SECONDS=300
TOP_N=5
MIN_SCORE=6
MAX_SYMBOLS_TO_SCAN=60
MIN_24H_VOLUME_USDT=800000

ER_MAX=0.45
RVOL_MIN=1.20
ATR_MULT_REG=1.35
CMF_THRESH=0.05
ATR_SL_MULT=1.4
TP1_R=1.2
TP2_R=2.2

LOG_LEVEL=INFO
```

## 3. Desplegar en Railway

1. Sube este repositorio a GitHub
2. En [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Selecciona el repo
4. Añade las variables de arriba
5. Railway detectará el `Procfile` y ejecutará `python main.py`

## Cómo interpretar las señales

- **LONG** → Cierre de vela roja + todos los filtros verdes
- **SHORT** → Cierre de vela verde + todos los filtros verdes
- Entra en el **open de la siguiente vela de 5 minutos**
- Respeta el SL y los TP que te envía el bot
- Score alto (8-10) = setup de mayor calidad

## Disclaimer

Este bot es solo una herramienta de señales. No garantiza beneficios. Opera siempre con gestión de riesgo estricta (máximo 0.5-1% por operación).
