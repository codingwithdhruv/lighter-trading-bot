import re
from dataclasses import dataclass
from typing import Optional

@dataclass
class TradeSignal:
    asset: str
    condition_type: str # 'ABOVE' or 'BELOW'
    condition_price: float
    size: float
    leverage: int
    side: str # 'LONG' or 'SHORT'
    tp: Optional[float] = None
    sl: Optional[float] = None
    expiry_at: int = 0  # Timestamp in seconds

def parse_signal(text: str) -> Optional[TradeSignal]:
    """
    Parses a telegram message with high flexibility.
    Supports:
    - BTC < 65000 SHORT TP: 64000
    - ETH > 2500 SIZE: 10
    - BTC CLOSE BELOW 68000 SIDE: SHORT
    """
    try:
        text_upper = text.strip().upper()
        lines = [line.strip() for line in text_upper.split('\n') if line.strip()]
        if not lines: return None
        
        # 1. Parse Header (Asset, Condition, Price)
        # Regex to match: [ASSET] [CONDITION] [PRICE]
        # Condition can be: ABOVE, BELOW, >, <, >=, <=, CLOSE ABOVE, CLOSE BELOW
        header_pattern = r'^([A-Z0-9]+)\s*(?:CLOSE\s+)?(ABOVE|BELOW|>|<|>=|<=)\s*([\d\.,]+)'
        header_match = re.search(header_pattern, lines[0])
        if not header_match:
            return None
            
        asset = header_match.group(1)
        cond_sym = header_match.group(2)
        condition_price = float(header_match.group(3).replace(',', ''))
        
        # Map symbols to ABOVE/BELOW
        if cond_sym in ('>', '>=', 'ABOVE'):
            condition_type = 'ABOVE'
        else:
            condition_type = 'BELOW'
            
        # 2. Extract Key-Value Pairs from whole text
        data_map = {}
        # Find all KEY: VALUE matches
        kv_pairs = re.findall(r'([A-Z]+)\s*:\s*([\d\.,A-Z]+)', text_upper)
        for k, v in kv_pairs:
            data_map[k] = v
            
        # 3. Apply Defaults and Extract Specific Fields
        # Side: look for 'LONG' or 'SHORT' anywhere if not in KV
        side = data_map.get('SIDE')
        if not side:
            if 'SHORT' in text_upper: side = 'SHORT'
            elif 'LONG' in text_upper: side = 'LONG'
            else:
                # Smart Default based on condition
                side = 'LONG' if condition_type == 'ABOVE' else 'SHORT'
        
        # Size: Clear 'USDC', default 2.0
        size_str = data_map.get('SIZE', '2.0').replace('USDC', '').strip()
        size = float(size_str)
        
        # Leverage: Clear 'X', default 40
        lev_str = data_map.get('LEVERAGE', data_map.get('LEV', '40')).replace('X', '').strip()
        leverage = int(lev_str)
        
        # TP/SL
        tp = float(data_map.get('TP', '0'))
        sl = float(data_map.get('SL', '0'))
        
        # Expiry (Default 120 mins)
        expiry_mins = 120
        expiry_match = re.search(r"EXPIRY\s*:\s*(\d+)", text_upper)
        if expiry_match:
            expiry_mins = int(expiry_match.group(1))
        
        import time
        expiry_at = int(time.time()) + (expiry_mins * 60)

        from utils.logger import logger
        logger.info(f"Parsed Signal: {asset} {condition_type} {condition_price} Side:{side} TP:{tp} SL:{sl}")

        return TradeSignal(
            asset=asset,
            condition_type=condition_type,
            condition_price=condition_price,
            size=size,
            leverage=leverage,
            side=side,
            tp=tp,
            sl=sl,
            expiry_at=expiry_at
        )
    except Exception as e:
        import traceback
        # Optional: logger.debug(traceback.format_exc())
        return None
    except Exception as e:
        # Ignore completely invalid texts
        return None
