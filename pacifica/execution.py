import asyncio
import requests
import uuid
from utils.logger import logger
from pacifica.client import pacifica_client, PACIFICA_REST_URL
from pacifica.risk_engine import calculate_position_size

class PacificaExecutionEngine:
    def __init__(self):
        self.client = pacifica_client

    async def execute_copy_trade(self, symbol: str, side: str, sl_pips: float, max_loss_usd: float, leverage: int, tp_pips: float = 0.0, notification_callback=None):
        # 1. Fetch market info and balance
        entry_price = self.client.get_price(symbol)
        if entry_price <= 0:
            logger.error(f"PacificaExecution: Could not fetch price for {symbol}")
            return False

        available_margin = self.client.get_subaccount_balance()

        # 2. Compute exact position size based on max loss
        position_size, should_execute = calculate_position_size(
            entry_price=entry_price,
            sl_pips=sl_pips,
            max_loss_usd=max_loss_usd,
            leverage=leverage,
            available_margin=available_margin
        )

        if not should_execute:
            return False

        # Fetch market info for lot size rounding to avoid "not a multiple of lot size" error
        market_info = self.client.get_market_info(symbol)
        lot_size_str = market_info.get("lot_size", "0.00001") if market_info else "0.00001"
        lot_size = float(lot_size_str)
        
        # Round to nearest lot size
        rounded_size = round(position_size / lot_size) * lot_size
        
        # Determine number of decimal places from lot_size_str
        decimals = 0
        if "." in lot_size_str:
            decimals = len(lot_size_str.split(".")[1].rstrip("0"))
            
        formatted_size = f"{rounded_size:.{decimals}f}"
        
        if float(formatted_size) <= 0:
            logger.warning(f"PacificaExecution: Size {position_size:.8f} is too small (rounds to 0), aborting.")
            return False
        
        # Determine exact side string for order
        pacifica_side = "bid" if side.upper() == "LONG" else "ask"
        
        # Generate client order ID
        client_order_id = str(uuid.uuid4())

        # 3. Create exact market order parameters
        operation_data = {
            "symbol": symbol.upper(),
            "amount": formatted_size,
            "side": pacifica_side,
            "slippage_percent": "1",  # String type required by Pacifica API
            "reduce_only": False,
            "client_order_id": client_order_id
        }

        # Inline TP/SL with the market order (per docs, avoids separate call)
        # Prices must be multiples of tick size (1 for BTC), so round to integers
        signed_payload = self.client.sign_payload("create_market_order", operation_data)
        
        # 4. Execute the market order
        logger.info(f"Pacifica: Pushing {formatted_size} {symbol} {side} Market Order (Max Loss: ${max_loss_usd})")
        
        try:
            resp = await asyncio.to_thread(requests.post, f"{PACIFICA_REST_URL}/api/v1/orders/create_market", json=signed_payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                logger.info(f"Pacifica Order Response: {data}")
                
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
            else:
                logger.error(f"Pacifica Order Rejected: {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Pacifica Execute Exception: {e}")
            return False

    async def _monitor_tpsl(self, symbol: str, side: str, tp_price: float, sl_price: float, notification_callback=None):
        """Continuously monitor mark price to trigger local limit close loop on TP/SL hits."""
        logger.info(f"Pacifica: Started background TP/SL monitor for {symbol}. TP={tp_price}, SL={sl_price}")
        is_long = side.upper() == "LONG"
        
        while True:
            await asyncio.sleep(2)
            
            # 1. Fetch current position to ensure it hasn't been closed by UI tracking
            positions = self.client.get_positions(symbol)
            if not positions or float(positions[0].get("amount", "0")) <= 0:
                logger.info(f"Pacifica: Position {symbol} closed externally. Stopping TP/SL monitor.")
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
                msg = f"🔔 *Pacifica {trigger_type} Hit* for {symbol} at ${current_price:,.2f}!\nStarting mid-price limit closing loop..."
                logger.info(msg)
                if notification_callback:
                    await notification_callback(msg)
                    
                # Execute 100% close
                await self.execute_close_trade(symbol, side, 1.0, notification_callback)
                break

    async def execute_close_trade(self, symbol: str, side: str, percent_closed: float, notification_callback=None) -> bool:
        """Close a position using a mid-price limit order repricing loop."""
        # 1. Fetch current position
        positions = self.client.get_positions(symbol)
        if not positions:
            logger.info(f"Pacifica: No open positions found for {symbol} to close.")
            return True
            
        pos = positions[0] # Assuming 1 position per symbol
        current_amount = float(pos.get("amount", "0"))
        if current_amount <= 0:
            return True
            
        target_close_amount = current_amount * percent_closed
        
        pos_side = pos.get("side", "")
        # If open position is bid (LONG), close side must be ask (SHORT)
        close_side = "ask" if pos_side == "bid" else "bid"
        
        remaining_to_close = target_close_amount
        attempt = 0
        
        logger.info(f"Pacifica: Starting infinite mid-price limit close loop for {symbol}. Target amount: {target_close_amount:.6f}")
        
        while True:
            if remaining_to_close <= 0:
                logger.info(f"Pacifica: Successfully closed {percent_closed*100:.1f}% position for {symbol}.")
                return True
                
            attempt += 1
            if attempt > 1 and attempt % 6 == 0:
                # Every ~30 seconds, alert Telegram if still looping
                alert_msg = f"⚠️ *Pacifica Close Loop Warning*\nStill trying to close {remaining_to_close:.6f} {symbol}.\nAttempt: {attempt}"
                logger.warning(alert_msg)
                if notification_callback:
                    await notification_callback(alert_msg)
                
            # Fetch Orderbook to get mid price
            ob = self.client.get_orderbook(symbol)
            bids = ob.get("l", [[], []])[0]
            asks = ob.get("l", [[], []])[1]
            
            if not bids or not asks:
                logger.warning("Pacifica: Empty orderbook, skipping close attempt.")
                await asyncio.sleep(5)
                continue
                
            best_bid = float(bids[0].get("p", 0))
            best_ask = float(asks[0].get("p", 0))
            mid_price = (best_bid + best_ask) / 2
            
            # Format price - BTC tick size is 1, so round to integer
            if symbol.upper() == "BTC":
                formatted_price = str(int(round(mid_price)))
            else:
                formatted_price = f"{mid_price:.4f}"
                
            formatted_size = f"{remaining_to_close:.6f}".rstrip("0").rstrip(".")
            if not formatted_size or formatted_size == "0":
                break
                
            client_order_id = str(uuid.uuid4())
            
            operation_data = {
                "symbol": symbol.upper(),
                "price": formatted_price,
                "amount": formatted_size,
                "side": close_side,
                "tif": "GTC",
                "reduce_only": True,
                "client_order_id": client_order_id
            }
            
            signed_payload = self.client.sign_payload("create_limit_order", operation_data)
            
            try:
                resp = await asyncio.to_thread(requests.post, f"{PACIFICA_REST_URL}/api/v1/orders/create_limit", json=signed_payload, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    order_id = data.get("data", {}).get("order_id") or data.get("order_id")
                else:
                    logger.error(f"Pacifica limit close rejected: {resp.text}")
                    await asyncio.sleep(5)
                    continue
            except Exception as e:
                logger.error(f"Pacifica limit close error: {e}")
                await asyncio.sleep(5)
                continue
                
            # Wait for order to fill
            await asyncio.sleep(5)
            
            # Cancel the unfilled remainder
            if order_id:
                self.client.cancel_order(symbol, order_id)
                # Brief wait after cancel to let positions update
                await asyncio.sleep(1)
                
            # Update remaining amount by checking new position size
            current_positions = self.client.get_positions(symbol)
            if not current_positions:
                return True # Fully closed
                
            new_amount = float(current_positions[0].get("amount", "0"))
            filled_this_loop = current_amount - new_amount
            if filled_this_loop > 0:
                remaining_to_close -= filled_this_loop
                current_amount = new_amount

        return False

pacifica_executor = PacificaExecutionEngine()
