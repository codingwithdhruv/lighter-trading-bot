import asyncio
from pacifica.execution import pacifica_executor
from decibel.execution import decibel_executor
from core.config_manager import config_manager
from utils.logger import logger

from typing import Callable, Awaitable

class CopyEngine:
    def __init__(self, notification_callback: Callable[[str], Awaitable[None]] = None):
        self.notification_callback = notification_callback

    async def process_copy_signal(self, symbol: str, side: str, sl_pips: float, tp_pips: float):
        # TP/SL pips come strictly from the Lighter position's actual TP/SL orders.
        # If Lighter has no TP/SL set, copy targets will also have none.
        if sl_pips <= 0:
            logger.info(f"No SL detected on Lighter for {symbol}. Copy targets will have no SL.")
        if tp_pips <= 0:
            logger.info(f"No TP detected on Lighter for {symbol}. Copy targets will have no TP.")

        tasks = []
        exchange_names = []

        # Route to Pacifica
        if config_manager.pacifica_enabled:
            exchange_names.append("Pacifica")
            logger.info(f"CopyEngine: Routing {side} {symbol} to Pacifica -> max_loss=${config_manager.pacifica_max_loss_usd}, lev={config_manager.pacifica_leverage}x")
            tasks.append(pacifica_executor.execute_copy_trade(
                symbol=symbol,
                side=side,
                sl_pips=sl_pips,
                max_loss_usd=config_manager.pacifica_max_loss_usd,
                leverage=config_manager.pacifica_leverage,
                tp_pips=tp_pips,
                notification_callback=self.notification_callback
            ))

        # Route to Decibel
        if config_manager.decibel_enabled:
            exchange_names.append("Decibel")
            logger.info(f"CopyEngine: Routing {side} {symbol} to Decibel -> max_loss=${config_manager.decibel_max_loss_usd}, lev={config_manager.decibel_leverage}x")
            tasks.append(decibel_executor.execute_copy_trade(
                symbol=symbol,
                side=side,
                sl_pips=sl_pips,
                max_loss_usd=config_manager.decibel_max_loss_usd,
                leverage=config_manager.decibel_leverage,
                tp_pips=tp_pips,
                notification_callback=self.notification_callback
            ))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, res in enumerate(results):
                ex_name = exchange_names[i]
                if isinstance(res, Exception):
                    err_msg = f"❌ {ex_name} Copy Failed: {res}"
                    logger.error(f"CopyEngine task {i} failed: {res}")
                    if self.notification_callback:
                        await self.notification_callback(err_msg)
                elif res is False:
                    err_msg = f"❌ {ex_name} Execution Failed (check logs)"
                    if self.notification_callback:
                        await self.notification_callback(err_msg)
                else:
                    if self.notification_callback:
                        await self.notification_callback(f"✅ {ex_name} Copy Successful")
        else:
            logger.info("CopyEngine: No copy targets enabled.")

    async def process_close_signal(self, symbol: str, side: str, percent_closed: float):
        tasks = []
        exchange_names = []

        if config_manager.pacifica_enabled:
            exchange_names.append("Pacifica")
            logger.info(f"CopyEngine: Routing close signal for {symbol} to Pacifica ({percent_closed*100:.1f}%)")
            tasks.append(pacifica_executor.execute_close_trade(
                symbol=symbol,
                side=side,
                percent_closed=percent_closed,
                notification_callback=self.notification_callback
            ))

        if config_manager.decibel_enabled:
            exchange_names.append("Decibel")
            logger.info(f"CopyEngine: Routing close signal for {symbol} to Decibel ({percent_closed*100:.1f}%)")
            tasks.append(decibel_executor.execute_close_trade(
                symbol=symbol,
                side=side,
                percent_closed=percent_closed,
                notification_callback=self.notification_callback
            ))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, res in enumerate(results):
                ex_name = exchange_names[i]
                if isinstance(res, Exception):
                    err_msg = f"❌ {ex_name} Close Failed: {res}"
                    logger.error(f"CopyEngine close task {i} failed: {res}")
                    if self.notification_callback:
                        await self.notification_callback(err_msg)
                elif res is False:
                    err_msg = f"❌ {ex_name} Close Failed (check logs)"
                    if self.notification_callback:
                        await self.notification_callback(err_msg)
                else:
                    if self.notification_callback:
                        await self.notification_callback(f"✅ {ex_name} Close Successful")
        else:
            logger.info("CopyEngine: No copy targets enabled for close.")

copy_engine = CopyEngine()
