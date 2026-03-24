import asyncio
import time
from typing import Callable, Awaitable
from utils.logger import logger
from bot.parser import TradeSignal

class MarketListener:
    def __init__(self, execute_callback: Callable[[TradeSignal], Awaitable[bool]]):
        self.execute_callback = execute_callback
        self.active_signals = []
        self.price_alerts = []
        self._running = False
        self._last_btc_price = 0.0
        self.bot_handler = None # Set later by app
        
    def add_signal(self, signal: TradeSignal):
        self.active_signals.append(signal)
        logger.info(f"Added signal: {signal.asset} at {signal.condition_price} ({signal.condition_type})")

    def get_active_signals(self) -> list:
        return self.active_signals

    def clear_signals(self):
        self.active_signals = []
        logger.info("Cleared all active signals.")

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
        logger.info("Market Listener started: Bybit WebSocket for 5m candles + alerts")
        
        import websockets
        import json

        ws_url = "wss://stream.bybit.com/v5/public/spot"
        
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
            from utils.config import LIGHTER_ACCOUNT_INDEX
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
                            # It's a fill! Check if it looks like a TP or SL
                            # In this app, we don't have a perfect link, so we look for 'Market Sell' or 'Limit'
                            # But we can at least notify the general fill
                            price = float(o.get("limit_price", 0))
                            size = o.get("size", "0")
                            mkt = o.get("market_id")
                            msg = f"🔔 *Order Filled on Lighter!*\n📍 Mkt ID: {mkt}\n💰 Price: `${price:,.2f}`\n📊 Size: `{size}`"
                            
                            # Use any available bot handler to notify
                            if self.price_alerts:
                                await self.price_alerts[0]["bot"].send_message(msg)
                            elif hasattr(self, 'bot_handler') and self.bot_handler:
                                await self.bot_handler.send_message(msg)
                                
                    # Keep the set from growing forever
                    if len(self._known_inactive_orders) > 200:
                        self._known_inactive_orders = set(list(self._known_inactive_orders)[-100:])
                        
                except Exception as e:
                    logger.debug(f"Order monitor error (likely auth/network): {e}")
                
                await asyncio.sleep(20) # Poll every 20s
                
        cleanup_task = asyncio.create_task(cleanup_expired())
        monitor_task = asyncio.create_task(monitor_orders())
        
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
                                logger.info(f"Confirmed Bybit 5m Close: {topic_symbol} @ ${current_price:,.2f} (Candle Start: {time.strftime('%H:%M:%S', time.localtime(candle_ts))})")
                                
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

    def stop(self):
        self._running = False
        logger.info("Market Listener stopped")
