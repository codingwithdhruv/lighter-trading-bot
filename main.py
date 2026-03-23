import asyncio
import sys

from utils.config import validate_config
from utils.logger import logger
from bot.telegram_handler import TelegramBotHandler
from bot.parser import TradeSignal
from data.market_listener import MarketListener
from trading.lighter_client import lighter_wrapper
from trading.execution import execute_trade

class BotApplication:
    def __init__(self):
        self.market_listener = None
        self.telegram_bot = None

    async def initialize(self):
        try:
            logger.info("Initializing configuration...")
            validate_config()

            logger.info("Initializing Lighter Trading SDK...")
            err = lighter_wrapper.initialize()
            if err:
                logger.error("Failed to initialize system due to Lighter setup errors.")
                sys.exit(1)

            logger.info("Setting up internal modules...")
            self.market_listener = MarketListener(execute_callback=self._on_trade_execute)
            
            # The telegram bot takes a callback for what to do when a new signal arrives
            # We pass 'self' as app_context so the bot can fetch state (balances, active signals)
            self.telegram_bot = TelegramBotHandler(
                on_signal_callback=self._on_new_signal, 
                app_context=self
            )
            
        except Exception as e:
            logger.error(f"Initialization error: {e}")
            sys.exit(1)

    async def _on_new_signal(self, signal: TradeSignal):
        """Callback from Telegram when a valid signal is received."""
        # Add to the market listener to start monitoring
        self.market_listener.add_signal(signal)
        await self.telegram_bot.send_message(f"📡 Now monitoring for condition: {signal.asset} CLOSE {signal.condition_type} {signal.condition_price}")

    async def _on_trade_execute(self, signal: TradeSignal, trigger_price: float = None) -> bool:
        """Callback from Market Listener when the condition is met."""
        alert_msg = f"⚡ Alert! Condition Met. Executing {signal.side} order for {signal.size} USDC at {signal.leverage}x leverage..."
        if trigger_price:
            alert_msg += f"\n📊 Trigger Price (5min Close): {trigger_price}"
        
        await self.telegram_bot.send_message(alert_msg)
        
        success = await execute_trade(signal, trigger_price=trigger_price)
        
        if success:
            await self.telegram_bot.send_message(f"✅ Trade executed successfully! TP and SL are set.\nTP: {signal.tp}\nSL: {signal.sl}\nCheck your Lighter positions.")
        else:
            await self.telegram_bot.send_message(f"❌ Failed to execute trade or place TP/SL. Check logs.")
            
        return success

    async def run_forever(self):
        """Run the continuous loops for Telegram and Market Listener."""
        try:
            # Start telegram polling in bg
            await self.telegram_bot.app.initialize()
            await self.telegram_bot.app.start()
            # use polling
            await self.telegram_bot.app.updater.start_polling()
            
            logger.info("Bot is active and running.")
            await self.telegram_bot.send_message("✅ Bot System Online.")

            # Start monitoring prices (blocks forever)
            await self.market_listener.start()
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            await self.shutdown()
            
    async def shutdown(self):
        logger.info("Cleaning up resources...")
        if self.telegram_bot:
            await self.telegram_bot.stop()
        if self.market_listener:
            self.market_listener.stop()
        await lighter_wrapper.close()


async def main():
    app = BotApplication()
    await app.initialize()
    await app.run_forever()

if __name__ == "__main__":
    # Ensure Windows and Unix properly handle Asyncio loops if needed
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
