# MapleStax CBC → BingX → Telegram

Bot de **señales** (no opera solo) basado en el indicador de TradingView *MapleStax CBC v2.00*.
Analiza **todas las criptos de futuros perpetuos USDT-M de BingX + el petróleo (WTI y Brent)** y envía a Telegram
señales completas: **entrada, stop loss, 3 take profits, riesgo/beneficio y reglas de salida**, para que entres **manualmente**.
Además te avisa cuando se alcanza cada TP, el SL, o cuando el CBC cambia de color en contra de tu operación.

> **Estrategia por defecto (`STRATEGY=pullback`, 15m):**
> **CBC en VERDE → compra cuando cierra una vela ROJA** · **CBC en ROJO → vende cuando cierra una vela VERDE**.
> La estrategia original del indicador (entrar en el flip del acuerdo CBC LTF+HTF) sigue disponible con `STRATEGY=flip`.

> Usa solo la API **pública** de BingX: **no necesita API keys** ni acceso a tu cuenta.

---

## 0. La regla PULLBACK (15m)

| Situación al **cierre** de una vela 15m | Señal |
|---|---|
| CBC verde (alcista) **antes y después** de la vela + la vela es **roja** (`close < open`) | 🟢 **LONG** (compra el retroceso) |
| CBC rojo (bajista) **antes y después** de la vela + la vela es **verde** (`close > open`) | 🔴 **SHORT** (vende el rebote) |

- **"CBC antes y después"**: una vela roja que cierra *por debajo del mínimo anterior* hace que el CBC pase a rojo (flip).
  Eso ya no es un retroceso dentro de la tendencia verde, es la ruptura de la tendencia: **no se compra**.
- **Entrada**: cierre de la vela de señal. **SL**: extremo de esa vela ∓ `SL_BUFFER_ATR`×ATR. Es justo el nivel que,
  si la vela siguiente lo cierra, convierte el CBC al color contrario (la idea queda invalidada).
- **Salida**: SL, TP1/TP2/TP3 (1R/2R/3R, 40/30/30, SL a entrada tras TP1), **el CBC 15m cambia de color en contra**
  (`EXIT_ON_CBC_FLIP=true`) o tiempo máximo (24 h). Una operación por símbolo a la vez.
- **Filtros que siguen activos** (para no recibir cientos de señales por vela): confluencia `MIN_SCORE=3` (EMA 9/20, VWAP, EMA200
  y nube EMA del HTF a favor), stop no más ancho que `MAX_SL_ATR` y espacio hasta PDH/PDL/PDC ≥ `MIN_ROOM_R`.
  Para la regla **pura** sin esos filtros: `MIN_SCORE=0 MIN_ROOM_R=0 SKIP_WIDE_SL=false` (verás muchas más señales).
- `PULLBACK_REQUIRE_HTF=true` exige además que el CBC del HTF (1h) tenga el mismo color.

---

## 1. Cómo traduce la estrategia (original, `STRATEGY=flip`)

| Pine Script (indicador) | En el bot |
|---|---|
| CBC (estado alcista/bajista: `close > high[1]` / `close < low[1]`, rupturas estrictas) | Idéntico, en LTF y HTF |
| "CBC agreement" (LTF y HTF en la misma dirección) | **Disparador de la señal**: la señal nace cuando el acuerdo *empieza* en la última vela cerrada |
| CBC Reversal (doble flip) | Se marca con ⚡ en el mensaje |
| EMA cloud 9/20, VWAP, EMA 200 | Puntuación de confluencia (0-4, junto con la nube EMA del HTF). `MIN_SCORE=3` por defecto |
| Niveles clave YDH / YDL / YDC | Equivalente 24/7: **PDH / PDL / PDC** (día UTC anterior). Se usan para medir el "espacio libre" hasta el siguiente nivel |
| Flip level (precio del próximo flip) | Es el **Stop Loss**: extremo de la vela de señal + colchón ATR |
| HTF 10m | BingX no tiene 10m. Por defecto **LTF 15m / HTF 1h** (configurable) |

**No incluido** (son solo visuales o de sesión NY y no aportan a una señal en 24/7): Bill Breaker, Opening Range, PMH/PML, tabla de estado, etiquetas LONG/SHORT, pivotes.

### Plan de trade (esto lo define el bot; el indicador original no trae SL/TP)
- **Entrada**: cierre de la vela de señal (≈ precio de mercado).
- **SL**: extremo de la vela de señal ∓ `SL_BUFFER_ATR`×ATR, acotado entre `MIN_SL_ATR` y `MAX_SL_ATR`×ATR. Si el stop natural es más ancho que el máximo se **descarta** la señal (entrada tardía).
- **TP1/TP2/TP3**: 1R / 2R / 3R. Cierra 40% / 30% / 30% y **mueve el SL a entrada tras TP1**.
- **Salida**: SL, TPs, señal contraria / cambio de color del CBC o tiempo máximo (24 h).
- **Filtro de espacio**: se descarta si el siguiente nivel PDH/PDL/PDC queda a menos de `MIN_ROOM_R` (1R).
- Si pones `ACCOUNT_USDT`, cada señal incluye el **tamaño de posición** para arriesgar `RISK_PCT`%.

---

## 2. Estructura (TODOS los archivos van sueltos en la raíz, sin carpetas)

```
main.py            arranque (bot, --once, --test-telegram)
backtest.py        backtest con el mismo motor
config.py          variables de entorno
bingx.py           cliente API pública BingX (contratos, ticker 24h, klines v3)
universe.py        criptos + petróleo (detección automática del símbolo)
indicators.py      EMA, ATR, VWAP, CBC, agregación HTF
strategy.py        motor de señales (pullback / flip) + SL/TP/R:R
tracker.py         seguimiento TP/SL/salida por flip CBC y estadística
scanner.py         bucle: analiza al cierre de cada vela
messages.py        mensajes de Telegram
telegram.py        envío
models.py          modelos de datos
test_pullback.py   pruebas offline (regla pullback, tracker, backtest)
test_scanner.py    prueba offline del bucle en vivo (BingX/Telegram simulados)
Dockerfile, railway.json, requirements.txt, .env.example, .gitignore, .dockerignore
```
> Si subes a GitHub, sube **todos** los archivos a la raíz del repositorio (nada de subcarpetas).

---

## 3. Puesta en marcha

### 3.1 Telegram
1. En Telegram habla con **@BotFather** → `/newbot` → guarda el **token**.
2. Abre tu bot y pulsa **Start** (o añádelo a tu grupo/canal y dale permiso de enviar).
3. Obtén tu **chat id**: abre `https://api.telegram.org/bot<TOKEN>/getUpdates` tras escribirle al bot y copia `chat.id`
   (los grupos/canales empiezan por `-100`).

### 3.2 Probar en local (recomendado antes de Railway)
```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # edita token y chat id

python -m unittest discover -p "test_*.py"           # pruebas offline
python main.py --test-telegram                        # ¿llega un mensaje?
python main.py --once --dry-run                       # analiza YA y muestra señales en consola
python main.py                                        # bot en marcha
```
En `--once` se evalúa la última vela cerrada aunque no sea reciente, para que veas el formato.
Revisa en el log la línea `Universo: ... petróleo=[...]` para confirmar que encontró los contratos de petróleo.

### 3.3 Subir a GitHub
```bash
git init && git add . && git commit -m "MapleStax CBC bot"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/bingx-cbc-bot.git
git push -u origin main
```
`.env` está en `.gitignore`: **nunca subas tu token**.

### 3.4 Desplegar en Railway
1. railway.com → **New Project → Deploy from GitHub repo** → elige el repositorio (detecta el `Dockerfile`).
2. Pestaña **Variables**: añade al menos `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` (el resto, ver `.env.example`).
3. Deploy. En **Logs** deberías ver `Universo: N criptos...` y recibirás el mensaje "Bot iniciado" en Telegram.
4. *(Opcional, recomendado)* **Volume** montado en `/data` + variable `STATE_FILE=/data/state.json`: así el seguimiento de operaciones abiertas y la estadística sobreviven a reinicios.
5. Si los logs muestran errores 403 / bloqueo de BingX, cambia la **región** del servicio en Settings (p. ej. a Europa).

Es un *worker* (no abre puertos). Railway cobra por uso: revisa tu plan.

---

## 4. Variables principales

| Variable | Defecto | Qué hace |
|---|---|---|
| `STRATEGY` | `pullback` | `pullback` (CBC verde→compra velas rojas / CBC rojo→vende velas verdes) o `flip` (original) |
| `PULLBACK_REQUIRE_HTF` | `false` | Pullback: exige que el CBC del HTF tenga el mismo color |
| `EXIT_ON_CBC_FLIP` | `true` | Pullback: cierra la operación si el CBC LTF cambia de color en contra |
| `LTF` / `HTF` | `15m` / `1h` | Timeframes (HTF múltiplo de LTF). El CBC y el color de las velas se leen en el LTF |
| `MIN_SCORE` | `3` | Confluencias mínimas (0-4) |
| `HTF_USE_FORMING` | `true` | HTF con vela en formación (como el indicador en vivo) o solo cerrado |
| `MIN_SL_ATR` / `MAX_SL_ATR` | `0.8` / `2.5` | Rango permitido del stop en ATR |
| `TP_R` / `TP_SPLIT` | `1,2,3` / `40,30,30` | Objetivos en R y reparto |
| `MIN_ROOM_R` | `1.0` | Espacio mínimo hasta PDH/PDL/PDC (0 = off) |
| `MIN_QUOTE_VOLUME_USDT` | `1000000` | Volumen 24h mínimo de cada cripto |
| `INCLUDE_OIL` / `EXTRA_SYMBOLS` | `true` / vacío | Petróleo y símbolos extra exactos |
| `ACCOUNT_USDT` / `RISK_PCT` | `0` / `1` | Tamaño sugerido por señal |
| `MAX_SIGNALS_PER_SCAN` | `15` | Tope por vela (se envían las de mejor puntuación) |
| `REQUESTS_PER_SECOND` | `10` | Ritmo de peticiones a BingX |

Los símbolos de petróleo se detectan solos (BingX los lista como `NCCO1OILWTI2USD-USDT` y `NCCO1OILBRENT2USD-USDT`).
Si BingX cambia el nombre, ponlo exacto en `EXTRA_SYMBOLS`.

---

## 5. Backtest (haz esto antes de arriesgar dinero)

```bash
python backtest.py --top 30 --days 14                       # pullback 15m, 30 pares más líquidos
python backtest.py --symbols BTC-USDT,ETH-USDT --days 30
python backtest.py --symbols NCCO1OILWTI2USD-USDT --days 20
MIN_SCORE=4 python backtest.py --top 30 --days 30                  # más estricto
MIN_SCORE=0 MIN_ROOM_R=0 python backtest.py --top 30 --days 30     # regla pura, sin filtros
EXIT_ON_CBC_FLIP=false python backtest.py --top 30 --days 30       # sin salida por cambio de color
STRATEGY=flip LTF=5m HTF=15m python backtest.py --top 30 --days 14 # estrategia original
```
Compara siempre **varias configuraciones sobre el mismo periodo** (con/sin `EXIT_ON_CBC_FLIP`, distintos `MIN_SCORE`, `TP_R`)
y mejor aún en dos periodos distintos, para no ajustar el bot a un tramo concreto del mercado.
Muestra operaciones, winrate, expectativa en R, profit factor y drawdown, **netos de comisión** (`--fee-bps`, 5 = 0,05% por lado).
Los resultados contabilizan "ganadora" cualquier operación que cierre con R neto > 0, incluidas las que cierran en positivo tras TP1.

---

## 6. Avisos importantes

- **Esto no es asesoría financiera.** El indicador original no define SL/TP; el plan de trade de este bot es una propuesta razonable, **sin ventaja demostrada**. Valídalo con `backtest.py` sobre datos reales de BingX y en pequeño antes de usarlo en serio.
- Comprar retrocesos en tendencia **gana mucho cuando la tendencia continúa y pierde varias veces seguidas cuando se agota**
  (varias velas rojas seguidas hasta el SL). Por eso importan el `MIN_SCORE`, la salida por flip y el tamaño de posición pequeño.
- En timeframes bajos las **comisiones pesan mucho**: con stops de 0,4-1% y comisión taker de 0,05% por lado, cada operación pierde ≈ 0,1-0,25R solo en comisiones.
- El seguimiento de TP/SL es una **simulación** con velas cerradas (si SL y TP caen en la misma vela, asume SL primero). No sabe qué señales tomaste tú.
- Las señales se calculan al **cierre** de cada vela; el precio real al entrar a mano puede diferir (slippage). El apalancamiento amplifica pérdidas.
- Los límites de la API de BingX pueden cambiar: si hay muchos fallos, baja `REQUESTS_PER_SECOND` o sube `MIN_QUOTE_VOLUME_USDT` / usa `MAX_SYMBOLS`.
