"""비상 상황 → 진입 금지 구역 / 사용 불가 슬롯 변환.

중앙 관제(LangGraph 에이전트)는 센서·카메라 감지 결과를 받아
`zone_for(kind, cell, layout)` 로 구역을 만들고,
- 실행 계층: FleetSimulator.run(..., events=[EmergencyEvent(t, zone.cells, kind=...)])
  → 해당 구역을 지나갈 AMR을 즉시 멈추고 우회 경로로 다시 계획, 구역 안 AMR은 밖으로 탈출
- 계획 계층: Inventory.block_slots(zone.slots)
  → 그 구역 슬롯에는 새로 적재하지 않고, 출고도 다른 위치의 같은 SKU에서 꺼냄

구역 크기는 포트폴리오용 기본값이다. 실제 현장 기준(소방 규정 등)에 맞춰 조정하면 된다.
"""
from __future__ import annotations

from dataclasses import dataclass

from .layout import Cell, Slot, WarehouseLayout

# kind: (반경(체비쇼프 거리, 칸), 설명)
RULES: dict[str, tuple[int, str]] = {
    "fire":          (3, "화재: 반경 3칸 전면 차단, 해당 구역 랙 슬롯 모두 사용 중지"),
    "rack_collapse": (2, "랙 무너짐: 반경 2칸 차단 (낙하물·잔해 범위)"),
    "rack_tilt":     (1, "랙 기울어짐: 반경 1칸 차단, 해당 랙 적재·출고 중지"),
    "leak":          (1, "상품 누수·누액: 반경 1칸 차단 (미끄럼·오염)"),
    "injury":        (2, "사람 부상: 반경 2칸 차단, 구조 인력 동선 확보"),
}


@dataclass
class Zone:
    kind: str
    origin: Cell
    cells: set[Cell]      # 진입 금지 바닥 칸 (통로 + 랙)
    slots: set[Slot]      # 사용 불가 슬롯
    description: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "origin": list(self.origin), "description": self.description,
                "cells": sorted(map(list, self.cells)), "slots": sorted(map(list, self.slots))}


def zone_for(kind: str, cell: Cell, layout: WarehouseLayout, radius: int | None = None) -> Zone:
    r, desc = RULES[kind]
    r = r if radius is None else radius
    cells = {
        (x, y)
        for x in range(cell[0] - r, cell[0] + r + 1)
        for y in range(cell[1] - r, cell[1] + r + 1)
        if layout.in_bounds((x, y))
    }
    # 도크·대기 칸은 막지 않는다 (막아야 하면 radius로 직접 지정)
    cells -= {layout.dock, layout.inbound_dock, *layout.parking}
    slots = {s for s in layout.slots if (s[0], s[1]) in cells}
    return Zone(kind, cell, cells, slots, desc)
