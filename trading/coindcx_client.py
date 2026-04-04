import hmac
import hashlib
import json
import time
import aiohttp
from bot.parser import TradeSignal
from utils.logger import logger
from utils.config import COINDCX_KEY, COINDCX_SECRET, COINDCX_LEVERAGE, COINDCX_ALLOCATION_INR

class CoinDCXClient:
    def __init__(self):
        self.api_key = COINDCX_KEY
        self.secret = COINDCX_SECRET
        self.base_url = "https://api.coindcx.com"
        
        if self.secret:
            self.secret_bytes = bytes(self.secret, encoding='utf-8')

    async def _fetch_inr_balance(self) -> float:
        """Fetches the available INR balance for the authenticated user."""
        try:
            timestamp = int(round(time.time() * 1000))
            body = {"timestamp": timestamp}
            json_body = json.dumps(body, separators=(',', ':'))
            signature = hmac.new(self.secret_bytes, json_body.encode(), hashlib.sha256).hexdigest()

            headers = {
                'Content-Type': 'application/json',
                'X-AUTH-APIKEY': self.api_key,
                'X-AUTH-SIGNATURE': signature
            }

            url = f"{self.base_url}/exchange/v1/derivatives/futures/wallets"
            async with aiohttp.ClientSession() as session:
                # Note: CoinDCX expects GET for this endpoint despite the signed JSON body
                async with session.get(url, data=json_body, headers=headers) as response:
                    if response.status == 200:
                        balances = await response.json()
                        for b in balances:
                            # The futures wallets use 'currency_short_name' instead of 'currency'
                            if b.get("currency_short_name", b.get("currency")) == "INR":
                                bal = float(b.get("balance", 0))
                                locked = float(b.get("locked_balance", 0))
                                logger.info(f"CoinDCX INR Futures Balance: ₹{bal:.2f} (locked: ₹{locked:.2f})")
                                return bal
                    else:
                        _txt = await response.text()
                        logger.error(f"CoinDCX balance fetch failed (Status {response.status}): {_txt}")
        except Exception as e:
            logger.error(f"CoinDCX _fetch_inr_balance error: {e}")
        return 0.0

    async def _fetch_usdt_inr_rate(self) -> float:
        """Fetches the current USDTINR spot rate from CoinDCX public ticker."""
        try:
            url = f"{self.base_url}/exchange/ticker"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        for item in data:
                            if item.get("market") == "USDTINR":
                                rate = float(item.get("last_price", 0))
                                logger.info(f"CoinDCX USDT/INR rate: ₹{rate:.2f}")
                                return rate
        except Exception as e:
            logger.error(f"CoinDCX _fetch_usdt_inr_rate error: {e}")
        return 0.0

    async def _fetch_price(self, pair: str) -> float:
        """Fetches the latest price for a given pair from the public ticker."""
        try:
            url = f"{self.base_url}/exchange/ticker"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        for item in data:
                            if item.get("market") == pair:
                                return float(item.get("last_price", 0))
        except Exception as e:
            logger.error(f"CoinDCX _fetch_price error: {e}")
        return 0.0

    async def _fetch_instrument_details(self, pair: str, margin_currency: str = "INR") -> dict:
        """Fetches detailed instrument metadata including precision and limits."""
        try:
            url = f"{self.base_url}/exchange/v1/derivatives/futures/data/instrument"
            params = {"pair": pair, "margin_currency_short_name": margin_currency}
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        raw_data = await response.json()
                        return raw_data.get("instrument", {})
                    else:
                        _txt = await response.text()
                        logger.warning(f"CoinDCX instrument fetch failed for {pair}: {_txt}")
        except Exception as e:
            logger.error(f"CoinDCX _fetch_instrument_details error: {e}")
        return {}

    async def execute_trade(self, signal: TradeSignal, base_entry_price: float):
        """
        Executes an INR-margined futures market order on CoinDCX with embedded TP/SL.
        Allocation is specified in INR. The bot dynamically converts to USDT-equivalent
        trade sizing using the live USDTINR spot rate.
        """
        if not self.api_key or not self.secret:
            logger.error("CoinDCX API keys missing. Cannot execute copy trade.")
            return False
            
        try:
            timestamp = int(round(time.time() * 1000))
            
            # Map symbol (e.g. BTC -> B-BTC_USDT)
            asset = signal.asset.upper()
            if asset.endswith("USDC") or asset.endswith("USDT"):
                asset = asset[:-4]
            pair = f"B-{asset}_USDT"
            
            # Fetch current exchange price for accurate pip/size calculation
            exchange_price = await self._fetch_price(pair)
            anchor_price = exchange_price if exchange_price > 0 else base_entry_price
            
            # Fetch INR balance and USDT/INR rate for sizing
            available_inr = await self._fetch_inr_balance()
            usdt_inr_rate = await self._fetch_usdt_inr_rate()

            if usdt_inr_rate <= 0:
                logger.error("CoinDCX Trade skipped. Could not fetch USDTINR rate.")
                return False

            # Determine INR allocation: min of configured vs available (with 1% fee buffer)
            target_inr = min(COINDCX_ALLOCATION_INR, available_inr * 0.99)
            
            if target_inr <= 0:
                logger.error(f"CoinDCX Trade skipped. Insufficient INR balance: ₹{available_inr:.2f}")
                return False
            
            # Convert INR allocation to USDT-equivalent for quantity calculation
            allocation_usdt_equiv = target_inr / usdt_inr_rate
            notional = allocation_usdt_equiv * COINDCX_LEVERAGE
            
            # Fetch instrument details for proper rounding and limits
            inst = await self._fetch_instrument_details(pair, "INR")
            # Default to very small if fetch fails
            qty_inc = float(inst.get("quantity_increment", 0.001))
            min_qty = float(inst.get("min_quantity", 0.001))
            
            # Calculate and round quantity to the nearest valid increment
            raw_qty = notional / anchor_price
            quantity = round(raw_qty / qty_inc) * qty_inc
            
            # Formatting to avoid floating point noise (e.g. 0.0001000000000001)
            # Find decimal places from increment
            decimal_places = len(str(qty_inc).split('.')[-1]) if '.' in str(qty_inc) else 0
            quantity = float(f"{quantity:.{decimal_places}f}")

            logger.info(
                f"CoinDCX sizing: ₹{target_inr:.0f} INR → "
                f"${allocation_usdt_equiv:.2f} USDT (rate: ₹{usdt_inr_rate:.2f}) → "
                f"Notional ${notional:.2f} @ ${anchor_price:.2f} → Raw Qty {raw_qty:.6f} → Rounded {quantity} {asset}"
            )
            
            if quantity < min_qty:
                logger.error(f"CoinDCX quantity {quantity} too small (min: {min_qty}). Skipping trade.")
                return False


            order_payload = {
                "side": "buy" if signal.side == "LONG" else "sell",
                "pair": pair,
                "order_type": "market_order",
                "total_quantity": quantity,
                "leverage": COINDCX_LEVERAGE,
                "notification": "no_notification",
                "margin_currency_short_name": "INR",
            }
            
            # Resolve TP/SL logic
            is_buy = (signal.side == "LONG")
            tp = 0
            sl = 0
            
            # Apply pips if available, otherwise use absolute price from signal
            if getattr(signal, 'tp_pips', 0) > 0:
                tp = anchor_price + signal.tp_pips if is_buy else anchor_price - signal.tp_pips
            elif getattr(signal, 'tp', 0) > 0:
                tp = signal.tp

            if getattr(signal, 'sl_pips', 0) > 0:
                sl = anchor_price - signal.sl_pips if is_buy else anchor_price + signal.sl_pips
            elif getattr(signal, 'sl', 0) > 0:
                sl = signal.sl

            if tp > 0:
                order_payload["take_profit_price"] = float(tp)
            if sl > 0:
                order_payload["stop_loss_price"] = float(sl)

            body = {
                "timestamp": timestamp,
                "order": order_payload
            }
            
            json_body = json.dumps(body, separators=(',', ':'))
            signature = hmac.new(self.secret_bytes, json_body.encode(), hashlib.sha256).hexdigest()

            headers = {
                'Content-Type': 'application/json',
                'X-AUTH-APIKEY': self.api_key,
                'X-AUTH-SIGNATURE': signature
            }

            url = f"{self.base_url}/exchange/v1/derivatives/futures/orders/create"
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=json_body, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"CoinDCX Trade Executed: {data}")
                        return True
                    else:
                        _txt = await response.text()
                        logger.error(f"CoinDCX Trade Failed (Status {response.status}): {_txt}")
                        return False

        except Exception as e:
            logger.error(f"Failed to execute CoinDCX trade: {e}")
            return False
