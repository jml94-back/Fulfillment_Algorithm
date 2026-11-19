"""알고리즘 코어(저장소 루트의 warehouse/)를 MQTT 쪽에 끼우는 어댑터.

의존 방향은 comm → 코어 한쪽뿐이다. 코어(warehouse/, plans/)는 paho-mqtt·whmqtt·sim 을 import 하지 않으며,
comm/ 에서 코어를 쓰는 코드는 모두 이 패키지에 모은다. comm/ 만 떼어 낼 때는 이 패키지와
코어 패키지(pip 설치 또는 PYTHONPATH)만 이어 주면 된다.

comm/ 을 작업 폴더로 실행하는 기존 방식(python -m sim.run_demo)을 그대로 쓰기 위해,
코어가 아직 import 되지 않으면 저장소 루트를 sys.path 뒤쪽에 붙인다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

if importlib.util.find_spec("warehouse") is None:
    sys.path.append(str(Path(__file__).resolve().parents[2]))
