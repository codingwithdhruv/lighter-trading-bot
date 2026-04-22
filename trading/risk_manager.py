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
    Places TP and SL orders. If both are present, uses OCO. If only one, places single.
    
    If the initial market order was a LONG (is_ask=False), 
    then the TP/SL need to be SELL orders (is_ask_for_tp_sl=True).
    """
    client = lighter_wrapper.signer_client
    if not client:
        logger.error("SignerClient is not initialized")
        return False
        
    try:
        is_ask_for_tp_sl = 1 if not is_ask else 0
        market_id = market_registry.get_market_id(signal.asset)
        PRICE_SCALE = market_registry.get_price_scale(signal.asset) 
        
        has_tp = getattr(signal, 'tp', 0) > 0
        has_sl = getattr(signal, 'sl', 0) > 0
        
        if not has_tp and not has_sl:
            return True

        orders = []
        
        if has_tp:
            tp_trigger = int(signal.tp * PRICE_SCALE)
            # Adjust limit to ensure fill
            if is_ask_for_tp_sl: # Sell at TP
                tp_limit = int(signal.tp * PRICE_SCALE * 0.999) 
            else: # Buy at TP
                tp_limit = int(signal.tp * PRICE_SCALE * 1.001)

            orders.append(CreateOrderTxReq(
                MarketIndex=market_id,
                ClientOrderIndex=0 if has_sl else generate_client_order_index(),
                BaseAmount=0,
                Price=tp_limit,
                IsAsk=is_ask_for_tp_sl,
                Type=client.ORDER_TYPE_TAKE_PROFIT_LIMIT,
                TimeInForce=client.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
                ReduceOnly=1,
                TriggerPrice=tp_trigger,
                OrderExpiry=-1,
            ))

        if has_sl:
            sl_trigger = int(signal.sl * PRICE_SCALE)
            # Adjust limit to ensure fill
            if is_ask_for_tp_sl: # Sell at SL
                sl_limit = int(signal.sl * PRICE_SCALE * 0.999)
            else: # Buy at SL
                sl_limit = int(signal.sl * PRICE_SCALE * 1.001)

            orders.append(CreateOrderTxReq(
                MarketIndex=market_id,
                ClientOrderIndex=0 if has_tp else generate_client_order_index(),
                BaseAmount=0,
                Price=sl_limit,
                IsAsk=is_ask_for_tp_sl,
                Type=client.ORDER_TYPE_STOP_LOSS_LIMIT,
                TimeInForce=client.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
                ReduceOnly=1,
                TriggerPrice=sl_trigger,
                OrderExpiry=-1,
            ))

        if has_tp and has_sl:
            logger.info(f"Placing TP/SL OCO Order for {signal.asset}")
            tx, resp, err = await client.create_grouped_orders(
                grouping_type=client.GROUPING_TYPE_ONE_CANCELS_THE_OTHER,
                orders=orders,
            )
        else:
            logger.info(f"Placing single {'TP' if has_tp else 'SL'} order for {signal.asset}")
            tx, resp, err = await client.create_grouped_orders(
                grouping_type=client.GROUPING_TYPE_NONE,
                orders=orders,
            )

        if err:
            logger.error(f"Failed to place TP/SL orders: {err}")
            return False
            
        logger.info(f"TP/SL orders executed successfully. TxHash: {resp.tx_hash}")
        return True
    
    except Exception as e:
        logger.error(f"Failed to place TP/SL setup: {e}")
        return False
