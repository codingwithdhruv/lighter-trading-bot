# Lighter Pro Terminal V2.0

A high-fidelity, institutional-grade command center for the **Lighter** Orderbook DEX, featuring real-time copy trading to **Pacifica** and **Decibel**.

---

## ✨ Features

*   **🕹️ Unified Command Center**: A sleek, professional Telegram interface with a persistent bottom menu and futuristic hero header.
*   **🛰️ Real-time UI Mirroring**: Any trade you place on the Lighter Web UI is instantly detected via WebSocket and mirrored to Pacifica and Decibel with sub-second latency.
*   **💼 Portfolio Aggregator**: View your consolidated net worth across Lighter, Pacifica, and Decibel with dynamic visual distribution bars.
*   **📈 Position HUD**: Detailed Lighter position tracking with live mark prices, unrealized PnL %, and pip-based TP/SL distance visualization.
*   **🔔 Smart Alerts**: Set instant crossing alerts or professional 5-minute candle close alerts with automatic direction detection.
*   **🛡️ Institutional Risk Management**: Position sizing for copy trades is strictly based on a constant USD loss model (`MAX_LOSS_USD / SL_PIPS`).

---

## 🛠 Prerequisites

*   **Python 3.9+**
*   **Node.js 20+** (Required for the Decibel sidecar)
*   **Telegram Bot Token** (from @BotFather)
*   **Exchange Credentials**: Lighter Private Key, Pacifica Agent Key, and Decibel API Wallet.

---

## 📦 Quick Start

1.  **Clone & Install**:
    ```bash
    git clone https://github.com/your-repo/lighter-trading-bot.git
    cd lighter-trading-bot
    pip install -r requirements.txt
    ```

2.  **Setup Decibel Sidecar**:
    ```bash
    cd decibel && npm install && cd ..
    ```

3.  **Configure Environment**:
    ```bash
    cp .env.example .env
    # Fill in your keys, Lighter Account Index, and allowed Telegram IDs
    ```
    *Optional:* If you don't know your Lighter Account Index, you can derive it using your Ethereum Address or L1 Private Key:
    ```bash
    python3 find_account_index.py <0x_L1_ADDRESS_OR_L1_PRIVATE_KEY>
    ```

4.  **Launch Terminal**:
    ```bash
    python3 main.py
    ```

---

## ⌨️ Command Reference

### 🚀 Trading & Signals
*   `/long` / `/short` — Fetch interactive signal templates.
*   `BTC > 70000 LONG SIZE: 2` — Paste signals directly; bot waits for 5m candle close.
*   `/tp <price/pips>` — Update Take Profit for active Lighter position.
*   `/sl <price/pips>` — Update Stop Loss for active Lighter position.
*   `/close <asset>` — Market close position on Lighter.

### 📊 Dashboards & Analytics
*   `/balance` — Unified portfolio overview with visual equity distribution.
*   `/positions` — Professional Position HUD with live PnL and order detection.
*   `/alert <price>` — Instant price crossing notification.
*   `/closingalert <price>` — Smart 5m candle close alert (Auto-detects above/below).

### ⚙️ System Control
*   `/settings` — Toggle copy trading for Pacifica/Decibel and update risk parameters.
*   `/help` — Interactive command guide.
*   `/stopall` — Emergency stop for all active signals and alerts.

---

## 📝 Configuration Tips
*   **Decibel**: Collateral must be deposited into a **Trading Account** (subaccount) created on Decibel. Ensure `DECIBEL_SUBACCOUNT` matches your trading address.
*   **Security**: Always populate `ALLOWED_TELEGRAM_USER_IDS` to prevent unauthorized access to your funds.
*   **Slippage**: Adjust `MAX_TRIGGER_SLIPPAGE` in `.env` to define your tolerance for candle close "wicks" relative to your signal trigger.

---

## ⚠️ Disclaimer
Trading perpetual futures carries high risk. This software is provided "as is". Always test with small sizes before committing significant capital.
