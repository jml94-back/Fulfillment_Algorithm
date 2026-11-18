"""D4 배치의 MQTT 기준 좌표와 AMR 바닥 경로 그래프.

좌표계 (MQTT 메시지 전체 공통)
- 도면 좌표, 단위 m. 원점 = 건물 남서쪽 바닥 안쪽 모서리. x = 동쪽(0~120), y = 북쪽(0~80), z = 위.
- 방향 theta = +x(동쪽)에서 반시계 방향 rad. 0 = 동쪽, pi/2 = 북쪽.
- Unity로 옮길 때: Vector3(x, z, y). yaw(Unity, +Z에서 시계 방향 deg) = 90 - degrees(theta).

좌표의 원본은 Unity 배치 데이터 `unity/d4_layout.json` (Unity 순서 [x, 높이, 북쪽]) 이다.
여기서는 그 파일의 `nav` 그래프와 정지점(계량대·하차대·버퍼 접점·충전기·검수)을 읽어 도면 좌표로 바꿔 쓴다.
"""
from __future__ import annotations

import heapq
import json
import math
from functools import lru_cache
from pathlib import Path

from . import ids

LAYOUT_PATH = Path(__file__).resolve().parents[2] / "unity" / "d4_layout.json"
LEVEL_H = 1.5


@lru_cache(maxsize=1)
def layout(path: str | None = None) -> dict:
    return json.loads(Path(path or LAYOUT_PATH).read_text(encoding="utf-8"))


def _xy(pos: list[float]) -> tuple[float, float]:
    """Unity [x, 높이, 북쪽] → 도면 (x, y)."""
    return round(pos[0], 3), round(pos[2], 3)


@lru_cache(maxsize=1)
def nav() -> tuple[dict[str, tuple[float, float]], dict[str, list[tuple[str, float]]]]:
    d = layout()["nav"]
    nodes = {n["id"]: _xy(n["pos"]) for n in d["nodes"]}
    adj: dict[str, list[tuple[str, float]]] = {k: [] for k in nodes}
    for e in d["edges"]:
        adj[e["from"]].append((e["to"], e["length"]))
        adj[e["to"]].append((e["from"], e["length"]))
    return nodes, adj


# 정지점 ID(메시지에 쓰는 이름) → nav 노드 ID
def _stop_nodes() -> dict[str, str]:
    m = {}
    for i in range(1, 7):
        m[f"SC-IN{i}"] = f"n_dock_IN{i}"
        m[f"DP-OUT{i}"] = f"n_dock_OUT{i}"
    for r in range(1, 4):
        m[f"L{r}-IN"] = f"n_buf_L{r}-IN"
        m[f"L{r}-OUT"] = f"n_buf_L{r}-OUT"
    for c in ids.CHARGERS:
        m[c] = f"n_{c}"
    m["QC-IN"], m["QC-OUT"] = "n_inbound_qc", "n_outbound_qc"
    return m


STOP_NODES = _stop_nodes()
NODE_STOPS = {v: k for k, v in STOP_NODES.items()}


def stop_xy(stop: str) -> tuple[float, float]:
    return nav()[0][STOP_NODES[stop]]


def stop_theta(stop: str) -> float:
    """정지점에서 AMR이 바라보는 방향: 도크·버퍼는 설비 쪽, 충전기는 벽 쪽."""
    if stop.startswith(("SC-", "DP-")):
        return -math.pi / 2
    if stop.startswith("L"):
        return math.pi / 2
    if stop.startswith("CHW"):
        return math.pi
    if stop.startswith("CHE"):
        return 0.0
    return math.pi / 2


def shortest_path(a: str, b: str) -> list[str]:
    """nav 노드 사이 최단 경로 (Dijkstra). a, b 는 정지점 ID 또는 nav 노드 ID."""
    nodes, adj = nav()
    s, t = STOP_NODES.get(a, a), STOP_NODES.get(b, b)
    dist, prev, pq = {s: 0.0}, {}, [(0.0, s)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == t:
            break
        if d > dist.get(u, math.inf):
            continue
        for v, w in adj[u]:
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v], prev[v] = nd, u
                heapq.heappush(pq, (nd, v))
    if t not in dist:
        raise ValueError(f"경로 없음: {a} → {b}")
    path = [t]
    while path[-1] != s:
        path.append(prev[path[-1]])
    return path[::-1]


def path_length(path: list[str]) -> float:
    nodes = nav()[0]
    return sum(math.dist(nodes[p], nodes[q]) for p, q in zip(path, path[1:]))


def nearest_node(x: float, y: float) -> str:
    nodes = nav()[0]
    return min(nodes, key=lambda k: math.dist(nodes[k], (x, y)))


def lane_xyz(lane: str, pos: int = 1) -> tuple[float, float, float]:
    """레인 안 pos 번째 팔레트 자리 중심 (pos 1 = 레일 쪽 입·출구). z = 팔레트 바닥 높이."""
    r, side, lv, row = ids.parse_lane(lane)
    floor_id = f"R{r}-{side}-N{row:02d}"
    ln = _lanes()[floor_id]
    x = ln["slot0_x"] + (pos - 1) * ln["slot_step_x"]
    return round(x, 3), round(ln["center_z"], 3), round((lv - 1) * LEVEL_H, 3)


@lru_cache(maxsize=1)
def _lanes() -> dict[str, dict]:
    return {ln["id"]: ln for ln in layout()["storage"]["lanes"]}


def lift_xy(lift: str) -> tuple[float, float]:
    for lf in layout()["lifts"]:
        if lf["id"] == lift:
            return _xy(lf["center"])
    raise KeyError(lift)


def amr_spawn(amr: str) -> tuple[float, float, str]:
    for a in layout()["amrs"]:
        if a["id"] == amr:
            x, y = _xy(a["spawn"])
            return x, y, a["home_charger"]
    raise KeyError(amr)


def to_unity(x: float, y: float, z: float = 0.0) -> tuple[float, float, float]:
    return x, z, y


def theta_to_unity_yaw_deg(theta: float) -> float:
    return (90.0 - math.degrees(theta)) % 360.0
