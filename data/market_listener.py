import asyncio
import aiohttp
import time
from typing import Callable, Awaitable
from utils.logger import logger
from utils.config import LIGHTER_API_URL
from bot.parser import TradeSignal
from trading.lighter_client import lighter_wrapper

class MarketListener:
    def __init__(self, execute_callback: Callable[[TradeSignal], Awaitable[bool]]):
        self.execute_callback = execute_callback
        self.active_signals = []
        self._running = False
        
    def add_signal(self, signal: TradeSignal):
        # We assume 1 signal = 1 execution, so we just append to the list.
        # In a generic system, we'd replace old signals or manage duplicates.
        self.active_signals.append(signal)
        logger.info(f"Added new active signal to monitor: {signal.asset} at {signal.condition_price} ({signal.condition_type})")

    def get_active_signals(self) -> list:
        return self.active_signals

    def clear_signals(self):
        self.active_signals = []
        logger.info("Cleared all active signals from monitor.")

    async def _check_conditions(self, current_price: float):
        signals_to_remove = []
        for signal in self.active_signals:
            triggered = False
            if signal.condition_type == "ABOVE" and current_price > signal.condition_price:
                triggered = True
            elif signal.condition_type == "BELOW" and current_price < signal.condition_price:
                triggered = True
                
            if triggered:
                logger.info(f"Signal triggered at current price {current_price}! Executing trade...")
                success = await self.execute_callback(signal)
                if success:
                    signals_to_remove.append(signal)
                    
        for s in signals_to_remove:
            self.active_signals.remove(s)

    async def _fetch_candles(self, asset: str, resolution: str = "5m", count: int = 5):
        """Fetches candles using raw HTTP to avoid SDK model bugs."""
        # Symbol to ID mapping for common Lighter markers
        SYMBOL_MAP = {
            "BTC": 1,
            "ETH": 2,
            "SOL": 3,
            "XRP": 4,
            "HYPE": 5,
        }
        market_id = SYMBOL_MAP.get(asset.upper(), 1)
        
        url = f"{LIGHTER_API_URL}/api/v1/candles"
        now = int(time.time())
        params = {
            "market_id": market_id,
            "resolution": resolution,
            "start_timestamp": now - 3600, # 1 hour back
            "end_timestamp": now,
            "count_back": count
        }
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get('c', [])
                    else:
                        logger.error(f"Candle API error {resp.status}: {await resp.text()}")
            except Exception as e:
                logger.error(f"Failed to fetch candles: {e}")
        return []

    async def start(self):
        """Starts monitoring Lighter 5-min candle closes with high precision"""
        self._running = True
        
        last_processed_ts = 0
        logger.info("Market Listener started with high-precision 5-min alignment")
        
        while self._running:
            try:
                now = time.time()
                now_int = int(now)
                
                # Boundary calculation (aligned to 300s)
                candle_boundary = (now_int // 300) * 300
                
                # Buffer: Check 2 seconds after the boundary to ensure API has indexed the candle
                CHECK_OFFSET = 2
                
                # Sleep until the next possible check or the next second
                if candle_boundary > last_processed_ts:
                    # We have a NEW boundary to check
                    seconds_into_candle = now_int % 300
                    
                    if seconds_into_candle >= CHECK_OFFSET:
                        # IT'S TIME TO CHECK!
                        if not self.active_signals:
                            last_processed_ts = candle_boundary
                            continue
                            
                        logger.info(f"Boundary reached ({time.strftime('%H:%M:%S', time.localtime(candle_boundary))}). Checking candles...")
                        
                        # Fetch candles group by asset
                        assets_to_check = set(s.asset for s in self.active_signals)
                        
                        # WE SET THIS FIRST to prevent infinite loops if callback fails
                        last_processed_ts = candle_boundary
                        
                        for asset in assets_to_check:
                            candles = await self._fetch_candles(asset)
                            if not candles:
                                continue
                                
                            target_candle_start = candle_boundary - 300
                            selected_candle = None
                            
                            for c in reversed(candles):
                                c_start = int(c.get('t', 0)) // 1000
                                if c_start == target_candle_start:
                                    selected_candle = c
                                    break
                                elif c_start < target_candle_start:
                                    selected_candle = c
                                    break
                            
                            if not selected_candle:
                                logger.warning(f"Could not find closed candle starting at {time.strftime('%H:%M:%S', time.localtime(target_candle_start))}")
                                continue

                            close_price = float(selected_candle.get('c', 0))
                            candle_ts = int(selected_candle.get('t', 0)) // 1000
                            
                            logger.info(f"Target Closed Candle for {asset}: Close={close_price}, StartTime={time.strftime('%H:%M:%S', time.localtime(candle_ts))}")
                            
                            for signal in list(self.active_signals):
                                if signal.asset != asset:
                                    continue
                                    
                                triggered = False
                                if signal.condition_type == "ABOVE" and close_price > signal.condition_price:
                                    triggered = True
                                elif signal.condition_type == "BELOW" and close_price < signal.condition_price:
                                    triggered = True
                                    
                                if triggered:
                                    logger.info(f"Signal confirmed on candle close! {asset} at {close_price} (Target: {signal.condition_price}). Executing...")
                                    # Use background task for execution to avoid blocking the listener
                                    asyncio.create_task(self.execute_callback(signal, trigger_price=close_price))
                                    self.active_signals.remove(signal)
                    else:
                        # Wait until the CHECK_OFFSET
                        await asyncio.sleep(CHECK_OFFSET - seconds_into_candle + 0.1)
                else:
                    # We already processed this boundary, wait until the next one starts
                    seconds_to_next_boundary = 300 - (now_int % 300)
                    # We don't want to sleep the whole 5 mins, maybe just 1 second to stay snappy
                    await asyncio.sleep(1)

                # Clean up expired signals
                expired = [s for s in self.active_signals if s.expiry_at < now_int]
                for s in expired:
                    logger.info(f"Signal expired for {s.asset}. Removing.")
                    self.active_signals.remove(s)

            except Exception as e:
                logger.error(f"Market listener error: {e}")
                await asyncio.sleep(1)
            
    async def fallback_get_mark_price(self, api) -> float:
        import lighter
        try:
            order_api = lighter.OrderApi(api)
            # get exchange stats or order book to get mark price
            stats = await order_api.exchange_stats()
            # find BTC market (id=0)
            if hasattr(stats, 'market_stats'):
                for m_stat in stats.market_stats:
                    if m_stat.market_id == MARKET_ID_BTC:
                        # Needs scaling check in real environment
                        return float(m_stat.mark_price)
            return 0.0
        except Exception as e:
            logger.debug(f"Fallback price fetch failed: {e}")
            return 0.0

    def stop(self):
        self._running = False
        logger.info("Market Listener stopped")
