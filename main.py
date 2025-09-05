from telethon import TelegramClient, events
import MetaTrader5 as mt5
import re
import config

# ==============================
# FUNCIONES AUXILIARES
# ==============================

def calcular_sl_tp(symbol, entry_price, lotes, riesgo_usd, accion, ratio=2):
    """
    Calcula StopLoss y TakeProfit en función del riesgo y ratio.
    """
    info = mt5.symbol_info(symbol)
    if info is None:
        raise Exception(f"No se encontró información del símbolo {symbol}")

    tick_value = info.trade_tick_value
    tick_size = info.trade_tick_size

    # cuántos ticks equivalen al riesgo permitido
    ticks_riesgo = riesgo_usd / (lotes * tick_value)
    distancia = ticks_riesgo * tick_size  # distancia en precio

    if accion == "compra":
        sl = entry_price - distancia
        tp = entry_price + distancia * ratio
    else:  # venta
        sl = entry_price + distancia
        tp = entry_price - distancia * ratio

    return sl, tp

def enviar_orden(signal_symbol, accion, lotes, riesgo_pct, ratio):
    """
    Envía una orden a MT5 según los parámetros.
    """
    acc_info = mt5.account_info()
    if acc_info is None:
        raise Exception("No se pudo obtener información de la cuenta MT5")
    print(acc_info.login, acc_info.balance, acc_info.equity)

    # Cambiar símbolo de señal Telegram a símbolo Broker correcto
    signal_symbol = config.simbolos_broker.get(signal_symbol, signal_symbol)
       
    info_symbol = mt5.symbol_info(signal_symbol)
    if info_symbol is None:
        raise Exception(f"No se encontró información del símbolo {signal_symbol}")
    
    min_volume = info_symbol.volume_min
    max_volume = info_symbol.volume_max
    step_volume = info_symbol.volume_step


    equity = acc_info.equity
    riesgo_usd = equity * riesgo_pct

    lotes = lotes * equity / 10000 # ajustar lotes según balance de referencia en este caso 10k
    lotes = round(lotes / step_volume) * step_volume # redondear el loteje a uno permitido

    tick = mt5.symbol_info_tick(signal_symbol)
    if tick is None:
        raise Exception(f"No se pudo obtener el tick de {signal_symbol}")

    precio = tick.ask if accion == "compra" else tick.bid
    sl, tp = calcular_sl_tp(signal_symbol, precio, lotes, riesgo_usd, accion, ratio)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": signal_symbol,
        "volume": lotes,
        "type": mt5.ORDER_TYPE_BUY if accion == "compra" else mt5.ORDER_TYPE_SELL,
        "price": precio,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": config.magic_number,
        "comment": "Señal Telegram",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_FOK,
    }

    result = mt5.order_send(request)
    print("Resultado orden:", result)

# ==============================
# TELEGRAM LISTENER
# ==============================

client = TelegramClient("session", config.api_id, config.api_hash)

@client.on(events.NewMessage(chats=config.group_id))
async def handler(event):
    msg = event.message.message
    print("Mensaje recibido:\n", msg)

    # Regex para capturar: símbolo, acción, lotes
    patron = r"OPERACIÓN\s*-\s*(\w+).*?(Compra|Venta).*?([\d.]+)\s*lotes"
    match = re.search(patron, msg, re.S | re.I)

    if match:
        symbol, accion, lotes = match.groups()
        accion = accion.strip().lower()   # "compra" o "venta"
        lotes = float(lotes)

        print(f"→ Señal detectada: {accion.upper()} {symbol} con {lotes} lotes")

        # Inicializar MT5
        if not mt5.initialize():
            print("❌ Error al inicializar MT5:", mt5.last_error())
            return

        try:
            enviar_orden(symbol, accion, lotes, config.riesgo_pct, config.ratio)
        except Exception as e:
            print("❌ Error:", e)

        mt5.shutdown()

# ==============================
# EJECUCIÓN
# ==============================
print("📡 Esperando señales de Telegram...")
client.start()
client.run_until_disconnected()
