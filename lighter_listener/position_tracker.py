import asyncio
import json
from utils.logger import logger
from utils.config import LIGHTER_ACCOUNT_INDEX
from trading.lighter_client import lighter_wrapper
from core.copy_engine import copy_engine

class PositionTracker:
    def __init__(self):
        self._running = False
        self._task = None
        self._positions_cache = {}
        self._bot_executed_markets = set()
        
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
        for market_idx_str, pos in positions.items():
            self._positions_cache[market_idx_str] = float(pos.get("position", "0"))

    async def _handle_positions_update(self, positions: dict):
        for market_idx_str, pos_data in positions.items():
            new_size_str = pos_data.get("position", "0")
            new_size = float(new_size_str)
            old_size = self._positions_cache.get(market_idx_str, 0.0)
            
            if abs(new_size) > abs(old_size):
                symbol = pos_data.get("symbol", "UNKNOWN").split('-')[0]  # BTC-USD -> BTC
                side = "LONG" if new_size > old_size else "SHORT"
                entry_price = float(pos_data.get("avg_entry_price", "0"))
                
                normalized = symbol.upper().replace("USDC", "").replace("USDT", "").replace(" PERP", "").replace("-", "").strip()
                if normalized in self._bot_executed_markets:
                    logger.info(f"WS Tracker ignoring bot-executed trade for {symbol}")
                    self._bot_executed_markets.discard(normalized)
                    self._positions_cache[market_idx_str] = new_size
                    continue
                
                logger.info(f"Lighter WS Detected UI Trade: {side} {symbol} @ {entry_price}")
                
                tp_pips, sl_pips = await self._discover_tpsl_pips(market_idx_str, side, entry_price)
                
                await copy_engine.process_copy_signal(
                    symbol=symbol,
                    side=side,
                    sl_pips=sl_pips,
                    tp_pips=tp_pips
                )
                
            self._positions_cache[market_idx_str] = new_size

    async def _discover_tpsl_pips(self, market_idx: str, side: str, entry_price: float) -> tuple[float, float]:
        try:
            from lighter.api.order_api import OrderApi
            order_api = OrderApi(lighter_wrapper.api_client)
            resp = await order_api.account_active_orders_without_preload_content(LIGHTER_ACCOUNT_INDEX)
            
            data = await resp.json()
            market_id = int(market_idx)
            
            sl_pips = 0.0
            tp_pips = 0.0
            
            for o_group in data.get('orders', []):
                if o_group.get('market_index') == market_id:
                    for o in o_group.get('orders', []):
                        if o.get('type') == 'take-profit':
                            trigger = float(o.get('trigger_price', 0))
                            if trigger > 0 and entry_price > 0:
                                tp_pips = abs(trigger - entry_price)
                        elif o.get('type') == 'stop-loss':
                            trigger = float(o.get('trigger_price', 0))
                            if trigger > 0 and entry_price > 0:
                                sl_pips = abs(trigger - entry_price)
                                
            return tp_pips, sl_pips
        except Exception as e:
            logger.error(f"Failed to fetch TP/SL orders for Market {market_idx}: {e}")
            return 0.0, 0.0

lighter_position_tracker = PositionTracker()
