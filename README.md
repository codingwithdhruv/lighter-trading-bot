# Lighter Telegram Pro Bot 📉

A high-performance, Telegram-first trading terminal for **Lighter Exchange**. Optimized for "Closing Body" strategies and precision position tracking.

## 🎯 What's Inside?

-   **Interactive Position Cards**: Refreshable PnL, Mark Price, and TP/SL impact tracking.
-   **Closing Body Engine**: Triggers trades based on confirmed Bybit 5m candle closes.
-   **Real-time SL/TP Notifications**: Get notified the moment your orders are hit on Lighter.
-   **Position History**: Grouped trade events with accurate realized PnL reporting.
-   **Safeguards**: Dynamic trigger slippage prevention (`MAX_TRIGGER_SLIPPAGE`).

## 🛠️ Setup

1.  **Clone & Configure**:
    ```bash
    cp .env.example .env
    # Fill in your Lighter Private Key, API Key Index, and Telegram Token
    ```
2.  **Deploy**:

    **Using Docker (Recommended):**
    ```bash
    docker build -t lighter-bot .
    docker run --env-file .env lighter-bot
    ```

    **Using Local Python:**
    ```bash
    # Create and activate virtual environment
    python3 -m venv venv
    source venv/bin/activate

    # Install dependencies
    pip install -r requirements.txt

    # Run the bot
    python3 main.py
    ```

## 📱 Commands

-   `/alert <price> <msg>`: Instant price crossing alert.
-   `/closingalert above/below <price> <msg>`: Alerts on 5m candle close.
-   `/long` / `/short`: Interactive templates for Opening 'Closing Body' positions.
-   **💰 Balance**: Live USDC available balance.
-   **📜 Position History**: Grouped reports with realized PnL.

## ⚙️ Configuration (.env)

| Key | Description | Default |
|-----|-------------|---------|
| `MAX_TRIGGER_SLIPPAGE` | Max pts away from trigger for a valid close | `150` |
| `ALLOWED_TELEGRAM_USER_IDS` | Security: Restricted bot access | `Empty` |

---
*Built for precision on Lighter.*
