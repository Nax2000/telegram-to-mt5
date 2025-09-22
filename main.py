from telethon import TelegramClient, events
import MetaTrader5 as mt5
import re
import config
import subprocess
import time
import psutil

# ==============================
# FUNCIONES AUXILIARES
# ==============================

def ejecutar_mt5():
    # Comprobar si MT5 ya está en ejecución
    mt5_running = any("terminal64.exe" in p.name() for p in psutil.process_iter(['name']))

    if not mt5_running:
        print("🚀 Lanzando MetaTrader 5...")
        subprocess.Popen([config.PATH])
        time.sleep(5)  # Esperar a que arranque
    else:
        print("⚡ MT5 ya estaba abierto")


def calcular_lotaje(symbol, riesgo_usd, SL_distance_price):
    info = mt5.symbol_info(symbol)
    if info is None:
        raise Exception(f"No se encontró información del símbolo {symbol}")
    
    value_tick = info.trade_tick_value
    size_tick = info.trade_tick_size
    min_volume = info.volume_min
    max_volume = info.volume_max
    # step_volume = info.volume_step no parece necesario para validar
    
    print(SL_distance_price)

    SL_distance_tick = abs(SL_distance_price) / size_tick

    lot_size = riesgo_usd / (SL_distance_tick * value_tick) 
    lot_size = round(lot_size, 1)

    if lot_size < min_volume or lot_size > max_volume:
        raise Exception(f"Formato de lotaje no permitido por el broker: {lot_size}")

    return lot_size
    

def calcular_sl_tp(symbol, entry_price, accion, ratio=2):
    """
    Calcula StopLoss y TakeProfit en función de la penultima vela y ratio.
    """
    timeframe = mt5.TIMEFRAME_M5
    numero_velas = 2
    buffer = 5

    candle = mt5.copy_rates_from_pos(symbol, timeframe, 0, numero_velas)
    
    if accion == "compra":
        ref = candle[-1]['low']
        sl = ref - buffer
        tp = entry_price + ((entry_price - ref) * ratio)
    else:  # venta
        ref = candle[-1]['high']
        sl = ref + buffer
        tp = entry_price - ((ref - entry_price) * ratio)
    
    sl = round(sl, 1)
    tp = round(tp, 1)

    distancia = abs(entry_price - sl - buffer)
    return sl, tp, distancia

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
    
    tick = mt5.symbol_info_tick(signal_symbol)
    if tick is None:
        raise Exception(f"No se pudo obtener el tick de {signal_symbol}")
    
    equity = acc_info.equity
    riesgo_usd = equity * riesgo_pct

    spread = tick.ask - tick.bid
    precio = tick.ask if accion == "compra" else tick.bid
    print("Precio:", precio)
    print("Spread:", spread )
    sl, tp, distancia = calcular_sl_tp(signal_symbol, precio, accion, ratio)
    lotes = calcular_lotaje(signal_symbol, riesgo_usd, distancia) # faltaria validar el lote
    print("Distancia:", distancia)

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

def cerrar_orden(symbol):
    """
    Cierra todas las órdenes abiertas de un símbolo en MT5.
    """
    # Asegurarse de que el símbolo está habilitado
    if not mt5.symbol_select(symbol, True):
        print(f"No se pudo seleccionar {symbol}")
        return

    # Obtener posiciones abiertas en el símbolo
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        print(f"No hay posiciones abiertas en {symbol}")
        return
    
    for pos in positions:
        ticket = pos.ticket
        lot = pos.volume

        if pos.type == mt5.POSITION_TYPE_BUY:  # 0 → Buy
            order_type = mt5.ORDER_TYPE_SELL
            price = mt5.symbol_info_tick(symbol).bid
        elif pos.type == mt5.POSITION_TYPE_SELL:  # 1 → Sell
            order_type = mt5.ORDER_TYPE_BUY
            price = mt5.symbol_info_tick(symbol).ask
        else:
            print(f"Tipo de orden desconocido en ticket {ticket}")
            continue

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": config.magic_number,
            "comment": "Señal cierra Telegram",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"❌ Error al cerrar {ticket}: {result.retcode}")
        else:
            print(f"✅ Orden {ticket} cerrada correctamente")
    

# ==============================
# TELEGRAM LISTENER
# ==============================

client = TelegramClient("session", config.api_id, config.api_hash)

@client.on(events.NewMessage(chats=config.group_id))
async def handler(event):
    msg = event.message.message
    print("Mensaje recibido:\n", msg)

    # Patrón abrir
    patron = r"OPERACIÓN\s*-\s*(\w+).*?(Compra|Venta).*?([\d.]+)\s*lotes"
    match = re.search(patron, msg, re.S | re.I)

    # Patrón cerrar
    patron_cierre = r"CERRAR\s*-\s*(\w+)"
    match_cierre = re.search(patron_cierre, msg, re.S | re.I)

    # Inicializar MT5
    if not mt5.initialize():
        print("❌ Error al inicializar MT5:", mt5.last_error())
        return

    try:
        if match:
            symbol, accion, lotes = match.groups()
            accion = accion.strip().lower()   # "compra" o "venta"
            lotes = float(lotes)

            print(f"→ Señal detectada: {accion.upper()} {symbol} con {lotes} lotes")

            try:
                enviar_orden(symbol, accion, lotes, config.riesgo_pct, config.ratio)
            except Exception as e:
                print("❌ Error:", e)

        elif match_cierre:
            symbol = match_cierre.group(1)
            print(f"→ Señal detectada: CERRAR {symbol}")

            try:
                cerrar_orden(symbol)
            except Exception as e:
                print("❌ Error al cerrar:", e)

    except Exception as e:
        print("❌ Error en handler:", e)

    finally:
        mt5.shutdown()

# ==============================
# EJECUCIÓN
# ==============================
ejecutar_mt5()
print("📡 Esperando señales de Telegram...")
client.start()
client.run_until_disconnected()
