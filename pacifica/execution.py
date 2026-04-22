import asyncio
import requests
import uuid
from utils.logger import logger
from pacifica.client import pacifica_client, PACIFICA_REST_URL
from pacifica.risk_engine import calculate_position_size

class PacificaExecutionEngine:
    def __init__(self):
        self.client = pacifica_client

    async def execute_copy_trade(self, symbol: str, side: str, sl_pips: float, max_loss_usd: float, leverage: int, tp_pips: float = 0.0):
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

        # Format pos size properly. Assuming a default of 4 decimals but this could be fetched from market_info
        formatted_size = f"{position_size:.4f}"
        
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
        if sl_pips > 0:
            sl_price = entry_price - sl_pips if side.upper() == "LONG" else entry_price + sl_pips
            operation_data["stop_loss"] = {
                "stop_price": f"{sl_price:.2f}",
                "limit_price": f"{(sl_price * (0.999 if side.upper() == 'LONG' else 1.001)):.2f}"
            }

        if tp_pips > 0:
            tp_price = entry_price + tp_pips if side.upper() == "LONG" else entry_price - tp_pips
            operation_data["take_profit"] = {
                "stop_price": f"{tp_price:.2f}",
                "limit_price": f"{(tp_price * (1.001 if side.upper() == 'LONG' else 0.999)):.2f}"
            }

        signed_payload = self.client.sign_payload("create_market_order", operation_data)
        
        # 4. Execute the market order
        logger.info(f"Pacifica: Pushing {formatted_size} {symbol} {side} Market Order (Max Loss: ${max_loss_usd})")
        
        try:
            resp = await asyncio.to_thread(requests.post, f"{PACIFICA_REST_URL}/api/v1/orders/create_market", json=signed_payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                logger.info(f"Pacifica Order Response: {data}")
                return True
            else:
                logger.error(f"Pacifica Order Rejected: {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Pacifica Execute Exception: {e}")
            return False

pacifica_executor = PacificaExecutionEngine()
