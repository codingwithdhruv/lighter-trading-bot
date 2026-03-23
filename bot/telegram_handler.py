import asyncio
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
import time

# Conversation states
(ASSET, CONDITION, PRICE, SIDE, SIZE, LEVERAGE, TP, SL, CONFIRM) = range(9)

class TelegramBotHandler:
    def __init__(self, on_signal_callback: Callable[[TradeSignal], Awaitable[None]], app_context=None):
        self.on_signal_callback = on_signal_callback
        self.app_context = app_context # BotApplication instance for data access
        self.app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        self.chat_id = None 

        # 1. Register all handlers
        self.app.add_handler(CommandHandler("start", self._auth_wrapper(self._start_command)))
        self.app.add_handler(CommandHandler("status", self._auth_wrapper(self._status_command)))
        self.app.add_handler(CommandHandler("balance", self._auth_wrapper(self._balance_command)))
        self.app.add_handler(CommandHandler("long", self._auth_wrapper(self._long_command)))
        self.app.add_handler(CommandHandler("short", self._auth_wrapper(self._short_command)))
        self.app.add_handler(CommandHandler("help", self._auth_wrapper(self._help_command)))
        self.app.add_handler(CallbackQueryHandler(self._auth_wrapper(self._button_callback)))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._auth_wrapper(self._handle_message)))

    def _auth_wrapper(self, func):
        """Decorator to restrict access to allowed users only."""
        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user_id = update.effective_user.id
            if ALLOWED_TELEGRAM_USER_IDS and user_id not in ALLOWED_TELEGRAM_USER_IDS:
                logger.warning(f"Unauthorized access attempt by user {user_id}")
                if update.message:
                    await update.message.reply_text("⛔ *Unauthorized.* You do not have permission to use this bot.", parse_mode='Markdown')
                elif update.callback_query:
                    await update.callback_query.answer("⛔ Unauthorized Access", show_alert=True)
                return
            return await func(update, context)
        return wrapped

    def _get_main_menu_keyboard(self):
        keyboard = [
            [
                InlineKeyboardButton("📸 LONG Template", callback_data='tpl_long'),
                InlineKeyboardButton("📉 SHORT Template", callback_data='tpl_short'),
            ],
            [
                InlineKeyboardButton("📊 Status", callback_data='status'),
                InlineKeyboardButton("💰 Balance", callback_data='balance'),
            ],
            [
                InlineKeyboardButton("🛑 Stop All", callback_data='stop_all'),
                InlineKeyboardButton("🔄 Refresh", callback_data='refresh'),
            ],
        ]
        return InlineKeyboardMarkup(keyboard)

    # --- TEMPLATE COMMANDS ---
    async def _long_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        tpl = "BTC > 70000\nSIDE: LONG\nSIZE: 2\nLEV: 40\nTP: 71000\nSL: 69500"
        await update.message.reply_text(f"`{tpl}`", parse_mode='Markdown')

    async def _short_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        tpl = "BTC < 68000\nSIDE: SHORT\nSIZE: 2\nLEV: 40\nTP: 67000\nSL: 68500"
        await update.message.reply_text(f"`{tpl}`", parse_mode='Markdown')

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
        elif data == 'status': await self._show_status(query)
        elif data == 'balance': await self._show_balance(query)
        elif data == 'stop_all': await self._stop_all(query)
        elif data == 'refresh':
            await query.edit_message_text("Dashboard Refreshed.", reply_markup=self._get_main_menu_keyboard())

    # --- REST OF HANDLERS ---
    async def _start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.chat_id = update.effective_chat.id
        logger.info(f"Telegram bot started by chat_id: {self.chat_id}")
        
        welcome_text = (
            "🚀 *Lighter Trap Trading Bot* 🚀\n\n"
            "Ready for action. Send a signal directly, or use templates below.\n"
            "Commands: /long, /short, /status, /balance"
        )
        await update.message.reply_text(
            welcome_text, 
            reply_markup=self._get_main_menu_keyboard(),
            parse_mode='Markdown'
        )

    async def _help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "📖 *Usage Guide*\n\n"
            "1. Use `/long` or `/short` to get a template.\n"
            "2. Copy and Edit the template.\n"
            "3. Send it back to the bot to activate.\n\n"
            "*Global Controls:*\n"
            "/start - Dashboard\n"
            "/status - View monitoring\n"
            "/stop - Stop all"
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def _status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._show_status(update)

    async def _balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._show_balance(update)

    async def _show_status(self, update_or_query):
        if not self.app_context or not self.app_context.market_listener:
            text = "⚠️ System not fully initialized."
        else:
            signals = self.app_context.market_listener.get_active_signals()
            if not signals:
                text = "📊 *Current Status:*\nNo active signals."
            else:
                text = "📊 *Current Status:*\n"
                for i, sig in enumerate(signals):
                    import time
                    remaining = max(0, (sig.expiry_at - int(time.time())) // 60)
                    text += f"{i+1}. {sig.asset} {sig.condition_type} {sig.condition_price} ({sig.side}) - {remaining}m left\n"

        if hasattr(update_or_query, 'message') and update_or_query.message:
            await update_or_query.message.reply_text(text, parse_mode='Markdown', reply_markup=self._get_main_menu_keyboard())
        else:
            await update_or_query.edit_message_text(text, parse_mode='Markdown', reply_markup=self._get_main_menu_keyboard())

    async def _show_balance(self, update_or_query):
        from trading.lighter_client import lighter_wrapper
        text = "💰 *Balance:*\n"
        try:
            account_api = lighter.AccountApi(lighter_wrapper.api_client)
            from utils.config import LIGHTER_ACCOUNT_INDEX
            acc_info = await account_api.account(by="index", value=str(LIGHTER_ACCOUNT_INDEX))
            if acc_info.accounts:
                for asset in acc_info.accounts[0].assets:
                    if asset.asset_id == 3: text += f"USDC Available: `${float(asset.available_amount) / 1e6:,.2f}`\n"
            else: text += "No account found."
        except Exception as e: text += f"Error: {e}"

        if hasattr(update_or_query, 'message') and update_or_query.message:
            await update_or_query.message.reply_text(text, parse_mode='Markdown', reply_markup=self._get_main_menu_keyboard())
        else:
            await update_or_query.edit_message_text(text, parse_mode='Markdown', reply_markup=self._get_main_menu_keyboard())

    async def _stop_all(self, query):
        if self.app_context and self.app_context.market_listener:
            self.app_context.market_listener.clear_signals()
            await query.edit_message_text("🛑 Stopped all signals.", reply_markup=self._get_main_menu_keyboard())

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        signal = parse_signal(text)
        if not signal:
            await update.message.reply_text("❌ Unknown command or malformed signal.")
            return
            
        await update.message.reply_text(f"✅ *ACTIVE:* {signal.asset} {signal.side} if {signal.condition_type} {signal.condition_price}")
        asyncio.create_task(self.on_signal_callback(signal))

    async def send_message(self, text: str):
        if self.chat_id:
            try: await self.app.bot.send_message(chat_id=self.chat_id, text=text, parse_mode='Markdown')
            except: pass

    async def start(self):
        logger.info("Starting up Telegram Bot Polling (Ultra-Fast Mode)...")
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()
        
    async def stop(self):
        logger.info("Stopping Telegram Bot...")
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()
