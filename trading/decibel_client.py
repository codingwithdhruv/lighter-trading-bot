import hashlib
import time
from bot.parser import TradeSignal
from utils.logger import logger
from utils.config import DECIBEL_PRIVATE_KEY, DECIBEL_RPC_URL, DECIBEL_LEVERAGE, DECIBEL_ALLOCATION_USDC, DECIBEL_API_KEY
import requests

try:
    from aptos_sdk.account import Account
    from aptos_sdk.async_client import RestClient
    from aptos_sdk.account_address import AccountAddress
    from aptos_sdk.bcs import Serializer
    from aptos_sdk.transactions import EntryFunction, TransactionArgument, TransactionPayload
except ImportError:
    logger.warning("aptos_sdk not found or incompatible. Decibel trades will silently fail until the correct aptos-sdk is installed.")
    class TransactionArgument:
        def __init__(self, *args, **kwargs): pass
    class Serializer:
        def __init__(self, *args, **kwargs): pass


# Constants as defined in TS SDK / Config
PACKAGE_ADDRESS = "0x50ead22afd6ffd9769e3b3d6e0e64a2a350d68e8b102c4e72e33d0b8cfdfdb06"  # Mainnet
DECIBEL_REST_URL = "https://api.mainnet.aptoslabs.com/decibel"  # Hardcoded mainnet API for fetching markets


def _serialize_option_bytes(value=None, type_str="u64") -> bytes:
    """
    Manually serializes an Option<T> precisely as requested by the Aptos VM
    Option in Aptos BCS:
      - None: 0x00
      - Some: 0x01 + T bytes
    """
    ser = Serializer()
    if value is None:
        ser.bool(False)
    else:
        ser.bool(True)
        if type_str == "u64":
            ser.u64(int(value))
        elif type_str == "address":
            ser.struct(AccountAddress.from_hex(value))
    return ser.output()


def _serialize_option_string(value=None) -> bytes:
    """
    Serializes an Option<String> for Aptos BCS.
    """
    ser = Serializer()
    if value is None:
        ser.bool(False)
    else:
        ser.bool(True)
        ser.str(str(value))
    return ser.output()

class RawBytesArgument(TransactionArgument):
    """
    Bypasses standard typed serialization to strictly pass our Option serialization bytes.
    """
    def __init__(self, raw_bytes: bytes):
        super().__init__(raw_bytes, None)
        self.raw_bytes = raw_bytes
        
    def serialize(self, serializer: Serializer):
        serializer.fixed_bytes(self.raw_bytes)


class DecibelClient:
    def __init__(self):
        self.private_key = DECIBEL_PRIVATE_KEY
        self.api_key = DECIBEL_API_KEY
        self.rpc_url = DECIBEL_RPC_URL
        self.subaccount = None
        if self.private_key:
            try:
                # Sanitize: Remove ed25519-priv- prefix, 0x prefix, whitespace/quotes
                clean_key = str(self.private_key).strip().strip('"').strip("'")
                # Handle AIP-80 format: ed25519-priv-0x...
                if clean_key.startswith("ed25519-priv-"):
                    clean_key = clean_key[len("ed25519-priv-"):]
                if clean_key.startswith("0x"):
                    clean_key = clean_key[2:]
                
                self.account = Account.load_key(clean_key)
                # Note: RestClient might fail if aptos_sdk isn't fully imported
                if globals().get('RestClient'):
                    self.client = RestClient(self.rpc_url)
                else:
                    self.client = None
            except Exception as e:
                logger.error(f"Invalid Decibel Private Key configuration: {e}")
                self.account = None
                self.client = None
        else:
            self.account = None

        self.markets_cache = {}

    def _get_headers(self) -> dict:
        headers = {"Origin": "https://app.decibel.trade"}
        if self.api_key:
            # Sanitize API key: strip 0x prefix if present
            key = str(self.api_key).strip()
            if key.startswith("0x"):
                key = key[2:]
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _fetch_subaccount(self):
        if not self.account:
            logger.warning("Decibel _fetch_subaccount skipped: No account initialized (check private key).")
            return

        wallet_addr = str(self.account.address())
        headers = self._get_headers()
        
        # We try both the primary mainnet and the newer decibel.trade base URLs
        urls_to_try = [
            DECIBEL_REST_URL, # "https://api.mainnet.aptoslabs.com/decibel"
            "https://api.decibel.trade/decibel"
        ]

        logger.info(f"Resolving Decibel subaccounts for wallet: {wallet_addr}")
        
        for base_url in urls_to_try:
            try:
                url = f"{base_url}/api/v1/subaccounts"
                resp = requests.get(
                    url,
                    params={"owner": wallet_addr},
                    headers=headers,
                    timeout=10
                )
                
                if resp.status_code == 200:
                    data = resp.json()
                    logger.debug(f"Decibel subaccounts raw response from {base_url}: {data}")
                    
                    if data and len(data) > 0:
                        # Success case
                        first = data[0]
                        if isinstance(first, dict):
                            self.subaccount = first.get("subaccount_address", first.get("address"))
                        else:
                            self.subaccount = first
                        
                        if self.subaccount:
                            logger.info(f"Decibel active subaccount loaded: {self.subaccount} (via {base_url})")
                            return # Exit early on success
                    else:
                        logger.warning(f"Decibel API {base_url} returned empty subaccount list `[]` for owner {wallet_addr}. (Help: Create a Trading Account on https://app.decibel.trade)")
                else:
                    logger.error(f"Decibel API {base_url} error {resp.status_code}: {resp.text}")
                    
            except Exception as e:
                logger.error(f"Decibel _fetch_subaccount error for {base_url}: {e}")

        if not self.subaccount:
            logger.error(f"Decibel subaccount resolution failed for {wallet_addr}. CRITICAL: You must create a subaccount on app.decibel.trade first.")

    def _fetch_markets(self):
        # Fetch markets from public indexer mapping
        resp = requests.get(f"{DECIBEL_REST_URL}/api/v1/markets", headers=self._get_headers())
        if resp.status_code == 200:
            for m in resp.json():
                name = m.get("market_name")
                self.markets_cache[name] = m

    def _fetch_price(self, market_addr: str) -> float:
        """Fetches the current mark price for a specific market address."""
        try:
            resp = requests.get(f"{DECIBEL_REST_URL}/api/v1/prices", params={"market": market_addr}, headers=self._get_headers())
            if resp.status_code == 200:
                data = resp.json()
                if data and len(data) > 0:
                    return float(data[0].get("mark_px", 0))
        except Exception as e:
            logger.error(f"Decibel _fetch_price error: {e}")
        return 0.0

    async def execute_trade(self, signal: TradeSignal, base_entry_price: float):
        if not self.account:
            logger.error("Decibel private key missing. Cannot execute copy trade.")
            return False

        if not self.subaccount:
            self._fetch_subaccount()
            if not self.subaccount:
                logger.error("Decibel subaccount resolution failed. Ensure API Key and Wallet are set correctly.")
                return False

        if not self.markets_cache:
            self._fetch_markets()

        # Decibel usually expects standard symbol strings ex: BTC/USD
        asset = signal.asset.upper()
        if asset.endswith("USDC"):
            asset = asset[:-4]
        market_name = f"{asset}/USD"

        market = self.markets_cache.get(market_name)
        if not market:
            logger.error(f"Decibel Market not found for {market_name}")
            return False

        try:
            # 1. Math Out Chain Units for Execution
            # Fetch current mark price for the most accurate calculation if using pips
            mark_price = self._fetch_price(market["market_addr"])
            anchor_price = mark_price if mark_price > 0 else base_entry_price

            # Size in Asset = (AllocationUSD * Leverage) / anchor_price
            notional = DECIBEL_ALLOCATION_USDC * DECIBEL_LEVERAGE
            human_size = notional / anchor_price
            
            # Format according to Decibel specs
            sz_dec = market["sz_decimals"]
            px_dec = market["px_decimals"]
            human_lot = market["lot_size"] / (10 ** sz_dec)
            human_tick = market["tick_size"] / (10 ** px_dec)
            human_min = market["min_size"] / (10 ** sz_dec)

            human_size = round(human_size / human_lot) * human_lot
            human_price = round(anchor_price / human_tick) * human_tick
            
            if human_size < human_min:
                logger.error(f"Decibel size {human_size} too small (min: {human_min}).")
                return False

            chain_size = int(round(human_size * (10 ** sz_dec)))
            chain_price = int(round(human_price * (10 ** px_dec)))
            
            # 2. Resolve TP/SL (pip-based vs absolute)
            is_buy = (signal.side.upper() == "LONG")
            
            # If original pip distance is available, calculate from exchange anchor
            tp_price = 0
            sl_price = 0
            
            if getattr(signal, 'tp_pips', 0) > 0:
                tp_price = anchor_price + signal.tp_pips if is_buy else anchor_price - signal.tp_pips
            elif getattr(signal, 'tp', 0) > 0:
                tp_price = signal.tp

            if getattr(signal, 'sl_pips', 0) > 0:
                sl_price = anchor_price - signal.sl_pips if is_buy else anchor_price + signal.sl_pips
            elif getattr(signal, 'sl', 0) > 0:
                sl_price = signal.sl

            chain_tp_trigger = None
            chain_tp_limit = None
            chain_sl_trigger = None
            chain_sl_limit = None

            if tp_price > 0:
                tp_rounded = round(tp_price / human_tick) * human_tick
                chain_tp_trigger = int(round(tp_rounded * (10 ** px_dec)))
                chain_tp_limit = int(round(tp_rounded * 0.999 * (10 ** px_dec))) if is_buy else int(round(tp_rounded * 1.001 * (10 ** px_dec)))

            if sl_price > 0:
                sl_rounded = round(sl_price / human_tick) * human_tick
                chain_sl_trigger = int(round(sl_rounded * (10 ** px_dec)))
                chain_sl_limit = int(round(sl_rounded * 0.999 * (10 ** px_dec))) if is_buy else int(round(sl_rounded * 1.001 * (10 ** px_dec)))

            client_order_id = f"ldr-cp-{int(time.time() * 1000)}"

            # 3. Build BCS Payload Mapping
            payload = EntryFunction.natural(
                f"{PACKAGE_ADDRESS}::dex_accounts_entry",
                "place_order_to_subaccount",
                [],
                [
                    TransactionArgument(AccountAddress.from_hex(self.subaccount), Serializer.struct),
                    TransactionArgument(AccountAddress.from_hex(market["market_addr"]), Serializer.struct),
                    TransactionArgument(chain_price, Serializer.u64),
                    TransactionArgument(chain_size, Serializer.u64),
                    TransactionArgument(is_buy, Serializer.bool),
                    TransactionArgument(2, Serializer.u8),  # IOC (2) for market-like execution
                    TransactionArgument(False, Serializer.bool),  # reduce_only
                    RawBytesArgument(_serialize_option_string(client_order_id)),  # Option<String>
                    RawBytesArgument(_serialize_option_bytes(None)),  # stop_price
                    RawBytesArgument(_serialize_option_bytes(chain_tp_trigger)),  # tp_trigger_price
                    RawBytesArgument(_serialize_option_bytes(chain_tp_limit)),  # tp_limit_price
                    RawBytesArgument(_serialize_option_bytes(chain_sl_trigger)),  # sl_trigger_price
                    RawBytesArgument(_serialize_option_bytes(chain_sl_limit)),  # sl_limit_price
                    RawBytesArgument(_serialize_option_bytes(None, "address")),  # builder_address
                    RawBytesArgument(_serialize_option_bytes(None)),  # builder_fees
                ]
            )

            # 3. Submit Transaction Standard Block
            signed_transaction = self.client.create_bcs_signed_transaction(
                self.account, TransactionPayload(payload)
            )
            
            tx_hash = self.client.submit_bcs_transaction(signed_transaction)
            logger.info(f"Decibel Trade Dispatched! Hash: {tx_hash}")
            
            # Fire and forget; we don't block wait 
            # self.client.wait_for_transaction(tx_hash)
            return True

        except Exception as e:
            logger.error(f"Decibel Execution Error: {e}")
            return False
