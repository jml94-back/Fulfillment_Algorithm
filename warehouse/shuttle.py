"""셔틀 앤 세틀라이트 창고 이산 사건 시뮬레이션 (배치안 v3 기준, m·초 단위).

자원
----
- AMR 4대: 입고 도크 ↔ 리프트 입고 버퍼, 리프트 출고 버퍼 ↔ 출고 도크. 바닥 통로를 맨해튼 거리로 이동.
  (AMR끼리 충돌 회피 지연은 이 비교에서 제외: 리프트·셔틀 배치 효과만 본다.)
- 리프트: 레일마다 1~2기. 바닥(0층) 버퍼와 각 층 이송점 사이를 오르내린다.
  입·출고 겸용이면 "올라갈 때 입고 팔레트, 내려올 때 출고 팔레트"를 한 번에 처리(이중 사이클).
- 셔틀 세트(모셔틀 + 세틀라이트): 레일 x 층마다 1세트. 리프트 이송점 ↔ 레인.
- 레인: 깊이 10팔레트, SKU 1종 전용, 한쪽에서만 넣고 뺀다(선입후출).

흐름
----
입고: 트럭 하차·계량 → AMR → 리프트 입고 버퍼 → 리프트 → 셔틀 → 레인
출고: 주문 → 셔틀이 레인에서 꺼냄 → 리프트 → 출고 버퍼 → AMR → 출고 도크

비교 대상 배치(LAYOUTS)와 알고리즘 정책(Policy)은 아래에 정의한다.
"""
from __future__ import annotations

import heapq
import itertools
import random
import statistics
from dataclasses import dataclass, field

# ---------------------------------------------------------------- 장비 성능 (가정값)
AMR_SPEED = 1.2          # m/s
AMR_HANDLE = 8.0         # 팔레트 싣기/내리기 각각 (s)
LIFT_V = 1.5             # m/s (수직)
LIFT_OVERHEAD = 2.0      # 가감속 (s, 이동마다)
LIFT_XFER = 5.0          # 리프트 ↔ 컨베이어/이송점 이송 (s)
MOTHER_V = 2.0           # 모셔틀 m/s
MOTHER_OVERHEAD = 3.0
SAT_V = 1.0              # 세틀라이트 m/s
SAT_XFER = 4.0           # 세틀라이트 들어가기/나오기 각각, 팔레트 들기/놓기 포함 (s)
SHUTTLE_XFER = 4.0       # 모셔틀 ↔ 리프트 이송점 (s)
UNLOAD = 30.0            # 트럭에서 팔레트 1개 하차·계량 (도크당, s)

LEVELS, LEVEL_H = 10, 1.5
DEPTH, SLOT, LANE_W = 10, 1.3, 1.4
RAIL_X_DEFAULT = [32.2, 60.0, 87.8]
RAIL_X = list(RAIL_X_DEFAULT)      # Sim 생성 시 배치안 값으로 바뀜 (시뮬은 한 번에 하나씩 실행)
DOCK_Y = [19.8, 25.8, 31.8, 37.8, 43.8, 49.8]
IN_X, OUT_X = 13.0, 107.0          # 도크 쪽 AMR 상·하차 지점
CORR_W, CORR_E = 16.2, 103.8       # 서·동 연결 통로 중심선


@dataclass
class LiftSpec:
    rail: int
    y: float                 # 리프트가 레일과 만나는 y (셔틀이 팔레트를 주고받는 위치)
    aisle_y: float           # AMR이 버퍼에 접근하는 통로 중심 y
    x: float                 # AMR 상·하차 지점 x
    roles: frozenset         # {"in", "out"}


@dataclass
class Layout:
    name: str
    lane_ys: list[float]                 # 레인 중심 y (레일 한쪽 기준, 양쪽 동일)
    lifts: list[LiftSpec]
    aisle_rows: list[float]              # AMR 통로 중심 y 목록
    in_pts: list = field(default_factory=lambda: [(IN_X, y) for y in DOCK_Y])    # 입고 도크 AMR 상차 지점
    out_pts: list = field(default_factory=lambda: [(OUT_X, y) for y in DOCK_Y])  # 출고 도크 AMR 하차 지점
    dock_row: float | None = None        # 도크 점이 바로 붙은 AMR 통로 (None이면 서·동 연결 통로 경유)
    rail_x: list[float] | None = None    # 모셔틀 레일 x (None이면 RAIL_X_DEFAULT)
    depth: int = 10                      # 레인 깊이 (팔레트 수)
    positions: int = 0                   # 보관 용량 (팔레트, 비교표용)


def _lanes(y0: float, y1: float) -> list[float]:
    n = int((y1 - y0 + 1e-9) // LANE_W)
    return [y0 + (i + .5) * LANE_W for i in range(n)]


def make_layouts() -> dict[str, Layout]:
    both, inn, out = frozenset({"in", "out"}), frozenset({"in"}), frozenset({"out"})
    S_AISLE, S_LIFT = 4.2, 7.8                # 남쪽 통로 중심, 남쪽 리프트 위치
    lay: dict[str, Layout] = {}

    # A: 남쪽 끝 리프트 3기 겸용 (v3.1)
    lay["A 남쪽 3기 겸용"] = Layout("A", _lanes(9.0, 68.4),
                                 [LiftSpec(r, S_LIFT, S_AISLE, x, both) for r, x in enumerate(RAIL_X)], [S_AISLE])
    # B: 남쪽 + 북쪽 리프트 6기
    lanes_b = _lanes(9.0, 62.4)
    top = lanes_b[-1] + LANE_W / 2
    n_lift, n_aisle = top + 1.2, top + 2.4 + 2.4
    lay["B 남입·북출 6기 고정"] = Layout("B-fixed", lanes_b,
        [LiftSpec(r, S_LIFT, S_AISLE, x, inn) for r, x in enumerate(RAIL_X)] +
        [LiftSpec(r, n_lift, n_aisle, x, out) for r, x in enumerate(RAIL_X)], [S_AISLE, n_aisle])
    lay["B 남·북 6기 겸용"] = Layout("B-dual", lanes_b,
        [LiftSpec(r, S_LIFT, S_AISLE, x, both) for r, x in enumerate(RAIL_X)] +
        [LiftSpec(r, n_lift, n_aisle, x, both) for r, x in enumerate(RAIL_X)], [S_AISLE, n_aisle])
    # C: 가운데 동서 통로(AMR 4.8 + 양쪽 P&D 1.2씩 = 7.2m). 레인은 남·북 절반으로 나뉨.
    mid = (min(DOCK_Y) + max(DOCK_Y)) / 2          # 34.8: 도크 중앙과 같은 높이
    c_lanes = _lanes(1.2, mid - 3.6) + _lanes(mid + 3.6, 68.4)
    lay["C 중앙 통로 3기 겸용"] = Layout("C3", c_lanes,
        [LiftSpec(r, mid, mid, x, both) for r, x in enumerate(RAIL_X)], [mid])
    lay["C 중앙 통로 6기 겸용"] = Layout("C6", c_lanes,
        [LiftSpec(r, mid, mid, x + dx, both) for r, x in enumerate(RAIL_X) for dx in (-2.4, 2.4)], [mid])
    # D: 입고·출고 스테이지와 도크를 모두 남쪽 벽에. 남쪽 띠 = 스테이징 14.4 + AMR 통로 4.8 + P&D 2.4
    d_aisle, d_lift = 14.4 + 2.4, 14.4 + 4.8 + 1.2
    in_x = [12.0 + 5.0 * i for i in range(6)]            # 서쪽 절반에 입고 도크 6
    out_x = [108.0 - 5.0 * i for i in range(6)]          # 동쪽 절반에 출고 도크 6
    lay["D 남쪽 입·출고 + 3기 겸용"] = Layout("D", _lanes(14.4 + 7.2, 68.4),
        [LiftSpec(r, d_lift, d_aisle, x, both) for r, x in enumerate(RAIL_X)], [d_aisle],
        in_pts=[(x, 13.0) for x in in_x], out_pts=[(x, 13.0) for x in out_x], dock_row=d_aisle)
    # D2: D + 사무실·지원 시설을 남쪽 가운데(입고·출고 도크 사이)로 옮기고 북쪽 띠까지 레인으로 사용
    lay["D2 남쪽 도크 + 사무실 남쪽 중앙"] = Layout("D2", _lanes(14.4 + 7.2, 78.8),
        [LiftSpec(r, d_lift, d_aisle, x, both) for r, x in enumerate(RAIL_X)], [d_aisle],
        in_pts=[(x, 13.0) for x in in_x], out_pts=[(x, 13.0) for x in out_x], dock_row=d_aisle)
    # D3: 벽에서 벽까지, 가운데 방화벽으로 동·서 창고 분리(창고별 레일 2개), 리프트 줄 위 검수 띠 9.8m
    rx3 = [16.0, 43.8, 76.2, 104.0]
    lay["D3 동·서 창고 4레일 + 검수 띠"] = Layout("D3", _lanes(31.4, 78.8),
        [LiftSpec(r, d_lift, d_aisle, x, both) for r, x in enumerate(rx3)], [d_aisle],
        in_pts=[(x, 13.0) for x in in_x], out_pts=[(x, 13.0) for x in out_x], dock_row=d_aisle, rail_x=rx3)
    # D4: 창고 하나, 3레일 깊이 14로 벽에서 벽까지, 도크 3+3 사이 예비 구역, 검수 띠 9.8m
    rx4 = [21.8, 60.0, 98.2]
    in4 = [2.5, 7.5, 12.5, 27.5, 32.5, 37.5]
    out4 = [117.5, 112.5, 107.5, 92.5, 87.5, 82.5]
    lay["D4 3레일 깊이 14 + 검수 띠"] = Layout("D4", _lanes(31.4, 78.8),
        [LiftSpec(r, d_lift, d_aisle, x, both) for r, x in enumerate(rx4)], [d_aisle],
        in_pts=[(x, 13.0) for x in in4], out_pts=[(x, 13.0) for x in out4], dock_row=d_aisle, rail_x=rx4, depth=14)
    for L in lay.values():
        L.positions = len(L.rail_x or RAIL_X_DEFAULT) * 2 * len(L.lane_ys) * L.depth * LEVELS
    return lay


@dataclass
class Policy:
    dual_cycle: bool = True        # 리프트 이중 사이클
    balance: bool = True           # 레인 배정·출고 레인 선택 시 셔틀 대기열 반영
    abc: bool = True               # 회전율 높은 SKU를 리프트 가까운 레인에


# ---------------------------------------------------------------- 상태 객체
@dataclass
class Lane:
    idx: int
    rail: int
    level: int
    y: float
    sku: str | None = None
    count: int = 0          # 실제 적재 수
    reserved: int = 0       # 들어올 예정
    out_reserved: int = 0   # 꺼낼 예정

    @property
    def free(self) -> int:
        return DEPTH - self.count - self.reserved

    @property
    def available(self) -> int:
        return self.count - self.out_reserved


@dataclass
class Job:
    kind: str                # "in" / "out"
    sku: str
    created: float
    truck: int
    dock: int
    lane: Lane | None = None
    lift: int = -1
    done: float = -1.0
    slot_depth: int = 0      # 레인 안 위치(입구=0) → 세틀라이트 이동거리


class Sim:
    def __init__(self, layout: Layout, policy: Policy, rate_per_h: float, hours: float = 3.0,
                 n_amr: int = 4, n_skus: int = 400, fill: float = 0.6, seed: int = 0) -> None:
        self.L, self.P = layout, policy
        global DEPTH, RAIL_X
        DEPTH, RAIL_X = layout.depth, list(layout.rail_x or RAIL_X_DEFAULT)
        self.rate, self.hours, self.n_amr = rate_per_h, hours, n_amr
        self.rng = random.Random(seed)
        self.t = 0.0
        self._ev: list = []
        self._seq = itertools.count()
        # 수요 (Zipf)
        w = [1 / (r + 1) ** 1.0 for r in range(n_skus)]
        tot = sum(w)
        self.skus = [f"S{r:03d}" for r in range(n_skus)]
        self.demand = {s: w[i] / tot for i, s in enumerate(self.skus)}
        # 레인
        self.lanes: list[Lane] = []
        for r in range(len(RAIL_X)):
            for lv in range(LEVELS):
                for side in (0, 1):
                    for y in layout.lane_ys:
                        self.lanes.append(Lane(len(self.lanes), r, lv, y))
        self.static_cost = {ln.idx: self._lane_cost(ln) for ln in self.lanes}
        order = sorted(self.lanes, key=lambda ln: self.static_cost[ln.idx])
        self.rank = {ln.idx: i / len(order) for i, ln in enumerate(order)}
        cum, self.sku_rank = 0.0, {}
        for s in self.skus:
            self.sku_rank[s] = cum
            cum += self.demand[s]
        self.by_sku: dict[str, list[Lane]] = {s: [] for s in self.skus}
        # 장비 상태
        self.lift_busy = [False] * len(layout.lifts)
        self.lift_level = [0] * len(layout.lifts)
        self.lift_in: list[list[Job]] = [[] for _ in layout.lifts]     # 바닥 입고 버퍼
        self.lift_out: list[list[Job]] = [[] for _ in layout.lifts]    # 층 이송점에 대기 중인 출고 팔레트
        self.lift_load = [0] * len(layout.lifts)                       # 배정된 미완료 작업 수
        self.sh_busy: dict[tuple[int, int], bool] = {}
        self.sh_y: dict[tuple[int, int], float] = {}
        self.sh_q: dict[tuple[int, int], list] = {}
        for r in range(len(RAIL_X)):
            for lv in range(LEVELS):
                k = (r, lv)
                self.sh_busy[k], self.sh_q[k] = False, []
                self.sh_y[k] = layout.lifts[[i for i, l in enumerate(layout.lifts) if l.rail == r][0]].y
        self.amr_pos = [(layout.in_pts if i % 2 == 0 else layout.out_pts)[i % 6] for i in range(n_amr)]
        self.amr_row: list[float | None] = [layout.dock_row] * n_amr
        self.amr_free = [True] * n_amr
        self.amr_q: list[tuple[Job, tuple, tuple]] = []
        self.amr_busy_time = 0.0
        self.lift_busy_time = [0.0] * len(layout.lifts)
        self.done: list[Job] = []
        self.trucks: dict[int, dict] = {}
        self._fill(fill)

    # ------------------------------------------------------------ 거리·시간
    def amr_dist(self, p: tuple, q: tuple, p_row: float | None, q_row: float | None) -> float:
        """p, q: (x, y). *_row: 그 점이 붙어 있는 AMR 통로 y (도크 점이면 None → 가까운 연결 통로 사용)."""
        if p_row is not None and q_row is not None and p_row == q_row:
            return abs(p[0] - q[0]) + abs(p[1] - p_row) + abs(q[1] - q_row)
        def corrs(pt, row):
            if row is None:                       # 도크 점: 자기 쪽 연결 통로만
                return [CORR_W] if abs(pt[0] - CORR_W) < abs(pt[0] - CORR_E) else [CORR_E]
            return [CORR_W, CORR_E]
        best = float("inf")
        for cp in corrs(p, p_row):
            for cq in corrs(q, q_row):
                if cp == cq:
                    d = abs(p[0] - cp) + abs(p[1] - q[1]) + abs(cp - q[0])
                else:                             # 통로 하나를 가로질러 반대편 연결 통로로
                    d = min(abs(p[0] - cp) + abs(p[1] - r) + abs(cp - cq) + abs(r - q[1]) + abs(cq - q[0])
                            for r in self.L.aisle_rows)
                best = min(best, d)
        return best

    def lift_move(self, a: int, b: int) -> float:
        return 0.0 if a == b else abs(a - b) * LEVEL_H / LIFT_V + LIFT_OVERHEAD

    def mother(self, y0: float, y1: float) -> float:
        d = abs(y0 - y1)
        return 0.0 if d < 1e-6 else d / MOTHER_V + MOTHER_OVERHEAD

    def sat(self, depth_pos: int) -> float:
        return 2 * depth_pos * SLOT / SAT_V + 2 * SAT_XFER

    def _lifts_of(self, rail: int, role: str) -> list[int]:
        return [i for i, l in enumerate(self.L.lifts) if l.rail == rail and role in l.roles]

    def _lane_cost(self, ln: Lane) -> float:
        best = float("inf")
        for i in self._lifts_of(ln.rail, "out") or self._lifts_of(ln.rail, "in"):
            lf = self.L.lifts[i]
            best = min(best, self.mother(lf.y, ln.y) + self.lift_move(0, ln.level))
        return best

    # ------------------------------------------------------------ 초기 재고
    def _fill(self, fill: float) -> None:
        target = int(len(self.lanes) * DEPTH * fill)
        for s in self.skus:
            n = max(DEPTH, int(target * self.demand[s]))
            while n > 0:
                ln = self._new_lane(s, initial=True)
                if ln is None:
                    return
                k = min(DEPTH, n)
                ln.count, n = k, n - k

    def _new_lane(self, sku: str, initial: bool = False) -> Lane | None:
        empties = [ln for ln in self.lanes if ln.sku is None]
        if not empties:
            return None
        if self.P.abc:
            # SKU 누적 수요 순위와 비슷한 비용 순위의 레인 근처에서 찾는다
            target = self.sku_rank[sku]
            key = lambda ln: abs(self.rank[ln.idx] - target)
        else:
            key = lambda ln: self.static_cost[ln.idx]
        if self.P.balance and not initial:
            cands = sorted(empties, key=key)[:40]
            ln = min(cands, key=lambda l: (self._sh_backlog(l.rail, l.level), key(l)))
        else:
            ln = min(empties, key=key)
        ln.sku = sku
        self.by_sku[sku].append(ln)
        return ln

    def _sh_backlog(self, rail: int, level: int) -> int:
        k = (rail, level)
        return len(self.sh_q[k]) + (1 if self.sh_busy[k] else 0)

    # ------------------------------------------------------------ 이벤트 루프
    def at(self, t: float, fn, *args) -> None:
        heapq.heappush(self._ev, (t, next(self._seq), fn, args))

    def run(self) -> dict:
        end = self.hours * 3600
        n_trucks = 0
        # 입고·출고 트럭: 트럭당 26팔레트, 포아송 도착
        for kind in ("in", "out"):
            t = 0.0
            while True:
                t += self.rng.expovariate(self.rate / 26 / 3600)
                if t > end:
                    break
                self.at(t, self._truck, kind, n_trucks)
                n_trucks += 1
        self.dock_free = {("in", d): 0.0 for d in range(6)} | {("out", d): 0.0 for d in range(6)}
        while self._ev:
            t, _, fn, args = heapq.heappop(self._ev)
            if t > end * 3:      # 안전장치
                break
            self.t = t
            fn(*args)
        return self.metrics(end)

    # 트럭 도착 ---------------------------------------------------------
    def _truck(self, kind: str, tid: int) -> None:
        dock = min(range(6), key=lambda d: (self.dock_free[(kind, d)], d))
        start = max(self.t, self.dock_free[(kind, dock)])
        self.trucks[tid] = {"kind": kind, "arrive": self.t, "left": 26, "done": None}
        if kind == "in":
            skus = self.rng.choices(self.skus, [self.demand[s] for s in self.skus], k=3)
            for i in range(26):
                job = Job("in", skus[i % 3], start + (i + 1) * UNLOAD, tid, dock)
                self.at(job.created, self._inbound_ready, job)
            self.dock_free[(kind, dock)] = start + 26 * UNLOAD
        else:
            self.dock_free[(kind, dock)] = start + 60          # 다음 트럭 배정 간격(적치 차선 사용)
            for _ in range(26):
                s = self._pick_sku()
                job = Job("out", s, self.t, tid, dock)
                self._release_out(job)

    def _pick_sku(self) -> str:
        while True:
            s = self.rng.choices(self.skus, [self.demand[x] for x in self.skus])[0]
            if any(ln.available > 0 for ln in self.by_sku[s]):
                return s

    # 입고 ---------------------------------------------------------------
    def _inbound_ready(self, job: Job) -> None:
        lanes = [ln for ln in self.by_sku[job.sku] if ln.free > 0]
        ln = min(lanes, key=lambda l: self.static_cost[l.idx]) if lanes else self._new_lane(job.sku)
        if ln is None:
            raise RuntimeError("storage full")
        ln.reserved += 1
        job.lane = ln
        job.slot_depth = DEPTH - ln.count - ln.reserved     # 안쪽부터 채움 (입구=0)
        cands = self._lifts_of(ln.rail, "in")
        src = self.L.in_pts[job.dock]
        job.lift = min(cands, key=lambda i: self._lift_score(i, ln, src))
        self.lift_load[job.lift] += 1
        lf = self.L.lifts[job.lift]
        self._amr_request(job, src, self.L.dock_row, (lf.x - 3.0, lf.aisle_y), lf.aisle_y, self._lift_in_arrive)

    def _lift_score(self, i: int, ln: Lane, amr_pt: tuple | None) -> float:
        lf = self.L.lifts[i]
        s = self.mother(lf.y, ln.y) + self.lift_load[i] * 25.0
        if amr_pt is not None:
            s += self.amr_dist(amr_pt, (lf.x, lf.aisle_y), self.L.dock_row, lf.aisle_y) / AMR_SPEED
        return s

    def _lift_in_arrive(self, job: Job) -> None:
        self.lift_in[job.lift].append(job)
        self._lift_try(job.lift)

    # 출고 ---------------------------------------------------------------
    def _release_out(self, job: Job) -> None:
        lanes = [ln for ln in self.by_sku[job.sku] if ln.available > 0]
        if self.P.balance:
            ln = min(lanes, key=lambda l: (self._sh_backlog(l.rail, l.level) * 30 + self.static_cost[l.idx]))
        else:
            ln = min(lanes, key=lambda l: self.static_cost[l.idx])
        ln.out_reserved += 1
        job.lane = ln
        job.slot_depth = DEPTH - ln.count + ln.out_reserved - 1
        dst = self.L.out_pts[job.dock]
        job.lift = min(self._lifts_of(ln.rail, "out"), key=lambda i: self._lift_score(i, ln, dst))
        self.lift_load[job.lift] += 1
        self._sh_push((ln.rail, ln.level), ("out", job))

    # 셔틀 ----------------------------------------------------------------
    def _sh_push(self, k, task) -> None:
        self.sh_q[k].append(task)
        self._sh_try(k)

    def _sh_try(self, k) -> None:
        if self.sh_busy[k] or not self.sh_q[k]:
            return
        kind, job = self.sh_q[k].pop(0)
        lf = self.L.lifts[job.lift]
        y = self.sh_y[k]
        if kind == "in":     # 리프트 이송점 → 레인
            d = self.mother(y, lf.y) + SHUTTLE_XFER + self.mother(lf.y, job.lane.y) + self.sat(job.slot_depth)
            end_y = job.lane.y
        else:                # 레인 → 리프트 이송점
            d = self.mother(y, job.lane.y) + self.sat(job.slot_depth) + self.mother(job.lane.y, lf.y) + SHUTTLE_XFER
            end_y = lf.y
        self.sh_busy[k] = True
        self.at(self.t + d, self._sh_done, k, kind, job, end_y)

    def _sh_done(self, k, kind, job: Job, end_y: float) -> None:
        self.sh_busy[k], self.sh_y[k] = False, end_y
        if kind == "in":
            ln = job.lane
            ln.reserved -= 1
            ln.count += 1
            job.done = self.t
            self.lift_load[job.lift] -= 1
            self.done.append(job)
        else:
            self.lift_out[job.lift].append(job)
            self._lift_try(job.lift)
        self._sh_try(k)

    # 리프트 ---------------------------------------------------------------
    def _lift_try(self, i: int) -> None:
        if self.lift_busy[i]:
            return
        ins, outs = self.lift_in[i], self.lift_out[i]
        if not ins and not outs:
            return
        cur = self.lift_level[i]
        t = 0.0
        store = retrieve = None
        if ins and outs and self.P.dual_cycle:
            store = ins.pop(0)
            retrieve = min(outs, key=lambda j: (abs(j.lane.level - store.lane.level), j.created))
            outs.remove(retrieve)
        elif outs and (not ins or outs[0].created <= ins[0].created):
            retrieve = outs.pop(0)
        else:
            store = ins.pop(0)
        if store:
            t += self.lift_move(cur, 0) + LIFT_XFER + self.lift_move(0, store.lane.level) + LIFT_XFER
            cur = store.lane.level
        if retrieve:
            t += self.lift_move(cur, retrieve.lane.level) + LIFT_XFER + self.lift_move(retrieve.lane.level, 0) + LIFT_XFER
            cur = 0
        self.lift_busy[i] = True
        self.lift_busy_time[i] += t
        t_store = (self.t + self.lift_move(self.lift_level[i], 0) + 2 * LIFT_XFER +
                   self.lift_move(0, store.lane.level)) if store else None
        self.at(self.t + t, self._lift_done, i, cur, store, retrieve, t_store)

    def _lift_done(self, i, level, store, retrieve, t_store) -> None:
        self.lift_busy[i], self.lift_level[i] = False, level
        if store:
            self._sh_push((store.lane.rail, store.lane.level), ("in", store))
        if retrieve:
            ln = retrieve.lane
            ln.count -= 1
            ln.out_reserved -= 1
            if ln.count == 0 and ln.reserved == 0 and ln.out_reserved == 0:
                self.by_sku[ln.sku].remove(ln)
                ln.sku = None
            lf = self.L.lifts[i]
            self._amr_request(retrieve, (lf.x + 3.0, lf.aisle_y), lf.aisle_y, self.L.out_pts[retrieve.dock], self.L.dock_row,
                              self._out_delivered)
        self._lift_try(i)

    def _out_delivered(self, job: Job) -> None:
        job.done = self.t
        self.lift_load[job.lift] -= 1
        self.done.append(job)
        tr = self.trucks[job.truck]
        tr["left"] -= 1
        if tr["left"] == 0:
            tr["done"] = self.t

    # AMR ----------------------------------------------------------------
    def _amr_request(self, job, src, src_row, dst, dst_row, cb) -> None:
        self.amr_q.append((job, src, src_row, dst, dst_row, cb))
        self._amr_try()

    def _amr_try(self) -> None:
        while self.amr_q and any(self.amr_free):
            # 가장 오래된 작업 5개 중, 놀고 있는 AMR과 가장 가까운 조합
            best = None
            for qi, (job, src, srow, dst, drow, cb) in enumerate(self.amr_q[:5]):
                for a in range(self.n_amr):
                    if not self.amr_free[a]:
                        continue
                    d = self.amr_dist(self.amr_pos[a], src, self.amr_row[a], srow)
                    if best is None or d < best[0]:
                        best = (d, qi, a)
            d0, qi, a = best
            job, src, srow, dst, drow, cb = self.amr_q.pop(qi)
            d1 = self.amr_dist(src, dst, srow, drow)
            t = (d0 + d1) / AMR_SPEED + 2 * AMR_HANDLE
            self.amr_free[a] = False
            self.amr_busy_time += t
            self.at(self.t + t, self._amr_done, a, dst, drow, job, cb)

    def _amr_done(self, a, dst, drow, job, cb) -> None:
        self.amr_free[a] = True
        self.amr_pos[a], self.amr_row[a] = dst, drow
        cb(job)
        self._amr_try()

    # ------------------------------------------------------------ 결과
    def metrics(self, end: float) -> dict:
        ins = [j for j in self.done if j.kind == "in" and j.created < end]
        outs = [j for j in self.done if j.kind == "out" and j.created < end]
        trucks = [tr for tr in self.trucks.values() if tr["kind"] == "out" and tr["done"] is not None
                  and tr["arrive"] < end]
        n_out_trucks = sum(1 for tr in self.trucks.values() if tr["kind"] == "out" and tr["arrive"] < end)
        span = max(self.t, end)
        moved = sum(1 for j in self.done if j.done <= end)
        return {
            "offered_per_h": 2 * self.rate,
            "throughput_per_h": moved / (end / 3600),
            "putaway_mean_s": statistics.mean(j.done - j.created for j in ins) if ins else float("nan"),
            "putaway_p95_s": _p95([j.done - j.created for j in ins]),
            "retrieval_mean_s": statistics.mean(j.done - j.created for j in outs) if outs else float("nan"),
            "truck_ready_mean_min": statistics.mean((tr["done"] - tr["arrive"]) / 60 for tr in trucks) if trucks else float("nan"),
            "truck_ready_p95_min": _p95([(tr["done"] - tr["arrive"]) / 60 for tr in trucks]),
            "trucks_done": f"{len(trucks)}/{n_out_trucks}",
            "lift_util": sum(self.lift_busy_time) / (len(self.lift_busy_time) * span),
            "amr_util": self.amr_busy_time / (self.n_amr * span),
            "overrun_min": (self.t - end) / 60,
        }


def _p95(xs):
    xs = sorted(xs)
    return xs[int(.95 * (len(xs) - 1))] if xs else float("nan")
