# Lighter Trap Trading Bot 🕯️⚡

A professional, high-precision automated trading bot for the [Lighter Exchange](https://lighter.xyz/). This bot monitors 5-minute candlestick closes and executes trades based on Telegram signals with native TP/SL risk management.

## 🚀 Key Features

- **5-Min Candle Close Logic**: Executes only on confirmed candle bodies (not wicks).
- **High Precision Timing**: Aligns checks to the exact second of the candle boundary (+2s offset for API stability).
- **Native TP/SL (OCO)**: Uses Lighter's native `One-Cancels-the-Other` grouped orders for secure, atomized risk management.
- **Security Hardened**: 
  - **Telegram Whitelisting**: Restricts bot commands and signals to authorized User IDs only.
  - **SDK Workaround**: Uses raw HTTP for reliable OHLC data, bypassing known SDK model bugs.
- **Dockerized**: Simplified deployment for VPS or local environments.

## 📂 Project Structure

- `bot/`: Telegram bot handler and flexible signal parser.
- `data/`: High-precision market listener for 5-minute candle evaluation.
- `trading/`: Lighter SDK wrapper for orders, leverage, and OCO risk management.
- `utils/`: Configuration management and asynchronous logging.

## 🛠️ Setup & Installation

### 1. Prerequisites
- Python 3.9+ or Docker.
- A Lighter Account and **API Private Key** (Generate this in the Lighter UI).
- A Telegram Bot Token from [@BotFather](https://t.me/botfather).

### 2. Configuration
Copy `.env.example` to `.env` and fill in your details:
```bash
cp .env.example .env
```
**Important Security**: Set `ALLOWED_TELEGRAM_USER_IDS` to your Telegram ID (find it via @userinfobot) to prevent unauthorized access.

### 3. Run via Docker (Recommended)
```bash
docker build -t lighter-bot .
docker run --env-file .env lighter-bot
```

### 4. Run Locally
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

## 📡 Signal Format

Send messages to your bot in the following formats:

**Long Example:**
```text
BTC CLOSE ABOVE 68500
SIDE: LONG
SIZE: 2 USDC
LEVERAGE: 40x
TP: 69500
SL: 67800
```

**Short Example:**
```text
ETH CLOSE BELOW 3500
SIDE: SHORT
SIZE: 10 USDC
LEVERAGE: 20x
TP: 33500
SL: 36000
```

**Shortcut Commands:**
- `/long` or `/short`: Get a copy-paste template.
- `/status`: View active monitoring signals.
- `/balance`: Check your Lighter USDC balance.
- `/start`: Open the interactive dashboard.

## 🛡️ VPS Deployment Safety
- The bot is stateless; you can restart it anytime without losing active signals (re-send them if needed).
- Whitelisting ensures that even if your bot's username is discovered, only YOU can trigger trades.
- Use a persistent Docker container with `--restart always` for 24/7 uptime.

---
*Disclaimer: This is trading software. Use at your own risk. Past performance is not indicative of future results.*
