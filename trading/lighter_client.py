import lighter
from utils.logger import logger
from utils.config import LIGHTER_API_URL, LIGHTER_ACCOUNT_INDEX, LIGHTER_PRIVATE_KEY
import websockets
from typing import Tuple, Optional

class LighterTradingClient:
    def __init__(self):
        self.api_client = None
        self.signer_client = None
    
    def initialize(self) -> Optional[Exception]:
        try:
            from utils.config import LIGHTER_API_KEY_INDEX
            private_keys = {LIGHTER_API_KEY_INDEX: LIGHTER_PRIVATE_KEY}
            
            self.api_client = lighter.ApiClient(configuration=lighter.Configuration(host=LIGHTER_API_URL))
            self.signer_client = lighter.SignerClient(
                url=LIGHTER_API_URL,
                account_index=LIGHTER_ACCOUNT_INDEX,
                api_private_keys=private_keys,
            )
            
            err = self.signer_client.check_client()
            if err is not None:
                logger.error(f"SignerClient Check Failed: {err}")
                return err
                
            logger.info("Lighter SDK Clients Initialized successfully.")
            return None
        except Exception as e:
            logger.error(f"Failed to initialize Lighter clients: {e}")
            return e

    def get_auth_token(self) -> str:
        """Generate an auth token for authenticated API calls."""
        if not self.signer_client:
            return ""
        result = self.signer_client.create_auth_token_with_expiry()
        if isinstance(result, tuple):
            return result[0]
        return result

    async def get_ws_connection(self):
        ws_url = LIGHTER_API_URL.replace("https", "wss") + "/stream"
        return await websockets.connect(ws_url)

    async def close(self):
        if self.signer_client:
            await self.signer_client.close()
        if self.api_client:
            await self.api_client.close()
            
    async def get_mark_price(self, symbol: str) -> float:
        """Fetch the current mark price for a given symbol."""
        if not self.api_client:
            return 0.0
        try:
            from lighter.api.order_api import OrderApi
            order_api = OrderApi(self.api_client)
            resp = await order_api.exchange_stats_without_preload_content()
            data = await resp.json()
            for obs in data.get('order_book_stats', []):
                if obs['symbol'].upper() == symbol.upper():
                    return float(obs['last_trade_price'])
        except Exception as e:
            logger.error(f"Error fetching mark price for {symbol}: {e}")
        return 0.0

lighter_wrapper = LighterTradingClient()

