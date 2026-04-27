import asyncio
import uuid
from utils.logger import logger
from trading.lighter_client import lighter_copy_wrapper
from trading.risk_manager import place_tp_sl_orders
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
        market_info = market_registry.get_market(symbol)
        if not market_info:
            logger.error(f"LighterCopyExecutor: Market {symbol} not found in registry.")
            return False

        # 2. Compute exact position size based on max loss
        # Note: We reuse the logic or follow the same scaling as Pacifica
        # For Lighter, we might need to handle decimals specifically
        
        # Simple size calculation (scaling based on max loss and SL distance)
        # size = max_loss / sl_pips_distance
        if sl_pips <= 0:
            logger.warning(f"LighterCopyExecutor: No SL provided for {symbol}, cannot calculate risk-based size. Defaulting to small size.")
            position_size = 100.0 / entry_price # Placeholder for small trade if no SL
        else:
            # sl_pips is the distance in price. e.g. 1000 for BTC at 60000
            # risk = size * sl_pips
            position_size = max_loss_usd / sl_pips

        # Apply leverage cap if needed (though size is derived from risk)
        # In Lighter, we place market orders.
        
        # Round size to market increments
        min_size = market_info.min_size
        size_increment = market_info.size_increment
        rounded_size = round(position_size / size_increment) * size_increment
        if rounded_size < min_size:
            rounded_size = min_size
            
        # Convert to SDK units (int)
        # SDK uses base units, e.g. 1e8 for BTC
        base_asset_id = market_info.base_asset_id
        from trading.lighter_client import SignerClient
        scale = SignerClient.ASSET_TO_TICKER_SCALE.get(base_asset_id, 1e8)
        base_amount_sdk = int(rounded_size * scale)
        
        # Price for market order (SDK uses price as slippage protection)
        # We'll use 2% slippage
        slippage = 0.02
        if side.upper() == "LONG":
            exec_price = entry_price * (1 + slippage)
            is_ask = False
        else:
            exec_price = entry_price * (1 - slippage)
            is_ask = True
            
        price_sdk = int(exec_price * SignerClient.USDC_TICKER_SCALE)
        
        client_order_idx = int(time.time() * 1000) % (2**63 - 1)
        
        logger.info(f"LighterCopyExecutor: Pushing {rounded_size} {symbol} {side} Market Order (Max Loss: ${max_loss_usd})")
        
        try:
            # Create the order
            tx, resp, err = await self.wrapper.signer_client.create_market_order(
                market_index=market_info.index,
                client_order_index=client_order_idx,
                base_amount=base_amount_sdk,
                avg_execution_price=price_sdk,
                is_ask=is_ask
            )
            
            if err:
                logger.error(f"LighterCopyExecutor Order Rejected: {err}")
                return False
                
            logger.info(f"LighterCopyExecutor Order Placed. Response: {resp}")
            
            # 3. Place TP/SL
            if sl_pips > 0 or tp_pips > 0:
                await asyncio.sleep(1) # Wait for fill
                # Use existing risk_manager logic but with our copy wrapper
                from trading.risk_manager import place_tp_sl_orders
                # We need to adapt place_tp_sl_orders to take a specific wrapper or use a factory
                # For now, I'll implement a local version or modify risk_manager
                await self._place_tp_sl(symbol, side, entry_price, sl_pips, tp_pips, rounded_size)
                
            return True
        except Exception as e:
            logger.error(f"LighterCopyExecutor Execute Exception: {e}")
            return False

    async def _place_tp_sl(self, symbol, side, entry_price, sl_pips, tp_pips, size):
        market_info = market_registry.get_market(symbol)
        if not market_info: return
        
        tp_price = entry_price + tp_pips if side.upper() == "LONG" else entry_price - tp_pips
        sl_price = entry_price - sl_pips if side.upper() == "LONG" else entry_price + sl_pips
        
        # Determine is_ask for TP/SL (opposite of entry)
        # Entry LONG -> TP/SL are ASKS
        # Entry SHORT -> TP/SL are BIDS
        is_ask = (side.upper() == "LONG")
        
        from trading.lighter_client import SignerClient
        scale = SignerClient.ASSET_TO_TICKER_SCALE.get(market_info.base_asset_id, 1e8)
        base_amount_sdk = int(size * scale)
        
        if tp_pips > 0:
            tp_price_sdk = int(tp_price * SignerClient.USDC_TICKER_SCALE)
            logger.info(f"LighterCopyExecutor: Placing TP at {tp_price} for {symbol}")
            await self.wrapper.signer_client.create_order(
                market_index=market_info.index,
                client_order_index=int(time.time() * 1000) + 1,
                base_amount=base_amount_sdk,
                price=tp_price_sdk,
                is_ask=is_ask,
                order_type=SignerClient.ORDER_TYPE_TAKE_PROFIT_LIMIT,
                trigger_price=tp_price_sdk,
                reduce_only=True
            )
            
        if sl_pips > 0:
            sl_price_sdk = int(sl_price * SignerClient.USDC_TICKER_SCALE)
            logger.info(f"LighterCopyExecutor: Placing SL at {sl_price} for {symbol}")
            await self.wrapper.signer_client.create_order(
                market_index=market_info.index,
                client_order_index=int(time.time() * 1000) + 2,
                base_amount=base_amount_sdk,
                price=sl_price_sdk,
                is_ask=is_ask,
                order_type=SignerClient.ORDER_TYPE_STOP_LOSS_LIMIT,
                trigger_price=sl_price_sdk,
                reduce_only=True
            )

    async def execute_close_trade(self, symbol: str, side: str, percent_closed: float, notification_callback=None) -> bool:
        """Close a position on the copy account."""
        # For simplicity, we'll just place a market order to close
        # In a real bot, we'd check current position size
        logger.info(f"LighterCopyExecutor: Closing {percent_closed*100}% of {symbol} {side}")
        
        # We need to know the current position size on the copy account
        # I'll fetch it from the API
        try:
            account_api = lighter.AccountApi(self.wrapper.api_client)
            auth = self.wrapper.get_auth_token()
            resp = await account_api.account(by="index", value=str(self.wrapper.signer_client.account_index), _headers={"Authorization": auth})
            
            position = None
            for p in resp.accounts[0].positions:
                if p.symbol.upper() == symbol.upper():
                    position = p
                    break
            
            if not position or float(position.position) == 0:
                logger.info(f"LighterCopyExecutor: No position found for {symbol} on copy account.")
                return True
                
            current_size = abs(float(position.position))
            close_size = current_size * percent_closed
            
            # Place market order to close
            market_info = market_registry.get_market(symbol)
            is_ask = (float(position.position) > 0) # Close LONG -> ASK, Close SHORT -> BID
            
            from trading.lighter_client import SignerClient
            scale = SignerClient.ASSET_TO_TICKER_SCALE.get(market_info.base_asset_id, 1e8)
            base_amount_sdk = int(close_size * scale)
            
            # Fetch current price for slippage
            entry_price = await self.wrapper.get_mark_price(symbol)
            slippage = 0.02
            exec_price = entry_price * (1 - slippage) if is_ask else entry_price * (1 + slippage)
            price_sdk = int(exec_price * SignerClient.USDC_TICKER_SCALE)
            
            # Cancel all existing TP/SL first
            await self.wrapper.signer_client.order_api.cancel_all_orders(
                self.wrapper.signer_client.account_index,
                market_index=market_info.index,
                _headers={"Authorization": auth}
            )
            
            tx, resp, err = await self.wrapper.signer_client.create_market_order(
                market_index=market_info.index,
                client_order_index=int(time.time() * 1000),
                base_amount=base_amount_sdk,
                avg_execution_price=price_sdk,
                is_ask=is_ask,
                reduce_only=True
            )
            
            if err:
                logger.error(f"LighterCopyExecutor Close Failed: {err}")
                return False
                
            return True
        except Exception as e:
            logger.error(f"LighterCopyExecutor Close Exception: {e}")
            return False

    async def sync_sl_tp(self, symbol: str, side: str, sl_pips: float, tp_pips: float):
        """Update existing TP/SL orders on the copy account."""
        logger.info(f"LighterCopyExecutor: Syncing SL/TP for {symbol}. SL Pips: {sl_pips}, TP Pips: {tp_pips}")
        
        # 1. Fetch current position to get size
        try:
            account_api = lighter.AccountApi(self.wrapper.api_client)
            auth = self.wrapper.get_auth_token()
            resp = await account_api.account(by="index", value=str(self.wrapper.signer_client.account_index), _headers={"Authorization": auth})
            
            position = None
            for p in resp.accounts[0].positions:
                if p.symbol.upper() == symbol.upper():
                    position = p
                    break
            
            if not position or float(position.position) == 0:
                return
                
            current_size = abs(float(position.position))
            market_info = market_registry.get_market(symbol)
            
            # 2. Cancel old TP/SL
            # Lighter doesn't have a specific "Cancel TP/SL" but we can cancel all orders for this market
            await self.wrapper.signer_client.order_api.cancel_all_orders(
                self.wrapper.signer_client.account_index,
                market_index=market_info.index,
                _headers={"Authorization": auth}
            )
            
            # 3. Place new TP/SL
            entry_price = await self.wrapper.get_mark_price(symbol) # Approx entry
            # Wait, the pips are relative to entry. We should ideally get the entry price from the position object if possible.
            # position.avg_entry_price is available
            actual_entry = float(position.avg_entry_price)
            
            await self._place_tp_sl(symbol, side, actual_entry, sl_pips, tp_pips, current_size)
            
        except Exception as e:
            logger.error(f"LighterCopyExecutor Sync SL/TP Exception: {e}")

import time
import lighter
lighter_copy_executor = LighterCopyExecutor()
