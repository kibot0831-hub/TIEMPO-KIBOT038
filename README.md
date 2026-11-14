# MapleStax CBC 5m Bot – BingX + Telegram

Bot que analiza **todas las monedas perpetuas USDT de BingX**, aplica la estrategia **MapleStax CBC 5m** (BRSG + filtros de edge) y envía las **5 mejores señales** a Telegram cada 5 minutos.

## Características

- Escanea **todas** las monedas perpetuas USDT de BingX Futures (con control de límite de peticiones)
- Aplica exactamente la lógica del indicador Pine (ER, RVOL, CVD, OBV, CMF, VWAP, ATR regime, Volume Flip, HTF bias)
- Ranking por score de calidad
- Envía solo las Top 5 mejores setups
- Diseñado para correr 24/7 en **Railway**

## Estructura del proyecto

```
maplestax-cbc-bot/
├── main.py              # Loop principal
├── strategy.py          # Lógica CBC + filtros
├── bingx_client.py      # Cliente BingX (público)
├── telegram_sender.py   # Envío de mensajes
├── config.py            # Configuración
├── requirements.txt
├── Procfile             # Para Railway
├── .python-version
├── railway.json
├── .env.example
└── README.md
```

## 1. Configurar Telegram

1. Habla con [@BotFather](https://t.me/BotFather) → `/newbot` → copia el **token**
2. Habla con [@userinfobot](https://t.me/userinfobot) → copia tu **Chat ID**
3. (Opcional) Si quieres enviar a un grupo, añade el bot al grupo y usa el ID del grupo (empieza por `-`)

## 2. Variables de entorno

Copia `.env.example` a `.env` y rellena:

```env
TELEGRAM_BOT_TOKEN=tu_token_aqui
TELEGRAM_CHAT_ID=tu_chat_id_aqui
```

## 3. Probar en local

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # edita el .env
python main.py
```

## 4. Desplegar en Railway (recomendado)

1. Sube este repositorio a **GitHub**
2. Entra en [railway.app](https://railway.app) → New Project → Deploy from GitHub repo
3. Selecciona el repositorio
4. En **Variables** añade:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
5. Railway detectará el `Procfile` y ejecutará `python main.py` como worker
6. ¡Listo! El bot empezará a escanear y enviarte señales

### Notas Railway

- Usa el plan Hobby o superior para que no se duerma.
- El proceso es tipo `worker` (no necesita puerto web).
- Los logs se ven en la pestaña Deployments → View Logs.

## Cómo interpretar las señales

- **LONG** → Cierre de vela roja + todos los filtros verdes
- **SHORT** → Cierre de vela verde + todos los filtros verdes
- Entra en el **open de la siguiente vela de 5 minutos**
- Respeta el SL y los TP que te envía el bot
- Score alto (8-10) = setup de mayor calidad

## Personalización

Puedes modificar en `config.py`:

- `TOP_N` → cuántas señales enviar (default 5)
- `MIN_SCORE` → score mínimo para aceptar la señal
- `MAX_SYMBOLS_TO_SCAN` → cuántas monedas analizar (0 = todas)
- `MIN_24H_VOLUME_USDT` → volumen mínimo 24h (0 = sin filtro)
- `SCAN_INTERVAL_SECONDS` → cada cuánto escanear (300 = 5 min)
- `ER_MAX`, `RVOL_MIN`, etc. → umbrales de los filtros

## Disclaimer

Este bot es solo una herramienta de señales. No garantiza beneficios. Opera siempre con gestión de riesgo estricta (máximo 0.5-1% por operación).

---
Hecho con la estrategia MapleStax CBC 5m Pro.