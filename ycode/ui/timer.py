"""使用单调时钟测量单轮响应耗时。"""

import time
from collections.abc import Callable


class ResponseTimer:
    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self._clock = clock
        self._started_at: float | None = None
        self._stopped_at: float | None = None

    @property
    def running(self) -> bool:
        return self._started_at is not None and self._stopped_at is None

    @property
    def elapsed(self) -> float:
        if self._started_at is None:
            return 0.0
        end = self._clock() if self._stopped_at is None else self._stopped_at
        return max(0.0, end - self._started_at)

    def start(self) -> None:
        self._started_at = self._clock()
        self._stopped_at = None

    def stop(self) -> float:
        if self._started_at is None:
            return 0.0
        if self._stopped_at is None:
            self._stopped_at = self._clock()
        return self.elapsed
