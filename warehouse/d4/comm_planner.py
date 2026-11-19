"""MQTT 스케줄러(comm/sim/scheduler_bridge.py)에 D4 알고리즘을 끼우는 어댑터.

    from warehouse.d4.comm_planner import D4Planner
    Scheduler(host, port, planner=D4Planner())          # comm/ 를 PYTHONPATH 에 넣고 실행

Unity 구현(2026-11-19 팀원 명세)에서는 레인(채널)·칸 선택과 리프트·모셔틀 제어를 통로마다
ShuttleRackSystem 이 맡는다. 그래서 MQTT 쪽 알고리즘이 정하는 것은 "어느 통로(R1~R3)로 보내고 어느 통로에서
꺼낼지"(명세 7장 4번 "통로 간 배분" = WMS 몫)뿐이다.
- choose_rail (입고): warehouse/d4/storage.py Storage.choose_rail 과 같은 규칙.
  같은 SKU 채널이 있는 통로 우선(채널 보충) → 진행 중 작업이 적은 통로.
- pick_rail (출고): 그 SKU 재고가 있는 통로 중 진행 중 작업이 적은 곳, 같으면 재고가 많은 곳.
choose_amr 는 스케줄러 기본(가장 가까운 놀고 있는 AMR)을 쓴다.
"""
from __future__ import annotations

from sim.scheduler_bridge import Planner        # comm/ 안의 패키지
from whmqtt import ids


class D4Planner(Planner):
    def __init__(self, *_a, **_k) -> None:
        super().__init__()

    def choose_rail(self, sku: str, weight: float, load: dict[str, int], stock: dict[str, dict[str, int]]) -> str:
        return min(ids.RAILS, key=lambda r: (0 if stock.get(r, {}).get(sku) else 1, load.get(r, 0), r))

    def pick_rail(self, sku: str, load: dict[str, int], stock: dict[str, dict[str, int]]) -> str | None:
        cands = [r for r in ids.RAILS if stock.get(r, {}).get(sku, 0) > 0]
        if not cands:
            return None
        return min(cands, key=lambda r: (load.get(r, 0), -stock[r][sku], r))
