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
from telegram.error import BadRequest

from utils.logger import logger
from utils.config import TELEGRAM_BOT_TOKEN, ALLOWED_TELEGRAM_USER_IDS
from bot.parser import parse_signal, TradeSignal
import lighter
from utils.helpers import detect_tp_sl_from_orders

class TelegramBotHandler:
    def __init__(self, on_signal_callback: Callable[[TradeSignal], Awaitable[None]], app_context=None):
        self.on_signal_callback = on_signal_callback
        self.app_context = app_context
        self.app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        
        # Initialize chat_id from the first allowed user ID if available
        # This ensures the bot can send notifications (like UI trades) even before a user talks to it.
        self.chat_id = ALLOWED_TELEGRAM_USER_IDS[0] if ALLOWED_TELEGRAM_USER_IDS else None 

        self.app.add_handler(CommandHandler("start", self._auth_wrapper(self._start_command)))
        self.app.add_handler(CommandHandler("status", self._auth_wrapper(self._status_command)))
        self.app.add_handler(CommandHandler("balance", self._auth_wrapper(self._balance_command)))
        self.app.add_handler(CommandHandler("long", self._auth_wrapper(self._long_command)))
        self.app.add_handler(CommandHandler("short", self._auth_wrapper(self._short_command)))
        self.app.add_handler(CommandHandler("help", self._auth_wrapper(self._help_command)))
        self.app.add_handler(CommandHandler("alert", self._auth_wrapper(self._alert_command)))
        self.app.add_handler(CommandHandler("closingalert", self._auth_wrapper(self._closing_alert_command)))
        self.app.add_handler(CommandHandler("tp", self._auth_wrapper(self._tp_command)))
        self.app.add_handler(CommandHandler("sl", self._auth_wrapper(self._sl_command)))
        self.app.add_handler(CommandHandler("cancel_tp", self._auth_wrapper(self._cancel_tp_command)))
        self.app.add_handler(CommandHandler("cancel_sl", self._auth_wrapper(self._cancel_sl_command)))
        self.app.add_handler(CommandHandler("close", self._auth_wrapper(self._close_command)))
        self.app.add_handler(CallbackQueryHandler(self._auth_wrapper(self._button_callback)))
        self.app.add_handler(CommandHandler("settings", self._auth_wrapper(self._settings_command)))
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
                InlineKeyboardButton("📜 History", callback_data='trade_history'),
                InlineKeyboardButton("🔔 Alerts", callback_data='alerts_list'),
            ],
            [
                InlineKeyboardButton("📊 Status", callback_data='status'),
                InlineKeyboardButton("⚙️ Settings", callback_data='settings'),
            ],
            [
                InlineKeyboardButton("🛑 Stop All", callback_data='stop_all'),
            ],
        ]
        return InlineKeyboardMarkup(keyboard)

    def _get_settings_keyboard(self):
        from trading.copy_manager import copy_config
        decibel_status = "🟢 ON" if copy_config.decibel_enabled else "🔴 OFF"
        coindcx_status = "🟢 ON" if copy_config.coindcx_enabled else "🔴 OFF"
        
        keyboard = [
            [
                InlineKeyboardButton(f"Decibel: {decibel_status}", callback_data='toggle_decibel'),
            ],
            [
                InlineKeyboardButton(f"CoinDCX: {coindcx_status}", callback_data='toggle_coindcx'),
            ],
            [
                InlineKeyboardButton("🏠 Main Menu", callback_data='menu'),
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    # =========================================================================
    #  TEMPLATES
    # =========================================================================
    async def _long_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        tpl = (
            "BTC > 70000\n"
            "SIDE: LONG\n"
            "SIZE: 2\n"
            "LEV: 40\n"
            "TP: 71000\n"
            "SL: 69500\n"
            "\n"
            "💡 Pip mode:\n"
            "TP: 500p\n"
            "SL: 250p"
        )
        await update.message.reply_text(f"`{tpl}`", parse_mode='Markdown')

    async def _short_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        tpl = (
            "BTC < 68000\n"
            "SIDE: SHORT\n"
            "SIZE: 2\n"
            "LEV: 40\n"
            "TP: 67000\n"
            "SL: 68500\n"
            "\n"
            "💡 Pip mode:\n"
            "TP: 500p\n"
            "SL: 250p"
        )
        await update.message.reply_text(f"`{tpl}`", parse_mode='Markdown')

    # =========================================================================
    #  TP / SL / CLOSE COMMANDS
    # =========================================================================
    async def _tp_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/tp <price> [asset] — Set take profit on current position"""
        args = context.args
        if not args:
            await update.message.reply_text(
                "🎯 *Take Profit Usage:*\n"
                "`/tp 71000` — Set TP at $71,000\n"
                "`/tp 500p` — Set TP 500 points from entry\n"
                "`/tp 71000 ETH` — Set TP for ETH",
                parse_mode='Markdown'
            )
            return
        
        try:
            tp_raw = args[0].upper()
            is_pips = tp_raw.endswith('P')
            tp_value = float(tp_raw.rstrip('P'))
            asset = args[1].upper() if len(args) > 1 else "BTC"
            
            # Get current position to determine side and resolve pips
            pos_info = await self._get_position_info(asset)
            if not pos_info:
                await update.message.reply_text(f"❌ No open position for {asset}.", parse_mode='Markdown')
                return
            
            is_long = pos_info['size'] > 0
            entry = pos_info['entry']
            
            if is_pips:
                tp_price = entry + tp_value if is_long else entry - tp_value
            else:
                tp_price = tp_value
            
            # Validate
            if is_long and tp_price <= entry:
                await update.message.reply_text(f"⚠️ TP must be above entry (${entry:,.2f}) for LONG.", parse_mode='Markdown')
                return
            elif not is_long and tp_price >= entry:
                await update.message.reply_text(f"⚠️ TP must be below entry (${entry:,.2f}) for SHORT.", parse_mode='Markdown')
                return
            
            from trading.risk_manager import place_single_tp_order
            await update.message.reply_text(f"⏳ Setting TP at `${tp_price:,.2f}`...", parse_mode='Markdown')
            
            success = await place_single_tp_order(asset, tp_price, is_long)
            if success:
                # Calculate estimated profit
                margin = pos_info['margin']
                leverage = pos_info['leverage']
                est_profit = abs(tp_price - entry) / entry * margin * leverage if entry > 0 else 0
                
                await update.message.reply_text(
                    f"✅ *Take Profit Set*\n"
                    f"├ Asset: {asset}\n"
                    f"├ Entry: `${entry:,.2f}`\n"
                    f"├ TP: `${tp_price:,.2f}`\n"
                    f"└ Est. Profit: `+${est_profit:,.2f}`",
                    parse_mode='Markdown',
                    reply_markup=self._get_main_menu_keyboard()
                )
            else:
                await update.message.reply_text("❌ Failed to set TP. Check logs.", parse_mode='Markdown')
                
        except ValueError:
            await update.message.reply_text("❌ Invalid price format.", parse_mode='Markdown')
        except Exception as e:
            logger.error(f"TP command error: {e}")
            await update.message.reply_text(f"❌ Error: {self._escape_md(str(e))}", parse_mode='Markdown')

    async def _sl_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/sl <price> [asset] — Set stop loss on current position"""
        args = context.args
        if not args:
            await update.message.reply_text(
                "🛑 *Stop Loss Usage:*\n"
                "`/sl 69000` — Set SL at $69,000\n"
                "`/sl 250p` — Set SL 250 points from entry\n"
                "`/sl 69000 ETH` — Set SL for ETH",
                parse_mode='Markdown'
            )
            return
        
        try:
            sl_raw = args[0].upper()
            is_pips = sl_raw.endswith('P')
            sl_value = float(sl_raw.rstrip('P'))
            asset = args[1].upper() if len(args) > 1 else "BTC"
            
            pos_info = await self._get_position_info(asset)
            if not pos_info:
                await update.message.reply_text(f"❌ No open position for {asset}.", parse_mode='Markdown')
                return
            
            is_long = pos_info['size'] > 0
            entry = pos_info['entry']
            
            if is_pips:
                sl_price = entry - sl_value if is_long else entry + sl_value
            else:
                sl_price = sl_value
            
            # Validate
            if is_long and sl_price >= entry:
                await update.message.reply_text(f"⚠️ SL must be below entry (${entry:,.2f}) for LONG.", parse_mode='Markdown')
                return
            elif not is_long and sl_price <= entry:
                await update.message.reply_text(f"⚠️ SL must be above entry (${entry:,.2f}) for SHORT.", parse_mode='Markdown')
                return
            
            from trading.risk_manager import place_single_sl_order
            await update.message.reply_text(f"⏳ Setting SL at `${sl_price:,.2f}`...", parse_mode='Markdown')
            
            success = await place_single_sl_order(asset, sl_price, is_long)
            if success:
                margin = pos_info['margin']
                leverage = pos_info['leverage']
                est_loss = abs(sl_price - entry) / entry * margin * leverage if entry > 0 else 0
                
                await update.message.reply_text(
                    f"✅ *Stop Loss Set*\n"
                    f"├ Asset: {asset}\n"
                    f"├ Entry: `${entry:,.2f}`\n"
                    f"├ SL: `${sl_price:,.2f}`\n"
                    f"└ Est. Loss: `-${est_loss:,.2f}`",
                    parse_mode='Markdown',
                    reply_markup=self._get_main_menu_keyboard()
                )
            else:
                await update.message.reply_text("❌ Failed to set SL. Check logs.", parse_mode='Markdown')
                
        except ValueError:
            await update.message.reply_text("❌ Invalid price format.", parse_mode='Markdown')
        except Exception as e:
            logger.error(f"SL command error: {e}")
            await update.message.reply_text(f"❌ Error: {self._escape_md(str(e))}", parse_mode='Markdown')

    async def _close_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/close [asset] — Close position at market"""
        args = context.args
        asset = args[0].upper() if args else "BTC"
        
        try:
            pos_info = await self._get_position_info(asset)
            if not pos_info:
                await update.message.reply_text(f"❌ No open position for {asset}.", parse_mode='Markdown')
                return
            
            is_long = pos_info['size'] > 0
            side_str = "LONG" if is_long else "SHORT"
            
            await update.message.reply_text(
                f"⏳ Closing {side_str} position for {asset} at market...",
                parse_mode='Markdown'
            )
            
            from trading.risk_manager import close_position_market
            success = await close_position_market(asset, is_long)
            if success:
                await update.message.reply_text(
                    f"✅ *Position Closed*\n"
                    f"├ Asset: {asset}\n"
                    f"├ Side: {side_str}\n"
                    f"└ uPnL was: `${pos_info['unrealized_pnl']:+,.2f}`",
                    parse_mode='Markdown',
                    reply_markup=self._get_main_menu_keyboard()
                )
            else:
                await update.message.reply_text("❌ Failed to close position. Check logs.", parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Close command error: {e}")
            await update.message.reply_text(f"❌ Error: {self._escape_md(str(e))}", parse_mode='Markdown')

    async def _cancel_tp_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/cancel_tp [asset] — Cancel take profit orders"""
        args = context.args
        asset = args[0].upper() if args else "BTC"
        await self._cancel_orders_logic(update, asset, "TAKE_PROFIT")

    async def _cancel_sl_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/cancel_sl [asset] — Cancel stop loss orders"""
        args = context.args
        asset = args[0].upper() if args else "BTC"
        await self._cancel_orders_logic(update, asset, "STOP_LOSS")

    async def _cancel_orders_logic(self, update_or_query, asset: str, order_type_filter: str):
        from trading.lighter_client import lighter_wrapper
        from utils.config import LIGHTER_ACCOUNT_INDEX
        from trading.market_config import market_registry
        
        try:
            mkt_id = market_registry.get_market_id(asset)
            auth_token = lighter_wrapper.get_auth_token()
            order_api = lighter.OrderApi(lighter_wrapper.api_client)
            
            # Fetch active orders
            resp = await order_api.account_active_orders_without_preload_content(
                account_index=LIGHTER_ACCOUNT_INDEX, auth=auth_token
            )
            data = await resp.json()
            orders = data.get('orders', [])
            
            to_cancel = []
            for o in orders:
                if o.get('market_id') == mkt_id:
                    otype = o.get('type', '').upper()
                    if order_type_filter in otype:
                        to_cancel.append(o.get('order_id'))
            
            if not to_cancel:
                await self._reply(update_or_query, f"ℹ️ No active {order_type_filter} orders for {asset}.")
                return
            
            await self._reply(update_or_query, f"⏳ Canceling {len(to_cancel)} {order_type_filter} order(s) for {asset}...")
            
            # Note: SignerClient cancel_order is usually used here
            client = lighter_wrapper.signer_client
            for oid in to_cancel:
                tx, tx_hash, err = await client.cancel_order(market_index=mkt_id, order_id=oid)
                if err:
                    logger.error(f"Failed to cancel order {oid}: {err}")
            
            await self._reply(update_or_query, f"✅ Scaled out/Canceled {order_type_filter} for {asset}.", reply_markup=self._get_main_menu_keyboard())
            
        except Exception as e:
            logger.error(f"Cancel order error: {e}")
            await self._reply(update_or_query, f"❌ Error: {self._escape_md(str(e))}")

    # =========================================================================
    #  ALERT COMMANDS
    # =========================================================================
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

    # =========================================================================
    #  BUTTON CALLBACK
    # =========================================================================
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
        elif data == 'settings':
            await self._settings_command(query, context)
        elif data == 'toggle_decibel':
            from trading.copy_manager import copy_config
            new_state = copy_config.toggle_decibel()
            await query.answer(f"Decibel Copy: {'ENABLED' if new_state else 'DISABLED'}")
            await query.edit_message_reply_markup(reply_markup=self._get_settings_keyboard())
        elif data == 'toggle_coindcx':
            from trading.copy_manager import copy_config
            new_state = copy_config.toggle_coindcx()
            await query.answer(f"CoinDCX Copy: {'ENABLED' if new_state else 'DISABLED'}")
            await query.edit_message_reply_markup(reply_markup=self._get_settings_keyboard())
        elif data == 'menu':
            await query.edit_message_text("📱 *Main Menu*", parse_mode='Markdown', reply_markup=self._get_main_menu_keyboard())
        elif data.startswith('refresh_pos_'):
            await self._refresh_position_callback(query)
        elif data.startswith('close_pos_'):
            await self._close_position_callback(query)
        elif data.startswith('set_tp_'):
            asset = data.split('_', 2)[2]
            await query.message.reply_text(f"To set TP for {asset}, type:\n`/tp <price> {asset}` or `/tp <pips>p {asset}`", parse_mode='Markdown')
        elif data.startswith('set_sl_'):
            asset = data.split('_', 2)[2]
            await query.message.reply_text(f"To set SL for {asset}, type:\n`/sl <price> {asset}` or `/sl <pips>p {asset}`", parse_mode='Markdown')
        elif data.startswith('cancel_tp_'):
            asset = data.split('_', 2)[2]
            await self._cancel_orders_logic(query, asset, "TAKE_PROFIT")
        elif data.startswith('cancel_sl_'):
            asset = data.split('_', 2)[2]
            await self._cancel_orders_logic(query, asset, "STOP_LOSS")
        elif data == 'stop_all':
            await self._stop_all(query)

    # =========================================================================
    #  CORE COMMANDS
    # =========================================================================
    async def _start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.chat_id = update.effective_chat.id
        logger.info(f"Telegram bot started by chat_id: {self.chat_id}")
        
        welcome_text = (
            "🚀 *Lighter Trading Bot* 🚀\n\n"
            "*Signal:* Paste a trade signal\n"
            "*Commands:*\n"
            "`/long` `/short` — Templates\n"
            "`/tp 71000` — Set take profit\n"
            "`/sl 69000` — Set stop loss\n"
            "`/close` — Close position\n"
            "`/alert 87000` — Price alert\n"
            "`/balance` — Account info\n"
            "`/help` — Full guide"
        )
        await update.message.reply_text(
            welcome_text, 
            reply_markup=self._get_main_menu_keyboard(),
            parse_mode='Markdown'
        )

    async def _help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "📖 *Trading Bot Guide*\n\n"
            "*📝 Signal Format:*\n"
            "`BTC > 70000`\n"
            "`SIDE: LONG`\n"
            "`SIZE: 2`\n"
            "`LEV: 40`\n"
            "`TP: 71000` or `TP: 500p`\n"
            "`SL: 69500` or `SL: 250p`\n\n"
            "*🎯 Position Management:*\n"
            "`/tp 71000` — Set TP (price)\n"
            "`/tp 500p` — Set TP (pips from entry)\n"
            "`/sl 69000` — Set SL (price)\n"
            "`/sl 250p` — Set SL (pips from entry)\n"
            "`/close` — Market close BTC\n"
            "`/close ETH` — Market close ETH\n\n"
            "*🔔 Alerts:*\n"
            "`/alert 87000 msg` — Price crossing\n"
            "`/closingalert above 87000` — Candle close\n\n"
            "*📊 Info:*\n"
            "`/balance` `/status` `/long` `/short`\n\n"
            "*💡 Pips:* `250p` = 250 price points\n"
            "LONG: TP = entry+250, SL = entry-250\n"
            "SHORT: TP = entry-250, SL = entry+250"
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def _status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._show_status(update)

    async def _settings_command(self, update_or_query, context=None):
        text = (
            "⚙️ *Copy Trading Settings*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Toggle copy trading for each exchange below. Settings are saved automatically."
        )
        await self._reply(update_or_query, text, reply_markup=self._get_settings_keyboard())

    async def _balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._show_balance(update)

    # =========================================================================
    #  HELPER: Get Position Info
    # =========================================================================
    async def _get_position_info(self, asset: str) -> dict:
        """Fetch current position info for an asset from Lighter."""
        from trading.lighter_client import lighter_wrapper
        from utils.config import LIGHTER_ACCOUNT_INDEX
        
        try:
            account_api = lighter.AccountApi(lighter_wrapper.api_client)
            acc_info = await account_api.account(by="index", value=str(LIGHTER_ACCOUNT_INDEX))
            
            if not acc_info.accounts:
                return None
            
            account = acc_info.accounts[0]
            for pos in (account.positions or []):
                pos_size = float(pos.position)
                if pos_size == 0:
                    continue
                if pos.symbol.upper().startswith(asset.upper()):
                    entry = float(pos.avg_entry_price)
                    margin = float(pos.allocated_margin)
                    imf = float(pos.initial_margin_fraction)
                    leverage = round(100.0 / imf, 1) if imf > 0 else 0
                    return {
                        'size': pos_size,
                        'entry': entry,
                        'margin': margin,
                        'leverage': leverage,
                        'unrealized_pnl': float(pos.unrealized_pnl),
                        'symbol': pos.symbol,
                        'market_id': pos.market_id,
                    }
            return None
        except Exception as e:
            logger.error(f"Error fetching position info: {e}")
            return None

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
                usdc_val = 0.0
                for asset in (account.assets or []):
                    if asset.symbol == 'USDC':
                        usdc_val = float(asset.balance) - float(asset.locked_balance)
                        break
                
                usdc_in_positions = sum(float(p.allocated_margin) for p in (account.positions or []))
                collateral = float(account.collateral)
                total_val = float(account.total_asset_value)
                
                text += f"💵 USDC Available: `${usdc_val:,.2f}`\n"
                text += f"💰 USDC in Positions: `${usdc_in_positions:,.2f}`\n"
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
    #  POSITIONS (with dynamic PnL estimation)
    # =========================================================================
    async def _show_positions(self, update_or_query):
        from trading.lighter_client import lighter_wrapper
        text = "🛰️ *POSITION TRACKER HUD*\n━━━━━━━━━━━━━━━━━━━━\n"
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
            
            # Fetch active orders for TP/SL detection per active market
            active_orders = {}
            try:
                auth_token = lighter_wrapper.get_auth_token()
                order_api = lighter.OrderApi(lighter_wrapper.api_client)
                for pos in (account.positions or []):
                    if float(pos.position) == 0: continue
                    mkt = pos.market_id
                    if mkt in active_orders: continue
                    
                    orders_resp = await order_api.account_active_orders_without_preload_content(
                        account_index=LIGHTER_ACCOUNT_INDEX, market_id=mkt, auth=auth_token
                    )
                    orders_raw = await orders_resp.json()
                    active_orders[mkt] = orders_raw.get('orders', [])
            except Exception as e:
                logger.error(f"Failed to fetch HUD orders: {e}")
            
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
                is_long = pos_size > 0
                
                side_str = "🟢 LONG" if is_long else "🔴 SHORT"
                pnl_emoji = "✅" if unrealized_pnl >= 0 else "❌"
                pnl_pct = (unrealized_pnl / margin * 100) if margin > 0 else 0
                
                text += f"```\n"
                text += f"SIZE   : {abs(pos_size):.4f} ({'BUY' if is_long else 'SELL'})\n"
                text += f"ENTRY  : ${entry:,.2f}\n"
                if live_price > 0:
                    text += f"MARK   : ${live_price:,.2f}\n"
                text += f"MARGIN : ${margin:,.2f} ({leverage}x)\n"
                text += f"```\n"
                text += f"*{pnl_emoji} PnL: ${unrealized_pnl:,.2f} ({pnl_pct:+.2f}%)*\n"
                if realized_pnl != 0:
                    text += f"└ Realized: `${realized_pnl:,.2f}`\n"
                
                # Detect TP/SL from active orders
                tp_price, sl_price = detect_tp_sl_from_orders(
                    active_orders.get(pos.market_id, []), is_long
                )
                
                # Fallback: check in-memory signals
                if (tp_price == 0 or sl_price == 0) and self.app_context and self.app_context.market_listener:
                    for sig in self.app_context.market_listener.get_active_signals():
                        if sig.asset.upper() == symbol.upper():
                            if tp_price == 0 and sig.tp and sig.tp > 0:
                                tp_price = sig.tp
                            if sl_price == 0 and sig.sl and sig.sl > 0:
                                sl_price = sig.sl
                            break
                
                # Show TP/SL with estimated PnL
                pos_value = margin * leverage if margin > 0 else 0
                # Show TP/SL with estimated PnL and Pips
                pos_value = margin * leverage if margin > 0 else 0
                if tp_price > 0:
                    tp_pnl = abs(tp_price - entry) / entry * pos_value if entry > 0 else 0
                    tp_pct = abs(tp_price - entry) / entry * leverage * 100 if entry > 0 else 0
                    tp_pips = abs(tp_price - entry)
                    text += f"├ 🎯 TP: `${tp_price:,.2f}` ({tp_pips:.1f}p) → `${tp_pnl:,.2f}` (+{tp_pct:.1f}%)\n"
                if sl_price > 0:
                    sl_pnl = abs(sl_price - entry) / entry * pos_value if entry > 0 else 0
                    sl_pct = abs(sl_price - entry) / entry * leverage * 100 if entry > 0 else 0
                    sl_pips = abs(sl_price - entry)
                    text += f"├ 🛑 SL: `${sl_price:,.2f}` ({sl_pips:.1f}p) → `${sl_pnl:,.2f}` (-{sl_pct:.1f}%)\n"
                
                if tp_price == 0 and sl_price == 0:
                    text += f"└ ⚠️ No TP/SL set\n"
                else:
                    text += "━━━━━━━━━━━━━━━━━━━━\n"
                
            keyboard = []
            if not has_positions:
                text += "\nNo open positions."
                keyboard = self._get_main_menu_keyboard()
            else:
                for pos in (account.positions or []):
                    if float(pos.position) == 0:
                        continue
                    clean_asset = pos.symbol.replace("USDC", "").replace("-", "")
                    keyboard.append([
                        InlineKeyboardButton(f"🎯 TP {clean_asset}", callback_data=f'set_tp_{clean_asset}'),
                        InlineKeyboardButton(f"🛑 SL {clean_asset}", callback_data=f'set_sl_{clean_asset}'),
                    ])
                    keyboard.append([
                        InlineKeyboardButton(f"🇽 Cancel TP", callback_data=f'cancel_tp_{clean_asset}'),
                        InlineKeyboardButton(f"🇽 Cancel SL", callback_data=f'cancel_sl_{clean_asset}'),
                    ])
                    keyboard.append([
                        InlineKeyboardButton(f"💀 Market Close {clean_asset}", callback_data=f'close_pos_{clean_asset}')
                    ])
                keyboard.append([
                    InlineKeyboardButton("🔄 Refresh Positions", callback_data='positions'),
                    InlineKeyboardButton("🏠 Main Menu", callback_data='menu')  # if handled, or just use main menu layout
                ])
                keyboard = InlineKeyboardMarkup(keyboard)
                
        except Exception as e:
            text += f"Error: {self._escape_md(str(e))}"

        await self._reply(update_or_query, text, reply_markup=keyboard)


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
                groups = {}
                WINDOW_MS = 300000 
                for t in trades:
                    mkt = t.get('market_id')
                    ts = int(t.get('timestamp') or 0)
                    key = f"{mkt}_{ts // WINDOW_MS}"
                    
                    if key not in groups:
                        groups[key] = {
                            "trades": [], 
                            "total_usd": 0, 
                            "total_pnl": 0, 
                            "timestamp": ts, 
                            "mkt": mkt
                        }
                    groups[key]["trades"].append(t)
                    groups[key]["total_usd"] += float(t.get('usd_amount', 0))
                    
                    pnl = 0.0
                    if str(t.get('ask_account_id')) == str(LIGHTER_ACCOUNT_INDEX):
                        pnl = float(t.get('ask_account_pnl', 0))
                    elif str(t.get('bid_account_id')) == str(LIGHTER_ACCOUNT_INDEX):
                        pnl = float(t.get('bid_account_pnl', 0))
                    groups[key]["total_pnl"] += pnl

                sorted_txs = [
                    (k, v) for k, v in groups.items() if abs(v['total_pnl']) > 0.001
                ]
                sorted_txs = sorted(sorted_txs, key=lambda x: x[1]['timestamp'], reverse=True)[:5]
                
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
                    tp_label = f"TP:${sig.tp:,.0f}" if sig.tp and not sig.tp_is_pips else (f"TP:{sig.tp}p" if sig.tp else "TP:—")
                    sl_label = f"SL:${sig.sl:,.0f}" if sig.sl and not sig.sl_is_pips else (f"SL:{sig.sl}p" if sig.sl else "SL:—")
                    text += f"{i+1}. {sig.asset} {sig.condition_type} {sig.condition_price} ({sig.side}) {tp_label} {sl_label} - {remaining}m left\n"

        await self._reply(update_or_query, text)

    async def _stop_all(self, query):
        if self.app_context and self.app_context.market_listener:
            self.app_context.market_listener.clear_signals()
            self.app_context.market_listener.clear_price_alerts()
            await self._reply(query, "🛑 Stopped all signals and alerts.")

    # =========================================================================
    #  MESSAGE HANDLER
    # =========================================================================
    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        signal = parse_signal(text)
        if not signal:
            await update.message.reply_text("❌ Unknown command or malformed signal.")
            return
        
        # Build confirmation message
        tp_display = f"{signal.tp}p" if signal.tp_is_pips else f"${signal.tp:,.2f}" if signal.tp else "—"
        sl_display = f"{signal.sl}p" if signal.sl_is_pips else f"${signal.sl:,.2f}" if signal.sl else "—"
        
        await update.message.reply_text(
            f"✅ *Signal Activated*\n"
            f"├ {signal.asset} {signal.condition_type} {signal.condition_price}\n"
            f"├ Side: {signal.side} | Size: {signal.size} | Lev: {signal.leverage}x\n"
            f"├ TP: {tp_display}\n"
            f"└ SL: {sl_display}",
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

    async def _reply(self, update_or_query, text, reply_markup=None):
        try:
            rm = reply_markup if reply_markup else self._get_main_menu_keyboard()
            if hasattr(update_or_query, 'message') and update_or_query.message and not hasattr(update_or_query, 'data'):
                await update_or_query.message.reply_text(text, parse_mode='Markdown', reply_markup=rm)
            else:
                await update_or_query.edit_message_text(text, parse_mode='Markdown', reply_markup=rm)
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                raise e # re-raise if it's not a redundancy error
            logger.debug(f"Redundant message edit: {e}")
        except Exception as e:
            logger.warning(f"Markdown render failed, fallback: {e}")
            plain = text.replace('*', '').replace('`', '').replace('_', '').replace('\\', '')
            try:
                rm = reply_markup if reply_markup else self._get_main_menu_keyboard()
                if hasattr(update_or_query, 'message') and update_or_query.message and not hasattr(update_or_query, 'data'):
                    await update_or_query.message.reply_text(plain, reply_markup=rm)
                else:
                    await update_or_query.edit_message_text(plain, reply_markup=rm)
            except BadRequest as e:
                if "not modified" not in str(e).lower():
                    logger.warning(f"Fallback edit failed: {e}")
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
            [
                InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_pos_{signal.asset}_{signal.side}"),
                InlineKeyboardButton("🏁 Close", callback_data=f"close_pos_{signal.asset}_{signal.side}"),
            ]
        ]
        await self.app.bot.send_message(
            chat_id=self.chat_id,
            text=text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def _refresh_position_callback(self, query):
        """Callback for the 'Refresh' button on a position card."""
        try:
            parts = query.data.split('_')
            # refresh_pos_BTC_LONG
            asset = parts[2]
            side = parts[3]
            
            from trading.lighter_client import lighter_wrapper
            from utils.config import LIGHTER_ACCOUNT_INDEX
            
            account_api = lighter.AccountApi(lighter_wrapper.api_client)
            acc_info = await account_api.account(by="index", value=str(LIGHTER_ACCOUNT_INDEX))
            
            if not acc_info.accounts:
                await query.answer("Account not found.")
                return

            account = acc_info.accounts[0]
            
            target_pos = None
            for pos in (account.positions or []):
                if pos.symbol.upper().startswith(asset.upper()):
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

            # Detect TP/SL from active orders
            tp_price, sl_price = 0.0, 0.0
            try:
                auth_token = lighter_wrapper.get_auth_token()
                orders_resp = await order_api.account_active_orders_without_preload_content(
                    account_index=LIGHTER_ACCOUNT_INDEX, market_id=target_pos.market_id, auth=auth_token
                )
                orders_raw = await orders_resp.json()
                market_orders = [o for o in orders_raw.get('orders', []) if o.get('market_id') == target_pos.market_id]
                is_long = float(target_pos.position) > 0
                tp_price, sl_price = self._detect_tp_sl_from_orders(market_orders, is_long)
            except Exception: pass

            text = self._format_position_card_text_refresh(target_pos, mark_price, tp_price, sl_price)
            
            keyboard = [
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data=query.data),
                    InlineKeyboardButton("🏁 Close", callback_data=f"close_pos_{asset}_{side}"),
                ]
            ]
            await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
            await query.answer("Updated!")
            
        except Exception as e:
            logger.error(f"Refresh error: {e}")
            await query.answer("Error refreshing.")

    async def _close_position_callback(self, query):
        """Callback for the 'Close' button on a position card."""
        try:
            parts = query.data.split('_')
            asset = parts[2]
            side = parts[3]
            is_long = (side == 'LONG')
            
            from trading.risk_manager import close_position_market
            
            await query.edit_message_text(f"⏳ Closing {side} position for {asset}...", parse_mode='Markdown')
            
            success = await close_position_market(asset, is_long)
            if success:
                await query.edit_message_text(
                    f"✅ *Position Closed* — {asset} {side}",
                    parse_mode='Markdown',
                    reply_markup=self._get_main_menu_keyboard()
                )
            else:
                keyboard = [[InlineKeyboardButton("🔄 Retry", callback_data=query.data)]]
                await query.edit_message_text(
                    f"❌ Failed to close {asset}. Check logs.",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        except Exception as e:
            logger.error(f"Close position callback error: {e}")
            await query.answer("Error closing position.")

    def _format_position_card_text(self, signal, entry, mark_price, size):
        pnl = 0.0
        pnl_pct = 0.0
        side_emoji = "🟢 LONG" if signal.side == 'LONG' else "🔴 SHORT"
        
        # Calculate TP/SL estimated PnL
        pos_value = signal.size * signal.leverage
        tp_pnl = abs(signal.tp - entry) / entry * pos_value if entry > 0 and signal.tp and signal.tp > 0 else 0
        sl_pnl = abs(signal.sl - entry) / entry * pos_value if entry > 0 and signal.sl and signal.sl > 0 else 0
        
        text = (
            f"⚡ *Position Opened:* {signal.asset}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"├ Type: {side_emoji} ×{signal.leverage}x\n"
            f"├ Entry: `${entry:,.2f}`\n"
            f"├ Mark: `${mark_price:,.2f}`\n"
            f"├ PnL: `${pnl:+.2f}` ({pnl_pct:+.2f}%)\n"
        )
        if signal.tp and signal.tp > 0:
            tp_pips = abs(signal.tp - entry)
            text += f"├ 🎯 TP: `${signal.tp:,.2f}` ({tp_pips:,.0f}p) → +`${tp_pnl:,.2f}`\n"
        if signal.sl and signal.sl > 0:
            sl_pips = abs(signal.sl - entry)
            text += f"├ 🛑 SL: `${signal.sl:,.2f}` ({sl_pips:,.0f}p) → -`${sl_pnl:,.2f}`\n"
        
        text += (
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
        pnl_emoji = "✅" if u_pnl >= 0 else "❌"
        
        # Est Profit at TP/SL
        pos_value = margin * leverage
        tp_pnl = abs(tp - entry) / entry * pos_value if entry > 0 and tp > 0 else 0
        sl_pnl = abs(sl - entry) / entry * pos_value if entry > 0 and sl > 0 else 0
        tp_pct = abs(tp - entry) / entry * leverage * 100 if entry > 0 and tp > 0 else 0
        sl_pct = abs(sl - entry) / entry * leverage * 100 if entry > 0 and sl > 0 else 0
        
        text = (
            f"📈 *Active Position:* {pos.symbol}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"├ Type: {side_str} ×{leverage}x\n"
            f"├ Entry: `${entry:,.2f}`\n"
            f"├ Mark: `${mark:,.2f}`\n"
            f"├ {pnl_emoji} PnL: *`${u_pnl:+.2f}`* ({pnl_pct:+.2f}%)\n"
        )
        if tp > 0:
            tp_pips = abs(tp - entry)
            text += f"├ 🎯 TP: `${tp:,.2f}` ({tp_pips:,.0f}p) → +`${tp_pnl:,.2f}` (+{tp_pct:.1f}%)\n"
        if sl > 0:
            sl_pips = abs(sl - entry)
            text += f"├ 🛑 SL: `${sl:,.2f}` ({sl_pips:,.0f}p) → -`${sl_pnl:,.2f}` (-{sl_pct:.1f}%)\n"
        
        if tp == 0 and sl == 0:
            text += f"└ ⚠️ No TP/SL detected\n"
            
        text += (
            f"━━━━━━━━━━━━━━━━━━\n"
            f"_Last Updated: {time.strftime('%H:%M:%S')}_"
        )
        return text

    async def send_message(self, text: str):
        if self.chat_id:
            try:
                await self.app.bot.send_message(chat_id=self.chat_id, text=text, parse_mode='Markdown')
                logger.debug(f"Telegram notification sent to chat_id {self.chat_id}")
            except Exception as e:
                logger.warning(f"Failed to send Telegram notification to {self.chat_id}: {e}")
        else:
            logger.warning("Telegram send_message skipped: No chat_id set (no user has interacted or ALLOWED_TELEGRAM_USER_IDS is empty).")

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
