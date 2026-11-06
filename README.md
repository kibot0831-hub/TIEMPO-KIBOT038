# IPDA BingX Bot v3

Escanea **todos los perps USDT de BingX** al cierre de cada vela y envía señales a Telegram (`MODE=SIGNAL`) o ejecuta en BingX (`MODE=LIVE`).

## Despliegue (Railway)
1. Sube el repo a GitHub → Railway → Deploy from GitHub (servicio **Worker**).
2. Volumen en `/data`.
3. Pega `railway_vars.txt` en Variables y rellena `TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID`.
4. Deploy. Primero `MODE=SIGNAL` varios días.
5. LIVE: `BINGX_API_KEY`, `BINGX_API_SECRET`, `MODE=LIVE`, `LIVE_CONFIRMED=true`. Requiere cuenta en modo **Hedge** (posición LONG/SHORT).

## Estrategia
Sweep de liquidez sobre caja 20/40 velas (medida ANTES del sweep) → Accept/Reject en 3 velas → score.
Filtros: RVOL percentil sobre todo el universo, régimen BTC por EMA50/200, tendencia propia, funding real por símbolo, absorción, ATR%/riesgo% mínimos (comisiones).

## Novedades v3.1
- **SL estructural**: detrás del extremo de la mecha del sweep (antes podía quedar dentro de la mecha). En CONT, detrás del nivel roto.
- **Tiers**: `A` = setup completo (RVOL p≥`XSEC_RVOL_PCT`). `B` = filtro de volumen relajado (p≥`XSEC_B_PCT`) y todas las CONT: solo señal, nunca LIVE. `SEND_TIER_B=false` los desactiva.
- **Embudo en el log**: cada ciclo muestra en qué filtro cae cada símbolo y los percentiles reales de RVOL del universo.
- **Seguimiento en papel** (`tracker.py`): resuelve cada señal (TP1/TP2/SL) con las velas que ya se descargan y publica win% y esperanza en R neta de comisiones en el heartbeat, desglosada por tier, origen, BTC, tendencia, absorción, percentil, tamaño de stop y distancia. Datos en `TRACK_PATH`.
- Última vela cerrada por reloj; precios del aviso redondeados a la precisión del contrato.

## Novedades v3.2
- **Confirmación coherente**: el cierre ACTUAL debe seguir del lado de la señal. Antes salía un SHORT con la entrada por encima del nivel barrido si el último cierre ya había vuelto a subir (setup fallido).
- **Percentil con la misma métrica**: el RVOL del candidato se compara contra el universo usando el máximo de la ventana de sweep (antes se comparaba la vela de sweep, siempre ≤ ese máximo → percentil sesgado a la baja).
- **Tracker con IC95%**: cada esperanza se muestra como `E+0.12±0.25R` y con ⚠ si n < 30. Nuevos desgloses: LONG/SHORT, score y funding (valida el edge de funding).
- **Puerta de edge para LIVE** (`LIVE_REQUIRE_EDGE=true`): el bot solo opera si las últimas `LIVE_EDGE_WINDOW` señales A resueltas en papel suman ≥ `LIVE_MIN_SAMPLES` y esperanza neta > `LIVE_MIN_EDGE_R` para el `LIVE_TP` elegido. Si no, sigue enviando señales pero no ordena. Pon `false` para desactivarla.
- **Guardas de ejecución LIVE**: no entra si el precio se desplazó más de `MAX_ENTRY_DRIFT_R` (en R) respecto a la señal; el tamaño se calcula sobre el riesgo real hasta el SL desde el precio actual; tope `MAX_SAME_SIDE` de posiciones abiertas en el mismo sentido (las alts caen y suben juntas: 3 LONG ≈ 1 apuesta); un fallo de `set_margin_mode` ya no tumba el ciclo.
- **`reconcile` por SL/TP**: el resultado W/L se decide por qué nivel tocó el precio, no por «precio actual vs entrada» (que contaba como ganada una operación parada por SL si luego rebotaba). `MAX_LOSSES_DAY` depende de esas cuentas.
- **Parada limpia**: SIGTERM/SIGINT con `loop.add_signal_handler`; antes el bot no reaccionaba mientras esperaba la próxima vela.

### Break-even con comisiones (FEE_RT_PCT=0.10)
Win rate mínimo para esperanza 0: TP1 (1R) = (1 + c)/2 y TP2 (2R) = (1 + c)/3, con c = comisión / riesgo%.

| riesgo del stop | TP1 | TP2 |
|---|---|---|
| 0.30% | 66.7% | 44.4% |
| 0.50% | 60.0% | 40.0% |
| 1.00% | 55.0% | 36.7% |

Con `MIN_RISK_PCT=0.30` y `LIVE_TP=TP1` los stops pequeños necesitan una tasa de acierto muy alta. El tracker mide ambos objetivos: elige el que muestre mejor E con IC95% claramente por encima de 0, y no ajustes filtros con buckets de n < 100 (con tantos desgloses, alguno saldrá «ganador» por azar).

## Archivos
bot.py · strategy.py · bingx.py · notify.py · tracker.py · config.py · Procfile · railway.toml · railway_vars.txt · requirements.txt · .python-version · .gitignore
