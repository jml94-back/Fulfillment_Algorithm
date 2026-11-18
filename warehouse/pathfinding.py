"""격자 경로 탐색.

- astar(): 두 칸 사이 최단 경로(웨이포인트 리스트). AMR에 넘길 실제 경로.
- DistanceOracle: 출발점별 BFS 결과를 캐시해 거리 질의를 O(1)로 만든다.
  모든 이동 비용이 1인 격자라 BFS 거리 = A* 거리.
"""
from __future__ import annotations

import heapq
from collections import deque

from .layout import Cell, WarehouseLayout


def manhattan(a: Cell, b: Cell) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def astar(layout: WarehouseLayout, start: Cell, goal: Cell) -> list[Cell]:
    if start == goal:
        return [start]
    open_heap: list[tuple[int, int, Cell]] = [(manhattan(start, goal), 0, start)]
    came: dict[Cell, Cell] = {}
    g: dict[Cell, int] = {start: 0}
    while open_heap:
        _, cost, cur = heapq.heappop(open_heap)
        if cur == goal:
            path = [cur]
            while cur in came:
                cur = came[cur]
                path.append(cur)
            return path[::-1]
        if cost > g[cur]:
            continue
        for n in layout.neighbors(cur):
            nc = cost + 1
            if nc < g.get(n, 1 << 30):
                g[n] = nc
                came[n] = cur
                heapq.heappush(open_heap, (nc + manhattan(n, goal), nc, n))
    raise ValueError(f"no path {start} -> {goal}")


class DistanceOracle:
    def __init__(self, layout: WarehouseLayout) -> None:
        self.layout = layout
        self._cache: dict[Cell, dict[Cell, int]] = {}

    def _bfs(self, src: Cell) -> dict[Cell, int]:
        dist = {src: 0}
        q = deque([src])
        while q:
            c = q.popleft()
            for n in self.layout.neighbors(c):
                if n not in dist:
                    dist[n] = dist[c] + 1
                    q.append(n)
        return dist

    def dist(self, a: Cell, b: Cell) -> int:
        if a not in self._cache:
            self._cache[a] = self._bfs(a)
        return self._cache[a][b]
