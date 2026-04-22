"""
Decibel Execution Engine — mirrors pacifica/execution.py pattern.

Uses the shared risk_engine to compute position size from max USD loss,
then converts to Decibel chain units and dispatches via the Node.js sidecar.
"""

import asyncio
import math
from utils.logger import logger
from decibel.client import decibel_client
from pacifica.risk_engine import calculate_position_size


def amount_to_chain_units(amount: float, decimals: int = 6) -> int:
    """Convert a human-readable amount to Decibel chain units."""
    return int(math.floor(amount * (10 ** decimals)))


def round_to_tick(chain_value: int, tick_size: int) -> int:
    """Round a chain-unit price to the nearest valid tick multiple."""
    if tick_size <= 0 or chain_value <= 0:
        return chain_value
    return round(chain_value / tick_size) * tick_size


def round_to_lot(chain_value: int, lot_size: int, min_size: int) -> int:
    """Round a chain-unit size to the nearest valid lot multiple, respecting min_size."""
    if lot_size <= 0 or chain_value <= 0:
        return chain_value
    rounded = round(chain_value / lot_size) * lot_size
    return max(rounded, min_size)


class DecibelExecutionEngine:
    def __init__(self):
        self.client = decibel_client

    async def execute_copy_trade(self, symbol: str, side: str, sl_pips: float,
                                 max_loss_usd: float, leverage: int, tp_pips: float = 0.0,
                                 notification_callback=None) -> bool:
        """
        Execute a copy trade on Decibel.
        Mirrors the Pacifica execution flow exactly:
          1. Fetch price
          2. Risk-size the position
          3. Convert to chain units
          4. Place IOC order (market-like)
          5. Apply TP/SL
        """
        if not self.client.is_configured:
            logger.warning("Decibel not configured. Skipping copy trade.")
            return False

        # Map asset symbol to Decibel market name (e.g. BTC -> BTC-USD)
        market_name = self._resolve_market_name(symbol)

        # 1. Fetch current mark price
        entry_price = self.client.get_price(market_name)
        if entry_price <= 0:
            logger.error(f"DecibelExecution: Could not fetch price for {market_name}")
            return False

        # 2. Get account balance
        available_margin = self.client.get_account_balance()

        # 3. Calculate position size using shared risk engine
        position_size, should_execute = calculate_position_size(
            entry_price=entry_price,
            sl_pips=sl_pips,
            max_loss_usd=max_loss_usd,
            leverage=leverage,
            available_margin=available_margin,
        )

        if not should_execute:
            logger.warning(f"DecibelExecution: Risk engine rejected trade (size={position_size:.6f})")
            return False

        # 4. Fetch market config for precision
        market_config = self.client.get_market_config(market_name)
        if not market_config:
            # Try SDK fetch as fallback
            await self.client.fetch_markets_via_sdk()
            market_config = self.client.get_market_config(market_name)

        if not market_config:
            logger.error(f"DecibelExecution: No market config for {market_name}")
            return False

        px_decimals = market_config.get("px_decimals", 6)
        sz_decimals = market_config.get("sz_decimals", 8)
        tick_size = market_config.get("tick_size", 100000)
        lot_size = market_config.get("lot_size", 1000)
        min_size = market_config.get("min_size", 2000)
        market_addr = market_config.get("market_addr", "")

        is_buy = side.upper() == "LONG"

        # 5. Convert to chain units and round
        # For IOC (market-like), set a generous price to ensure fill
        slippage_mult = 1.005 if is_buy else 0.995
        order_price = entry_price * slippage_mult

        chain_price = round_to_tick(amount_to_chain_units(order_price, px_decimals), tick_size)
        chain_size = round_to_lot(amount_to_chain_units(position_size, sz_decimals), lot_size, min_size)

        logger.info(
            f"Decibel: Placing {side} {market_name} | "
            f"size={position_size:.6f} (chain={chain_size}) | "
            f"price={order_price:.2f} (chain={chain_price}) | "
            f"max_loss=${max_loss_usd}"
        )

        # 6. Place order via sidecar
        result = await self.client.place_order(
            market_name=market_name,
            price=chain_price,
            size=chain_size,
            is_buy=is_buy,
        )

        if not result.get("success"):
            logger.error(f"Decibel order failed: {result.get('error', 'unknown')}")
            return False

        tx_hash = result.get("transactionHash", "N/A")
        logger.info(f"Decibel order placed. TxHash: {tx_hash}")
        
        # Spawn background TP/SL monitor task if needed
        if sl_pips > 0 or tp_pips > 0:
            tp_price = 0.0
            sl_price = 0.0
            if side.upper() == "LONG":
                if tp_pips > 0: tp_price = entry_price + tp_pips
                if sl_pips > 0: sl_price = entry_price - sl_pips
            else:
                if tp_pips > 0: tp_price = entry_price - tp_pips
                if sl_pips > 0: sl_price = entry_price + sl_pips
                
            asyncio.create_task(self._monitor_tpsl(symbol, side, tp_price, sl_price, notification_callback))
            
        return True

    async def _monitor_tpsl(self, symbol: str, side: str, tp_price: float, sl_price: float, notification_callback=None):
        """Continuously monitor mark price to trigger local limit close loop on TP/SL hits."""
        logger.info(f"Decibel: Started background TP/SL monitor for {symbol}. TP={tp_price}, SL={sl_price}")
        is_long = side.upper() == "LONG"
        
        while True:
            await asyncio.sleep(2)
            
            # 1. Fetch current position to ensure it hasn't been closed by UI tracking
            positions = self.client.get_positions(symbol)
            if not positions:
                logger.info(f"Decibel: Position {symbol} closed externally. Stopping TP/SL monitor.")
                break
                
            pos = positions[0]
            current_amount = float(pos.get("position_size", pos.get("size", "0")))
            if current_amount == 0:
                logger.info(f"Decibel: Position {symbol} closed externally. Stopping TP/SL monitor.")
                break
                
            current_price = self.client.get_price(symbol)
            if current_price <= 0:
                continue
                
            triggered = False
            trigger_type = ""
            
            if is_long:
                if tp_price and current_price >= tp_price:
                    triggered = True
                    trigger_type = "TP"
                elif sl_price and current_price <= sl_price:
                    triggered = True
                    trigger_type = "SL"
            else:
                if tp_price and current_price <= tp_price:
                    triggered = True
                    trigger_type = "TP"
                elif sl_price and current_price >= sl_price:
                    triggered = True
                    trigger_type = "SL"
                    
            if triggered:
                msg = f"🔔 *Decibel {trigger_type} Hit* for {symbol} at ${current_price:,.2f}!\nStarting mid-price limit closing loop..."
                logger.info(msg)
                if notification_callback:
                    await notification_callback(msg)
                    
                # Execute 100% close
                await self.execute_close_trade(symbol, side, 1.0, notification_callback)
                break

    async def execute_close_trade(self, symbol: str, side: str, percent_closed: float, notification_callback=None) -> bool:
        """Close a position using a mid-price limit order repricing loop on Decibel."""
        positions = self.client.get_positions(symbol)
        if not positions:
            logger.info(f"Decibel: No open positions found for {symbol} to close.")
            return True
            
        pos = positions[0]
        # Decibel REST position object typically has 'position_size' or 'size'
        current_size_str = pos.get("position_size", pos.get("size", "0"))
        current_amount = float(current_size_str)
        
        if current_amount <= 0:
            return True
            
        target_close_amount = current_amount * percent_closed
        
        # Determine side (if currently long/positive, close with short/sell)
        pos_side = "LONG" if current_amount > 0 else "SHORT"
        is_buy = pos_side == "SHORT"
        
        # Decibel SDK expects positive size
        remaining_to_close = abs(target_close_amount)
        current_amount_abs = abs(current_amount)
        attempt = 0
        
        logger.info(f"Decibel: Starting infinite mid-price limit close loop for {symbol}. Target amount: {remaining_to_close:.6f}")
        
        market_config = self.client.get_market_config(symbol)
        if not market_config:
            logger.error(f"Decibel: Cannot find market config for {symbol}")
            return False
            
        market_name = market_config.get("market_name", symbol)
        px_decimals = market_config.get("px_decimals", 6)
        sz_decimals = market_config.get("sz_decimals", 6)
        tick_size = market_config.get("tick_size", 100)
        lot_size = market_config.get("lot_size", 1000)
        min_size = market_config.get("min_size", 1000)
        
        while True:
            if remaining_to_close <= 0:
                logger.info(f"Decibel: Successfully closed {percent_closed*100:.1f}% position for {symbol}.")
                return True
                
            attempt += 1
            if attempt > 1 and attempt % 6 == 0:
                # Every ~30 seconds, alert Telegram if still looping
                alert_msg = f"⚠️ *Decibel Close Loop Warning*\nStill trying to close {remaining_to_close:.6f} {symbol}.\nAttempt: {attempt}"
                logger.warning(alert_msg)
                if notification_callback:
                    await notification_callback(alert_msg)
                
            # For Decibel, get_price returns mark_px which tracks mid-price well.
            # Using mark price avoids needing to query full orderbook just for mid
            mid_price = self.client.get_price(symbol)
            if mid_price <= 0:
                logger.warning("Decibel: Invalid mark price, skipping close attempt.")
                await asyncio.sleep(5)
                continue
                
            chain_price = round_to_tick(amount_to_chain_units(mid_price, px_decimals), tick_size)
            chain_size = round_to_lot(amount_to_chain_units(remaining_to_close, sz_decimals), lot_size, min_size)
            
            # Place GoodTillCanceled (0) limit order
            result = await self.client.place_order(
                market_name=market_name,
                price=chain_price,
                size=chain_size,
                is_buy=is_buy,
                time_in_force=0, 
                is_reduce_only=True
            )
            
            order_id = result.get("orderId")
            if not result.get("success"):
                logger.error(f"Decibel limit close rejected: {result.get('error', 'unknown')}")
                await asyncio.sleep(5)
                continue
                
            # Wait for order to fill
            await asyncio.sleep(5)
            
            # Cancel the unfilled remainder
            if order_id:
                await self.client.cancel_order(order_id)
                await asyncio.sleep(1)
                
            # Update remaining amount
            current_positions = self.client.get_positions(symbol)
            if not current_positions:
                return True
                
            new_amount_str = current_positions[0].get("position_size", current_positions[0].get("size", "0"))
            new_amount_abs = abs(float(new_amount_str))
            
            filled_this_loop = current_amount_abs - new_amount_abs
            if filled_this_loop > 0:
                remaining_to_close -= filled_this_loop
                current_amount_abs = new_amount_abs

        return False

    def _resolve_market_name(self, symbol: str) -> str:
        """Convert asset symbol to Decibel SDK market name format (e.g. BTC -> BTC-USD).
        The SDK placeOrder uses hyphenated format: BTC-USD.
        """
        symbol = symbol.upper().replace("USDC", "").replace("USDT", "").replace(" PERP", "").replace("-", "").replace("/", "").strip()
        return f"{symbol}-USD"


decibel_executor = DecibelExecutionEngine()
