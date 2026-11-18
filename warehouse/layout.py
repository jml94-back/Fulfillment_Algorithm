"""창고 레이아웃: 통로/랙 격자와 적재 슬롯 정의.

좌표계
------
- 바닥 격자 (x, y): x는 가로(통로 방향과 수직), y는 세로(통로 방향).
- 슬롯 (x, y, level): 랙 셀 (x, y)의 level 층. level 0 = 바닥층.
- Unity 연동 시 셀 1칸 = cell_size 미터로 변환하면 된다 (to_world 참고).

기본 형태 (rack_blocks=2, rack_length=4 예시)::

    y=6  . . . . . . .    <- 뒤쪽 교차 통로
    y=5  . R R . R R .
    y=4  . R R . R R .
    y=3  . R R . R R .
    y=2  . R R . R R .
    y=1  . . . . . . .    <- 앞쪽 교차 통로
    y=0  D I . . . P P    <- 스테이징 열: D = 출고(입출고 기준) 도크, I = 입고 도크, P = AMR 대기 칸
         x=0 1 2 3 4 5 6

통로는 x % 3 == 0 인 열이고, 각 랙 셀은 바로 옆 통로 칸에서 접근한다.
스테이징 열은 AMR이 대기·충전하는 칸이라 대기 중인 AMR이 교차 통로를 막지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

Cell = tuple[int, int]
Slot = tuple[int, int, int]


@dataclass
class WarehouseLayout:
    # 기본값 = 배치 비교 실험(experiments/layout_compare.py)의 추천안
    rack_blocks: int = 6          # 랙 블록(양면 랙 2열) 개수
    rack_length: int = 10         # 랙 길이(셀 수)
    levels: int = 4               # 랙 층 수
    cell_size: float = 1.2        # 셀 1칸의 실제 길이(m), Unity 변환용
    level_cost: float = 1.5       # 한 층 올라갈 때 추가 비용(이동 칸 환산)
    cross_aisles: int = 1         # 랙 중간을 가로지르는 교차 통로 수 (랙 셀 수는 그대로)
    dock_position: str = "center" # "corner" = 왼쪽 끝, "center" = 앞면 가운데
    n_parking: int = 8            # AMR 대기 칸 수 (스테이징 열 오른쪽 끝부터)
    # 랙 허용 하중 (검수 하중 센서로 잰 무게와 비교). 층이 높을수록 낮다.
    level_max_kg: tuple[float, ...] = (1500, 1200, 1000, 800)
    bay_max_kg: float = 3200      # 랙 한 칸(4개 층 합계) 허용 하중

    dock: Cell = field(init=False)           # 출고 도크 (적재 전략의 거리 기준점)
    inbound_dock: Cell = field(init=False)   # 입고 도크
    width: int = field(init=False)
    height: int = field(init=False)
    _racks: set[Cell] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.width = self.rack_blocks * 3 + 1
        self.height = self.rack_length + self.cross_aisles + 3
        # 교차 통로를 랙 구간 사이에 고르게 배치
        k = self.cross_aisles
        seg = [self.rack_length // (k + 1) + (1 if i < self.rack_length % (k + 1) else 0) for i in range(k + 1)]
        rack_rows, y = [], 2
        self.cross_rows: list[int] = []
        for i, n in enumerate(seg):
            rack_rows += range(y, y + n)
            y += n
            if i < k:
                self.cross_rows.append(y)
                y += 1
        self._racks = {(x, y) for x in range(self.width) for y in rack_rows if x % 3 != 0}

        if self.dock_position == "center":
            mid = (self.width // 2) // 3 * 3          # 가운데에 가장 가까운 통로 열
            self.dock, self.inbound_dock = (mid, 0), (mid + 1, 0)
        elif self.dock_position == "corner":
            self.dock, self.inbound_dock = (0, 0), (1, 0)
        else:
            raise ValueError(f"unknown dock_position {self.dock_position}")
        free = [(x, 0) for x in reversed(range(self.width)) if (x, 0) not in (self.dock, self.inbound_dock)]
        self.parking: list[Cell] = free[: self.n_parking]
        for c in (self.dock, self.inbound_dock, *self.parking):
            if not self.is_walkable(c) or c[1] != 0:
                raise ValueError(f"{c} must be a walkable cell on the staging row (y=0)")
        if {self.dock, self.inbound_dock} & set(self.parking):
            raise ValueError("parking overlaps a dock")

    # ---- 격자 질의 -------------------------------------------------
    def in_bounds(self, c: Cell) -> bool:
        return 0 <= c[0] < self.width and 0 <= c[1] < self.height

    def is_rack(self, c: Cell) -> bool:
        return c in self._racks

    def is_walkable(self, c: Cell) -> bool:
        return self.in_bounds(c) and c not in self._racks

    def neighbors(self, c: Cell) -> list[Cell]:
        x, y = c
        cand = [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
        return [n for n in cand if self.is_walkable(n)]

    # ---- 슬롯 --------------------------------------------------------
    @property
    def slots(self) -> list[Slot]:
        return [
            (x, y, lv)
            for (x, y) in sorted(self._racks)
            for lv in range(self.levels)
        ]

    def access_point(self, slot: Slot | Cell) -> Cell:
        """슬롯을 넣고 뺄 때 AMR이 서는 통로 칸."""
        x, y = slot[0], slot[1]
        return (x - 1, y) if x % 3 == 1 else (x + 1, y)

    def to_world(self, c: Cell | Slot) -> tuple[float, float, float]:
        """Unity 월드 좌표 (x, height, z). y축이 높이인 Unity 관례를 따른다."""
        lv = c[2] if len(c) == 3 else 0
        return (c[0] * self.cell_size, lv * self.cell_size, c[1] * self.cell_size)

    def ascii(self, marks: dict[Cell, str] | None = None) -> str:
        marks = marks or {}
        rows = []
        for y in reversed(range(self.height)):
            row = []
            for x in range(self.width):
                c = (x, y)
                if c in marks:
                    row.append(marks[c])
                elif c == self.dock:
                    row.append("D")
                elif c == self.inbound_dock:
                    row.append("I")
                elif c in self.parking:
                    row.append("P")
                else:
                    row.append("R" if self.is_rack(c) else ".")
            rows.append(" ".join(row))
        return "\n".join(rows)

    def to_dict(self) -> dict:
        """Unity 팀원에게 넘길 레이아웃 JSON."""
        return {
            "width": self.width,
            "height": self.height,
            "levels": self.levels,
            "cell_size": self.cell_size,
            "dock": list(self.dock),
            "inbound_dock": list(self.inbound_dock),
            "parking": [list(c) for c in self.parking],
            "cross_rows": self.cross_rows,
            "racks": [list(c) for c in sorted(self._racks)],
        }
