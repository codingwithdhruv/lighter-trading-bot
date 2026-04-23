import requests
import json
from dotenv import load_dotenv
import os
from eth_account import Account
from eth_account.messages import encode_defunct
import time

load_dotenv()
API_URL = os.getenv("LIGHTER_API_URL")
PK = os.getenv("LIGHTER_PRIVATE_KEY")
INDEX = os.getenv("LIGHTER_ACCOUNT_INDEX")

def get_auth_token():
    account = Account.from_key(PK)
    msg = f"Lighter auth token for {account.address} at {int(time.time()*1000)}"
    signed = account.sign_message(encode_defunct(text=msg))
    # Actually Lighter Auth is more complex. I will just try the public position API if there is one.
    pass

# Can't easily sign. Let's just look at the code fix!
