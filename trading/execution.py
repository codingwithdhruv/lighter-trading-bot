from trading.lighter_client import lighter_wrapper
from bot.parser import TradeSignal
from utils.logger import logger
from trading.market_config import market_registry
from trading.risk_manager import place_tp_sl_orders

async def execute_trade(signal: TradeSignal, trigger_price: float = None) -> bool:
    """
    Executes a market order based on the parsed trade signal.
    """
    client = lighter_wrapper.signer_client
    if not client:
        logger.error("SignerClient is not initialized")
        return False
        
    try:
        market_id = market_registry.get_market_id(signal.asset)
        
        # 1. Update Leverage
        margin_mode = client.ISOLATED_MARGIN_MODE
        tx, tx_hash, err = await client.update_leverage(
            market_index=market_id,
            leverage=signal.leverage,
            margin_mode=margin_mode
        )
        if err:
            logger.error(f"Failed to update leverage: {err}")
            return False
            
        logger.info(f"Updated Leverage to {signal.leverage}x. TxHash: {tx_hash}")
        
        # 2. Calculate quote_amount (size * leverage)
        quote_amount = signal.size * signal.leverage
        is_ask = (signal.side == 'SHORT') # True if SHORT (sell), False if LONG (buy)
        
        # We need a client_order_index, this can be managed carefully. For now, 0 or a timestamp.
        import time
        client_order_index = int(time.time() * 1000) % 2**31
        
        # 3. Create Market Order
        log_msg = f"Placing Market Order: {signal.side} {quote_amount} USDC worth of {signal.asset}"
        if trigger_price:
            log_msg += f" (Triggered by 5min Close: {trigger_price})"
        logger.info(log_msg)
        
        tx, tx_hash, err = await client.create_market_order_quote_amount(
            market_index=market_id,
            client_order_index=client_order_index,
            quote_amount=quote_amount,
            max_slippage=0.01, # 1% slippage tolerance
            is_ask=is_ask
        )
        if err:
            logger.error(f"Failed to create market order: {err}")
            return False
            
        logger.info(f"Market Order Executed successfully. TxHash: {tx_hash}")
        
        # 4. Immediately trigger Risk Manager (TP/SL)
        tp_sl_success = await place_tp_sl_orders(signal, is_ask, client_order_index)
        if not tp_sl_success:
            logger.warning("Market order placed, but failed to set TP/SL. Manual intervention might be needed.")
            # Note: A real production system might attempt to close the position here if TP/SL fails
            return False
            
        return True
            
    except Exception as e:
        logger.error(f"Execution exception: {e}")
        return False
