# Lighter Telegram Bot: Technical Documentation

## 🏗️ Architecture Overview

The bot is designed as a high-reliability, asynchronous trading engine centered around **Telegram** as the primary interface. It eliminates the need for a web frontend by providing interactive controls directly via Telegram's inline keyboards.

### Core Components:
1.  **Market Listener (`data/market_listener.py`)**:
    -   Connects to **Bybit V5 WebSocket** for real-time BTC/USDT spot prices.
    -   Implements **'Closing Body' logic**: Monitors 5m (or 1h) candles and executes trades ONLY when the candle officially closes (`confirm: true`).
    -   **Price Safeguard**: Dynamically checks if the closing price has jumped too far (defined by `MAX_TRIGGER_SLIPPAGE`) from the target, invalidating potentially risky trades.
2.  **Trade Execution (`trading/execution.py`)**:
    -   Routes orders to **Lighter Exchange** via the official SDK (`SignerClient`).
    -   Ensures **Isolated Margin Mode** for every trade.
    -   Automatically places **TP (Take Profit)** and **SL (Stop Loss)** limit orders immediately after a market fill.
3.  **Telegram Handler (`bot/telegram_handler.py`)**:
    -   Provides interactive **Position Cards** with a [Refresh] button.
    -   Manages **Position History** by grouping trades by transaction hash and calculating realized PnL.
    -   Handles real-time alerts and manual margin/leverage adjustments.

## 🚀 Key Features

-   **Closing Body Trigger**: No more 'wick' triggers. The bot waits for a confirmed candle close to validate your entry.
-   **Interactive Position Management**: Track live PnL, current mark price, and estimated profit/loss at your TP/SL levels without leaving Telegram.
-   **Automated TP/SL**: High-speed secondary order placement ensures your downside is protected immediately.
-   **Realized PnL History**: A professional history view that bundles partial fills into coherent trade events.
-   **Security**: Restricted access to specific Telegram User IDs via `.env`.

## 🛠️ Tech Stack

-   **Language**: Python 3.9+
-   **Infrastructure**: Docker (Containerized for 24/7 uptime)
-   **Exchange Connectivity**: Lighter SDK (SignerClient)
-   **Market Data**: Bybit V5 Websocket
-   **Asynchrony**: `asyncio` for non-blocking I/O across Telegram, WebSocket, and Polling.

## 🔒 Safety & Safeguards

-   **Trigger Slippage**: Prevents entries during extreme volatility where the candle closes far beyond your target.
-   **Isolated Margin**: Limits risk to the specific position's collateral.
-   **Auto-Cleanup**: Expired signals are purged from memory to prevent stale trades.
