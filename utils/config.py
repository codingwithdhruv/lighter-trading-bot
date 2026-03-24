import os
from dotenv import load_dotenv

load_dotenv()

# Lighter settings
LIGHTER_API_URL = os.getenv("LIGHTER_API_URL", "https://mainnet.zklighter.elliot.ai")
LIGHTER_ACCOUNT_INDEX = int(os.getenv("LIGHTER_ACCOUNT_INDEX", 0))
LIGHTER_API_KEY_INDEX = int(os.getenv("LIGHTER_API_KEY_INDEX", 0))
LIGHTER_PRIVATE_KEY = os.getenv("LIGHTER_PRIVATE_KEY")

# Telegram variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Security: List of allowed Telegram User IDs (e.g. "123456,789012")
# From logs, your ID is: 1060740758
ALLOWED_TELEGRAM_USER_IDS = [
    int(x.strip()) for x in os.getenv("ALLOWED_TELEGRAM_USER_IDS", "").split(",") if x.strip()
]

# Internal Config
POLL_INTERVAL_SEC = 10 # frequency to check candlesticks in seconds
MARKET_ID_BTC = 1 # 1 is BTC/USDC Perp on Lighter Mainnet
MAX_TRIGGER_SLIPPAGE = float(os.getenv("MAX_TRIGGER_SLIPPAGE", 150.0))

def validate_config():
    missing = []
    if not LIGHTER_PRIVATE_KEY:
        missing.append("LIGHTER_PRIVATE_KEY")
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
