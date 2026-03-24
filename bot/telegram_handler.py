import asyncio
import json
import time
from typing import Callable, Awaitable
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    CallbackQueryHandler, 
    filters, 
    ContextTypes
)

from utils.logger import logger
from utils.config import TELEGRAM_BOT_TOKEN, ALLOWED_TELEGRAM_USER_IDS
from bot.parser import parse_signal, TradeSignal
import lighter

class TelegramBotHandler:
    def __init__(self, on_signal_callback: Callable[[TradeSignal], Awaitable[None]], app_context=None):
        self.on_signal_callback = on_signal_callback
        self.app_context = app_context
        self.app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        self.chat_id = None 

        self.app.add_handler(CommandHandler("start", self._auth_wrapper(self._start_command)))
        self.app.add_handler(CommandHandler("status", self._auth_wrapper(self._status_command)))
        self.app.add_handler(CommandHandler("balance", self._auth_wrapper(self._balance_command)))
        self.app.add_handler(CommandHandler("long", self._auth_wrapper(self._long_command)))
        self.app.add_handler(CommandHandler("short", self._auth_wrapper(self._short_command)))
        self.app.add_handler(CommandHandler("help", self._auth_wrapper(self._help_command)))
        self.app.add_handler(CommandHandler("alert", self._auth_wrapper(self._alert_command)))
        self.app.add_handler(CommandHandler("closingalert", self._auth_wrapper(self._closing_alert_command)))
        self.app.add_handler(CallbackQueryHandler(self._auth_wrapper(self._button_callback)))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._auth_wrapper(self._handle_message)))

    def _auth_wrapper(self, func):
        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user_id = update.effective_user.id
            if ALLOWED_TELEGRAM_USER_IDS and user_id not in ALLOWED_TELEGRAM_USER_IDS:
                logger.warning(f"Unauthorized access attempt by user {user_id}")
                if update.message:
                    await update.message.reply_text("⛔ Unauthorized.", parse_mode='Markdown')
                elif update.callback_query:
                    await update.callback_query.answer("⛔ Unauthorized", show_alert=True)
                return
            return await func(update, context)
        return wrapped

    def _get_main_menu_keyboard(self):
        keyboard = [
            [
                InlineKeyboardButton("📸 LONG", callback_data='tpl_long'),
                InlineKeyboardButton("📉 SHORT", callback_data='tpl_short'),
            ],
            [
                InlineKeyboardButton("📈 Positions", callback_data='positions'),
                InlineKeyboardButton("💰 Balance", callback_data='balance'),
            ],
            [
                InlineKeyboardButton("📜 Trade History", callback_data='trade_history'),
                InlineKeyboardButton("🔔 Alerts", callback_data='alerts_list'),
            ],
            [
                InlineKeyboardButton("📊 Status", callback_data='status'),
                InlineKeyboardButton("🛑 Stop All", callback_data='stop_all'),
            ],
        ]
        return InlineKeyboardMarkup(keyboard)

    # --- TEMPLATES ---
    async def _long_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        tpl = "BTC > 70000\nSIDE: LONG\nSIZE: 2\nLEV: 40\nTP: 71000\nSL: 69500"
        await update.message.reply_text(f"`{tpl}`", parse_mode='Markdown')

    async def _short_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        tpl = "BTC < 68000\nSIDE: SHORT\nSIZE: 2\nLEV: 40\nTP: 67000\nSL: 68500"
        await update.message.reply_text(f"`{tpl}`", parse_mode='Markdown')

    # --- ALERT COMMANDS ---
    async def _alert_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/alert <price> <optional message> — instant price crossing alert"""
        args = context.args
        if not args:
            await update.message.reply_text(
                "🔔 *Price Alert Usage:*\n"
                "`/alert 87000 resistance test`\n"
                "`/alert 85000 sl hit`",
                parse_mode='Markdown'
            )
            return
        
        try:
            price = float(args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid price.", parse_mode='Markdown')
            return
        
        custom_msg = " ".join(args[1:]) if len(args) > 1 else ""
        
        if self.app_context and self.app_context.market_listener:
            self.app_context.market_listener.add_price_alert(price, custom_msg, self, alert_type="crossing")
            await update.message.reply_text(
                f"✅ *Crossing Alert Set*\n"
                f"📍 Price: `${price:,.2f}`\n"
                f"💬 {custom_msg or 'No message'}" ,
                parse_mode='Markdown',
                reply_markup=self._get_main_menu_keyboard()
            )
        else:
            await update.message.reply_text("⚠️ System not ready.", parse_mode='Markdown')

    async def _closing_alert_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/closingalert above/below <price> <optional message> — 5m candle close alert"""
        args = context.args
        if not args or len(args) < 2:
            await update.message.reply_text(
                "🔔 *Closing Alert Usage:*\n"
                "`/closingalert above 87000 breakout confirmed`\n"
                "`/closingalert below 85000 support lost`",
                parse_mode='Markdown'
            )
            return
        
        direction = args[0].lower()
        if direction not in ("above", "below"):
            await update.message.reply_text("❌ Use `above` or `below`.", parse_mode='Markdown')
            return
        
        try:
            price = float(args[1])
        except ValueError:
            await update.message.reply_text("❌ Invalid price.", parse_mode='Markdown')
            return
        
        custom_msg = " ".join(args[2:]) if len(args) > 2 else ""
        
        if self.app_context and self.app_context.market_listener:
            self.app_context.market_listener.add_price_alert(
                price, custom_msg, self, alert_type="closing", direction=direction
            )
            await update.message.reply_text(
                f"✅ *Closing Alert Set*\n"
                f"📍 BTC close {direction} `${price:,.2f}`\n"
                f"💬 {custom_msg or 'No message'}",
                parse_mode='Markdown',
                reply_markup=self._get_main_menu_keyboard()
            )
        else:
            await update.message.reply_text("⚠️ System not ready.", parse_mode='Markdown')

    # --- BUTTON CALLBACK ---
    async def _button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data
        if data == 'tpl_long':
            tpl = "BTC > 70000\nSIDE: LONG\nSIZE: 2\nLEV: 40\nTP: 71000\nSL: 69500"
            await query.message.reply_text(f"`{tpl}`", parse_mode='Markdown')
        elif data == 'tpl_short':
            tpl = "BTC < 68000\nSIDE: SHORT\nSIZE: 2\nLEV: 40\nTP: 67000\nSL: 68500"
            await query.message.reply_text(f"`{tpl}`", parse_mode='Markdown')
        elif data == 'status':
            await self._show_status(query)
        elif data == 'balance':
            await self._show_balance(query)
        elif data == 'positions':
            await self._show_positions(query)
        elif data == 'trade_history':
            await self._show_trade_history(query)
        elif data == 'alerts_list':
            await self._show_alerts(query)
        elif data.startswith('refresh_pos_'):
            await self._refresh_position_callback(query)
        elif data == 'stop_all':
            await self._stop_all(query)

    # --- CORE COMMANDS ---
    async def _start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.chat_id = update.effective_chat.id
        logger.info(f"Telegram bot started by chat_id: {self.chat_id}")
        
        welcome_text = (
            "🚀 *Lighter Trading Bot* 🚀\n\n"
            "Commands: /long /short /balance /alert /closingalert"
        )
        await update.message.reply_text(
            welcome_text, 
            reply_markup=self._get_main_menu_keyboard(),
            parse_mode='Markdown'
        )

    async def _help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "📖 *Usage Guide*\n\n"
            "`/long` `/short` — Signal templates\n"
            "`/alert 87000 msg` — Price crossing alert\n"
            "`/closingalert above 87000 msg` — Candle close alert\n"
            "`/balance` — Account balance\n"
            "`/status` — Active signals"
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def _status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._show_status(update)

    async def _balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._show_balance(update)

    # =========================================================================
    #  BALANCE
    # =========================================================================
    async def _show_balance(self, update_or_query):
        from trading.lighter_client import lighter_wrapper
        from utils.config import LIGHTER_ACCOUNT_INDEX
        text = "💰 *Account Balance*\n━━━━━━━━━━━━━━━━━━\n"
        try:
            account_api = lighter.AccountApi(lighter_wrapper.api_client)
            acc_info = await account_api.account(by="index", value=str(LIGHTER_ACCOUNT_INDEX))
            if acc_info.accounts:
                account = acc_info.accounts[0]
                # Find USDC in assets
                usdc_val = 0.0
                for asset in (account.assets or []):
                    if asset.symbol == 'USDC':
                        usdc_val = float(asset.available_amount)
                        break
                
                collateral = float(account.collateral)
                total_val = float(account.total_asset_value)
                
                text += f"💵 USDC Available: `${usdc_val:,.2f}`\n"
                text += f"🏦 Collateral: `${collateral:,.2f}`\n"
                text += f"📊 Total Equity: `${total_val:,.2f}`\n"
                
                open_count = sum(1 for p in (account.positions or []) if float(p.position) != 0)
                text += f"\n📈 Open Positions: `{open_count}`"
            else:
                text += "No account found."
        except Exception as e:
            text += f"Error: {self._escape_md(str(e))}"

        await self._reply(update_or_query, text)

    # =========================================================================
    #  POSITIONS
    # =========================================================================
    async def _show_positions(self, update_or_query):
        from trading.lighter_client import lighter_wrapper
        text = "📈 *Active Positions*\n━━━━━━━━━━━━━━━━━━\n"
        try:
            account_api = lighter.AccountApi(lighter_wrapper.api_client)
            from utils.config import LIGHTER_ACCOUNT_INDEX
            acc_info = await account_api.account(by="index", value=str(LIGHTER_ACCOUNT_INDEX))
            
            if not acc_info.accounts:
                text += "No account found."
                await self._reply(update_or_query, text)
                return

            account = acc_info.accounts[0]
            
            # Fetch mark prices
            mark_prices = {}
            try:
                order_api = lighter.OrderApi(lighter_wrapper.api_client)
                resp = await order_api.exchange_stats_without_preload_content()
                raw = await resp.json()
                for obs in raw.get('order_book_stats', []):
                    mark_prices[obs['symbol']] = float(obs['last_trade_price'])
            except Exception:
                pass
            
            has_positions = False
            for pos in (account.positions or []):
                pos_size = float(pos.position)
                if pos_size == 0:
                    continue
                has_positions = True
                
                symbol = pos.symbol
                entry = float(pos.avg_entry_price)
                unrealized_pnl = float(pos.unrealized_pnl)
                realized_pnl = float(pos.realized_pnl)
                margin = float(pos.allocated_margin)
                imf = float(pos.initial_margin_fraction)
                leverage = round(100.0 / imf, 1) if imf > 0 else 0
                live_price = mark_prices.get(symbol, 0.0)
                
                side_str = "🟢 LONG" if pos_size > 0 else "🔴 SHORT"
                pnl_emoji = "✅" if unrealized_pnl >= 0 else "❌"
                pnl_pct = (unrealized_pnl / margin * 100) if margin > 0 else 0
                
                text += f"\n*{symbol}* {side_str}\n"
                text += f"├ Margin: `${margin:,.2f}` ×{leverage}x\n"
                text += f"├ Entry: `${entry:,.4f}`\n"
                if live_price > 0:
                    text += f"├ Mark: `${live_price:,.4f}`\n"
                text += f"├ {pnl_emoji} uPnL: `${unrealized_pnl:,.2f}` ({pnl_pct:+.2f}%)\n"
                if realized_pnl != 0:
                    text += f"├ rPnL: `${realized_pnl:,.2f}`\n"
                
                # TP/SL estimates from active signals
                if self.app_context and self.app_context.market_listener:
                    for sig in self.app_context.market_listener.get_active_signals():
                        if sig.asset.upper() == symbol.upper():
                            tp_dist = abs(sig.tp - entry)
                            sl_dist = abs(sig.sl - entry)
                            pos_value = margin * leverage if margin > 0 else 0
                            tp_pnl = (tp_dist / entry) * pos_value if entry > 0 else 0
                            sl_pnl = (sl_dist / entry) * pos_value if entry > 0 else 0
                            tp_pct = (tp_dist / entry) * leverage * 100 if entry > 0 else 0
                            sl_pct = (sl_dist / entry) * leverage * 100 if entry > 0 else 0
                            text += f"├ 🎯 TP: `${sig.tp:,.2f}` → +`${tp_pnl:,.2f}` (+{tp_pct:.1f}%)\n"
                            text += f"└ 🛑 SL: `${sig.sl:,.2f}` → -`${sl_pnl:,.2f}` (-{sl_pct:.1f}%)\n"
                            break
                
                text += "━━━━━━━━━━━━━━━━━━\n"
            
            if not has_positions:
                text += "\nNo open positions."
                
        except Exception as e:
            text += f"Error: {self._escape_md(str(e))}"

        await self._reply(update_or_query, text)

    # =========================================================================
    #  TRADE HISTORY (Authenticated)
    # =========================================================================
    async def _show_trade_history(self, update_or_query):
        from trading.lighter_client import lighter_wrapper
        from utils.config import LIGHTER_ACCOUNT_INDEX
        text = "📜 *Position History (Recent)*\n━━━━━━━━━━━━━━━━━━\n"
        try:
            order_api = lighter.OrderApi(lighter_wrapper.api_client)
            auth_token = lighter_wrapper.get_auth_token()
            
            resp = await order_api.trades_without_preload_content(
                sort_by="timestamp",
                limit=20,
                account_index=LIGHTER_ACCOUNT_INDEX,
                sort_dir="desc",
                auth=auth_token
            )
            raw = await resp.json()
            trades = raw.get('trades', [])
            
            if not trades:
                text += "\nNo transaction history found."
            else:
                # Group by tx_hash to show 'Position Events'
                groups = {}
                for t in trades:
                    tx = t.get('tx_hash')
                    if tx not in groups:
                        groups[tx] = {"trades": [], "total_usd": 0, "total_pnl": 0, "timestamp": t.get('timestamp'), "mkt": t.get('market_id')}
                    groups[tx]["trades"].append(t)
                    groups[tx]["total_usd"] += float(t.get('usd_amount', 0))
                    
                    pnl = 0.0
                    if str(t.get('ask_account_id')) == str(LIGHTER_ACCOUNT_INDEX):
                        pnl = float(t.get('ask_account_pnl', 0))
                    elif str(t.get('bid_account_id')) == str(LIGHTER_ACCOUNT_INDEX):
                        pnl = float(t.get('bid_account_pnl', 0))
                    groups[tx]["total_pnl"] += pnl

                # Sort by timestamp desc and take top 5 entries
                sorted_txs = sorted(groups.items(), key=lambda x: x[1]['timestamp'], reverse=True)[:5]
                
                for i, (tx, data) in enumerate(sorted_txs):
                    ts = int(data['timestamp'])
                    time_str = time.strftime('%m/%d %H:%M', time.localtime(ts // 1000))
                    pnl = data['total_pnl']
                    pnl_emoji = "✅" if pnl >= 0 else "❌"
                    pnl_str = f"{pnl_emoji} PnL: `${pnl:+.2f}`" if abs(pnl) > 0.001 else "ℹ️ Position Open/Adjust"
                    
                    t0 = data['trades'][0]
                    is_ask = str(t0.get('ask_account_id')) == str(LIGHTER_ACCOUNT_INDEX)
                    side = "🔴 SELL" if is_ask else "🟢 BUY"
                    
                    text += f"\n{i+1}. {side} | Mkt:{data['mkt']} | {time_str}\n"
                    text += f"   Value: `${data['total_usd']:,.2f}`\n"
                    text += f"   {pnl_str}\n"
                
                if not sorted_txs:
                    text += "\nNo recent position history."
        except Exception as e:
            text += f"Error: {self._escape_md(str(e))}"

        await self._reply(update_or_query, text)

    # =========================================================================
    #  ALERTS LIST
    # =========================================================================
    async def _show_alerts(self, update_or_query):
        text = "🔔 *Active Alerts*\n━━━━━━━━━━━━━━━━━━\n"
        text += "`/alert 87000 msg` — crossing\n"
        text += "`/closingalert above 87000 msg` — candle close\n\n"
        
        if self.app_context and self.app_context.market_listener:
            alerts = self.app_context.market_listener.get_price_alerts()
            if not alerts:
                text += "No active alerts."
            else:
                for i, alert in enumerate(alerts):
                    atype = alert.get('alert_type', 'crossing')
                    direction = alert.get('direction', '?')
                    msg = alert.get('message', '')
                    if atype == "closing":
                        text += f"{i+1}. Closing {direction} `${alert['price']:,.2f}`"
                    else:
                        text += f"{i+1}. Crossing `${alert['price']:,.2f}`"
                    if msg:
                        text += f" - {msg}"
                    text += "\n"
        else:
            text += "System not ready."

        await self._reply(update_or_query, text)

    # =========================================================================
    #  STATUS / STOP
    # =========================================================================
    async def _show_status(self, update_or_query):
        if not self.app_context or not self.app_context.market_listener:
            text = "⚠️ System not fully initialized."
        else:
            signals = self.app_context.market_listener.get_active_signals()
            if not signals:
                text = "📊 *Status:* No active signals."
            else:
                text = "📊 *Active Signals:*\n"
                for i, sig in enumerate(signals):
                    remaining = max(0, (sig.expiry_at - int(time.time())) // 60)
                    text += f"{i+1}. {sig.asset} {sig.condition_type} {sig.condition_price} ({sig.side}) - {remaining}m left\n"

        await self._reply(update_or_query, text)

    async def _stop_all(self, query):
        if self.app_context and self.app_context.market_listener:
            self.app_context.market_listener.clear_signals()
            self.app_context.market_listener.clear_price_alerts()
            await query.edit_message_text("🛑 Stopped all signals and alerts.", reply_markup=self._get_main_menu_keyboard())

    # =========================================================================
    #  MESSAGE HANDLER
    # =========================================================================
    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        signal = parse_signal(text)
        if not signal:
            await update.message.reply_text("❌ Unknown command or malformed signal.")
            return
            
        await update.message.reply_text(
            f"✅ *ACTIVE:* {signal.asset} {signal.side} if {signal.condition_type} {signal.condition_price}",
            parse_mode='Markdown'
        )
        asyncio.create_task(self.on_signal_callback(signal))

    # =========================================================================
    #  UTILITIES
    # =========================================================================
    def _escape_md(self, text: str) -> str:
        for ch in ['_', '*', '[', ']', '`']:
            text = text.replace(ch, f'\\{ch}')
        return text

    async def _reply(self, update_or_query, text):
        try:
            if hasattr(update_or_query, 'message') and update_or_query.message and not hasattr(update_or_query, 'data'):
                await update_or_query.message.reply_text(text, parse_mode='Markdown', reply_markup=self._get_main_menu_keyboard())
            else:
                await update_or_query.edit_message_text(text, parse_mode='Markdown', reply_markup=self._get_main_menu_keyboard())
        except Exception as e:
            logger.warning(f"Markdown render failed, fallback: {e}")
            plain = text.replace('*', '').replace('`', '').replace('_', '').replace('\\', '')
            try:
                if hasattr(update_or_query, 'message') and update_or_query.message and not hasattr(update_or_query, 'data'):
                    await update_or_query.message.reply_text(plain, reply_markup=self._get_main_menu_keyboard())
                else:
                    await update_or_query.edit_message_text(plain, reply_markup=self._get_main_menu_keyboard())
            except Exception:
                pass

    # =========================================================================
    #  POSITION CARDS
    # =========================================================================
    async def send_position_card(self, signal: TradeSignal, entry_price: float):
        """Sends an interactive position card after a trade executes."""
        if not self.chat_id: return
        
        text = self._format_position_card_text(signal, entry_price, mark_price=entry_price, size=signal.size)
        keyboard = [
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_pos_{signal.asset}_{signal.side}")]
        ]
        await self.app.bot.send_message(
            chat_id=self.chat_id,
            text=text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def _refresh_position_callback(self, query):
        """Callback for the 'Refresh' button on a position card."""
        # data: refresh_pos_BTC_LONG
        try:
            _, _, asset, side = query.data.split('_')
            
            # Fetch latest data from Lighter
            from trading.lighter_client import lighter_wrapper
            from utils.config import LIGHTER_ACCOUNT_INDEX
            
            account_api = lighter.AccountApi(lighter_wrapper.api_client)
            acc_info = await account_api.account(by="index", value=str(LIGHTER_ACCOUNT_INDEX))
            
            if not acc_info.accounts:
                await query.answer("Account not found.")
                return

            account = acc_info.accounts[0]
            
            # Find the position for this asset
            target_pos = None
            for pos in (account.positions or []):
                if pos.symbol.upper() == asset.upper():
                    target_pos = pos
                    break
            
            if not target_pos or float(target_pos.position) == 0:
                await query.edit_message_text(f"🏁 Position for {asset} is now CLOSED.", reply_markup=self._get_main_menu_keyboard())
                return

            # Fetch mark price
            mark_price = 0.0
            try:
                order_api = lighter.OrderApi(lighter_wrapper.api_client)
                resp = await order_api.exchange_stats_without_preload_content()
                raw = await resp.json()
                for obs in raw.get('order_book_stats', []):
                    if obs['symbol'] == target_pos.symbol:
                        mark_price = float(obs['last_trade_price'])
                        break
            except Exception: pass

            # Re-fetch Signal-like data for TP/SL from current active orders
            # (In a real app, you'd store the original signal, but for now we poll)
            tp_price = 0.0
            sl_price = 0.0
            try:
                auth_token = lighter_wrapper.get_auth_token()
                orders_resp = await order_api.account_active_orders_without_preload_content(
                    account_index=LIGHTER_ACCOUNT_INDEX, auth=auth_token
                )
                orders_raw = await orders_resp.json()
                for o in orders_raw.get('orders', []):
                    if o.get('market_id') == target_pos.market_id:
                        # Simple heuristic: Higher sell is TP for long, or vice versa
                        # Better: Lighter usually marks them in metadata if we set it, but for now let's use the first 2
                        p = float(o.get('limit_price'))
                        if tp_price == 0: tp_price = p
                        else: sl_price = p
            except Exception: pass

            # Ensure TP is indeed better than entry (for the display)
            entry = float(target_pos.avg_entry_price)
            if side == 'LONG' and sl_price > tp_price: tp_price, sl_price = sl_price, tp_price
            elif side == 'SHORT' and tp_price > sl_price: tp_price, sl_price = sl_price, tp_price

            size = float(target_pos.position)
            text = self._format_position_card_text_refresh(target_pos, mark_price, tp_price, sl_price)
            
            # Update the message
            keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data=query.data)]]
            await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
            await query.answer("Updated!")
            
        except Exception as e:
            logger.error(f"Refresh error: {e}")
            await query.answer("Error refreshing.")

    def _format_position_card_text(self, signal, entry, mark_price, size):
        pnl = 0.0
        pnl_pct = 0.0
        side_emoji = "🟢 LONG" if signal.side == 'LONG' else "🔴 SHORT"
        
        text = (
            f"⚡ *Position Opened:* {signal.asset}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"├ Type: {side_emoji} ×{signal.leverage}x\n"
            f"├ Entry: `${entry:,.2f}`\n"
            f"├ Mark: `${mark_price:,.2f}`\n"
            f"├ PnL: `${pnl:+.2f}` ({pnl_pct:+.2f}%)\n"
            f"├ 🎯 TP: `${signal.tp:,.2f}`\n"
            f"└ 🛑 SL: `${signal.sl:,.2f}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"_Last Updated: {time.strftime('%H:%M:%S')}_"
        )
        return text

    def _format_position_card_text_refresh(self, pos, mark, tp, sl):
        entry = float(pos.avg_entry_price)
        margin = float(pos.allocated_margin)
        imf = float(pos.initial_margin_fraction)
        leverage = round(100.0 / imf, 1) if imf > 0 else 0
        u_pnl = float(pos.unrealized_pnl)
        pnl_pct = (u_pnl / margin * 100) if margin > 0 else 0
        side_str = "🟢 LONG" if float(pos.position) > 0 else "🔴 SHORT"
        
        # Est Profit at TP/SL
        pos_value = margin * leverage
        tp_pnl = abs(tp - entry) / entry * pos_value if entry > 0 and tp > 0 else 0
        sl_pnl = abs(sl - entry) / entry * pos_value if entry > 0 and sl > 0 else 0
        
        text = (
            f"📈 *Active Position:* {pos.symbol}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"├ Type: {side_str} ×{leverage}x\n"
            f"├ Entry: `${entry:,.2f}`\n"
            f"├ Mark: `${mark:,.2f}`\n"
            f"├ PnL: *`${u_pnl:+.2f}`* ({pnl_pct:+.2f}%)\n"
        )
        if tp > 0:
            text += f"├ 🎯 TP: `${tp:,.2f}` (+$`{tp_pnl:,.2f}`)\n"
        if sl > 0:
            text += f"└ 🛑 SL: `${sl:,.2f}` (-$`{sl_pnl:,.2f}`)\n"
        else:
            text += "└ 🛑 SL: _None detected_\n"
            
        text += (
            f"━━━━━━━━━━━━━━━━━━\n"
            f"_Last Updated: {time.strftime('%H:%M:%S')}_"
        )
        return text

    async def send_message(self, text: str):
        if self.chat_id:
            try:
                await self.app.bot.send_message(chat_id=self.chat_id, text=text, parse_mode='Markdown')
            except Exception:
                pass

    async def start(self):
        logger.info("Starting up Telegram Bot Polling...")
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()
        
    async def stop(self):
        logger.info("Stopping Telegram Bot...")
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()
