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
                                 max_loss_usd: float, leverage: int, tp_pips: float = 0.0) -> bool:
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

        # 7. Apply TP/SL after a brief settlement delay
        if (sl_pips > 0 or tp_pips > 0) and market_addr:
            await asyncio.sleep(2)
            await self._apply_tpsl(market_addr, side, entry_price, sl_pips, tp_pips,
                                   chain_size, px_decimals, tick_size)

        return True

    async def _apply_tpsl(self, market_addr: str, side: str, entry_price: float,
                          sl_pips: float, tp_pips: float, chain_size: int,
                          px_decimals: int, tick_size: int):
        """Place TP/SL for the position via sidecar."""
        if sl_pips <= 0 and tp_pips <= 0:
            return

        is_long = side.upper() == "LONG"

        tp_trigger = None
        tp_limit = None
        sl_trigger = None
        sl_limit = None

        if tp_pips > 0:
            tp_price = entry_price + tp_pips if is_long else entry_price - tp_pips
            tp_trigger = round_to_tick(amount_to_chain_units(tp_price, px_decimals), tick_size)
            # Limit slightly worse than trigger to ensure fill
            tp_limit_price = tp_price * (0.999 if is_long else 1.001)
            tp_limit = round_to_tick(amount_to_chain_units(tp_limit_price, px_decimals), tick_size)

        if sl_pips > 0:
            sl_price = entry_price - sl_pips if is_long else entry_price + sl_pips
            sl_trigger = round_to_tick(amount_to_chain_units(sl_price, px_decimals), tick_size)
            # Limit slightly worse than trigger to ensure fill
            sl_limit_price = sl_price * (0.999 if is_long else 1.001)
            sl_limit = round_to_tick(amount_to_chain_units(sl_limit_price, px_decimals), tick_size)

        logger.info(
            f"Decibel: Setting TP/SL for {market_addr} | "
            f"TP={tp_trigger} SL={sl_trigger}"
        )

        result = await self.client.place_tpsl(
            market_addr=market_addr,
            tp_trigger=tp_trigger,
            tp_limit=tp_limit,
            tp_size=chain_size,
            sl_trigger=sl_trigger,
            sl_limit=sl_limit,
            sl_size=chain_size,
        )

        if result.get("success"):
            logger.info(f"Decibel TP/SL set. TxHash: {result.get('transactionHash', 'N/A')}")
        else:
            logger.error(f"Decibel TP/SL failed: {result.get('error', 'unknown')}")

    def _resolve_market_name(self, symbol: str) -> str:
        """Convert asset symbol to Decibel market name format."""
        symbol = symbol.upper().replace("USDC", "").replace("USDT", "").replace(" PERP", "").replace("-", "").strip()
        return f"{symbol}-USD"


decibel_executor = DecibelExecutionEngine()
