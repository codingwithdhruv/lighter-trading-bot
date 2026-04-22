import time
import json
import requests
from utils.logger import logger
from utils.config import PACIFICA_API_KEY, PACIFICA_SUBACCOUNT

try:
    import base58
    import nacl.signing
except ImportError:
    logger.warning("base58 or PyNaCl not found. Pacifica interactions will fail.")

PACIFICA_REST_URL = "https://api.pacifica.fi"

def sort_json_keys(value):
    if isinstance(value, dict):
        sorted_dict = {}
        for key in sorted(value.keys()):
            sorted_dict[key] = sort_json_keys(value[key])
        return sorted_dict
    elif isinstance(value, list):
        return [sort_json_keys(item) for item in value]
    else:
        return value

class PacificaClient:
    def __init__(self):
        self.private_key_b58 = PACIFICA_API_KEY
        self.signing_key = None
        self.public_key_b58 = None
        self.account = PACIFICA_SUBACCOUNT
        
        if self.private_key_b58:
            try:
                decoded_key = base58.b58decode(self.private_key_b58)
                seed = decoded_key[:32]
                self.signing_key = nacl.signing.SigningKey(seed)
                pub_key = self.signing_key.verify_key.encode()
                self.public_key_b58 = base58.b58encode(pub_key).decode('ascii')
            except Exception as e:
                logger.error(f"Invalid Pacifica Private Key: {e}")

    def sign_payload(self, operation_type: str, operation_data: dict) -> dict:
        if not self.signing_key:
            return operation_data
            
        timestamp = int(time.time() * 1000)
        expiry_window = 5000
        target_account = self.account if self.account else self.public_key_b58
        agent_wallet = self.public_key_b58 if self.account and self.account != self.public_key_b58 else None

        data_to_sign = {
            "type": operation_type,
            "timestamp": timestamp,
            "expiry_window": expiry_window,
            "data": operation_data
        }
        
        sorted_message = sort_json_keys(data_to_sign)
        compact_json = json.dumps(sorted_message, separators=(',', ':'))
        message_bytes = compact_json.encode('utf-8')
        
        sig = self.signing_key.sign(message_bytes).signature
        signature_b58 = base58.b58encode(sig).decode('ascii')
        
        final_request = {
            "account": target_account,
            "agent_wallet": agent_wallet,
            "signature": signature_b58,
            "timestamp": timestamp,
            "expiry_window": expiry_window,
            **operation_data
        }
        return final_request

    def get_market_info(self, symbol: str) -> dict:
        try:
            resp = requests.get(f"{PACIFICA_REST_URL}/api/v1/info")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    for m in data.get("data", []):
                        if m["symbol"].upper() == symbol.upper():
                            return m
        except Exception as e:
            logger.error(f"Pacifica Fetch Market Info Error: {e}")
        return None

    def get_price(self, symbol: str) -> float:
        try:
            resp = requests.get(f"{PACIFICA_REST_URL}/api/v1/info/prices")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    for m in data.get("data", []):
                        if m["symbol"].upper() == symbol.upper():
                            return float(m.get("oracle", m.get("mark", 0)))
        except Exception as e:
            logger.error(f"Pacifica Fetch Price Error: {e}")
        return 0.0

    def get_subaccount_balance(self) -> float:
        if not self.signing_key:
            return 0.0
            
        url = f"{PACIFICA_REST_URL}/api/v1/account/subaccount/list"
        signed_payload = self.sign_payload("list_subaccounts", {})
        
        try:
            resp = requests.post(url, json=signed_payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    subs = data.get("data", {}).get("subaccounts", [])
                    for sub in subs:
                        if self.account and sub.get("address") == self.account:
                            return float(sub.get("balance", "0"))
                    if subs:
                        return float(subs[0].get("balance", "0"))
            else:
                logger.error(f"Failed to fetch Pacifica subaccounts: {resp.text}")
        except Exception as e:
            logger.error(f"Error fetching Pacifica subaccounts: {e}")
        return 0.0

pacifica_client = PacificaClient()
