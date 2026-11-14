import time
import logging
import schedule
from datetime import datetime
import config
from bingx_client import BingXClient
from strategy import analyze_symbol
from telegram_sender import send_top_signals, send_telegram_message

# Logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


def run_scan():
    logger.info("=" * 60)
    logger.info("Iniciando escaneo MapleStax CBC 5m...")
    
    client = BingXClient()
    
    # 1. Obtener los símbolos más líquidos
    symbols = client.get_top_liquid_symbols(
        max_symbols=config.MAX_SYMBOLS_TO_SCAN,
        min_volume=config.MIN_24H_VOLUME_USDT
    )
    
    if not symbols:
        logger.error("No se pudieron obtener símbolos de BingX")
        send_telegram_message("❌ Error: no se pudieron obtener símbolos de BingX")
        return
    
    logger.info(f"Analizando {len(symbols)} símbolos...")
    
    signals = []
    
    for i, symbol in enumerate(symbols, 1):
        try:
            klines = client.get_klines(symbol, interval=config.TIMEFRAME, limit=config.LOOKBACK_BARS)
            if not klines:
                continue
            
            # Descartar la vela en formación: la estrategia trabaja con velas cerradas
            klines = klines[:-1]
            result = analyze_symbol(klines, symbol)
            if result:
                signals.append(result)
                logger.info(f"  ✓ Señal encontrada: {symbol} {result['direction']} (score {result['score']})")
            
            # Pequeña pausa para no saturar la API
            if i % 10 == 0:
                time.sleep(0.4)
                
        except Exception as e:
            logger.warning(f"Error analizando {symbol}: {e}")
            continue
    
    # Ordenar por score descendente y quedarnos con las TOP_N
    signals.sort(key=lambda x: x["score"], reverse=True)
    top_signals = signals[:config.TOP_N]
    
    logger.info(f"Total señales válidas: {len(signals)} | Enviando top {len(top_signals)}")
    
    # Enviar a Telegram
    send_top_signals(top_signals)
    
    logger.info("Escaneo finalizado.")
    logger.info("=" * 60)


def main():
    logger.info("MapleStax CBC Bot iniciado")
    logger.info(f"Intervalo de escaneo: {config.SCAN_INTERVAL_SECONDS}s")
    logger.info(f"Top señales a enviar: {config.TOP_N}")
    
    # Mensaje de arranque
    send_telegram_message(
        "🤖 <b>MapleStax CBC Bot iniciado</b>\n"
        f"Escaneo cada {config.SCAN_INTERVAL_SECONDS // 60} minutos\n"
        f"Analizando top {config.MAX_SYMBOLS_TO_SCAN} monedas más líquidas de BingX\n"
        f"Enviando las {config.TOP_N} mejores señales"
    )
    
    # Ejecutar una vez al arrancar
    run_scan()
    
    # Programar cada X segundos
    schedule.every(config.SCAN_INTERVAL_SECONDS).seconds.do(run_scan)
    
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()