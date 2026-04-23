import asyncio
import os
from dotenv import load_dotenv
load_dotenv()
from utils.config import LIGHTER_API_URL, LIGHTER_ACCOUNT_INDEX, LIGHTER_PRIVATE_KEY
from trading.lighter_client import lighter_wrapper
import json
import websockets

async def main():
    await lighter_wrapper.initialize()
    token = lighter_wrapper.get_auth_token()
    ws_url = LIGHTER_API_URL.replace("https", "wss") + "/stream"
    print(f"Connecting to {ws_url}")
    async with websockets.connect(ws_url) as ws:
        req = {
            "type": "subscribe",
            "channels": [
                {
                    "name": "account_all_positions",
                    "account_index": int(LIGHTER_ACCOUNT_INDEX),
                    "token": token
                }
            ]
        }
        await ws.send(json.dumps(req))
        print("Sent subscribe request")
        while True:
            msg = await ws.recv()
            print(msg)
            if "positions" in msg:
                break
                
asyncio.run(main())
