from typing import Dict, Any, Optional
import lighter
from utils.logger import logger

class MarketRegistry:
    def __init__(self):
        self.markets = {}
        # Mapping from string symbol like "BTC" to its internal market dictionary
        self.symbol_to_market = {}

    async def initialize(self, api_client: lighter.ApiClient):
        """
        Fetches the active market configurations from Lighter API
        and caches their parameters for order execution and scaling.
        """
        try:
            order_api = lighter.OrderApi(api_client)
            obs = await order_api.order_books()
            if not obs or not obs.order_books:
                logger.error("Failed to fetch order books during MarketRegistry initialization.")
                return False

            for market in obs.order_books:
                market_id = market.market_id
                symbol = market.symbol.upper()
                
                # Derive components if it's a perpetual (usually no hyphen) 
                # or spot (like WETH-USDC). We match symbol exactly for ease.
                if symbol.endswith("-USDC"):
                    symbol = symbol.replace("-USDC", "")
                
                # Often perps might symbol as "ETH", "BTC" etc. We store as exact given.
                config = {
                    "market_id": market_id,
                    "symbol": market.symbol,
                    "search_symbol": symbol,
                    "price_decimals": getattr(market, "supported_price_decimals", 2),
                    "size_decimals": getattr(market, "supported_size_decimals", 2),
                    "quote_decimals": getattr(market, "supported_quote_decimals", 6),
                }
                
                self.markets[market_id] = config
                self.symbol_to_market[symbol] = config
                
            logger.info(f"MarketRegistry initialized with {len(self.markets)} markets.")
            return True
        except Exception as e:
            logger.error(f"MarketRegistry initialization failed: {e}")
            return False

    def get_market_config(self, symbol: str) -> Optional[Dict[str, Any]]:
        sym = symbol.upper()
        if sym in self.symbol_to_market:
            return self.symbol_to_market[sym]
            
        # Try finding partial matches e.g. "ETH" matching "ETH-USDC"
        for k, v in self.symbol_to_market.items():
            if k.startswith(sym):
                return v
        return None

    def get_market_id(self, symbol: str) -> int:
        cfg = self.get_market_config(symbol)
        if not cfg:
            raise ValueError(f"Unknown asset symbol: {symbol}")
        return cfg["market_id"]

    def get_price_scale(self, symbol: str) -> int:
        cfg = self.get_market_config(symbol)
        if not cfg:
            return 100 # Safe fallback
        return 10 ** cfg["price_decimals"]

    def get_size_scale(self, symbol: str) -> int:
        cfg = self.get_market_config(symbol)
        if not cfg:
            return 100
        return 10 ** cfg["size_decimals"]


market_registry = MarketRegistry()
