"""상위 계획기 흉내: 정해진 정류장까지 거리장(Dijkstra)을 미리 만들어 두고 경유점을 뽑는다.

실제 시스템에서는 warehouse/fleet.py 의 시간 예약 + Cooperative A* 가 경유점을 내려 준다
(MQTT wh/v1/amr/<id>/cmd/mission). RL 정책은 그 경유점까지의 국소 주행·회피만 맡는다.
여기서는 고정 장애물만 아는 최단 경로를 쓰므로, 다른 AMR·작업자·임시 팔레트는 정책이 피해야 한다.
"""
from __future__ import annotations

import heapq
import math

import numpy as np

from .geometry import GridMap, Station

_NB = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
       (1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)), (-1, 1, math.sqrt(2)), (-1, -1, math.sqrt(2))]


LANE_BAND = (14.4, 19.2)       # AMR 통로 (geometry.AISLE)
LANE_MID = 16.8                 # 통로 가운데 선. 동쪽으로 갈 땐 남쪽 절반, 서쪽으로 갈 땐 북쪽 절반 (우측 통행)
LANE_GAP = 1.0                  # 가운데 선에서 이만큼 떨어져야 제 차선 (두 차선 중심 간격 2.4 m: AMR 지름 1.4 m + 여유 1.0 m)


class WaypointPlanner:
    """lanes=True: 통로에서 역방향 차선으로 가로 이동하면 비용 x lane_penalty → 우측 통행 경로."""

    def __init__(self, grid: GridMap, goals: list[Station], spacing: float = 6.0, lanes: bool = True,
                 lane_penalty: float = 2.5) -> None:
        self.grid = grid
        self.spacing = spacing
        self.lanes, self.lane_penalty = lanes, lane_penalty
        self.fields = {g.name: self._dijkstra(grid.cell(g.x, g.y)) for g in goals}

    def _dijkstra(self, src: tuple[int, int]) -> np.ndarray:
        occ = self.grid.occ
        nx, ny = occ.shape
        dist = np.full(occ.shape, np.inf)
        dist[src] = 0.0
        pq = [(0.0, src)]
        while pq:
            d, (i, j) = heapq.heappop(pq)
            if d > dist[i, j]:
                continue
            for di, dj, c in _NB:
                a, b = i + di, j + dj
                if not (0 <= a < nx and 0 <= b < ny) or occ[a, b]:
                    continue
                if self.lanes and di != 0:
                    # 역방향 탐색: 실제 이동은 (a, b) → (i, j), 즉 x 방향 -di
                    yb = (b + 0.5) * self.grid.res
                    east = -di > 0
                    if LANE_BAND[0] < yb < LANE_BAND[1] and ((east and yb > LANE_MID - LANE_GAP)
                                                              or (not east and yb < LANE_MID + LANE_GAP)):
                        c *= self.lane_penalty
                if d + c < dist[a, b]:
                    dist[a, b] = d + c
                    heapq.heappush(pq, (d + c, (a, b)))
        return dist

    def _los(self, p: tuple[float, float], q: tuple[float, float]) -> bool:
        n = max(2, int(math.dist(p, q) / (self.grid.res * 0.5)))
        east = q[0] - p[0] > 0.5
        west = q[0] - p[0] < -0.5
        for t in np.linspace(0, 1, n):
            x, y = p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t
            if not self.grid.free(x, y):
                return False
            # 줄 당기기가 차선을 가로질러 지름길을 내지 않도록 (끝점 근처 1 m 는 허용)
            if self.lanes and LANE_BAND[0] < y < LANE_BAND[1] and 0.05 < t < 0.95:
                if (east and y > LANE_MID - LANE_GAP + 0.1) or (west and y < LANE_MID + LANE_GAP - 0.1):
                    return False
        return True

    def plan(self, start: tuple[float, float], goal: Station) -> list[tuple[float, float]]:
        f = self.fields[goal.name]
        i, j = self.grid.cell(*start)
        if not np.isfinite(f[i, j]):                       # 부풀린 장애물 안에서 시작: 가장 가까운 유효 칸으로
            ii, jj = np.nonzero(np.isfinite(f))
            k = np.argmin((ii - i) ** 2 + (jj - j) ** 2)
            i, j = int(ii[k]), int(jj[k])
        cells = [(i, j)]
        nx, ny = f.shape
        while f[i, j] > 0:
            best = min(((f[a, b], a, b) for di, dj, _ in _NB
                        if 0 <= (a := i + di) < nx and 0 <= (b := j + dj) < ny), default=None)
            if best is None or best[0] >= f[i, j]:
                break
            _, i, j = best
            cells.append((i, j))
        pts = [start] + [self.grid.center(*c) for c in cells[1:-1]] + [(goal.x, goal.y)]
        # 줄 당기기(string pulling)로 꺾이는 점만 남김
        out = [pts[0]]
        k = 0
        while k < len(pts) - 1:
            m = len(pts) - 1
            while m > k + 1 and not self._los(pts[k], pts[m]):
                m -= 1
            out.append(pts[m])
            k = m
        # 긴 구간은 spacing 간격으로 쪼갬 (정책은 "다음 경유점 + 그다음" 만 본다)
        wps: list[tuple[float, float]] = []
        for p, q in zip(out, out[1:]):
            n = max(1, math.ceil(math.dist(p, q) / self.spacing))
            wps += [(p[0] + (q[0] - p[0]) * s / n, p[1] + (q[1] - p[1]) * s / n) for s in range(1, n + 1)]
        # 너무 촘촘한 경유점(1.5 m 미만)은 합친다. 마지막 점은 항상 남김
        out2: list[tuple[float, float]] = []
        last = start
        for k, w in enumerate(wps):
            if k == len(wps) - 1 or math.dist(last, w) >= 1.5:
                out2.append(w)
                last = w
        return out2
