# Lighter Pro Terminal & Copy Trading Engine

## 1. Technical Overview
The Lighter Trading Bot is a high-performance, asynchronous trading system designed to bridge the liquidity and ease-of-use of the **Lighter** orderbook DEX with the deep perpetual markets of **Pacifica** and **Decibel**.

It provides a unified command center via Telegram, enabling institutional-grade control over multiple DEXes from a single interface.

---

## 2. Operational Modes

### 🚀 Mode A: Signal Execution (Bybit Sync)
*   **Trigger**: User inputs a signal via Telegram (e.g., `BTC > 70000 LONG SIZE: 2`).
*   **Monitoring**: The `MarketListener` connects to the Bybit SPOT WebSocket.
*   **Execution Criteria**: The bot waits for a **confirmed 5-minute candle close** that satisfies the condition.
*   **Slippage Guard**: If the close price deviates more than `MAX_TRIGGER_SLIPPAGE` from the target, the trade is aborted to prevent "trap" entries.
*   **Execution Flow**: Once triggered, a market order is placed on Lighter, followed by automated TP/SL placement.

### 🛰️ Mode B: UI Mirroring (Real-Time Copy)
*   **Trigger**: A trade is placed manually on the Lighter web interface.
*   **Monitoring**: `PositionTracker` monitors the Lighter account via WebSocket.
*   **Discovery**: Upon detecting a position increase, the bot automatically scans active orders to discover Take Profit (TP) and Stop Loss (SL) distances.
*   **Concurrency**: The `CopyEngine` dispatches the trade to Pacifica and Decibel simultaneously.
*   **Loop Protection**: Trades executed by the bot are marked and ignored by the tracker to prevent circular copying.

---

## 3. Risk Engine & Execution Pipeline

### 📉 Risk-Based Sizing
To maintain professional risk management, the bot uses a "Constant USD Loss" model for copy targets (Pacifica/Decibel).

**Formula:**
`Position Size (Base) = MAX_LOSS_USD / SL_DISTANCE_PIPS`

This ensures that regardless of entry price or leverage, a Stop Loss event always results in a predictable USD loss (e.g., exactly $20).

### 🛠 Execution Engines
1.  **Lighter (Source)**: Python-based SDK integration for high-speed market and limit orders.
2.  **Pacifica (Target)**: Direct REST API execution using Agent Keys. Supports dynamic TP/SL price resolution relative to the target's fill price.
3.  **Decibel (Target)**: Integrated via a Node.js sidecar (`executor.mjs`). Communicates with the Aptos blockchain via the Decibel SDK. Uses IOC (Immediate-or-Cancel) orders for market-like fills with slippage control.

---

## 4. Pro Terminal UX

### 💼 Portfolio Aggregator
The `/balance` command provides a consolidated dashboard fetching real-time equity from all exchanges. It features:
*   **Visual Distribution**: Horizontal progress bars showing capital weight.
*   **Unified Net Worth**: Summed USD value across all platforms.
*   **Refresh Utility**: Inline buttons to trigger a fresh multi-exchange sync.

### 🛰️ Position Tracker HUD
The `/positions` command (and persistent menu) opens a high-fidelity HUD for Lighter positions:
*   **Live PnL**: Real-time estimation using current mark prices.
*   **TP/SL Visualization**: Shows distance in pips and estimated USD profit/loss at target.
*   **Interactive Controls**: Buttons to adjust TP/SL or close at market instantly.

### 🔔 Smart Alerts
*   **Crossing Alerts**: Instant notification when a price is touched.
*   **Closing Alerts**: Professional 5m candle close alerts.
*   **Auto-Detection**: The bot automatically determines if an alert is "Above" or "Below" based on the current market price, simplifying user input.

---

## 5. Security & Reliability
1.  **Authentication**: Strict Telegram User ID filtering via `ALLOWED_TELEGRAM_USER_IDS`.
2.  **Encrypted Signing**: Lighter and Decibel keys remain local; Pacifica uses delegated Agent Keys.
3.  **Graceful Fallback**: If a copy target fails, the bot notifies the user via Telegram but continues monitoring the primary exchange.
4.  **Persistent Settings**: Risk parameters and exchange toggles are saved to `data/copy_settings.json` and persist across reloads.
