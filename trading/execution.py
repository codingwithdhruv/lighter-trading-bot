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
        is_ask = (signal.side == 'SHORT')  # True if SHORT (sell), False if LONG (buy)
        
        from utils.helpers import generate_client_order_index
        client_order_index = generate_client_order_index()
        
        # 3. Create Market Order
        log_msg = f"Placing Market Order: {signal.side} {quote_amount} USDC worth of {signal.asset}"
        if trigger_price:
            log_msg += f" (Condition met on Bybit SPOT 5m Close: ${trigger_price:,.2f})"
        logger.info(log_msg)
        
        tx, tx_hash, err = await client.create_market_order_quote_amount(
            market_index=market_id,
            client_order_index=client_order_index,
            quote_amount=quote_amount,
            max_slippage=0.01,  # 1% slippage tolerance
            is_ask=is_ask
        )
        if err:
            logger.error(f"Failed to create market order: {err}")
            return False
            
        logger.info(f"Market Order Executed successfully. TxHash: {tx_hash}")
        
        # 4. Resolve pip-based TP/SL to absolute prices for Lighter orders.
        # We use the Lighter mark price as the most accurate "entry" for Lighter orders
        # rather than the Bybit trigger price.
        lighter_mark = await lighter_wrapper.get_mark_price(signal.asset)
        entry_for_pips = lighter_mark if lighter_mark > 0 else (trigger_price or 0)
        
        if entry_for_pips > 0:
            _resolve_pip_tp_sl(signal, entry_for_pips)
            
        # 5. Dispatch Copy Trades Asynchronously
        import asyncio
        from trading.copy_manager import dispatch_copy_trade
        # Copy trades should use the Bybit trigger as the benchmark for entry if available
        asyncio.create_task(dispatch_copy_trade(signal, trigger_price or entry_for_pips))
        
        # 6. Place TP/SL if both are set
        if getattr(signal, 'tp', 0) > 0 and getattr(signal, 'sl', 0) > 0:
            tp_sl_success = await place_tp_sl_orders(signal, is_ask, client_order_index)
            if not tp_sl_success:
                logger.warning("Market order placed, but failed to set TP/SL. Manual intervention might be needed.")
                return False
        else:
            logger.info("No TP/SL values provided, skipping TP/SL placement.")
            
        return True
            
    except Exception as e:
        logger.error(f"Execution exception: {e}")
        return False


def _resolve_pip_tp_sl(signal: TradeSignal, entry_price: float):
    """
    Convert pip-based TP/SL to absolute prices.
    Pips = price points (e.g., 250p on BTC at $69500 → TP=$69750 for LONG).
    
    When tp_is_pips/sl_is_pips is True, the tp/sl value is the pip distance.
    """
    if getattr(signal, 'tp_is_pips', False) and signal.tp and signal.tp > 0:
        pip_distance = signal.tp
        if signal.side == 'LONG':
            signal.tp = entry_price + pip_distance
        else:
            signal.tp = entry_price - pip_distance
        signal.tp_is_pips = False
        logger.info(f"Resolved TP from {pip_distance}p → ${signal.tp:,.2f}")
    
    if getattr(signal, 'sl_is_pips', False) and signal.sl and signal.sl > 0:
        pip_distance = signal.sl
        if signal.side == 'LONG':
            signal.sl = entry_price - pip_distance
        else:
            signal.sl = entry_price + pip_distance
        signal.sl_is_pips = False
        logger.info(f"Resolved SL from {pip_distance}p → ${signal.sl:,.2f}")
