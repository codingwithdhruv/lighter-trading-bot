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
        
        GET /api/v1/prices returns a list of objects with 'market' (address),
        'mark_px', 'oracle_px', 'mid_px'. We fetch all and match by address.
        """
        try:
            if not self._market_cache:
                self.get_markets()
            
            market_config = self._market_cache.get(symbol, {})
            target_addr = market_config.get("market_addr", "")
            
            resp = requests.get(
                f"{DECIBEL_REST_BASE}/api/v1/prices",
                headers=self._headers(),
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    for item in data:
                        if target_addr and item.get("market") == target_addr:
                            return float(item.get("mark_px", 0))
                    # If no match by address, try first item if only one market
                    if len(data) == 1:
                        return float(data[0].get("mark_px", 0))
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

    def get_positions(self, symbol: str = None) -> list:
        """Fetch open positions from /api/v1/account_positions.
        
        Params: account (subaccount address), market_address (optional filter).
        Response items have 'market_address', 'open_size', 'avg_entry_px', etc.
        """
        if not self.subaccount: return []
        if not self._market_cache: self.get_markets()
        try:
            params = {"account": self.subaccount}
            
            # If filtering by symbol, resolve to market_address
            if symbol:
                market_addr = ""
                for name, cfg in self._market_cache.items():
                    if name.upper() == symbol.upper() or name.upper() == symbol.upper().replace("/", "-"):
                        market_addr = cfg.get("market_addr", "")
                        break
                if market_addr:
                    params["market_address"] = market_addr
            
            resp = requests.get(
                f"{DECIBEL_REST_BASE}/api/v1/account_positions",
                headers=self._headers(),
                params=params,
                timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
                # Some endpoints wrap in {"positions": [...]}
                if isinstance(data, dict):
                    return data.get("positions", [])
        except Exception as e:
            logger.error(f"Decibel get_positions error: {e}")
        return []

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
                          sl_trigger: int = None, sl_limit: int = None,
                          time_in_force: int = None, is_reduce_only: bool = False) -> dict:
        """Place an order via the sidecar."""
        args = {
            "marketName": market_name,
            "price": price,
            "size": size,
            "isBuy": is_buy,
            "isReduceOnly": is_reduce_only
        }
        if time_in_force is not None:
            args["timeInForce"] = time_in_force
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

    async def cancel_order(self, order_id: str) -> bool:
        result = await self._call_sidecar("cancel_order", {"orderId": order_id})
        return result.get("success", False)

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
