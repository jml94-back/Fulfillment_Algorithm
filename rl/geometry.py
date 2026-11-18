"""D4 배치의 AMR 운행 구역(남쪽 띠) 2D 지도.

AMR 4대는 도크·계량대 ↔ 리프트 P&D 사이만 오간다(랙 안은 셔틀 담당). 그래서 RL 환경은
창고 전체가 아니라 남쪽 벽(y=0)부터 리프트 줄 끝(y=21.6)까지, 폭 120 m 띠만 다룬다.

좌표: x = 동쪽(m), y = 북쪽(m). Unity로는 (x, y) → (x, z) 로 그대로 옮기면 된다.
수치는 plans/d_plan.py 와 같다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

W, H = 120.0, 21.6                    # 운행 구역 (리프트 줄 북쪽 끝까지)
STAGE = 14.4                          # 도크 적치·계량 구역 깊이
AISLE = (14.4, 19.2)                  # AMR 통로 4.8 m
LIFT_ROW = (19.2, 21.6)
OFFICE = (40.0, 80.0)
IN_X = [2.5, 7.5, 12.5, 27.5, 32.5, 37.5]
OUT_X = [117.5, 112.5, 107.5, 92.5, 87.5, 82.5]
SPARES = [(15.5, 24.5), (95.5, 104.5)]
RAIL_CX = [21.8, 60.0, 98.2]          # 모셔틀 레일(=리프트) 중심 x
SLOT, PD_BUF = 1.3, 5                 # 리프트 버퍼 컨베이어 칸 간격·칸 수

ROBOT_R = 0.7                         # AMR + T11 팔레트(1.1 m) 외접원 근사
WORKER_R = 0.3


Rect = tuple[float, float, float, float]   # x0, y0, x1, y1


def static_rects() -> list[Rect]:
    """도면에 있는 고정 장애물. 벽은 따로 처리한다."""
    r: list[Rect] = [(OFFICE[0], 0.0, OFFICE[1], STAGE)]
    for xd in IN_X + OUT_X:
        r.append((xd - 2.35, 1.8, xd + 2.35, 10.2))       # 도크 적치 차선 4줄 x 7칸
        r.append((xd - 1.2, 10.6, xd + 1.2, 13.0))        # 계량대(입고) / 하차대(출고)
    for a, b in SPARES:
        r.append((a, 1.8, b, 10.2))                       # 도크 사이 예비 구역
    for cx in RAIL_CX:
        r.append((cx - 1.2 - PD_BUF * SLOT, LIFT_ROW[0], cx + 1.2 + PD_BUF * SLOT, LIFT_ROW[1]))  # 리프트+버퍼
    return r


def wall_rects(t: float = 1.0) -> list[Rect]:
    return [(-t, -t, 0.0, H + t), (W, -t, W + t, H + t), (-t, -t, W + t, 0.0), (-t, H, W + t, H + t)]


@dataclass(frozen=True)
class Station:
    name: str
    x: float
    y: float
    kind: str        # "in_pick" 계량대, "lift_in" 리프트 입고 버퍼 끝, "lift_out" 리프트 출고 버퍼 끝, "out_drop" 하차대


def stations() -> list[Station]:
    s: list[Station] = []
    for i, xd in enumerate(IN_X):
        s.append(Station(f"IN{i + 1}", xd, 13.0 + ROBOT_R + 0.3, "in_pick"))
    for i, xd in enumerate(OUT_X):
        s.append(Station(f"OUT{i + 1}", xd, 13.0 + ROBOT_R + 0.3, "out_drop"))
    for i, cx in enumerate(RAIL_CX):
        end = 1.2 + (PD_BUF - 0.5) * SLOT
        s.append(Station(f"L{i + 1}in", cx - end, LIFT_ROW[0] - ROBOT_R - 0.3, "lift_in"))
        s.append(Station(f"L{i + 1}out", cx + end, LIFT_ROW[0] - ROBOT_R - 0.3, "lift_out"))
    return s


@dataclass
class GridMap:
    """고정 장애물을 로봇 반경만큼 부풀린 점유 격자. 상위 계획기(A*)용."""
    res: float = 0.3
    inflate: float = ROBOT_R + 0.15
    occ: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        nx, ny = int(round(W / self.res)), int(round(H / self.res))
        xs = (np.arange(nx) + 0.5) * self.res
        ys = (np.arange(ny) + 0.5) * self.res
        X, Y = np.meshgrid(xs, ys, indexing="ij")
        occ = (X < self.inflate) | (X > W - self.inflate) | (Y < self.inflate) | (Y > H - self.inflate)
        for x0, y0, x1, y1 in static_rects():
            dx = np.maximum(np.maximum(x0 - X, 0), X - x1)
            dy = np.maximum(np.maximum(y0 - Y, 0), Y - y1)
            occ |= np.hypot(dx, dy) < self.inflate
        self.occ = occ

    def cell(self, x: float, y: float) -> tuple[int, int]:
        i = int(np.clip(x / self.res, 0, self.occ.shape[0] - 1))
        j = int(np.clip(y / self.res, 0, self.occ.shape[1] - 1))
        return i, j

    def center(self, i: int, j: int) -> tuple[float, float]:
        return (i + 0.5) * self.res, (j + 0.5) * self.res

    def free(self, x: float, y: float) -> bool:
        return not self.occ[self.cell(x, y)]
