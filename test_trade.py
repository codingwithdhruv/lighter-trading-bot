import asyncio
import logging
from dotenv import load_dotenv

load_dotenv()

# Setup simple logging
logging.basicConfig(level=logging.INFO)

from utils.config import validate_config
from core.copy_engine import copy_engine
from core.config_manager import config_manager
from decibel.client import decibel_client

async def main():
    validate_config()
    
    # Initialize decibel SDK
    await decibel_client.fetch_markets_via_sdk()
    
    # Temporarily set tiny risk sizes
    config_manager.pacifica_max_loss_usd = 0.10
    config_manager.decibel_max_loss_usd = 0.10
    config_manager.pacifica_leverage = 20
    config_manager.decibel_leverage = 20
    
    print("Testing tiny LONG BTC trade...")
    await copy_engine.process_copy_signal(
        symbol="BTC",
        side="LONG",
        sl_pips=2000.0, # large stop loss so position size is very small
        tp_pips=1000.0
    )

if __name__ == "__main__":
    asyncio.run(main())
