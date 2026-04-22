from utils.logger import logger

def calculate_position_size(entry_price: float, sl_pips: float, max_loss_usd: float, leverage: int, available_margin: float) -> tuple[float, bool]:
    """
    Calculates the exact position size based on max loss.
    Returns:
        (position_size_in_base, should_execute)
    """
    if sl_pips <= 0:
        logger.error("Stop loss pips must be strictly positive to calculate risk.")
        return 0, False

    # Position Size = Max Loss / SL Distance
    # Loss = Size * SL_Distance
    position_size = max_loss_usd / sl_pips

    notional_value = position_size * entry_price
    required_margin = notional_value / leverage

    if required_margin > available_margin:
        logger.warning(f"Aborting trade: Required Margin (${required_margin:,.2f}) > Available Margin (${available_margin:,.2f}) for Size {position_size:.4f}")
        return 0, False

    return position_size, True
