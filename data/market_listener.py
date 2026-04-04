import asyncio
import time
from typing import Callable, Awaitable
from utils.logger import logger
from bot.parser import TradeSignal
from utils.helpers import detect_tp_sl_from_orders
from utils.config import POLL_INTERVAL_SEC, BYBIT_WS_URL

class MarketListener:
    def __init__(self, execute_callback: Callable[[TradeSignal], Awaitable[bool]]):
        self.execute_callback = execute_callback
        self.active_signals = []
        self.price_alerts = []
        self._running = False
        self._last_btc_price = 0.0
        self.bot_handler = None # Set later by app
        self._bot_executed_markets = set()
        
    def add_signal(self, signal: TradeSignal):
        self.active_signals.append(signal)
        logger.info(f"Added signal: {signal.asset} at {signal.condition_price} ({signal.condition_type})")

    def get_active_signals(self) -> list:
        return self.active_signals

    def clear_signals(self):
        self.active_signals = []
        logger.info("Cleared all active signals.")

    def mark_as_bot_executed(self, asset: str):
        """Mark an asset as bot-executed so position monitor won't auto-copy it."""
        # Normalize to base asset (e.g., "BTC-USDC" -> "BTC", "BTC" -> "BTC")
        normalized = asset.upper().replace("USDC", "").replace("USDT", "").replace(" PERP", "").replace("-", "").strip()
        self._bot_executed_markets.add(normalized)
        logger.debug(f"Marked {normalized} as bot-executed (to skip auto-copy).")

    # --- Price Alert Methods ---
    def add_price_alert(self, price: float, message: str, bot_handler, alert_type="crossing", direction=None):
        """
        alert_type: "crossing" (instant tick) or "closing" (5m candle close)
        direction: "above" or "below" — for crossing alerts, auto-detected from current price
        """
        if direction is None:
            direction = "above" if price > self._last_btc_price else "below"
        
        self.price_alerts.append({
            "price": price,
            "message": message,
            "bot": bot_handler,
            "alert_type": alert_type,
            "direction": direction,
        })
        logger.info(f"Alert set ({alert_type} {direction}): ${price:,.2f} - {message}")

    def get_price_alerts(self) -> list:
        return self.price_alerts

    def clear_price_alerts(self):
        self.price_alerts = []
        logger.info("Cleared all alerts.")

    async def _check_crossing_alerts(self, current_price: float):
        """Check crossing alerts on every WS tick."""
        triggered = []
        for alert in list(self.price_alerts):
            if alert["alert_type"] != "crossing":
                continue
            hit = False
            if alert["direction"] == "above" and current_price >= alert["price"]:
                hit = True
            elif alert["direction"] == "below" and current_price <= alert["price"]:
                hit = True
            
            if hit:
                triggered.append(alert)
                custom = f" - {alert['message']}" if alert['message'] else ""
                msg = f"🔔 BTC Crossing ${current_price:,.2f}{custom}"
                try:
                    await alert["bot"].send_message(msg)
                    logger.info(f"Crossing alert fired at ${current_price:,.2f}")
                except Exception as e:
                    logger.error(f"Failed to send crossing alert: {e}")
        
        for a in triggered:
            try: self.price_alerts.remove(a)
            except ValueError: pass

    async def _check_closing_alerts(self, close_price: float):
        """Check closing alerts only on confirmed 5m candle close."""
        triggered = []
        for alert in list(self.price_alerts):
            if alert["alert_type"] != "closing":
                continue
            hit = False
            if alert["direction"] == "above" and close_price > alert["price"]:
                hit = True
            elif alert["direction"] == "below" and close_price < alert["price"]:
                hit = True
            
            if hit:
                triggered.append(alert)
                direction = alert["direction"]
                custom = f" - {alert['message']}" if alert['message'] else ""
                msg = f"🔔 BTC Closing {direction} ${close_price:,.2f}{custom}"
                try:
                    await alert["bot"].send_message(msg)
                    logger.info(f"Closing alert fired ({direction}) at ${close_price:,.2f}")
                except Exception as e:
                    logger.error(f"Failed to send closing alert: {e}")
        
        for a in triggered:
            try: self.price_alerts.remove(a)
            except ValueError: pass

    async def start(self):
        """Bybit WebSocket listener for 5m candles + instant/closing price alerts."""
        self._running = True
        logger.info("Market Listener started: Bybit SPOT WebSocket for 5m candles + alerts")
        
        import websockets
        import json

        ws_url = BYBIT_WS_URL
        
        self._known_inactive_orders = set()
        
        async def cleanup_expired():
            while self._running:
                now_int = int(time.time())
                expired = [s for s in self.active_signals if s.expiry_at < now_int]
                for s in expired:
                    logger.info(f"Signal expired for {s.asset}. Removing.")
                    self.active_signals.remove(s)
                await asyncio.sleep(60)
        
        async def monitor_orders():
            """Polls for filled TP/SL orders."""
            from trading.lighter_client import lighter_wrapper
            from utils.config import LIGHTER_ACCOUNT_INDEX, BYBIT_WS_URL
            from lighter.api.order_api import OrderApi
            
            order_api = OrderApi(lighter_wrapper.api_client)
            
            while self._running:
                try:
                    auth_token = lighter_wrapper.get_auth_token()
                    resp = await order_api.account_inactive_orders_without_preload_content(
                        account_index=LIGHTER_ACCOUNT_INDEX,
                        limit=10,
                        auth=auth_token
                    )
                    data = await resp.json()
                    orders = data.get("orders", [])
                    
                    for o in orders:
                        oid = o.get("order_id")
                        if oid in self._known_inactive_orders:
                            continue
                        
                        # New inactive order (Filled or Canceled)
                        self._known_inactive_orders.add(oid)
                        
                        status = o.get("status")
                        if status == "FILLED":
                            price = float(o.get("limit_price", 0))
                            size = o.get("size", "0")
                            mkt = o.get("market_id")
                            order_type = o.get("type", "").upper()
                            is_ask = o.get("is_ask", False)
                            
                            # Identify the order type for clearer notifications
                            if "TAKE_PROFIT" in order_type:
                                fill_label = "🎯 Take Profit Hit!"
                                fill_emoji = "✅"
                            elif "STOP_LOSS" in order_type:
                                fill_label = "🛑 Stop Loss Hit!"
                                fill_emoji = "❌"
                            elif "MARKET" in order_type:
                                fill_label = "📈 Market Order Filled"
                                fill_emoji = "🔄"
                            else:
                                fill_label = "📋 Order Filled"
                                fill_emoji = "🔔"
                            
                            side_str = "SELL" if is_ask else "BUY"
                            msg = (
                                f"{fill_emoji} *{fill_label}*\n"
                                f"📍 Market ID: {mkt}\n"
                                f"💰 Fill Price: `${price:,.2f}`\n"
                                f"📊 Size: `{size}` ({side_str})"
                            )
                            
                            # Prefer bot_handler over price_alerts for notifications
                            if hasattr(self, 'bot_handler') and self.bot_handler:
                                await self.bot_handler.send_message(msg)
                            elif self.price_alerts:
                                await self.price_alerts[0]["bot"].send_message(msg)
                                
                    # Keep the set from growing forever
                    if len(self._known_inactive_orders) > 200:
                        self._known_inactive_orders = set(list(self._known_inactive_orders)[-100:])
                        
                except Exception as e:
                    logger.debug(f"Order monitor error (likely auth/network): {e}")
                
                await asyncio.sleep(POLL_INTERVAL_SEC) # Poll every X seconds
                
        async def monitor_positions():
            """Polls for changes in active positions (e.g. manual UI trades).
            When a new position is detected that wasn't opened by the bot,
            auto-dispatches copy trades to Decibel/CoinDCX."""
            from trading.lighter_client import lighter_wrapper
            from utils.config import LIGHTER_ACCOUNT_INDEX
            import lighter
            
            account_api = lighter.AccountApi(lighter_wrapper.api_client)
            last_positions = None
            
            while self._running:
                try:
                    resp = await account_api.account(by="index", value=str(LIGHTER_ACCOUNT_INDEX))
                    if resp.accounts:
                        account = resp.accounts[0]
                        current_positions = {}
                        position_details = {}
                        for pos in (account.positions or []):
                            size = float(pos.position)
                            if size != 0:
                                current_positions[pos.symbol] = size
                                position_details[pos.symbol] = {
                                    "entry": float(pos.avg_entry_price),
                                    "size": size,
                                    "market_id": pos.market_id,
                                }
                        
                        # Compare with last_positions (ignore very first load)
                        if last_positions is not None:
                            for symbol, size in current_positions.items():
                                last_size = last_positions.get(symbol, 0)
                                if size != last_size:
                                    diff = size - last_size
                                    # Identify direction: if size magnitude increased
                                    if abs(size) > abs(last_size):
                                        action = "🟢 INCREASED LONG" if diff > 0 else "🔴 INCREASED SHORT"
                                        is_new_position = (last_size == 0)
                                        if is_new_position:
                                            action = "🟢 OPENED LONG" if diff > 0 else "🔴 OPENED SHORT"
                                            
                                        # We will send the Telegram message after we fetch TP/SL details.
                                        # Auto-copy new positions to other exchanges
                                        # Only copy if this looks like a UI trade (not bot-executed)
                                        # Normalize symbol for comparison with _bot_executed_markets
                                        normalized_symbol = symbol.upper().replace("USDC", "").replace("USDT", "").replace(" PERP", "").replace("-", "").strip()
                                        if is_new_position and normalized_symbol not in self._bot_executed_markets:
                                            try:
                                                from trading.copy_manager import dispatch_copy_trade_from_position
                                                side = "LONG" if diff > 0 else "SHORT"
                                                details = position_details.get(symbol, {})
                                                entry = details.get("entry", 0)
                                                market_id = details.get("market_id")
                                                
                                                # NEW: Poll for active orders to find TP/SL for this UI trade
                                                tp_pips = 0
                                                sl_pips = 0
                                                tp_price = 0
                                                sl_price = 0
                                                try:
                                                    if market_id is not None:
                                                        from lighter.api.order_api import OrderApi
                                                        order_api = OrderApi(lighter_wrapper.api_client)
                                                        auth_token = lighter_wrapper.get_auth_token()
                                                        orders_resp = await order_api.account_active_orders_without_preload_content(
                                                            account_index=LIGHTER_ACCOUNT_INDEX, market_id=market_id, auth=auth_token
                                                        )
                                                        orders_data = await orders_resp.json()
                                                        mkt_orders = [o for o in orders_data.get("orders", []) if o.get("market_id") == market_id]
                                                    
                                                        tp_price, sl_price = detect_tp_sl_from_orders(mkt_orders, (side == "LONG"))
                                                        if tp_price > 0 and entry > 0:
                                                            tp_pips = abs(tp_price - entry)
                                                        if sl_price > 0 and entry > 0:
                                                            sl_pips = abs(sl_price - entry)
                                                            
                                                        if tp_pips > 0 or sl_pips > 0:
                                                            logger.info(f"Detected TP/SL for UI trade {symbol}: TP pips={tp_pips:.1f}, SL pips={sl_pips:.1f}")
                                                except Exception as e:
                                                    logger.warning(f"Failed to fetch TP/SL orders for UI trade Sync: {e}")

                                                # Build comprehensive message "with all details"
                                                msg = (
                                                    f"🛰️ *UI TRADE DETECTED*\n"
                                                    f"━━━━━━━━━━━━━━━━━━━━\n"
                                                    f"📍 Asset  : `{symbol}`\n"
                                                    f"📈 Action : *{action}*\n"
                                                    f"├ Entry: `${entry:,.2f}`\n"
                                                    f"├ Size : `{size}`\n"
                                                )
                                                if tp_price > 0:
                                                    msg += f"├ 🎯 TP: `${tp_price:,.2f}` ({tp_pips:,.0f}p)\n"
                                                if sl_price > 0:
                                                    msg += f"├ 🛑 SL: `${sl_price:,.2f}` ({sl_pips:,.0f}p)\n"
                                                msg += f"└ 🔗 *Syncing to Copy Bots...*"
                                                
                                                # Notify via Telegram
                                                if self.bot_handler:
                                                    await self.bot_handler.send_message(msg)
                                                else:
                                                    logger.warning("No bot_handler set for position update notification.")

                                                if entry > 0:
                                                    asyncio.create_task(
                                                        dispatch_copy_trade_from_position(symbol, side, abs(size), entry, tp_pips=tp_pips, sl_pips=sl_pips)
                                                    )
                                            except Exception as e:
                                                logger.warning(f"Auto-copy dispatch error: {e}")
                                        
                                        else:
                                            # If not new position (just size change), send basic message
                                            msg = (
                                                f"🛰️ *UI TRADE UPDATE*\n"
                                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                                f"📍 Asset  : `{symbol}`\n"
                                                f"📈 Action : *{action}*\n"
                                                f"📊 Size   : `{size}`"
                                            )
                                            if hasattr(self, 'bot_handler') and self.bot_handler:
                                                await self.bot_handler.send_message(msg)
                                            elif hasattr(self, 'price_alerts') and self.price_alerts:
                                                await self.price_alerts[0]["bot"].send_message(msg)
                                                
                                        # Clear the bot-executed marker after detection
                                        if hasattr(self, '_bot_executed_markets'):
                                            self._bot_executed_markets.discard(normalized_symbol)
                        
                        last_positions = current_positions
                        
                except Exception as e:
                    logger.debug(f"Position monitor error: {e}")
                
                await asyncio.sleep(POLL_INTERVAL_SEC)
                
        cleanup_task = asyncio.create_task(cleanup_expired())
        monitor_task = asyncio.create_task(monitor_orders())
        position_task = asyncio.create_task(monitor_positions())
        
        while self._running:
            try:
                logger.info(f"Connecting to Bybit WS: {ws_url}")
                async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
                    subscribed_assets = set()
                    
                    while self._running:
                        needed_assets = set(s.asset.upper() for s in self.active_signals)
                        needed_assets.add("BTC")
                        
                        for needed in needed_assets:
                            if needed not in subscribed_assets:
                                symbol = f"{needed}USDT"
                                req = {"op": "subscribe", "args": [f"kline.5.{symbol}"]}
                                await ws.send(json.dumps(req))
                                subscribed_assets.add(needed)
                                logger.info(f"Subscribed to Bybit kline.5.{symbol}")

                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                            
                        data_payload = json.loads(msg)
                        topic = data_payload.get("topic", "")
                        
                        if topic.startswith("kline.5.") and "data" in data_payload:
                            kline_data_list = data_payload["data"]
                            if not kline_data_list:
                                continue
                            
                            kline = kline_data_list[0]
                            topic_symbol = topic.split("kline.5.")[1]
                            current_price = float(kline.get("close", 0))
                            is_closed = kline.get("confirm", False)
                            
                            # Crossing alerts fire on every tick
                            if topic_symbol == "BTCUSDT":
                                self._last_btc_price = current_price
                                await self._check_crossing_alerts(current_price)
                            
                            # Closing alerts + trade signals fire only on confirmed close
                            if is_closed:
                                candle_ts = int(kline.get("start", 0)) // 1000
                                
                                # Diagnostic: Fetch current Lighter price for side-by-side comparison
                                from trading.lighter_client import lighter_wrapper
                                ltr_price = await lighter_wrapper.get_mark_price(topic_symbol.replace("USDT", ""))
                                
                                logger.info(
                                    f"Confirmed Bybit SPOT 5m Close: {topic_symbol} @ ${current_price:,.2f} "
                                    f"(Lighter Sync Ref: ${ltr_price:,.2f}) "
                                    f"[Candle Start: {time.strftime('%H:%M:%S', time.localtime(candle_ts))}]"
                                )
                                
                                # Check closing alerts
                                if topic_symbol == "BTCUSDT":
                                    await self._check_closing_alerts(current_price)
                                
                                # Check trade signals
                                signals_to_remove = []
                                for signal in list(self.active_signals):
                                    asset = signal.asset.upper()
                                    expected_symbol = f"{asset}USDT"
                                    if expected_symbol != topic_symbol:
                                        continue
                                        
                                    triggered = False
                                    invalidated = False
                                    from utils.config import MAX_TRIGGER_SLIPPAGE
                                    
                                    price_diff = abs(current_price - signal.condition_price)
                                    
                                    if signal.condition_type == "ABOVE" and current_price > signal.condition_price:
                                        if price_diff > MAX_TRIGGER_SLIPPAGE:
                                            invalidated = True
                                        else:
                                            triggered = True
                                    elif signal.condition_type == "BELOW" and current_price < signal.condition_price:
                                        if price_diff > MAX_TRIGGER_SLIPPAGE:
                                            invalidated = True
                                        else:
                                            triggered = True
                                        
                                    if invalidated:
                                        msg = (
                                            f"⚠️ *Signal Invalidated (Slippage)*\n"
                                            f"📍 Asset: {asset}\n"
                                            f"🎯 Trigger: {signal.condition_price}\n"
                                            f"🏁 Closed at: {current_price}\n"
                                            f"❌ Points Diff: {price_diff:.1f} (Max: {MAX_TRIGGER_SLIPPAGE})\n"
                                            f"No trade executed."
                                        )
                                        logger.warning(f"Signal invalidated due to high slippage: {asset} @ {current_price}")
                                        if self.bot_handler:
                                            asyncio.create_task(self.bot_handler.send_message(msg))
                                        signals_to_remove.append(signal)
                                    elif triggered:
                                        logger.info(f"Signal Triggered on Candle Close: {asset} close {current_price} {signal.condition_type} {signal.condition_price}. Executing.")
                                        asyncio.create_task(self.execute_callback(signal, trigger_price=current_price))
                                        signals_to_remove.append(signal)

                                for s in signals_to_remove:
                                    try: self.active_signals.remove(s)
                                    except ValueError: pass

            except Exception as e:
                logger.error(f"Bybit WS error: {e}")
                if self._running:
                    logger.info("Reconnecting in 5s...")
                    await asyncio.sleep(5)
        
        cleanup_task.cancel()
        position_task.cancel()

    def stop(self):
        self._running = False
        logger.info("Market Listener stopped")
