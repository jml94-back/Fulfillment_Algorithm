"""다중 AMR 실행 계층: 작업 할당 + 시공간 예약 기반 충돌 회피.

구조
----
계획 계층(Simulator)이 "무엇을 할지"(trip: 어느 슬롯에서 무엇을 넣고/꺼낼지, 방문 순서)를
정하면, 이 실행 계층이 "누가, 언제, 어떤 길로" 할지를 정한다.

1. 작업 할당(Dispatcher)
   대기 중인 trip을 먼저 생긴 순서대로 꺼내, 놀고 있는 AMR 중 첫 목적지까지
   가장 가까운 AMR에 맡긴다 (greedy nearest-idle).

2. 충돌 회피 (Prioritized planning + Cooperative A*)
   - 시공간 예약표에 이미 계획된 AMR들의 (칸, 시각)과 (칸→칸, 시각) 이동을 기록한다.
   - 새로 계획하는 AMR은 시공간 A*로 (x, y, t) 공간을 탐색하며 예약된 칸과
     맞교환(swap) 이동을 피한다. 제자리 대기도 행동으로 허용한다.
   - 작업이 끝난 AMR은 자기 대기 칸으로 돌아가 다음 작업까지 그 칸을 점유한다.
   먼저 계획된 경로는 바뀌지 않으므로 결과 경로는 구성상 충돌이 없다.
   (verify_no_collisions 로 매 실행마다 검증)

3. 시간 모델
   1 tick = 셀 1칸 이동 시간. 슬롯 작업 시간 = handle_ticks + 층 * lift_ticks,
   도크 상·하역 시간 = dock_ticks.

나중에 RL 주행 정책으로 바꿀 때는 plan_segment 를 학습된 정책으로 대체하면 된다.
"""
from __future__ import annotations

import heapq
from collections import defaultdict
from dataclasses import dataclass, field

from .layout import Cell, WarehouseLayout
from .pathfinding import DistanceOracle, astar
from .simulator import Trip


@dataclass
class FleetConfig:
    n_amr: int = 4
    order_interval: float = 14.0   # 주문 1건 간격(tick). 계획 계층의 주문 번호 → 시각 변환
    handle_ticks: int = 4
    lift_ticks: int = 2            # 층당 승강 시간
    dock_ticks: int = 3
    avoidance: bool = True         # False면 각자 최단경로 (충돌 수 비교용)
    horizon: int = 800             # 시공간 탐색 최대 시간 폭
    tick_seconds: float = 1.2      # 1칸(1.2m)을 1m/s로 이동


@dataclass
class TripLog:
    trip_index: int
    kind: str
    amr: int
    released: int
    started: int
    finished: int          # 마지막 상·하역 완료 시각
    travel: int            # 실제 이동 시간(tick, 혼잡 대기 포함, 상·하역 제외)
    shortest: int          # 혼잡 없을 때 최단 이동 칸 수


@dataclass
class FleetResult:
    logs: list[TripLog] = field(default_factory=list)
    tracks: list[list[Cell]] = field(default_factory=list)   # tracks[amr][t] = 위치
    busy: list[int] = field(default_factory=list)            # AMR별 작업 중 tick
    collisions: int = 0
    plan_failures: int = 0
    skipped_stops: list[tuple[int, Cell, int]] = field(default_factory=list)   # 비상 구역 때문에 못 간 정류장
    events_log: list[dict] = field(default_factory=list)

    @property
    def makespan(self) -> int:
        return max((l.finished for l in self.logs), default=0)

    @property
    def mean_lead(self) -> float:
        return sum(l.finished - l.released for l in self.logs) / max(len(self.logs), 1)

    @property
    def p95_lead(self) -> float:
        xs = sorted(l.finished - l.released for l in self.logs)
        return xs[int(0.95 * (len(xs) - 1))] if xs else 0.0

    @property
    def utilization(self) -> float:
        return sum(self.busy) / max(len(self.busy) * self.makespan, 1)

    @property
    def congestion_wait(self) -> float:
        """혼잡 때문에 생긴 추가 시간 비율 (실제 이동시간/최단 - 1)."""
        travel = sum(l.travel for l in self.logs)
        short = sum(l.shortest for l in self.logs)
        return travel / max(short, 1) - 1


class Reservations:
    """시공간 예약표."""

    def __init__(self) -> None:
        self.vertex: dict[tuple[Cell, int], int] = {}
        self.edge: dict[tuple[Cell, Cell, int], int] = {}
        self.hold: dict[Cell, tuple[int, int]] = {}     # cell -> (amr, from_t): 그 시각 이후 계속 점유
        self.last: dict[Cell, int] = defaultdict(lambda: -1)
        self.zones: dict[Cell, list[tuple[int, float]]] = defaultdict(list)   # 비상 구역: [from, until)

    def blocked(self, c: Cell, t: int) -> bool:
        return any(f <= t < u for f, u in self.zones.get(c, ()))

    def free(self, c: Cell, t: int, me: int, ignore_zones: bool = False) -> bool:
        o = self.vertex.get((c, t))
        if o is not None and o != me:
            return False
        if not ignore_zones and c in self.zones and self.blocked(c, t):
            return False
        h = self.hold.get(c)
        return not (h and h[0] != me and t >= h[1])

    def release(self, me: int, after: int) -> None:
        """me의 after 이후 예약을 모두 지운다 (재계획용)."""
        self.vertex = {k: v for k, v in self.vertex.items() if not (v == me and k[1] > after)}
        self.edge = {k: v for k, v in self.edge.items() if not (v == me and k[2] >= after)}

    def can_move(self, a: Cell, b: Cell, t: int, me: int, ignore_zones: bool = False) -> bool:
        if not self.free(b, t + 1, me, ignore_zones):
            return False
        o = self.edge.get((b, a, t))
        return o is None or o == me

    def reserve(self, path: list[Cell], t0: int, me: int) -> None:
        for i, c in enumerate(path):
            self.vertex[(c, t0 + i)] = me
            self.last[c] = max(self.last[c], t0 + i)
        for i in range(len(path) - 1):
            self.edge[(path[i], path[i + 1], t0 + i)] = me

    def prune(self, before: int) -> None:
        self.vertex = {k: v for k, v in self.vertex.items() if k[1] >= before}
        self.edge = {k: v for k, v in self.edge.items() if k[2] >= before}


def plan_segment(layout: WarehouseLayout, oracle: DistanceOracle, res: Reservations, me: int,
                 start: Cell, t0: int, goal: Cell, dwell: int, park: bool, horizon: int,
                 ignore_zones: bool = False) -> list[Cell] | None:
    """start(t0) → goal 도착 후 dwell tick 머무는 경로. park=True면 도착 후 무기한 점유 가능해야 함.
    ignore_zones=True 는 비상 구역 안에 갇힌 AMR이 밖으로 빠져나올 때만 쓴다."""

    def goal_ok(t: int) -> bool:
        if park:
            return res.last[goal] < t and all(res.free(goal, t + k, me, ignore_zones) for k in range(dwell + 1))
        return all(res.free(goal, t + k, me, ignore_zones) for k in range(dwell + 1))

    heap = [(oracle.dist(start, goal), t0, start)]
    parent: dict[tuple[Cell, int], tuple[Cell, int] | None] = {(start, t0): None}
    while heap:
        _, t, c = heapq.heappop(heap)
        if c == goal and goal_ok(t):
            path = []
            node: tuple[Cell, int] | None = (c, t)
            while node is not None:
                path.append(node[0])
                node = parent[node]
            return path[::-1] + [goal] * dwell
        if t - t0 >= horizon:
            continue
        for n in [*layout.neighbors(c), c]:
            key = (n, t + 1)
            if key in parent or not res.can_move(c, n, t, me, ignore_zones):
                continue
            parent[key] = (c, t)
            heapq.heappush(heap, (t + 1 + oracle.dist(n, goal), t + 1, n))
    return None


def trip_waypoints(layout: WarehouseLayout, trip: Trip, cfg: FleetConfig) -> list[tuple[Cell, int]]:
    """trip → [(목적지 칸, 머무는 tick)]. 같은 접근점의 연속 작업은 하나로 합친다."""
    legs: list[tuple[Cell, int]] = []
    if trip.kind == "inbound":
        legs.append((layout.inbound_dock, cfg.dock_ticks))
    for st in trip.stops:
        ap = layout.access_point(st.slot)
        d = cfg.handle_ticks + st.slot[2] * cfg.lift_ticks
        if legs and legs[-1][0] == ap:
            legs[-1] = (ap, legs[-1][1] + d)
        else:
            legs.append((ap, d))
    if trip.kind == "outbound":
        legs.append((layout.dock, cfg.dock_ticks))
    return legs


@dataclass
class EmergencyEvent:
    """비상 구역 이벤트. 관제(예: LangGraph 에이전트)가 감지 결과를 이 형태로 넘긴다."""
    t: int                       # 발생 시각(tick)
    cells: set[Cell]             # 진입 금지 칸 (emergency.zone_for 로 생성)
    until: float = float("inf")  # 해제 시각
    kind: str = "unknown"


@dataclass
class _Active:
    trip_index: int
    legs: list[tuple[Cell, int, int, int]]   # (목적지, 머무는 tick, 도착 시각, 완료 시각)
    log: TripLog


class FleetSimulator:
    def __init__(self, layout: WarehouseLayout, cfg: FleetConfig, oracle: DistanceOracle | None = None) -> None:
        if cfg.n_amr > len(layout.parking):
            raise ValueError("not enough parking cells for the fleet")
        self.layout = layout
        self.cfg = cfg
        self.oracle = oracle or DistanceOracle(layout)

    # ---- 메인 루프 -----------------------------------------------------------
    def run(self, trips: list[Trip], events: list[EmergencyEvent] | None = None) -> FleetResult:
        L, cfg = self.layout, self.cfg
        self.homes = L.parking[: cfg.n_amr]
        self.res = res = Reservations()
        self.out = out = FleetResult(tracks=[[h] for h in self.homes], busy=[0] * cfg.n_amr)
        self.free_at = [0] * cfg.n_amr
        self.parked = list(self.homes)          # 대기 중일 때 점유하는 칸
        self.active: dict[int, _Active] = {}
        self.trips = trips
        for i, h in enumerate(self.homes):
            if cfg.avoidance:
                res.hold[h] = (i, 0)

        releases = sorted((int(t.released_at * cfg.order_interval), i) for i, t in enumerate(trips))
        events = sorted(events or [], key=lambda e: e.t)
        pending: list[tuple[int, list[tuple[Cell, int]] | None]] = []   # (trip, 남은 legs or None=전체)
        ri = ei = 0
        now = last_prune = 0
        while ri < len(releases) or pending or ei < len(events):
            while ei < len(events) and events[ei].t <= now:
                pending[:0] = self._on_emergency(events[ei], now)
                ei += 1
            while ri < len(releases) and releases[ri][0] <= now:
                pending.append((releases[ri][1], None))
                ri += 1
            idle = [a for a in range(cfg.n_amr) if self.free_at[a] <= now]
            stuck = False
            while pending and idle:
                ti, legs = pending[0]
                fresh = legs is None
                if fresh:
                    legs = trip_waypoints(L, trips[ti], cfg)
                legs = self._drop_blocked(ti, legs, now)
                docks = {L.dock, L.inbound_dock}
                if not legs or (fresh and all(g in docks for g, _ in legs)):
                    pending.pop(0)
                    continue
                first = legs[0][0]
                amr = min(idle, key=lambda a: (self.oracle.dist(self.parked[a], first), a))
                if not self._execute(ti, legs, amr, now):
                    out.plan_failures += 1
                    stuck = True
                    break          # 다음 tick에 다시 시도
                pending.pop(0)
                idle.remove(amr)
            nxt = [releases[ri][0]] if ri < len(releases) else []
            nxt += [events[ei].t] if ei < len(events) else []
            nxt += [f for f in self.free_at if f > now]
            now = now + 1 if (stuck or not nxt) else max(now + 1, min(nxt))
            if now - last_prune > 2000:
                res.prune(now - 1)
                last_prune = now

        horizon = max(len(tr) for tr in out.tracks)
        for tr in out.tracks:
            tr.extend([tr[-1]] * (horizon - len(tr)))
        out.collisions = verify_no_collisions(out.tracks)
        return out

    # ---- 비상 상황 ---------------------------------------------------------
    def _drop_blocked(self, ti: int, legs, now: int):
        keep = []
        for g, d in legs:
            if self.res.blocked(g, now):
                self.out.skipped_stops.append((ti, g, now))
            else:
                keep.append((g, d))
        return keep

    def _on_emergency(self, ev: EmergencyEvent, now: int) -> list:
        """구역 차단 → 그 구역을 앞으로 지나갈 AMR을 멈추고 남은 작업을 다시 계획한다."""
        res, out = self.res, self.out
        for c in ev.cells:
            res.zones[c].append((ev.t, ev.until))
        replanned, requeue = [], []
        for amr, act in list(self.active.items()):
            future = out.tracks[amr][now + 1:]
            if not any(c in ev.cells for c in future):
                continue
            remaining = []
            for g, d, arrive, end in act.legs:
                if end <= now:
                    continue
                if arrive <= now:              # 지금 그 자리에서 작업 중
                    d = end - now
                remaining.append((g, d))
            res.release(amr, now)
            res.hold.pop(self.homes[amr], None)
            del out.tracks[amr][now + 1:]
            out.busy[amr] -= max(0, self.free_at[amr] - now)
            self.free_at[amr] = now
            self.parked[amr] = out.tracks[amr][now]
            out.logs.remove(act.log)
            del self.active[amr]
            replanned.append(amr)
            if not self._execute(act.trip_index, self._drop_blocked(act.trip_index, remaining, now), amr, now,
                                 released=act.log.released):
                # 지금 계획이 안 되면 그 자리에서 대기하다가 다시 시도
                res.hold[self.parked[amr]] = (amr, now)
                requeue.append((act.trip_index, remaining))
        out.events_log.append({"t": now, "kind": ev.kind, "cells": len(ev.cells), "replanned": replanned})
        return requeue

    def _nearest_safe(self, start: Cell, now: int) -> Cell:
        from collections import deque
        seen, q = {start}, deque([start])
        while q:
            c = q.popleft()
            if not self.res.blocked(c, now) and c not in self.res.hold:
                return c
            for n in self.layout.neighbors(c):
                if n not in seen:
                    seen.add(n)
                    q.append(n)
        return start

    # ---- 한 작업 실행 계획 ----------------------------------------------------
    def _execute(self, ti: int, legs, amr: int, now: int, released: int | None = None) -> bool:
        L, cfg, res, out = self.layout, self.cfg, self.res, self.out
        track = out.tracks[amr]
        track.extend([track[-1]] * (now + 1 - len(track)))   # 대기 중이던 시간 채우기
        pos, t = track[now], now
        full: list[Cell] = [pos]
        shortest = 0
        planned: list[tuple[Cell, int, int, int]] = []
        home = self.homes[amr]
        if cfg.avoidance:
            res.hold.pop(self.parked[amr], None)
        targets = [*legs, (home, 0)]
        if cfg.avoidance and res.blocked(pos, now):
            targets.insert(0, (self._nearest_safe(pos, now), -1))   # 먼저 비상 구역 밖으로 탈출
        for k, (goal, dwell) in enumerate(targets):
            park = k == len(targets) - 1
            escape = dwell == -1
            dwell = max(dwell, 0)
            shortest += self.oracle.dist(pos, goal)
            if cfg.avoidance:
                seg = plan_segment(L, self.oracle, res, amr, pos, t, goal, dwell, park, cfg.horizon,
                                   ignore_zones=escape)
                if seg is None:
                    res.hold[self.parked[amr]] = (amr, now)   # 실패: 원상복구
                    return False
            else:
                seg = astar(L, pos, goal) + [goal] * dwell
            full += seg[1:]
            t += len(seg) - 1
            if not park and not escape:
                planned.append((goal, dwell, t - dwell, t))
            pos = goal
        if cfg.avoidance:
            res.reserve(full, now, amr)
            res.hold[home] = (amr, now + len(full) - 1)
        self.parked[amr] = home
        del track[now:]
        track.extend(full)
        self.free_at[amr] = now + len(full) - 1
        out.busy[amr] += len(full) - 1
        dwell_total = sum(max(d, 0) for _, d in legs)
        rel = released if released is not None else int(self.trips[ti].released_at * cfg.order_interval)
        finish = planned[-1][3] if planned else now
        log = TripLog(ti, self.trips[ti].kind, amr, rel, now, finish, len(full) - 1 - dwell_total, shortest)
        out.logs.append(log)
        self.active[amr] = _Active(ti, planned, log)
        return True


def verify_no_collisions(tracks: list[list[Cell]]) -> int:
    """같은 시각 같은 칸(vertex) + 맞교환(swap) 충돌 수."""
    n = 0
    T = len(tracks[0])
    for t in range(T):
        seen: dict[Cell, int] = {}
        for a, tr in enumerate(tracks):
            if tr[t] in seen:
                n += 1
            seen[tr[t]] = a
        if t + 1 < T:
            for a in range(len(tracks)):
                for b in range(a + 1, len(tracks)):
                    if tracks[a][t] == tracks[b][t + 1] and tracks[b][t] == tracks[a][t + 1] \
                            and tracks[a][t] != tracks[a][t + 1]:
                        n += 1
    return n
