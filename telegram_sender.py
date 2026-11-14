import requests
import logging
from typing import List, Dict
import config

logger = logging.getLogger(__name__)

def send_telegram_message(text: str, parse_mode: str = "HTML") -> bool:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("Telegram no configurado (falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID)")
        return False
    
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            return True
        else:
            logger.error(f"Telegram error {r.status_code}: {r.text}")
            return False
    except Exception as e:
        logger.error(f"Error enviando a Telegram: {e}")
        return False


def format_signal(signal: Dict) -> str:
    emoji = "🟢" if signal["direction"] == "LONG" else "🔴"
    side = signal["direction"]
    
    msg = (
        f"{emoji} <b>MapleStax CBC 5m</b>\n"
        f"<b>{signal['symbol']}</b> → <b>{side}</b>\n\n"
        f"💰 Entry: <code>{signal['entry']}</code>\n"
        f"🛑 SL: <code>{signal['sl']}</code>\n"
        f"🎯 TP1: <code>{signal['tp1']}</code> (1.2R)\n"
        f"🎯 TP2: <code>{signal['tp2']}</code> (2.2R)\n\n"
        f"📊 Score: <b>{signal['score']}/10</b>\n"
        f"ER: {signal['er']} | RVOL: {signal['rvol']}x\n"
        f"CMF: {signal['cmf']}\n"
        f"TF: 5m | BingX Futures"
    )
    return msg


def send_top_signals(signals: List[Dict]):
    if not signals:
        send_telegram_message("⏳ MapleStax CBC: No se encontraron setups de calidad en este ciclo.")
        return
    
    # Header
    header = f"🚀 <b>MapleStax CBC – Top {len(signals)} señales</b>\n"
    header += f"Timeframe: 5m | Exchange: BingX\n"
    header += "────────────────────\n"
    
    send_telegram_message(header)
    
    for i, sig in enumerate(signals, 1):
        msg = f"<b>#{i}</b>\n" + format_signal(sig)
        send_telegram_message(msg)
    
    footer = (
        "────────────────────\n"
        "✅ Entra en el <b>open de la siguiente vela de 5m</b>\n"
        "⚠️ Gestiona el riesgo (0.5%-1% por trade)"
    )
    send_telegram_message(footer)