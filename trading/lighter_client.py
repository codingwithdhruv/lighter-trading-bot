import lighter
from utils.logger import logger
from utils.config import LIGHTER_API_URL, LIGHTER_ACCOUNT_INDEX, LIGHTER_PRIVATE_KEY
import websockets
from typing import Tuple, Optional

class LighterTradingClient:
    def __init__(self, name="Main"):
        self.api_client = None
        self.signer_client = None
        self.name = name
    
    def initialize(self, api_url=None, account_index=None, api_key_index=None, private_key=None) -> Optional[Exception]:
        try:
            from utils.config import LIGHTER_API_URL, LIGHTER_ACCOUNT_INDEX, LIGHTER_API_KEY_INDEX, LIGHTER_PRIVATE_KEY
            
            url = api_url or LIGHTER_API_URL
            acc_idx = account_index if account_index is not None else LIGHTER_ACCOUNT_INDEX
            key_idx = api_key_index if api_key_index is not None else LIGHTER_API_KEY_INDEX
            priv_key = private_key or LIGHTER_PRIVATE_KEY

            if priv_key and priv_key.startswith("0x"):
                priv_key = priv_key[2:]

            self.api_client = lighter.ApiClient(configuration=lighter.Configuration(host=url))
            
            # Derive account index if not provided
            if acc_idx is None and priv_key:
                logger.info(f"Lighter Client ({self.name}): Deriving account index...")
                acc_idx = asyncio.run(self._derive_account_index(priv_key))
                if acc_idx is None:
                    return Exception("Failed to derive account index")
                logger.info(f"Lighter Client ({self.name}): Derived account index: {acc_idx}")

            private_keys = {key_idx: priv_key}
            self.signer_client = lighter.SignerClient(
                url=url,
                account_index=acc_idx,
                api_private_keys=private_keys,
            )
            
            err = self.signer_client.check_client()
            if err is not None:
                logger.error(f"Lighter Client ({self.name}) SignerClient Check Failed: {err}")
                return Exception(err)
                
            logger.info(f"Lighter Client ({self.name}) Initialized successfully.")
            return None
        except Exception as e:
            logger.error(f"Failed to initialize Lighter Client ({self.name}): {e}")
            return e

    async def _derive_account_index(self, private_key: str) -> Optional[int]:
        try:
            from eth_account import Account
            address = Account.from_key(private_key).address
            account_api = lighter.AccountApi(self.api_client)
            resp = await account_api.accounts_by_l1_address(l1_address=address)
            if resp.accounts:
                # Return the first account found for this address
                return int(resp.accounts[0].index)
        except Exception as e:
            logger.error(f"Error deriving account index for address {address if 'address' in locals() else 'unknown'}: {e}")
        return None

    def get_auth_token(self) -> str:
        """Generate an auth token for authenticated API calls."""
        if not self.signer_client:
            return ""
        result = self.signer_client.create_auth_token_with_expiry()
        if isinstance(result, tuple):
            return result[0]
        return result

    async def get_ws_connection(self):
        from utils.config import LIGHTER_API_URL
        ws_url = LIGHTER_API_URL.replace("https", "wss") + "/stream"
        return await websockets.connect(ws_url)

    async def close(self):
        if self.signer_client:
            # SignerClient in SDK might not be async or have close()
            try:
                if asyncio.iscoroutinefunction(self.signer_client.close):
                    await self.signer_client.close()
                else:
                    self.signer_client.close()
            except: pass
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

    async def get_account_info(self):
        """Fetch full account details for the current account index."""
        if not self.api_client:
            raise Exception("Client not initialized")
        from lighter.api.account_api import AccountApi
        account_api = AccountApi(self.api_client)
        # Search for this account index
        resp = await account_api.account(by="index", value=str(self.signer_client.account_index))
        if resp.accounts:
            return resp.accounts[0]
        raise Exception(f"Account {self.signer_client.account_index} not found")

lighter_wrapper = LighterTradingClient(name="Main")
lighter_copy_wrapper = LighterTradingClient(name="Copy")

