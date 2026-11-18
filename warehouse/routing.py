"""피킹/적재 경로(방문 순서) 결정.

AMR 한 대가 도크에서 출발해 여러 슬롯을 들른 뒤 도크로 돌아오는 순서를 정한다.
경로 길이는 실제 격자 최단거리(BFS/A*)로 계산한다.

- sshape  : 업계 기본형 S자 순서. 통로를 왼→오른쪽으로, 통로 안에서는 위/아래를 번갈아 방문.
- nn2opt  : 최근접 이웃으로 초기 순서를 만든 뒤 2-opt로 교차 구간을 풀어 개선.
- optimal : Held-Karp 동적계획법으로 최적 순서 (정류장 12개 이하에서 사용).
"""
from __future__ import annotations

from itertools import combinations

from .layout import Cell, WarehouseLayout
from .pathfinding import DistanceOracle


def tour_length(oracle: DistanceOracle, depot: Cell, stops: list[Cell]) -> int:
    pts = [depot, *stops, depot]
    return sum(oracle.dist(a, b) for a, b in zip(pts, pts[1:]))


def route_sshape(layout: WarehouseLayout, oracle: DistanceOracle, depot: Cell, stops: list[Cell]) -> list[Cell]:
    by_aisle: dict[int, list[Cell]] = {}
    for c in stops:
        by_aisle.setdefault(c[0], []).append(c)
    order: list[Cell] = []
    upward = True
    for x in sorted(by_aisle):
        order.extend(sorted(by_aisle[x], key=lambda c: c[1], reverse=not upward))
        upward = not upward
    return order


def route_nn2opt(layout: WarehouseLayout, oracle: DistanceOracle, depot: Cell, stops: list[Cell]) -> list[Cell]:
    remaining = list(stops)
    order: list[Cell] = []
    cur = depot
    while remaining:
        nxt = min(remaining, key=lambda c: (oracle.dist(cur, c), c))
        remaining.remove(nxt)
        order.append(nxt)
        cur = nxt
    return two_opt(oracle, depot, order)


def two_opt(oracle: DistanceOracle, depot: Cell, order: list[Cell]) -> list[Cell]:
    best = list(order)
    best_len = tour_length(oracle, depot, best)
    improved = True
    while improved:
        improved = False
        for i, j in combinations(range(len(best)), 2):
            if j - i < 1:
                continue
            cand = best[:i] + best[i : j + 1][::-1] + best[j + 1 :]
            L = tour_length(oracle, depot, cand)
            if L < best_len:
                best, best_len, improved = cand, L, True
    return best


def route_optimal(layout: WarehouseLayout, oracle: DistanceOracle, depot: Cell, stops: list[Cell]) -> list[Cell]:
    n = len(stops)
    if n == 0:
        return []
    if n > 12:
        return route_nn2opt(layout, oracle, depot, stops)
    d = [[oracle.dist(a, b) for b in stops] for a in stops]
    d0 = [oracle.dist(depot, s) for s in stops]
    INF = float("inf")
    dp = [[INF] * n for _ in range(1 << n)]
    parent = [[-1] * n for _ in range(1 << n)]
    for i in range(n):
        dp[1 << i][i] = d0[i]
    for mask in range(1 << n):
        for last in range(n):
            cur = dp[mask][last]
            if cur == INF or not (mask >> last) & 1:
                continue
            for nxt in range(n):
                if (mask >> nxt) & 1:
                    continue
                nm = mask | (1 << nxt)
                v = cur + d[last][nxt]
                if v < dp[nm][nxt]:
                    dp[nm][nxt] = v
                    parent[nm][nxt] = last
    full = (1 << n) - 1
    last = min(range(n), key=lambda i: dp[full][i] + d0[i])
    order, mask = [], full
    while last != -1:
        order.append(stops[last])
        prev = parent[mask][last]
        mask ^= 1 << last
        last = prev
    return order[::-1]


ROUTERS = {
    "sshape": route_sshape,
    "nn2opt": route_nn2opt,
    "optimal": route_optimal,
}
