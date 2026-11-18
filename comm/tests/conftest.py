import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def broker():
    """테스트 전용 mosquitto (빈 포트). mosquitto 가 없으면 건너뜀."""
    from sim.run_demo import free_port, start_broker
    port = free_port()
    try:
        p = start_broker(port)
    except RuntimeError as e:
        pytest.skip(str(e))
    yield "127.0.0.1", port
    p.terminate()
    p.wait(5)
