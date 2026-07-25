from collections.abc import Iterator

import pytest

from tests.support.sse_server import SSETestServer


@pytest.fixture
def sse_server() -> Iterator[SSETestServer]:
    with SSETestServer() as server:
        yield server
