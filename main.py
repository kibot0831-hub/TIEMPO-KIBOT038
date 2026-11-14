import time
import logging
import schedule
from datetime import datetime
import config
from bingx_client import BingXClient
from strategy import analyze_symbol
from telegram_sender import send_top_signals, send_telegram_message

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, "INFO"),
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


def run_scan():
    logger.info("=" * 60)
    logger.info("Iniciando escaneo MapleStax CBC 5m...")
    
    try:
        client = BingXClient()
        
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
                if len(klines) < 60:
                    continue
                
                result = analyze_symbol(klines, symbol)
                if result:
                    signals.append(result)
                    logger.info(f"  ✓ {symbol} {result['direction']} (score {result['score']})")
                
                if i % 8 == 0:
                    time.sleep(0.35)
                    
            except Exception as e:
                logger.warning(f"Error analizando {symbol}: {e}")
                continue
        
        signals.sort(key=lambda x: x["score"], reverse=True)
        top_signals = signals[:config.TOP_N]
        
        logger.info(f"Total señales válidas: {len(signals)} | Enviando top {len(top_signals)}")
        
        send_top_signals(top_signals)
        
    except Exception as e:
        logger.error(f"Error crítico en run_scan: {e}")
        try:
            send_telegram_message(f"❌ Error crítico en el bot:\n<code>{str(e)[:300]}</code>")
        except Exception:
            pass
    
    logger.info("Escaneo finalizado.")
    logger.info("=" * 60)


def main():
    logger.info("MapleStax CBC Bot iniciado")
    logger.info(f"Intervalo: {config.SCAN_INTERVAL_SECONDS}s | Top: {config.TOP_N}")
    
    # Verificar variables críticas
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.error("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID")
        logger.error("Configura las variables en Railway y reinicia el servicio")
        # Mantener el proceso vivo para que se vean los logs
        while True:
            time.sleep(60)
        return
    
    send_telegram_message(
        "🤖 <b>MapleStax CBC Bot iniciado</b>\n"
        f"Escaneo cada {config.SCAN_INTERVAL_SECONDS // 60} min\n"
        f"Analizando top {config.MAX_SYMBOLS_TO_SCAN} monedas BingX\n"
        f"Enviando las {config.TOP_N} mejores señales"
    )
    
    # Primera ejecución
    run_scan()
    
    schedule.every(config.SCAN_INTERVAL_SECONDS).seconds.do(run_scan)
    
    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except Exception as e:
            logger.error(f"Error en loop principal: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()