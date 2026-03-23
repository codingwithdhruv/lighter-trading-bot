from lighter.signer_client import CreateOrderTxReq
from trading.lighter_client import lighter_wrapper
from bot.parser import TradeSignal
from utils.logger import logger
from utils.config import MARKET_ID_BTC

async def place_tp_sl_orders(signal: TradeSignal, is_ask: bool, client_order_index: int) -> bool:
    """
    Places Grouped OCO orders for TP and SL.
    
    If the initial market order was a LONG (is_ask=False), 
    then the TP/SL need to be SELL orders (is_ask_for_tp_sl=True).
    Similarly, if initial was SHORT, TP/SL are BUY orders.
    """
    client = lighter_wrapper.signer_client
    if not client:
        logger.error("SignerClient is not initialized")
        return False
        
    try:
        is_ask_for_tp_sl = 1 if not is_ask else 0
        
        # We need to format the prices (Lighter usually expects prices in specific tick sizes/decimals)
        # Note: Depending on the asset, Lighter might expect integers with multiplied decimals.
        # Ensure we pass the precise integer format expected. Usually multiplied by 1e4 or 1e8 based on market.
        # Assuming the signal gives raw USD price (like 65000), let's assume we multiply by 100 for decimals?
        # WARNING: This logic needs proper scaling per Lighter Market specs.
        # For this prototype, we'll assume the raw values need an appropriate scale from the user or default to *100.
        # We will use the exact values, the SDK takes integers or floats based on definition.
        # In lighter examples `Price=1550_00` means a scale of 100 on ETH. So we multiply by 100 for standard format.
        PRICE_SCALE = 100 
        
        tp_limit = int(signal.tp * PRICE_SCALE)
        sl_limit = int(signal.sl * PRICE_SCALE)
        
        # We set trigger prices very close or equal to limit for simplicity. 
        # Wait, if LONG: TP trigger <= TP limit. If SHORT: TP trigger >= TP limit
        # For Stop Loss: If LONG: SL trigger >= SL limit. If SHORT: SL trigger <= SL limit
        # This prevents it from immediately crossing the book.
        
        tp_trigger = tp_limit
        sl_trigger = sl_limit
        
        # Adjust limit prices slightly to ensure fill
        if is_ask_for_tp_sl: # We are SELLING to close a LONG
            # we want to sell at SL trigger or less, so limit should be lower than trigger
            sl_limit = int(signal.sl * PRICE_SCALE * 0.999)
            # we want to sell at TP trigger or more, so limit should be lower to ensure fill
            tp_limit = int(signal.tp * PRICE_SCALE * 0.999) 
        else: # We are BUYING to close a SHORT
            # we want to buy at SL trigger or more, so limit should be higher
            sl_limit = int(signal.sl * PRICE_SCALE * 1.001)
            # we want to buy at TP or less, so limit should be higher to ensure fill
            tp_limit = int(signal.tp * PRICE_SCALE * 1.001)

        take_profit_order = CreateOrderTxReq(
            MarketIndex=MARKET_ID_BTC,
            ClientOrderIndex=client_order_index + 1,
            BaseAmount=0, # 0 means ReduceOnly for the full position typically
            Price=tp_limit,
            IsAsk=is_ask_for_tp_sl,
            Type=client.ORDER_TYPE_TAKE_PROFIT_LIMIT,
            TimeInForce=client.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
            ReduceOnly=1,
            TriggerPrice=tp_trigger,
            OrderExpiry=-1,
        )

        stop_loss_order = CreateOrderTxReq(
            MarketIndex=MARKET_ID_BTC,
            ClientOrderIndex=client_order_index + 2,
            BaseAmount=0,
            Price=sl_limit,
            IsAsk=is_ask_for_tp_sl,
            Type=client.ORDER_TYPE_STOP_LOSS_LIMIT,
            TimeInForce=client.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
            ReduceOnly=1,
            TriggerPrice=sl_trigger,
            OrderExpiry=-1,
        )

        logger.info(f"Placing TP/SL OCO Order. TP Limit: {tp_limit}, SL Limit: {sl_limit}")
        transaction = await client.create_grouped_orders(
            grouping_type=client.GROUPING_TYPE_ONE_CANCELS_THE_OTHER,
            orders=[take_profit_order, stop_loss_order],
        )
        logger.info(f"TP/SL Grouped Order Executed successfully: {transaction}")
        return True
    
    except Exception as e:
        logger.error(f"Failed to place TP/SL setup: {e}")
        return False
