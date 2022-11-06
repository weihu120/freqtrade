
import asyncio
import logging
import time
from datetime import datetime
from threading import Thread
from typing import Dict, List, Set, Tuple

from freqtrade.constants import Config
from freqtrade.enums.candletype import CandleType
from freqtrade.exchange.exchange import timeframe_to_seconds


logger = logging.getLogger(__name__)


class ExchangeWS():
    def __init__(self, config: Config, ccxt_object) -> None:
        self.config = config
        self.ccxt_object = ccxt_object
        self._thread = Thread(name="ccxt_ws", target=self.start)
        self._background_tasks: Set[asyncio.Task] = set()

        self._pairs_watching: Set[Tuple[str, str, CandleType]] = set()
        self._pairs_scheduled: Set[Tuple[str, str, CandleType]] = set()
        self.pairs_last_refresh: Dict[Tuple[str, str, CandleType], float] = {}
        self.pairs_last_request: Dict[Tuple[str, str, CandleType], float] = {}
        self._thread.start()

    def start(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._loop.run_forever()

    def cleanup(self) -> None:
        logger.debug("Cleanup called - stopping")
        self._pairs_watching.clear()
        self._loop.stop()
        self._thread.join()
        logger.debug("Stopped")

    def cleanup_expired(self) -> None:
        """
        Remove pairs from watchlist if they've not been requested within
        the last timeframe (+ offset)
        """
        for p in list(self._pairs_watching):
            _, timeframe, _ = p
            timeframe_s = timeframe_to_seconds(timeframe)
            last_refresh = self.pairs_last_request.get(p, 0)
            if last_refresh > 0 and time.time() - last_refresh > timeframe_s + 20:
                logger.info(f"Removing {p} from watchlist")
                self._pairs_watching.discard(p)

    async def schedule_while_true(self) -> None:

        for p in self._pairs_watching:
            if p not in self._pairs_scheduled:
                self._pairs_scheduled.add(p)
                pair, timeframe, candle_type = p
                task = asyncio.create_task(
                    self.continuously_async_watch_ohlcv(pair, timeframe, candle_type))
                self._background_tasks.add(task)
                task.add_done_callback(self.continuous_stopped)

    def continuous_stopped(self, task: asyncio.Task):
        self._background_tasks.discard(task)
        result = task.result()
        logger.info(f"Task finished {result}")
        # self._pairs_scheduled.discard(pair, timeframe, candle_type)

    async def continuously_async_watch_ohlcv(
            self, pair: str, timeframe: str, candle_type: CandleType) -> None:

        while (pair, timeframe, candle_type) in self._pairs_watching:
            start = time.time()
            data = await self.ccxt_object.watch_ohlcv(pair, timeframe)
            self.pairs_last_refresh[(pair, timeframe, candle_type)] = time.time()
            # logger.info(
            #     f"watch done {pair}, {timeframe}, data {len(data)} in {time.time() - start:.2f}s")

    def schedule_ohlcv(self, pair: str, timeframe: str, candle_type: CandleType) -> None:
        self._pairs_watching.add((pair, timeframe, candle_type))
        self.pairs_last_request[(pair, timeframe, candle_type)] = time.time()
        # asyncio.run_coroutine_threadsafe(self.schedule_schedule(), loop=self._loop)
        asyncio.run_coroutine_threadsafe(self.schedule_while_true(), loop=self._loop)
        self.cleanup_expired()

    async def get_ohlcv(
            self, pair: str, timeframe: str, candle_type: CandleType) -> Tuple[str, str, str, List]:
        """
        Returns cached klines from ccxt's "watch" cache.
        """
        candles = self.ccxt_object.ohlcvs.get(pair, {}).get(timeframe)
        # Fake 1 candle - which is then removed again
        # TODO: is this really a good idea??
        refresh_time = int(self.pairs_last_refresh[(pair, timeframe, candle_type)] * 1000)
        candles.append([refresh_time, 0, 0, 0, 0, 0])
        logger.info(
            f"watch result for {pair}, {timeframe} with length {len(candles)}, "
            f"{datetime.fromtimestamp(candles[-1][0] // 1000)}, "
            f"lref={datetime.fromtimestamp(self.pairs_last_refresh[(pair, timeframe, candle_type)])}")
        return pair, timeframe, candle_type, candles
