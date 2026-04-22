"""
Decibel Client — Python wrapper for REST reads + Node.js sidecar for writes.

REST reads use the Geomi Bearer token directly.
Writes are dispatched to `decibel/executor.mjs` via subprocess.
"""

import os
import json
import asyncio
import requests
from utils.logger import logger
from utils.config import DECIBEL_NODE_API_KEY, DECIBEL_SUBACCOUNT

DECIBEL_REST_BASE = "https://api.mainnet.aptoslabs.com/decibel"
SIDECAR_PATH = os.path.join(os.path.dirname(__file__), "executor.mjs")


class DecibelClient:
    def __init__(self):
        self.api_key = DECIBEL_NODE_API_KEY
        self.subaccount = DECIBEL_SUBACCOUNT
        self._market_cache = {}  # market_name -> config dict

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and os.getenv("DECIBEL_PRIVATE_KEY"))

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # ── REST Read Helpers ─────────────────────────────────────────────────

    def get_price(self, symbol: str) -> float:
        """Fetch mark price for a symbol (e.g. 'BTC-USD').
        
        The /api/v1/prices endpoint returns 'mark_px' and expects a market address 
        (not symbol) as the 'market' param. We fetch all prices and match by address.
        """
        try:
            # Ensure market cache is populated so we can map name -> address
            if not self._market_cache:
                self.get_markets()
            
            market_config = self._market_cache.get(symbol, {})
            market_addr = market_config.get("market_addr", "")
            
            params = {}
            if market_addr:
                params["market"] = market_addr
            # If no address found, fetch all and search by name
            
            resp = requests.get(
                f"{DECIBEL_REST_BASE}/api/v1/prices",
                headers=self._headers(),
                params=params,
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    if market_addr:
                        # Filtered response — just take the first
                        if len(data) > 0:
                            return float(data[0].get("mark_px", 0))
                    else:
                        # All prices returned — match by market address from cache
                        # Build reverse lookup: addr -> price
                        for item in data:
                            item_addr = item.get("market", "")
                            # Check if this address matches any cached market with our symbol
                            for name, cfg in self._market_cache.items():
                                if cfg.get("market_addr") == item_addr and name == symbol:
                                    return float(item.get("mark_px", 0))
            else:
                logger.error(f"Decibel prices HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.error(f"Decibel get_price error for {symbol}: {e}")
        return 0.0

    def get_markets(self) -> list:
        """Fetch all market configs from REST."""
        try:
            resp = requests.get(
                f"{DECIBEL_REST_BASE}/api/v1/markets",
                headers=self._headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                markets = resp.json()
                if isinstance(markets, list):
                    for m in markets:
                        name = m.get("market_name", "")
                        # Cache under both BTC-USD and BTC/USD formats
                        self._market_cache[name] = m
                        alt_name = name.replace("/", "-") if "/" in name else name.replace("-", "/")
                        self._market_cache[alt_name] = m
                    return markets
        except Exception as e:
            logger.error(f"Decibel get_markets error: {e}")
        return []

    def get_market_config(self, symbol: str) -> dict:
        """Get cached market config. Fetches if cache is empty. Tries both name formats."""
        if not self._market_cache:
            self.get_markets()
        config = self._market_cache.get(symbol, {})
        if not config:
            # Try alternate format
            alt = symbol.replace("/", "-") if "/" in symbol else symbol.replace("-", "/")
            config = self._market_cache.get(alt, {})
        return config

    def get_account_balance(self) -> float:
        """Fetch account equity/margin from REST."""
        if not self.subaccount:
            return 0.0
        try:
            resp = requests.get(
                f"{DECIBEL_REST_BASE}/api/v1/account_overviews",
                headers=self._headers(),
                params={"account": self.subaccount},
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                # Response is a list or single object; values are already in USD
                if isinstance(data, list) and len(data) > 0:
                    data = data[0]
                return float(data.get("perp_equity_balance", data.get("total_margin", 0)))
        except Exception as e:
            logger.error(f"Decibel get_account_balance error: {e}")
        return 0.0

    # ── Sidecar Write Helpers ─────────────────────────────────────────────

    async def _call_sidecar(self, command: str, args: dict = None) -> dict:
        """Invoke the Node.js executor via subprocess."""
        payload = json.dumps({"cmd": command, "args": args or {}})

        # ESM register hook resolves extensionless imports in @decibeltrade/sdk for Node 22+
        register_path = os.path.join(os.path.dirname(__file__), "esm-register.mjs")

        try:
            sidecar_dir = os.path.dirname(__file__)
            proc = await asyncio.create_subprocess_exec(
                "node", "--import", register_path, SIDECAR_PATH,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=sidecar_dir,  # Ensures node_modules resolution works
                env={**os.environ},  # Inherits DECIBEL_PRIVATE_KEY, DECIBEL_NODE_API_KEY
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=payload.encode()),
                timeout=30,
            )

            if stderr:
                stderr_text = stderr.decode().strip()
                if stderr_text:
                    logger.warning(f"Decibel sidecar stderr: {stderr_text}")

            if stdout:
                result = json.loads(stdout.decode().strip())
                # If the command failed, surface stderr in the error field
                if not result.get("success") and stderr:
                    stderr_text = stderr.decode().strip()
                    if stderr_text and not result.get("error"):
                        result["error"] = stderr_text
                return result
            else:
                return {"success": False, "error": f"No output from sidecar. stderr: {stderr.decode().strip() if stderr else 'none'}"}

        except asyncio.TimeoutError:
            logger.error("Decibel sidecar timed out (30s)")
            return {"success": False, "error": "Sidecar timeout"}
        except Exception as e:
            logger.error(f"Decibel sidecar error: {e}")
            return {"success": False, "error": str(e)}

    async def place_order(self, market_name: str, price: int, size: int,
                          is_buy: bool, tp_trigger: int = None, tp_limit: int = None,
                          sl_trigger: int = None, sl_limit: int = None) -> dict:
        """Place an order via the sidecar."""
        args = {
            "marketName": market_name,
            "price": price,
            "size": size,
            "isBuy": is_buy,
        }
        if tp_trigger is not None:
            args["tpTriggerPrice"] = tp_trigger
        if tp_limit is not None:
            args["tpLimitPrice"] = tp_limit
        if sl_trigger is not None:
            args["slTriggerPrice"] = sl_trigger
        if sl_limit is not None:
            args["slLimitPrice"] = sl_limit
        if self.subaccount:
            args["subaccountAddr"] = self.subaccount

        return await self._call_sidecar("place_order", args)

    async def place_tpsl(self, market_addr: str, tp_trigger: int = None,
                         tp_limit: int = None, tp_size: int = None,
                         sl_trigger: int = None, sl_limit: int = None,
                         sl_size: int = None) -> dict:
        """Place TP/SL for an existing position via the sidecar."""
        args = {"marketAddr": market_addr}
        if tp_trigger is not None:
            args["tpTriggerPrice"] = tp_trigger
        if tp_limit is not None:
            args["tpLimitPrice"] = tp_limit
        if tp_size is not None:
            args["tpSize"] = tp_size
        if sl_trigger is not None:
            args["slTriggerPrice"] = sl_trigger
        if sl_limit is not None:
            args["slLimitPrice"] = sl_limit
        if sl_size is not None:
            args["slSize"] = sl_size
        if self.subaccount:
            args["subaccountAddr"] = self.subaccount

        return await self._call_sidecar("place_tpsl", args)

    async def fetch_markets_via_sdk(self) -> list:
        """Fetch markets through the sidecar SDK (includes market_addr)."""
        result = await self._call_sidecar("get_markets")
        if result.get("success"):
            markets = result.get("data", [])
            for m in markets:
                self._market_cache[m.get("market_name", "")] = m
            return markets
        return []


decibel_client = DecibelClient()
