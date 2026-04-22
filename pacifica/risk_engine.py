from utils.logger import logger

def calculate_position_size(entry_price: float, sl_pips: float, max_loss_usd: float, leverage: int, available_margin: float) -> tuple[float, bool]:
    """
    Calculates the exact position size based on max loss.
    If sl_pips is 0 (no SL on source), falls back to fixed-notional sizing.
    Returns:
        (position_size_in_base, should_execute)
    """
    if sl_pips > 0:
        # Primary model: Size = Max Loss / SL Distance
        position_size = max_loss_usd / sl_pips
    else:
        # Fallback: no SL set on Lighter — use fixed notional based on max_loss as margin
        # Position notional = max_loss_usd * leverage, then convert to base units
        if entry_price <= 0:
            logger.error("Cannot size position: entry_price is 0.")
            return 0, False
        position_size = (max_loss_usd * leverage) / entry_price
        logger.warning(f"No SL on source. Fallback sizing: notional=${max_loss_usd * leverage:.2f}, size={position_size:.6f}")

    notional_value = position_size * entry_price
    required_margin = notional_value / leverage

    if required_margin > available_margin:
        # Cap the position size based on available margin (leaving a 5% buffer for fees)
        usable_margin = available_margin * 0.95
        max_notional = usable_margin * leverage
        capped_size = max_notional / entry_price
        
        logger.warning(
            f"Required Margin (${required_margin:,.2f}) > Available Margin (${available_margin:,.2f}). "
            f"Capping Size from {position_size:.4f} to {capped_size:.4f}"
        )
        position_size = capped_size

    if position_size <= 0:
        return 0, False

    return position_size, True
