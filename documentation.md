# Lighter Telegram Bot: Technical Documentation

## 🏗️ Architecture Overview

The bot is designed as a high-reliability, asynchronous trading engine centered around **Telegram** as the primary interface. It eliminates the need for a web frontend by providing interactive controls directly via Telegram's inline keyboards.

### Core Components:
1.  **Market Listener (`data/market_listener.py`)**:
    -   Connects to **Bybit V5 WebSocket** for real-time BTC/USDT spot prices.
    -   Implements **'Closing Body' logic**: Monitors 5m candles and executes trades ONLY when the candle officially closes (`confirm: true`).
    -   **Price Safeguard**: Dynamically checks if the closing price has jumped too far (defined by `MAX_TRIGGER_SLIPPAGE`) from the target.
2.  **Trade Execution (`trading/execution.py`)**:
    -   Routes orders to **Lighter Exchange** via the official SDK (`SignerClient`).
    -   Ensures **Isolated Margin Mode** for every trade.
    -   Automatically places **TP (Take Profit)** and **SL (Stop Loss)** as grouped OCO orders immediately after a market fill.
    -   Supports **pip-based TP/SL**: Auto-resolves `250p` to absolute prices using the trigger price as entry.
3.  **Risk Manager (`trading/risk_manager.py`)**:
    -   Places TP/SL OCO orders via `create_grouped_orders` (ClientOrderIndex=0).
    -   Standalone TP/SL placement for post-entry management.
    -   Market close functionality for quick position exit.
4.  **Telegram Handler (`bot/telegram_handler.py`)**:
    -   Provides interactive **Position Cards** with Refresh and Close buttons.
    -   Manages **Position History** by grouping trades and calculating realized PnL.
    -   Detects TP/SL from active Lighter orders for dynamic PnL estimation display.

## 🚀 Commands

| Command | Description |
|---------|-------------|
| `/long` `/short` | Signal templates (with pip examples) |
| `/tp 71000` or `/tp 500p` | Set take profit (price or pips) |
| `/sl 69000` or `/sl 250p` | Set stop loss (price or pips) |
| `/close [asset]` | Close position at market |
| `/alert 87000 msg` | Price crossing alert |
| `/closingalert above 87000` | 5m candle close alert |
| `/balance` | Account balance |
| `/status` | Active signals |
| `/help` | Full guide |

## 📝 Signal Format

```
BTC > 70000
SIDE: LONG
SIZE: 2
LEV: 40
TP: 71000     (or TP: 500p for pips)
SL: 69500     (or SL: 250p for pips)
```

**Pips**: `250p` = 250 price points from entry.
- LONG: TP = entry + 250, SL = entry - 250
- SHORT: TP = entry - 250, SL = entry + 250

## 🛠️ Tech Stack

-   **Language**: Python 3.9+
-   **Infrastructure**: Docker (Containerized for 24/7 uptime)
-   **Exchange Connectivity**: Lighter SDK (SignerClient)
-   **Market Data**: Bybit V5 Websocket
-   **Asynchrony**: `asyncio` for non-blocking I/O

## 🔒 Safety & Safeguards

-   **Trigger Slippage**: Prevents entries during extreme volatility.
-   **Isolated Margin**: Limits risk to the specific position's collateral.
-   **Auto-Cleanup**: Expired signals are purged from memory.
-   **TP/SL Validation**: Validates TP above entry (LONG) / below entry (SHORT) before placement.
