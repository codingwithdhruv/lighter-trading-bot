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
ALLOWED_TELEGRAM_USER_IDS = [
    int(x.strip()) for x in os.getenv("ALLOWED_TELEGRAM_USER_IDS", "").split(",") if x.strip()
]

# Internal Config
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", 15)) # frequency to check candlesticks/orders
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
MAX_TRIGGER_SLIPPAGE = float(os.getenv("MAX_TRIGGER_SLIPPAGE", 150.0))
BYBIT_WS_URL = os.getenv("BYBIT_WS_URL", "wss://stream.bybit.com/v5/public/spot")

def validate_config():
    missing = []
    if not LIGHTER_PRIVATE_KEY:
        missing.append("LIGHTER_PRIVATE_KEY")
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

# --- Copy Trading (Pacifica) ---
PACIFICA_API_KEY = os.getenv("PACIFICA_API_KEY")
PACIFICA_SUBACCOUNT = os.getenv("PACIFICA_SUBACCOUNT")

# --- Copy Trading (Decibel) ---
DECIBEL_PRIVATE_KEY = os.getenv("DECIBEL_PRIVATE_KEY")
DECIBEL_NODE_API_KEY = os.getenv("DECIBEL_NODE_API_KEY")
DECIBEL_SUBACCOUNT = os.getenv("DECIBEL_SUBACCOUNT")
DECIBEL_GAS_STATION_KEY = os.getenv("DECIBEL_GAS_STATION_KEY")

# --- Copy Trading (Lighter) ---
COPY_LIGHTER_PRIVATE_KEY = os.getenv("Copy_LIGHTER_PRIVATE_KEY")
COPY_LIGHTER_API_KEY_INDEX = int(os.getenv("Copy_LIGHTER_API_KEY_INDEX", 5))
# Will be derived if not set
COPY_LIGHTER_ACCOUNT_INDEX = os.getenv("Copy_LIGHTER_ACCOUNT_INDEX")
if COPY_LIGHTER_ACCOUNT_INDEX and COPY_LIGHTER_ACCOUNT_INDEX != "?/":
    COPY_LIGHTER_ACCOUNT_INDEX = int(COPY_LIGHTER_ACCOUNT_INDEX)
else:
    COPY_LIGHTER_ACCOUNT_INDEX = None
