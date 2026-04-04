# Zero-Delay Multi-Platform Trading Bot 📉⚡

A high-performance, Telegram-first automated trading terminal initially built for **Lighter Exchange**, upgraded into a synchronous, zero-delay router that executes copy trades concurrently across **Lighter, CoinDCX INR Futures, and Decibel (Aptos)**. 

The bot is entirely driven by a "Closing Body" execution engine powered by **Bybit's WebSocket**, ensuring your limit and market orders wait for confirmed 5-minute candle closes before instantly routing to multiple endpoints.

---

## 🎯 Architecture & Features

*   **⚡ Zero-Delay Execution Router**: By relying on Bybit's trigger price locally instead of waiting for primary exchange fills, the bot computes exact Take Profit and Stop Loss limits and fires API requests to Lighter, Decibel, and CoinDCX concurrently in a single async sweep.
*   **📱 Interactive Telegram UI**: Your command center. Manage complex OCO setups, view dynamic bracketed PnL, and utilize **inline buttons** to instantly Close, Set TP, or Set SL for tracked positions.
*   **⚙️ Persistent Settings Toggle**: Enable or disable copy trading for specific exchanges on-the-fly using the `/settings` menu. Choices are saved to `data/copy_settings.json` and persist across bot restarts.
*   **📡 Two-Way Sync**: Native UI polling detects manual trades placed directly on the Lighter web interface and pushes syncing updates directly to your Telegram chat with automated copy dispatch.
*   **🛡️ Dynamic Slippage Safeguards**: Customizable `MAX_TRIGGER_SLIPPAGE` automatically invalidates stale signals or heavily drifted wicks after a candle closes.
*   **📐 Pip-Based Target Resolution**: Input explicit target prices or relative "Pips" (e.g. `250p` for 250 price points). The routing engine calculates absolute prices uniformly mapping exact targets to all connected exchanges.

---

## 🌐 Supported Platforms

The bot supports completely isolated environments for each exchange. If you omit an API key for a specific platform, the bot will gracefully ignore it while maintaining operations on the remaining endpoints.

1. **Lighter (Core)**: ZK-Rollup Orderbook execution. Handled via `lighter-sdk`.
2. **CoinDCX (INR Futures)**: Executes REST-based **INR-margined** futures positions. Automates USDT/INR conversion and contract sizing.
3. **Decibel (Aptos)**: Natively implements Aptos `EntryFunction` payload logic via the Python `aptos-sdk`. Automates subaccount resolution for programmatic trading.

---

## 🛠️ Setup & Installation

### 1. Prerequisites 
Requires Python 3.9+ and pip. Node/TS is NOT required. 
```bash
# Clone the repository
git clone <repo-url>
cd lighter-trading-bot

# Set up environment variables
cp .env.example .env
```

### 2. Environment Configuration (`.env`)
Fill in the isolated credentials for your target platforms:

**Core Lighter Set**
*   `LIGHTER_PRIVATE_KEY`: Your Lighter API Account key (Not your wallet private key).
*   `TELEGRAM_BOT_TOKEN`: Acquired from `@BotFather`.
*   `ALLOWED_TELEGRAM_USER_IDS`: Comma-separated list of Telegram IDs allowed to use the bot. The bot auto-initializes notifications for the first ID in this list.

**CoinDCX Copy Trading**
*   `COINDCX_KEY` & `COINDCX_SECRET`: API credentials generated on CoinDCX.
*   `COINDCX_LEVERAGE` (Default `1`): Desired multiplier for CoinDCX active positions.
*   `COINDCX_ALLOCATION_INR` (e.g. `1000`): Fixed **INR magnitude** mapping against dynamic coin ratios per trade.

**Decibel Copy Trading**
*   `DECIBEL_PRIVATE_KEY`: Private Key of your funded Aptos Mainnet wallet.
*   `DECIBEL_API_KEY`: Client API key from [Geomi](https://geomi.dev).
*   `DECIBEL_LEVERAGE` & `DECIBEL_ALLOCATION_USDC`: Native limits for position scaling.

> [!IMPORTANT]
> **Decibel Required Action**: You MUST create a "Trading Account" (subaccount) on [app.decibel.trade](https://app.decibel.trade) and deposit USDC before the bot can resolve your trading address.

### 3. Deploy

**Using Local Python:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 main.py
```

**Using Docker:**
```bash
docker build -t lighter-bot .
docker run --env-file .env lighter-bot
```

---

## 📱 Telegram Usage Guide

### 1. Formatting Trade Signals
The bot parsing engine supports a flexible, multi-line string approach.
```text
BTC > 70000
SIDE: LONG
SIZE: 2
LEV: 40
TP: 71000
SL: 69500
```
*Note: Use `700p` for pip-based TP/SL offsets.*

### 2. Core Commands
*   `/status`: Renders active positions and Lighter account vitals with interactive control buttons.
*   `/settings`: Opens the Copy Trading configuration menu to toggle CoinDCX/Decibel.
*   `/balance`: View unified balance across platforms.
*   `/close <asset>`: Instantly close all platform risk for a specific ticker.
*   `/tp <limit> <asset>` / `/sl <limit> <asset>`: Push offset or absolute targets to active trades.

### 3. Custom Action Alerts
*   `/alert <price> [message]`: Immediate price sweep notification.
*   `/closingalert <above|below> <price> [message]`: 5m candle close specific notification.

---

*Built for precision algorithmic multi-routing.*
