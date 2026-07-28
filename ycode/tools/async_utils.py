"""在线程 I/O 与 asyncio 取消之间建立清理边界。"""

import asyncio
import threading
from collections.abc import Callable
from contextlib import suppress


class ThreadOperationCancelled(Exception):
    """线程操作观察到外层取消。"""


async def run_cancellable_thread[ResultT](
    operation: Callable[[threading.Event], ResultT],
) -> ResultT:
    cancelled = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(operation, cancelled))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancelled.set()
        with suppress(Exception):
            await asyncio.shield(task)
        raise


def check_thread_cancelled(cancelled: threading.Event) -> None:
    if cancelled.is_set():
        raise ThreadOperationCancelled
