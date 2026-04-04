import time
import lighter

from lighter.signer_client import CreateOrderTxReq
from trading.lighter_client import lighter_wrapper
from bot.parser import TradeSignal
from utils.logger import logger
from trading.market_config import market_registry


async def place_single_tp_order(asset: str, tp_price: float, is_long: bool) -> bool:
    """
    Places a single Take-Profit limit order on Lighter for the given asset.
    Used by the /tp Telegram command.
    """
    client = lighter_wrapper.signer_client
    if not client:
        logger.error("SignerClient is not initialized")
        return False

    try:
        market_id = market_registry.get_market_id(asset)
        PRICE_SCALE = market_registry.get_price_scale(asset)

        # TP exit direction: LONG position → sell (is_ask=1), SHORT → buy (is_ask=0)
        is_ask_for_tp = 1 if is_long else 0

        tp_trigger = int(tp_price * PRICE_SCALE)
        # Adjust limit slightly past trigger to ensure fill
        if is_ask_for_tp:  # Selling at TP for a LONG
            tp_limit = int(tp_price * PRICE_SCALE * 0.999)
        else:  # Buying at TP for a SHORT
            tp_limit = int(tp_price * PRICE_SCALE * 1.001)

        from utils.helpers import generate_client_order_index
        client_order_index = generate_client_order_index()

        tp_order = CreateOrderTxReq(
            MarketIndex=market_id,
            ClientOrderIndex=client_order_index,
            BaseAmount=0,  # 0 = reduce full position
            Price=tp_limit,
            IsAsk=is_ask_for_tp,
            Type=client.ORDER_TYPE_TAKE_PROFIT_LIMIT,
            TimeInForce=client.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
            ReduceOnly=1,
            TriggerPrice=tp_trigger,
            OrderExpiry=-1,
        )

        logger.info(f"Placing single TP order for {asset}: trigger={tp_trigger}, limit={tp_limit}")
        tx, resp, err = await client.create_grouped_orders(
            grouping_type=client.GROUPING_TYPE_NONE,
            orders=[tp_order],
        )
        if err:
            logger.error(f"Failed to place TP order: {err}")
            return False

        logger.info(f"TP order placed successfully. TxHash: {resp.tx_hash}")
        return True

    except Exception as e:
        logger.error(f"place_single_tp_order error: {e}")
        return False


async def place_single_sl_order(asset: str, sl_price: float, is_long: bool) -> bool:
    """
    Places a single Stop-Loss limit order on Lighter for the given asset.
    Used by the /sl Telegram command.
    """
    client = lighter_wrapper.signer_client
    if not client:
        logger.error("SignerClient is not initialized")
        return False

    try:
        market_id = market_registry.get_market_id(asset)
        PRICE_SCALE = market_registry.get_price_scale(asset)

        # SL exit direction: LONG position → sell (is_ask=1), SHORT → buy (is_ask=0)
        is_ask_for_sl = 1 if is_long else 0

        sl_trigger = int(sl_price * PRICE_SCALE)
        # Adjust limit slightly past trigger to ensure fill
        if is_ask_for_sl:  # Selling at SL for a LONG
            sl_limit = int(sl_price * PRICE_SCALE * 0.999)
        else:  # Buying at SL for a SHORT
            sl_limit = int(sl_price * PRICE_SCALE * 1.001)

        from utils.helpers import generate_client_order_index
        client_order_index = generate_client_order_index()

        sl_order = CreateOrderTxReq(
            MarketIndex=market_id,
            ClientOrderIndex=client_order_index,
            BaseAmount=0,  # 0 = reduce full position
            Price=sl_limit,
            IsAsk=is_ask_for_sl,
            Type=client.ORDER_TYPE_STOP_LOSS_LIMIT,
            TimeInForce=client.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
            ReduceOnly=1,
            TriggerPrice=sl_trigger,
            OrderExpiry=-1,
        )

        logger.info(f"Placing single SL order for {asset}: trigger={sl_trigger}, limit={sl_limit}")
        tx, resp, err = await client.create_grouped_orders(
            grouping_type=client.GROUPING_TYPE_NONE,
            orders=[sl_order],
        )
        if err:
            logger.error(f"Failed to place SL order: {err}")
            return False

        logger.info(f"SL order placed successfully. TxHash: {resp.tx_hash}")
        return True

    except Exception as e:
        logger.error(f"place_single_sl_order error: {e}")
        return False


async def close_position_market(asset: str, is_long: bool) -> bool:
    """
    Closes the current position on Lighter by placing a counter-directional market order.
    Used by the /close command and close buttons.
    """
    client = lighter_wrapper.signer_client
    if not client:
        logger.error("SignerClient is not initialized")
        return False

    try:
        from utils.config import LIGHTER_ACCOUNT_INDEX
        market_id = market_registry.get_market_id(asset)

        # Fetch the current position size to determine the close amount
        account_api = lighter.AccountApi(lighter_wrapper.api_client)
        acc_info = await account_api.account(by="index", value=str(LIGHTER_ACCOUNT_INDEX))

        if not acc_info.accounts:
            logger.error("No account found for close_position_market")
            return False

        account = acc_info.accounts[0]
        position_margin = 0.0
        position_leverage = 1

        for pos in (account.positions or []):
            if pos.symbol.upper().startswith(asset.upper()) and float(pos.position) != 0:
                position_margin = float(pos.allocated_margin)
                imf = float(pos.initial_margin_fraction)
                position_leverage = round(100.0 / imf) if imf > 0 else 1
                break

        if position_margin <= 0:
            logger.error(f"No open position found for {asset}")
            return False

        # Quote amount should be the full notional value of the position
        # margin × leverage = notional value. Add a small buffer to ensure full close
        quote_amount = position_margin * position_leverage * 1.01

        # To close a LONG, we sell (is_ask=True). To close a SHORT, we buy (is_ask=False).
        is_ask = is_long

        from utils.helpers import generate_client_order_index
        client_order_index = generate_client_order_index()

        logger.info(f"Closing {asset} position: {'LONG→SELL' if is_long else 'SHORT→BUY'}, quote_amount={quote_amount:.2f}")
        tx, tx_hash, err = await client.create_market_order_quote_amount(
            market_index=market_id,
            client_order_index=client_order_index,
            quote_amount=quote_amount,
            max_slippage=0.02,  # 2% slippage for close orders
            is_ask=is_ask
        )
        if err:
            logger.error(f"Failed to close position: {err}")
            return False

        logger.info(f"Position closed successfully. TxHash: {tx_hash}")
        return True

    except Exception as e:
        logger.error(f"close_position_market error: {e}")
        return False


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
        # We use the MarketRegistry scaling configured dynamically via Lighter API
        market_id = market_registry.get_market_id(signal.asset)
        PRICE_SCALE = market_registry.get_price_scale(signal.asset) 
        
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
            MarketIndex=market_id,
            ClientOrderIndex=0,  # Must be 0 (nil) for grouped OCO orders
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
            MarketIndex=market_id,
            ClientOrderIndex=0,  # Must be 0 (nil) for grouped OCO orders
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
        tx, resp, err = await client.create_grouped_orders(
            grouping_type=client.GROUPING_TYPE_ONE_CANCELS_THE_OTHER,
            orders=[take_profit_order, stop_loss_order],
        )
        if err:
            logger.error(f"Failed to place TP/SL OCO: {err}")
            return False
            
        logger.info(f"TP/SL Grouped Order Executed successfully. TxHash: {resp.tx_hash}")
        return True
    
    except Exception as e:
        logger.error(f"Failed to place TP/SL setup: {e}")
        return False
