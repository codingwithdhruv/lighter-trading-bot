import json
import os
from bot.parser import TradeSignal
from utils.logger import logger
from utils.config import COINDCX_KEY, DECIBEL_PRIVATE_KEY
from trading.coindcx_client import CoinDCXClient
from trading.decibel_client import DecibelClient

SETTINGS_FILE = "data/copy_settings.json"

class CopyConfig:
    def __init__(self):
        self.decibel_enabled = True
        self.coindcx_enabled = True
        self.load()

    def load(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r") as f:
                    data = json.load(f)
                    self.decibel_enabled = data.get("decibel_enabled", True)
                    self.coindcx_enabled = data.get("coindcx_enabled", True)
            except Exception as e:
                logger.error(f"Failed to load copy settings: {e}")

    def save(self):
        try:
            os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
            with open(SETTINGS_FILE, "w") as f:
                json.dump({
                    "decibel_enabled": self.decibel_enabled,
                    "coindcx_enabled": self.coindcx_enabled
                }, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save copy settings: {e}")

    def toggle_decibel(self) -> bool:
        self.decibel_enabled = not self.decibel_enabled
        self.save()
        return self.decibel_enabled

    def toggle_coindcx(self) -> bool:
        self.coindcx_enabled = not self.coindcx_enabled
        self.save()
        return self.coindcx_enabled

copy_config = CopyConfig()
coindcx_client = CoinDCXClient() if COINDCX_KEY else None
decibel_client = DecibelClient() if DECIBEL_PRIVATE_KEY else None

async def dispatch_copy_trade(signal: TradeSignal, base_entry_price: float):
    """
    Dispatches copy trades to Decibel and CoinDCX.
    
    Each exchange will resolve its own TP/SL based on its local mark price 
    if pip distances are provided in the signal (tp_pips/sl_pips).
    """
    logger.info(f"Dispatching Copy Trades for {signal.side} {signal.asset} at Approx Entry {base_entry_price}")
    
    tasks = []
    
    # Decibel
    if decibel_client:
        if copy_config.decibel_enabled:
            tasks.append(decibel_client.execute_trade(signal, base_entry_price))
        else:
            logger.info("Decibel Copy Trading is currently DISABLED. Skipping.")
    
    # CoinDCX
    if coindcx_client:
        if copy_config.coindcx_enabled:
            tasks.append(coindcx_client.execute_trade(signal, base_entry_price))
        else:
            logger.info("CoinDCX Copy Trading is currently DISABLED. Skipping.")
        
    if not tasks:
        logger.info("No active copy trading endpoints configured. Skipping.")
        return
        
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    for idx, res in enumerate(results):
        if isinstance(res, Exception):
            logger.error(f"Copy trade task {idx} failed with error: {res}")


async def dispatch_copy_trade_from_position(symbol: str, side: str, size: float, entry_price: float, tp_pips: float = 0, sl_pips: float = 0):
    """
    Dispatches copy trades when a new position is detected on Lighter
    (e.g., from the web UI). Creates a minimal TradeSignal and dispatches.
    
    Args:
        symbol: e.g., "BTCUSDC Perp"
        side: "LONG" or "SHORT"
        size: absolute position size in base asset
        entry_price: avg entry price
        tp_pips: distance to TP in pips
        sl_pips: distance to SL in pips
    """
    from utils.config import DECIBEL_LEVERAGE, COINDCX_LEVERAGE
    
    # Extract asset name from symbol (e.g., "BTCUSDC Perp" or "BTC-USDC" -> "BTC")
    asset = symbol.upper().replace("USDC", "").replace("USDT", "").replace(" PERP", "").replace("-", "").strip()
    if not asset:
        logger.warning(f"Could not extract asset from symbol: {symbol}")
        return
    
    # Build a minimal TradeSignal for the copy trade
    signal = TradeSignal(
        asset=asset,
        condition_type="ABOVE" if side == "LONG" else "BELOW",
        condition_price=entry_price,
        side=side,
        size=abs(size) * entry_price,  # Convert to USD notional
        leverage=max(DECIBEL_LEVERAGE, COINDCX_LEVERAGE),
        tp=tp_pips,
        sl=sl_pips,
        tp_is_pips=(tp_pips > 0),
        sl_is_pips=(sl_pips > 0),
        tp_pips=tp_pips,
        sl_pips=sl_pips,
    )
    
    logger.info(f"Auto-copying UI position: {side} {asset} @ ${entry_price:,.2f} (TP Pips: {tp_pips}, SL Pips: {sl_pips})")
    await dispatch_copy_trade(signal, entry_price)
