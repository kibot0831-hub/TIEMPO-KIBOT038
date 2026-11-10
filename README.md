# Bot MapleStax CBC · BingX → Telegram

**Regla (15m):** CBC VERDE → compra la vela ROJA cerrada · CBC ROJO → vende la vela VERDE cerrada.
El CBC debe ser del mismo color antes y después de cerrar la vela (si esa vela rompe el mínimo/máximo
anterior, el CBC cambia de color y no se opera). Salida: TP 1R/2R/3R, SL, o cierre cuando el CBC cambia en contra.

## Uso
    pip install -r requirements.txt
    cp .env.example .env            # rellena TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID
    python main.py --test-telegram  # prueba de Telegram
    python main.py --once --dry-run # un análisis, imprime en consola
    python main.py                  # bot en marcha (Railway)
    python backtest.py --top 30 --days 14
    python -m unittest discover -p "test_*.py"

## Regla pura (sin filtros)
Por defecto hay filtros (MIN_SCORE=3, MIN_ROOM_R=1). Para que dispare con CADA vela roja en CBC verde
y CADA vela verde en CBC rojo:  `MIN_SCORE=0  MIN_ROOM_R=0`  (más señales, más ruido).

## Railway
Monta un Volume en /data y pon `STATE_FILE=/data/state.json` para no perder las operaciones abiertas al redeploy.
