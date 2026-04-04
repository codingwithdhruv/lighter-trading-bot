#!/usr/bin/env python3
"""
Quick diagnostic: Compare Bybit WS kline close vs Lighter mark price
to verify which data source the bot is receiving.
"""
import asyncio
import json
import websockets
import time

async def main():
    ws_url = "wss://stream.bybit.com/v5/public/linear"
    
    print(f"[{time.strftime('%H:%M:%S')}] Connecting to Bybit WS: {ws_url}")
    
    async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
        # Subscribe to BTC 5m kline
        req = {"op": "subscribe", "args": ["kline.5.BTCUSDT"]}
        await ws.send(json.dumps(req))
        print(f"[{time.strftime('%H:%M:%S')}] Subscribed to kline.5.BTCUSDT")
        
        count = 0
        while count < 30:  # Listen for ~30 messages
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            
            data = json.loads(msg)
            topic = data.get("topic", "")
            
            if topic.startswith("kline.5."):
                kline_list = data.get("data", [])
                if kline_list:
                    k = kline_list[0]
                    close = k.get("close")
                    confirm = k.get("confirm")
                    start = int(k.get("start", 0))
                    ts = k.get("timestamp")
                    
                    # Print RAW data for every update
                    candle_start = time.strftime('%H:%M:%S', time.localtime(start // 1000))
                    marker = ">>> CONFIRMED CLOSE <<<" if confirm else "(in-progress)"
                    print(f"[{time.strftime('%H:%M:%S')}] Bybit WS raw: close={close} confirm={confirm} candle_start={candle_start} {marker}")
                    
                    # Print full raw JSON on confirmed close for audit
                    if confirm:
                        print(f"  FULL RAW: {json.dumps(k, indent=2)}")
                    
                    count += 1
            elif "op" in data:
                print(f"[{time.strftime('%H:%M:%S')}] Control msg: {json.dumps(data)}")

    print("\nDone. Compare the 'close' prices above against:")
    print("  - Bybit chart: https://www.bybit.com/trade/usdt/BTCUSDT (5m timeframe)")  
    print("  - Lighter chart: https://app.lighter.xyz/ (5m timeframe)")

asyncio.run(main())
