# Lighter Multi-Exchange Copy Trading Bot

A production-grade trading bot that synchronizes trades from **Lighter** to **Pacifica** and **Decibel**.

## 🚀 Features
*   **Real-time UI Mirroring**: Any trade you place manually on Lighter is instantly copied to Pacifica and Decibel.
*   **Smart Signal Detection**: Executes trades based on 5-minute candle closes on Bybit SPOT.
*   **Risk-Based Position Sizing**: Automatically calculates position size based on your desired maximum USD loss and the Stop Loss distance.
*   **Telegram Command Center**: Control your positions, set TP/SL, check balances, and toggle copy trading via a sleek Telegram interface.
*   **Multi-Platform Concurrency**: Dispatches orders to all exchanges simultaneously for minimum latency.

## 🛠 Prerequisites
*   **Python 3.9+**
*   **Node.js 20+** (Required for Decibel sidecar)
*   **Telegram Bot Token** (from @BotFather)

## 📦 Installation

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/your-repo/lighter-trading-bot.git
    cd lighter-trading-bot
    ```

2.  **Install Python dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Install Decibel Sidecar dependencies**:
    ```bash
    cd decibel
    npm install
    cd ..
    ```

4.  **Configure environment**:
    ```bash
    cp .env.example .env
    # Edit .env with your credentials
    ```

## 🤖 Usage

### Running Locally
```bash
python3 main.py
```

### Docker Deployment
```bash
docker build -t lighter-bot .
docker run --env-file .env lighter-bot
```

## ⌨️ Telegram Commands
*   `/start` - Initialize the bot and show main menu.
*   `/long` / `/short` - Get signal templates.
*   `/tp <price/pips>` - Set Take Profit for the active position.
*   `/sl <price/pips>` - Set Stop Loss for the active position.
*   `/close <asset>` - Close a position at market price on Lighter.
*   `/balance` - View balances across all connected platforms.
*   `/settings` - Toggle Pacifica/Decibel copy trading and update risk settings.

## 📝 Configuration Note
*   **Decibel**: You **must** create a Trading Account (subaccount) on [app.decibel.trade](https://app.decibel.trade) before using the bot. Collateral must be deposited into that subaccount.
*   **Pacifica**: You can trade from your main account. Simply deposit USDC to your wallet on [pacifica.fi](https://pacifica.fi).
*   **Lighter**: Ensure your account index and private key are correct.

## ⚠️ Risk Warning
Trading perpetual futures involves significant risk of loss. Ensure you test your configuration with small amounts before scaling.
