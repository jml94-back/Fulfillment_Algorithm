"""확정 배치안 D4를 알고리즘이 쓰는 데이터로 옮긴 것 (치수는 plans/d_plan.py 와 같다).

창고 120 x 80 m. 남쪽 벽부터:
- y 0~14.4    입고 구역(x 0~40) · 사무실(40~80) · 출고 구역(80~120). 도크 3+3 사이 가운데 예비 구역.
               도크마다 계량대(입고) / 하차대(출고)가 y 10.6~13 에 있고, AMR은 그 바로 북쪽 칸에서 팔레트를 주고받는다.
- y 14.4~19.2 AMR 통로 (동서 방향, 폭 4.8 m)
- y 19.2~21.6 리프트 줄: 레일마다 리프트 1기, 서쪽에 입고 버퍼 5칸, 동쪽에 출고 버퍼 5칸
- y 21.6~31.4 검수·지원 띠: 서벽 충전 3 + 정비 / 입고 검수·보류 / 포장·출고 검수 / 예비 부품 + 동벽 충전 3
- y 31.4~78.8 보관: 모셔틀 레일 3개(x 21.8, 60, 98.2), 레일 양쪽 레인 33줄, 깊이 14, 10층

AMR 바닥 격자는 1.2 m 칸(AMR 1.2 m/s → 1칸 = 1초). 걸을 수 있는 칸만 열어 둔다.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from dataclasses import dataclass, field

from plans import d_plan as P

Cell = tuple[int, int]
CELL = 1.2
LEVELS, LEVEL_H = P.LEVELS, P.LEVEL_H          # 10단, 단 간격 1.45 m (Unity ShuttleRackSystem)
DEPTH, SLOT, LANE_W = P.DEPTH, P.SLOT_D4, P.LANE   # 레인 칸 간격 1.25 m
BUF_PITCH = P.BUF_PITCH
PD_BUF = P.PD_BUF
# 층별 허용 하중 (kg/팔레트). 높은 층일수록 낮다.
LEVEL_MAX_KG = (1500, 1500, 1500, 1200, 1200, 1200, 1000, 1000, 1000, 1000)


def col(x: float) -> int:
    return int(math.floor(x / CELL + 1e-9))


def cx(c: int) -> float:
    """칸 중심 x (m)."""
    return (c + .5) * CELL


@dataclass(frozen=True)
class Lane:
    idx: int
    rail: int
    side: int           # 0 = 레일 서쪽, 1 = 레일 동쪽
    level: int
    row: int            # 0 = 남쪽(리프트 쪽) 첫 줄
    y: float            # 레인 중심 y (m)


@dataclass
class Lift:
    rail: int
    x: float                     # 리프트 중심 x
    in_drop: Cell                # AMR이 입고 버퍼에 팔레트를 내려놓는 칸 (통로 북쪽 줄)
    out_pick: Cell               # AMR이 출고 버퍼에서 팔레트를 집는 칸


@dataclass
class Dock:
    idx: int
    kind: str                    # "in" / "out"
    x: float
    cell: Cell                   # AMR이 서는 칸 (계량대·하차대 바로 북쪽)


@dataclass
class D4Layout:
    width_m: float = P.W
    height_m: float = P.H
    cols: int = field(init=False)
    rows: int = field(init=False)
    lanes: list[Lane] = field(init=False)
    lifts: list[Lift] = field(init=False)
    in_docks: list[Dock] = field(init=False)
    out_docks: list[Dock] = field(init=False)
    hold_cell: Cell = field(init=False)          # 입고 검수·보류 구역 입구
    chargers: list[Cell] = field(init=False)     # 벽면 충전기 앞 AMR 자리
    waiting: list[Cell] = field(init=False)      # 충전 안 할 때 쉬는 자리
    _walk: set[Cell] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        g = P.geometry()
        self.rails = g["rails"]
        self.rail_x = [sum(r["rail"]) / 2 for r in self.rails]
        self.n_rows = g["lanes_per_side"]
        self.cols = col(self.width_m)                       # 100
        self.rows = col(P.BAND[1])                          # y 0~31.2 → 26줄 (0~25)
        aisle_rows = range(col(P.AISLE[0]), col(P.AISLE[1]))        # 12~15
        lift_rows = range(col(P.LIFT_ROW[0]), col(P.LIFT_ROW[1]))   # 16~17
        band_rows = range(col(P.BAND[0]), self.rows)                # 18~25
        walk: set[Cell] = set()
        for c in range(self.cols):
            for r in [*aisle_rows, *lift_rows, *band_rows]:
                walk.add((c, r))
        # 리프트 + 버퍼 (리프트 줄)
        blocked: set[Cell] = set()
        for x in self.rail_x:
            for c in range(col(x - 1.2 - PD_BUF * BUF_PITCH), col(x + 1.2 + PD_BUF * BUF_PITCH - 1e-6) + 1):
                for r in lift_rows:
                    blocked.add((c, r))
        # 레일은 검수 띠를 지나 리프트까지 내려온다: 레일 칸은 AMR이 건너지 않는다
        for rl in self.rails:
            ra, rb = rl["rail"]
            for c in range(col(ra), col(rb - 1e-6) + 1):
                for r in [*lift_rows, *band_rows]:
                    blocked.add((c, r))
        # 검수 띠의 시설 상자 (d_plan 과 같은 규칙). AMR은 띠 남쪽 줄(리프트 줄)에서 드나든다.
        edges = [0.0] + [v for rl in self.rails for v in rl["rail"]] + [self.width_m]
        segs = [(edges[i], edges[i + 1]) for i in range(0, len(edges), 2)]
        boxes = [(segs[0][0] + 10.5, segs[0][1] - .8), (segs[1][0] + .8, segs[1][1] - .8),
                 (segs[2][0] + .8, segs[2][1] - .8), (segs[3][0] + .8, segs[3][1] - 10.5)]
        for a, b in boxes:
            for c in range(col(a), col(b - 1e-6) + 1):
                for r in band_rows:
                    blocked.add((c, r))
        walk -= blocked
        # 도크 칸 (계량대·하차대 북쪽, 통로 바로 아래 줄)
        dock_row = col(P.AISLE[0]) - 1                                   # 11
        self.in_docks = [Dock(i, "in", x, (col(x), dock_row)) for i, x in enumerate(P.IN_X)]
        self.out_docks = [Dock(i, "out", x, (col(x), dock_row)) for i, x in enumerate(P.OUT_X)]
        for d in [*self.in_docks, *self.out_docks]:
            walk.add(d.cell)
        self._walk = walk
        # 리프트 버퍼 접점: 통로 북쪽 줄(15)에서, 입고 버퍼 서쪽 끝 / 출고 버퍼 동쪽 끝
        top = aisle_rows[-1]
        self.lifts = [Lift(i, x, (col(x - 1.2 - PD_BUF * BUF_PITCH + .6), top), (col(x + 1.2 + PD_BUF * BUF_PITCH - .6), top))
                      for i, x in enumerate(self.rail_x)]
        # 검수·보류 구역 입구: 상자 남쪽 리프트 줄 가운데
        a, b = boxes[1]
        self.hold_cell = (col((a + b) / 2), lift_rows[-1])
        # 벽면 충전기 (서벽 3, 동벽 3): 충전 단자 앞 칸
        ys = [P.BAND[0] + .8 + .2 + k * 2.9 + 1.0 for k in range(3)]
        self.chargers = [(1, col(y)) for y in ys] + [(self.cols - 2, col(y)) for y in ys]   # CHW1~3, CHE1~3
        # 쉬는 자리: 리프트 버퍼 사이 리프트 줄 (통로를 막지 않는 곳)
        self.waiting = [(col(x), lift_rows[0]) for x in (33.0, 36.0, 84.0, 87.0)]
        for c in [self.hold_cell, *self.chargers, *self.waiting]:
            assert c in self._walk, c
        # 레인
        lanes = []
        for r in range(len(self.rails)):
            for lv in range(LEVELS):
                for side in (0, 1):
                    for row in range(self.n_rows):
                        lanes.append(Lane(len(lanes), r, side, lv, row, P.LANE_Y0 + (row + .5) * LANE_W))
        self.lanes = lanes
        self.lift_y = (P.LIFT_ROW[0] + P.LIFT_ROW[1]) / 2       # 리프트 중심 y
        self.xfer_y = P.SHUTTLE_XFER_Y                          # 모셔틀이 리프트와 팔레트를 주고받는 y (리프트 + 2.0 m)

        self.contacts = self._contacts()

    # ---- ID (Unity 배치 데이터 unity/d4_layout.json 과 같은 이름) ----
    @staticmethod
    def amr_id(i: int) -> str:
        return f"AMR{i + 1:02d}"

    @staticmethod
    def lift_id(r: int) -> str:
        return f"L{r + 1}"

    @staticmethod
    def rail_id(r: int) -> str:
        return f"R{r + 1}"

    @staticmethod
    def shuttle_id(r: int, lv: int) -> str:
        """Unity 모셔틀 오브젝트 Rack_R{r}/.../Shuttle_L{단} 을 통로 이름과 붙인 것."""
        return f"R{r + 1}-Shuttle_L{lv + 1:02d}"

    @staticmethod
    def lane_id(ln: Lane) -> str:
        """Unity ShuttleRackSystem 채널 이름 R{통로}-{W|E}{줄}-L{단} (줄 01 = 리프트에 가장 가까운 남쪽 줄)."""
        return f"R{ln.rail + 1}-{'WE'[ln.side]}{ln.row + 1:02d}-L{ln.level + 1:02d}"

    @staticmethod
    def buffer_id(r: int, kind: str) -> str:
        """입고·출고 버퍼 = Unity 위치 문자열 R1-IN / R1-OUT."""
        return f"R{r + 1}-{kind.upper()}"

    def _contacts(self) -> dict[Cell, tuple[str, tuple[float, float, float]]]:
        """AMR 접점 칸 → (ID, Unity 좌표). unity/d4_layout.json 이 있으면 그 좌표를 쓴다(격자 중심보다 정확)."""
        names: dict[Cell, str] = {}
        for d in self.in_docks:
            names[d.cell] = f"IN{d.idx + 1}"
        for d in self.out_docks:
            names[d.cell] = f"OUT{d.idx + 1}"
        for l in self.lifts:
            names[l.in_drop] = self.buffer_id(l.rail, "in")
            names[l.out_pick] = self.buffer_id(l.rail, "out")
        names[self.hold_cell] = "QC-IN"
        for i, c in enumerate(self.chargers):
            names[c] = f"CH{'WE'[i // 3]}{i % 3 + 1}"
        for i, c in enumerate(self.waiting):
            names[c] = f"WAIT{i + 1}"
        exact: dict[str, tuple] = {}
        for path in (Path(__file__).resolve().parents[2] / "unity" / "d4_layout.json",):
            if path.exists():
                u = json.loads(path.read_text())
                for d in u.get("docks", []):
                    exact[d["id"]] = tuple(d["amr_contact"])
                for b in u.get("buffers", []):
                    exact[b["id"]] = tuple(b["amr_contact"])
                for f in u.get("facilities", []):
                    exact[f["id"]] = tuple(f["amr_contact"])
                for c in u.get("chargers", []):
                    exact[c["id"]] = tuple(c["park_center"])
        return {c: (n, exact.get(n, self.to_world(c))) for c, n in names.items()}

    def point(self, c: Cell) -> tuple[float, float, float]:
        """칸의 Unity 좌표: 접점이면 정확한 접점 좌표, 아니면 칸 중심."""
        return self.contacts[c][1] if c in self.contacts else self.to_world(c)

    # ---- 격자 질의 (warehouse.pathfinding / fleet 의 계획기와 같은 인터페이스) ----
    def in_bounds(self, c: Cell) -> bool:
        return 0 <= c[0] < self.cols and 0 <= c[1] < self.rows

    def is_walkable(self, c: Cell) -> bool:
        return c in self._walk

    def neighbors(self, c: Cell) -> list[Cell]:
        x, y = c
        return [n for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)) if n in self._walk]

    @property
    def capacity(self) -> int:
        return len(self.lanes) * DEPTH

    def to_world(self, c: Cell) -> tuple[float, float, float]:
        """Unity 좌표 (x, 높이, z), 칸 중심. Unity 는 y 축이 높이."""
        return (round(cx(c[0]), 2), 0.0, round(cx(c[1]), 2))

    def to_dict(self) -> dict:
        """Unity 팀원용 배치 데이터."""
        return {
            "units": "m", "cell_size": CELL, "width": self.width_m, "depth": self.height_m,
            "rack_height": P.RACK_H, "levels": LEVELS, "level_height": LEVEL_H, "lane_depth": DEPTH,
            "slot_pitch": SLOT, "lane_pitch": LANE_W, "lanes_per_side": self.n_rows,
            "rails": [{"x": round(x, 2), "y_from": P.LIFT_ROW[1], "y_to": P.LANE_Y0 + self.n_rows * LANE_W}
                      for x in self.rail_x],
            "lifts": [{"rail": l.rail, "x": round(l.x, 2), "y": self.lift_y,
                       "in_drop": self.to_world(l.in_drop), "out_pick": self.to_world(l.out_pick)} for l in self.lifts],
            "in_docks": [{"idx": d.idx, "x": d.x, "amr_point": self.to_world(d.cell)} for d in self.in_docks],
            "out_docks": [{"idx": d.idx, "x": d.x, "amr_point": self.to_world(d.cell)} for d in self.out_docks],
            "hold_point": self.to_world(self.hold_cell),
            "chargers": [self.to_world(c) for c in self.chargers],
            "walkable_cells": sorted([list(c) for c in self._walk]),
        }
