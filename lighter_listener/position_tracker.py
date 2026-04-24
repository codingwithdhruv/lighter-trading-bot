import asyncio
import json
from typing import Callable, Awaitable
from utils.logger import logger
from utils.config import LIGHTER_ACCOUNT_INDEX
from trading.lighter_client import lighter_wrapper
from core.copy_engine import copy_engine

class PositionTracker:
    def __init__(self, notification_callback: Callable[[str], Awaitable[None]] = None):
        self._running = False
        self._task = None
        self._positions_cache = {}
        self._bot_executed_markets = set()
        self.notification_callback = notification_callback
        
    def mark_as_bot_executed(self, asset: str):
        normalized = asset.upper().replace("USDC", "").replace("USDT", "").replace(" PERP", "").replace("-", "").strip()
        self._bot_executed_markets.add(normalized)

    async def start(self):
        logger.info("Starting Lighter WebSocket Position Tracker...")
        self._running = True
        self._task = asyncio.create_task(self._listen())
        
    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            
    async def _listen(self):
        while self._running:
            try:
                ws = await lighter_wrapper.get_ws_connection()
                auth_token = lighter_wrapper.get_auth_token()
                if not auth_token:
                    logger.error("No auth token available, retrying in 5s")
                    await asyncio.sleep(5)
                    continue
                    
                subscribe_msg = {
                    "type": "subscribe",
                    "channel": f"account_all_positions/{LIGHTER_ACCOUNT_INDEX}",
                    "auth": auth_token
                }
                
                await ws.send(json.dumps(subscribe_msg))
                
                async def keepalive():
                    while True:
                        await asyncio.sleep(60)
                        try:
                            await ws.ping()
                        except:
                            break
                            
                keepalive_task = asyncio.create_task(keepalive())
                
                try:
                    async for message in ws:
                        if not self._running:
                            break
                            
                        data = json.loads(message)
                        msg_type = data.get("type")
                        
                        if msg_type == "update/account_all_positions":
                            await self._handle_positions_update(data.get("positions", {}))
                        elif msg_type == "subscribed/account_all_positions":
                            await self._handle_positions_snapshot(data.get("positions", {}))
                            
                finally:
                    keepalive_task.cancel()
                    await ws.close()
            except Exception as e:
                logger.error(f"Lighter PositionTracker WebSocket Error: {e}")
                await asyncio.sleep(5)

    async def _handle_positions_snapshot(self, positions: dict):
        for market_idx_str, pos_data in positions.items():
            size = float(pos_data.get("position", "0"))
            # Cache as positive — we track direction separately via TP/SL discovery
            self._positions_cache[market_idx_str] = abs(size)

    async def _handle_positions_update(self, positions: dict):
        for market_idx_str, pos_data in positions.items():
            new_size_str = pos_data.get("position", "0")
            new_size = abs(float(new_size_str))  # Always use absolute value
            old_size = abs(self._positions_cache.get(market_idx_str, 0.0))
            
            if new_size > old_size:
                # Position increased — new trade opened or added to
                symbol = pos_data.get("symbol", "UNKNOWN").split('-')[0]  # BTC-USD -> BTC
                entry_price = float(pos_data.get("avg_entry_price", "0"))
                
                normalized = symbol.upper().replace("USDC", "").replace("USDT", "").replace(" PERP", "").replace("-", "").strip()
                if normalized in self._bot_executed_markets:
                    logger.info(f"WS Tracker ignoring bot-executed trade for {symbol}")
                    self._bot_executed_markets.discard(normalized)
                    self._positions_cache[market_idx_str] = new_size
                    continue
                
                # Discover TP/SL FIRST (side-agnostic), then infer direction from order data
                tp_pips, sl_pips, tp_price, sl_price, order_side = await self._discover_tpsl_pips(market_idx_str, entry_price)
                
                # Infer trade direction: is_ask from TP/SL orders is primary signal
                side = self._infer_side_from_tpsl(entry_price, tp_price, sl_price, order_side)
                
                msg = f"🛰️ *UI Trade Detected:* {side} {symbol} @ {entry_price}"
                if sl_pips > 0: msg += f"\n🛡️ SL: {sl_pips:.2f} pips"
                else: msg += "\n⚠️ No SL found on Lighter."
                
                if self.notification_callback:
                    await self.notification_callback(msg)
                
                logger.info(f"Lighter WS Detected UI Trade: {side} {symbol} @ {entry_price}")
                
                await copy_engine.process_copy_signal(
                    symbol=symbol,
                    side=side,
                    sl_pips=sl_pips,
                    tp_pips=tp_pips
                )
            elif new_size < old_size:
                # Position was reduced or closed
                from core.config_manager import config_manager
                
                if config_manager.track_ui_closures and old_size != 0:
                    symbol = pos_data.get("symbol", "UNKNOWN").split('-')[0]
                    normalized = symbol.upper().replace("USDC", "").replace("USDT", "").replace(" PERP", "").replace("-", "").strip()
                    
                    if normalized in self._bot_executed_markets:
                        logger.info(f"WS Tracker ignoring bot-executed close for {symbol}")
                        self._bot_executed_markets.discard(normalized)
                    else:
                        percent_closed = (old_size - new_size) / old_size
                        # For closures, we need to know the original side.
                        # Use TP/SL discovery on the remaining position (if any) or cache.
                        entry_price = float(pos_data.get("avg_entry_price", "0"))
                        _, _, tp_price, sl_price, order_side = await self._discover_tpsl_pips(market_idx_str, entry_price)
                        original_side = self._infer_side_from_tpsl(entry_price, tp_price, sl_price, order_side)
                        
                        logger.info(f"Lighter WS Detected UI Close: {percent_closed*100:.1f}% of {original_side} {symbol}")
                        
                        if self.notification_callback:
                            await self.notification_callback(f"🛑 *UI Close Detected:* {percent_closed*100:.1f}% of {original_side} {symbol}")
                        
                        await copy_engine.process_close_signal(
                            symbol=symbol,
                            side=original_side,
                            percent_closed=percent_closed
                        )
                
            self._positions_cache[market_idx_str] = new_size

    def _infer_side_from_tpsl(self, entry_price: float, tp_price: float, sl_price: float, order_inferred_side: str = "") -> str:
        """Infer trade direction from TP/SL order data.
        
        Priority:
        1. Direct signal from TP/SL order's is_ask field (most reliable)
        2. TP/SL prices relative to entry (fallback)
        """
        # Priority 1: Direct from is_ask on the TP/SL order
        if order_inferred_side in ("LONG", "SHORT"):
            return order_inferred_side
        
        # Priority 2: TP/SL price relative to entry
        if tp_price > 0 and entry_price > 0:
            if tp_price < entry_price:
                return "SHORT"
            elif tp_price > entry_price:
                return "LONG"
        
        if sl_price > 0 and entry_price > 0:
            if sl_price > entry_price:
                return "SHORT"
            elif sl_price < entry_price:
                return "LONG"
        
        # If no TP/SL data at all, default to LONG (should be very rare)
        logger.warning(f"Could not infer side from TP/SL. TP={tp_price}, SL={sl_price}, Entry={entry_price}. Defaulting to LONG.")
        return "LONG"

    async def _discover_tpsl_pips(self, market_idx: str, entry_price: float) -> tuple[float, float, float, float, str]:
        """Fetch active TP/SL orders from Lighter and convert to pip distances.
        
        Returns: (tp_pips, sl_pips, tp_price, sl_price, inferred_side)
        """
        try:
            from lighter.api.order_api import OrderApi
            from utils.helpers import detect_tp_sl_from_orders
            
            order_api = OrderApi(lighter_wrapper.api_client)
            market_id = int(market_idx)
            auth_token = lighter_wrapper.get_auth_token()
            
            resp = await order_api.account_active_orders_without_preload_content(
                market_id=market_id, account_index=LIGHTER_ACCOUNT_INDEX, auth=auth_token
            )
            data = await resp.json()
            
            # The API returns a flat list of orders under 'orders' key
            orders = data.get('orders', [])
            
            # is_long=True for the primary pass (type-matching doesn't depend on side)
            tp_price, sl_price, inferred_side = detect_tp_sl_from_orders(orders, True)
            
            tp_pips = abs(tp_price - entry_price) if tp_price > 0 and entry_price > 0 else 0.0
            sl_pips = abs(sl_price - entry_price) if sl_price > 0 and entry_price > 0 else 0.0
            
            logger.info(f"TP/SL Discovery for Market {market_idx}: TP=${tp_price} ({tp_pips:.1f}p), SL=${sl_price} ({sl_pips:.1f}p), Side={inferred_side or 'unknown'}")
            return tp_pips, sl_pips, tp_price, sl_price, inferred_side
        except Exception as e:
            logger.error(f"Failed to fetch TP/SL orders for Market {market_idx}: {e}")
            return 0.0, 0.0, 0.0, 0.0, ""

lighter_position_tracker = PositionTracker()
