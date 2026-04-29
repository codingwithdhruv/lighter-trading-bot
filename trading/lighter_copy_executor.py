import asyncio
import time
import lighter
from lighter.signer_client import CreateOrderTxReq
from utils.logger import logger
from utils.helpers import generate_client_order_index
from trading.lighter_client import lighter_copy_wrapper
from trading.market_config import market_registry

class LighterCopyExecutor:
    def __init__(self):
        self.wrapper = lighter_copy_wrapper

    async def execute_copy_trade(self, symbol: str, side: str, sl_pips: float, max_loss_usd: float, leverage: int, tp_pips: float = 0.0, notification_callback=None):
        if not self.wrapper.signer_client:
            logger.error("LighterCopyExecutor: Signer client not initialized.")
            return False

        # 1. Fetch market info and balance
        entry_price = await self.wrapper.get_mark_price(symbol)
        if entry_price <= 0:
            logger.error(f"LighterCopyExecutor: Could not fetch price for {symbol}")
            return False

        # Get market details from registry
        market_info = market_registry.get_market_config(symbol)
        if not market_info:
            logger.error(f"LighterCopyExecutor: Market {symbol} not found in registry.")
            return False

        # 2. Compute exact position size based on max loss
        if sl_pips <= 0:
            logger.warning(f"LighterCopyExecutor: No SL provided for {symbol}, cannot calculate risk-based size. Defaulting to small size.")
            position_size = 100.0 / entry_price
        else:
            position_size = max_loss_usd / sl_pips

        # Round size to market increments
        min_size = market_info["min_size"]
        size_increment = market_info["size_increment"]
        rounded_size = round(position_size / size_increment) * size_increment
        if rounded_size < min_size:
            rounded_size = min_size
            
        # Use quote-amount based market order (same pattern as primary infra)
        quote_amount = rounded_size * entry_price
        is_ask = (side.upper() != "LONG")
        
        client_order_idx = generate_client_order_index()
        
        logger.info(f"LighterCopyExecutor: Pushing {rounded_size} {symbol} {side} Market Order (quote=${quote_amount:.2f}, Max Loss: ${max_loss_usd})")
        
        try:
            tx, resp, err = await self.wrapper.signer_client.create_market_order_quote_amount(
                market_index=market_info["market_id"],
                client_order_index=client_order_idx,
                quote_amount=quote_amount,
                max_slippage=0.02,
                is_ask=is_ask,
            )
            
            if err:
                logger.error(f"LighterCopyExecutor Order Rejected: {err}")
                return False
                
            logger.info(f"LighterCopyExecutor Order Placed. Response: {resp}")
            
            # 3. Place TP/SL
            if sl_pips > 0 or tp_pips > 0:
                await asyncio.sleep(1) # Wait for fill
                await self._place_tp_sl(symbol, side, entry_price, sl_pips, tp_pips)
                
            return True
        except Exception as e:
            logger.error(f"LighterCopyExecutor Execute Exception: {e}")
            return False

    async def _place_tp_sl(self, symbol, side, entry_price, sl_pips, tp_pips):
        """Place TP/SL orders using the same proven pattern as primary infra (risk_manager.py)."""
        client = self.wrapper.signer_client
        if not client:
            return
            
        market_info = market_registry.get_market_config(symbol)
        if not market_info:
            return
        
        market_id = market_info["market_id"]
        PRICE_SCALE = market_registry.get_price_scale(symbol)

        tp_price = entry_price + tp_pips if side.upper() == "LONG" else entry_price - tp_pips
        sl_price = entry_price - sl_pips if side.upper() == "LONG" else entry_price + sl_pips
        
        # TP/SL exit direction: LONG -> sell (is_ask=1), SHORT -> buy (is_ask=0)
        is_ask_for_tp_sl = 1 if side.upper() == "LONG" else 0
        
        orders = []
        has_tp = tp_pips > 0
        has_sl = sl_pips > 0
        
        if has_tp:
            tp_trigger = int(tp_price * PRICE_SCALE)
            tp_limit = int(tp_price * PRICE_SCALE * (0.999 if is_ask_for_tp_sl else 1.001))
            orders.append(CreateOrderTxReq(
                MarketIndex=market_id,
                ClientOrderIndex=0 if has_sl else generate_client_order_index(),
                BaseAmount=0,  # 0 = reduce full position
                Price=tp_limit,
                IsAsk=is_ask_for_tp_sl,
                Type=client.ORDER_TYPE_TAKE_PROFIT_LIMIT,
                TimeInForce=client.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
                ReduceOnly=1,
                TriggerPrice=tp_trigger,
                OrderExpiry=-1,
            ))
            logger.info(f"LighterCopyExecutor: Queuing TP at {tp_price} for {symbol}")
            
        if has_sl:
            sl_trigger = int(sl_price * PRICE_SCALE)
            sl_limit = int(sl_price * PRICE_SCALE * (0.999 if is_ask_for_tp_sl else 1.001))
            orders.append(CreateOrderTxReq(
                MarketIndex=market_id,
                ClientOrderIndex=0 if has_tp else generate_client_order_index(),
                BaseAmount=0,  # 0 = reduce full position
                Price=sl_limit,
                IsAsk=is_ask_for_tp_sl,
                Type=client.ORDER_TYPE_STOP_LOSS_LIMIT,
                TimeInForce=client.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
                ReduceOnly=1,
                TriggerPrice=sl_trigger,
                OrderExpiry=-1,
            ))
            logger.info(f"LighterCopyExecutor: Queuing SL at {sl_price} for {symbol}")

        if not orders:
            return

        if has_tp and has_sl:
            grouping = client.GROUPING_TYPE_ONE_CANCELS_THE_OTHER
        else:
            grouping = client.GROUPING_TYPE_NONE

        tx, resp, err = await client.create_grouped_orders(
            grouping_type=grouping,
            orders=orders,
        )
        if err:
            logger.error(f"LighterCopyExecutor TP/SL Failed: {err}")
        else:
            logger.info(f"LighterCopyExecutor TP/SL Placed. TxHash: {resp.tx_hash}")

    async def execute_close_trade(self, symbol: str, side: str, percent_closed: float, notification_callback=None) -> bool:
        """Close a position on the copy account using the same pattern as primary infra."""
        logger.info(f"LighterCopyExecutor: Closing {percent_closed*100}% of {symbol} {side}")
        
        client = self.wrapper.signer_client
        if not client:
            logger.error("LighterCopyExecutor: Signer client not initialized for close.")
            return False
        
        try:
            # 1. Fetch current position
            account_api = lighter.AccountApi(self.wrapper.api_client)
            resp = await account_api.account(by="index", value=str(client.account_index))
            
            position = None
            for p in resp.accounts[0].positions:
                if p.symbol.upper() == symbol.upper():
                    position = p
                    break
            
            if not position or float(position.position) == 0:
                logger.info(f"LighterCopyExecutor: No position found for {symbol} on copy account.")
                return True
            
            # 2. Cancel all existing TP/SL orders first (SignerClient method, not order_api)
            market_info = market_registry.get_market_config(symbol)
            if not market_info:
                logger.error(f"LighterCopyExecutor: Market {symbol} not found in registry.")
                return False
            
            timestamp_ms = int(time.time() * 1000)
            logger.info(f"LighterCopyExecutor: Cancelling all orders for {symbol} on copy account...")
            _, _, cancel_err = await client.cancel_all_orders(
                time_in_force=client.CANCEL_ALL_TIF_IMMEDIATE,
                timestamp_ms=timestamp_ms,
            )
            if cancel_err:
                logger.warning(f"LighterCopyExecutor: Cancel all orders warning: {cancel_err}")
            
            await asyncio.sleep(0.5)  # Brief pause for cancellation to settle
                
            # 3. Close position using quote_amount approach (same as primary infra in risk_manager.py)
            imf = float(position.initial_margin_fraction)
            position_leverage = round(100.0 / imf) if imf > 0 else 1
            
            # Compute notional from position value or from size * price
            current_size = abs(float(position.position))
            close_size = current_size * percent_closed
            
            entry_price = await self.wrapper.get_mark_price(symbol)
            if entry_price <= 0:
                logger.error(f"LighterCopyExecutor: Cannot fetch price for {symbol}")
                return False
            
            quote_amount = close_size * entry_price * 1.01  # Small buffer to ensure full close
            
            # To close a LONG, we sell (is_ask=True). To close a SHORT, we buy (is_ask=False).
            is_ask = (float(position.sign) > 0)
            
            client_order_idx = generate_client_order_index()
            
            logger.info(f"LighterCopyExecutor: Closing {symbol} position: {'LONG→SELL' if is_ask else 'SHORT→BUY'}, quote_amount={quote_amount:.2f}")
            tx, resp, err = await client.create_market_order_quote_amount(
                market_index=market_info["market_id"],
                client_order_index=client_order_idx,
                quote_amount=quote_amount,
                max_slippage=0.02,
                is_ask=is_ask,
                reduce_only=True,
            )
            
            if err:
                logger.error(f"LighterCopyExecutor Close Failed: {err}")
                return False
            
            logger.info(f"LighterCopyExecutor: Position closed successfully.")
            return True
        except Exception as e:
            logger.error(f"LighterCopyExecutor Close Exception: {e}")
            return False

    async def sync_sl_tp(self, symbol: str, side: str, sl_pips: float, tp_pips: float):
        """Update existing TP/SL orders on the copy account."""
        logger.info(f"LighterCopyExecutor: Syncing SL/TP for {symbol}. SL Pips: {sl_pips}, TP Pips: {tp_pips}")
        
        client = self.wrapper.signer_client
        if not client:
            logger.error("LighterCopyExecutor: Signer client not initialized for sync_sl_tp.")
            return
        
        try:
            # 1. Fetch current position to get entry price
            account_api = lighter.AccountApi(self.wrapper.api_client)
            resp = await account_api.account(by="index", value=str(client.account_index))
            
            position = None
            for p in resp.accounts[0].positions:
                if p.symbol.upper() == symbol.upper():
                    position = p
                    break
            
            if not position or float(position.position) == 0:
                return
                
            market_info = market_registry.get_market_config(symbol)
            if not market_info:
                return
            
            # 2. Cancel old TP/SL (SignerClient method, not order_api)
            timestamp_ms = int(time.time() * 1000)
            logger.info(f"LighterCopyExecutor: Cancelling existing orders for {symbol} before re-placing TP/SL...")
            _, _, cancel_err = await client.cancel_all_orders(
                time_in_force=client.CANCEL_ALL_TIF_IMMEDIATE,
                timestamp_ms=timestamp_ms,
            )
            if cancel_err:
                logger.warning(f"LighterCopyExecutor: Cancel all orders warning: {cancel_err}")
            
            await asyncio.sleep(0.5)  # Brief pause
            
            # 3. Place new TP/SL using the actual entry price from position
            actual_entry = float(position.avg_entry_price)
            
            await self._place_tp_sl(symbol, side, actual_entry, sl_pips, tp_pips)
            
        except Exception as e:
            logger.error(f"LighterCopyExecutor Sync SL/TP Exception: {e}")

lighter_copy_executor = LighterCopyExecutor()
