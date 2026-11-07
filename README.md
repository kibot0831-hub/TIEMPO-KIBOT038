# IPDA GRAB Bot (BingX + Telegram)

Bot de señales **GRAB** (barrido de liquidez + cierre de vuelta) en perpetuos BingX.

Filtros alineados al **seguimiento en papel**:

- Solo origen **GRAB** (CONT / B desactivados)
- `MIN_SCORE=85` · SHORT `90`
- Cerca del nivel (`NEAR_ATR_MAX=0.6`)
- Confirmación **5m reject**
- Alineación BTC opcional
- Sesión **07–18 UTC**
- `MODE=SIGNAL` por defecto (no envía órdenes)

## Deploy Railway

1. Sube este repo a GitHub.
2. New Project → Deploy from GitHub.
3. Añade volumen en `/data` (opcional pero recomendado).
4. Pega variables de `railway_vars.txt` (completa `TELEGRAM_*`).
5. Arranque: `worker` (Procfile).

## Local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_TOKEN=... TELEGRAM_CHAT_ID=... MODE=SIGNAL
python bot.py
```

## Modos

| MODE | Comportamiento |
|------|----------------|
| SIGNAL | Solo Telegram |
| PAPER | Señal + estado local (sin órdenes) |
| LIVE | Requiere API keys; path de orden desactivado hasta configurar qty |

## Criterio para LIVE

Tras ≥40 señales **solo GRAB score≥85** con esperanza TP1 > 0 en tu journal, valorar LIVE con `RISK_PCT≤0.5`.

No garantiza beneficios. Cripto es de alto riesgo.
