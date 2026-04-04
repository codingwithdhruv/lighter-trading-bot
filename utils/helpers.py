import time
import random

def generate_client_order_index() -> int:
    """
    Generates a unique-ish 31-bit integer for Lighter's ClientOrderIndex.
    Combines millisecond timestamp with a random component to reduce collision risk.
    Max value is 2**31 - 1 to ensure it fits in a signed 32-bit int if needed, 
    though Lighter usually supports u32.
    """
    # Use last 10 digits of ms timestamp + 3 random digits
    # 1718000000000 -> 0000000000 (10 digits)
    # Then take % 2**31
    ms = int(time.time() * 1000)
    rand = random.randint(0, 999)
    # Combine them: (timestamp_ms * 1000 + rand) % 2**31
    return (ms + rand) % (2**31)

def detect_tp_sl_from_orders(orders: list, is_long: bool) -> tuple:
    """
    Detect Take-Profit (TP) and Stop-Loss (SL) prices from active Lighter orders.
    
    Lighter API returns order types as hyphenated strings:
      - "take-profit", "take-profit-limit" 
      - "stop-loss", "stop-loss-limit"
    And price fields as: "trigger_price", "price" (both as strings)
    
    Returns:
        (tp_price, sl_price) as floats. Returns 0.0 if not found.
    """
    tp_price = 0.0
    sl_price = 0.0
    
    for o in orders:
        otype = str(o.get('type', '')).lower()
        # Use trigger_price if available (for TP/SL orders), otherwise fallback to price
        trigger = o.get('trigger_price', '0')
        limit = o.get('price', '0')
        price = float(trigger) if float(trigger or 0) > 0 else float(limit or 0)
        if price == 0:
            continue
        
        # Match hyphenated Lighter API type names
        if 'take-profit' in otype or 'take_profit' in otype.replace('-', '_') or otype in ('4', '5'):
            tp_price = price
        elif 'stop-loss' in otype or 'stop_loss' in otype.replace('-', '_') or otype in ('2', '3'):
            sl_price = price
    
    # Fallback heuristic if order types aren't explicitly labeled
    if tp_price == 0 and sl_price == 0 and len(orders) >= 2:
        prices = sorted([float(o.get('price', 0) or 0) for o in orders if float(o.get('price', 0) or 0) > 0])
        if len(prices) >= 2:
            if is_long:
                sl_price = prices[0]    # Lower = SL for long
                tp_price = prices[-1]   # Higher = TP for long
            else:
                tp_price = prices[0]    # Lower = TP for short
                sl_price = prices[-1]   # Higher = SL for short
    
    return tp_price, sl_price

