"""D4 통합 시뮬레이션: 트럭 → 계량 → AMR → 리프트 → 셔틀 → 레인, 그리고 반대 방향 출고.

시간 단위 1초 (AMR 1.2 m/s, 바닥 칸 1.2 m → 1칸/초).
- AMR 4대: 바닥 격자 위를 시공간 예약 A*(warehouse.fleet.plan_segment)로 움직인다. 먼저 계획한 AMR의
  경로를 예약해 두고 다음 AMR은 그 예약을 피해 계획한다. 통로 방향은 정해 두지 않고 예약으로 그때그때 정해진다.
  작업 하나 = [지금 위치 → 집는 곳(8초) → 놓는 곳(8초) → 쉬는 자리/충전기]를 한 번에 계획·예약.
- AMR 배정: 놀고 있는 AMR과 준비된 작업을 (거리 - 대기 시간 - 출고 버퍼 압박) 비용이 가장 낮은 쌍부터 묶는다.
- 장비 규칙·속도는 Unity 구현(ShuttleRackSystem, 2026-11-19 팀원 명세)에 맞춘다 (storage.py 머리 상수).
- 리프트(레일마다 1기): 기본은 Unity 와 같이 한 번에 한 작업(입고 또는 출고). dual_cycle=True 면 이중 사이클.
  층 이송점은 입·출고 각 1칸(Unity 는 모셔틀에 바로 넘김). 버퍼 끝칸 ↔ 첫칸 컨베이어 10.4 s.
- 셔틀(레일 x 층 = 30세트): 이송점 ↔ 레인. 같은 단 작업은 등록 순서대로(Unity). consolidate=True 면 놀 때 레인 합치기.
- 배터리: 이동 5시간에 100% 소모, 충전 1.5시간에 100%. 쉴 때 70% 미만이면 충전기 자리에서 쉰다(기회 충전).
  25% 미만이면 작업을 받지 않고 충전기로 가서 90%까지 채운다.
"""
from __future__ import annotations

import heapq
import itertools
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from ..fleet import Reservations, plan_segment, verify_no_collisions
from ..pathfinding import DistanceOracle
from .bus import EventBus
from .layout import DEPTH, LEVEL_MAX_KG, LEVELS, PD_BUF, Cell, D4Layout, cx
from .storage import (CONVEYOR_SLOT, LIFT_XFER, SHUTTLE_XFER, LaneState, Storage, StoragePolicy, lift_move, lift_time,
                      mother_time, sat_time)

STAGE_CAP = 28             # 도크별 적치 칸 (4줄 x 7)
HANDLE = 8                 # AMR 팔레트 싣기/내리기 (초)
LEVEL_SLOT = 1             # 층 이송점 칸 수 (입고·출고 각각). Unity 는 리프트가 모셔틀에 바로 넘기므로 1칸(모셔틀 위)으로 근사
CONVEYOR_RUN = (PD_BUF - 1) * CONVEYOR_SLOT   # 버퍼 끝칸 ↔ 첫칸 4칸 = 10.4 s (Unity 컨베이어 0.5 m/s)
WEIGH = 20                 # 계량·라벨 확인 (초)
HORIZON = 900              # 경로 탐색 시간 한계 (초)
BAT_MOVE = 100 / (5 * 3600)
BAT_IDLE = 100 / (24 * 3600)
BAT_CHARGE = 100 / (1.5 * 3600)
BAT_LOW = 25               # 이 아래면 작업을 받지 않고 충전기로 (90%까지)
CHARGE_PREF = 70           # 이 아래면 쉴 때 충전기 자리를 고른다


@dataclass
class SimConfig:
    hours: float = 8.0
    in_per_h: float = 60.0           # 입고 팔레트/시간
    out_per_h: float = 60.0          # 출고 팔레트/시간
    pallets_per_truck: int = 24
    n_amr: int = 4
    n_skus: int = 400
    fill: float = 0.6                # 시작 재고 (용량 대비)
    unload_s: float = 30.0           # 트럭에서 팔레트 1개 내리기 (지게차)
    load_s: float = 30.0             # 출고 트럭에 1개 싣기
    mismatch: float = 0.02           # 신고 무게와 ±5% 넘게 다른 팔레트 비율
    dispatch: str = "cost"           # "cost" (거리·대기·버퍼 압박) / "fifo" (오래된 작업부터)
    dual_cycle: bool = False         # Unity 리프트는 한 번에 한 작업(liftOwner). True = 올라갈 때 입고 + 내려올 때 출고
    shuttle_fifo: bool = True        # Unity: 같은 단 작업은 등록 순서대로
    battery: bool = True
    seed: int = 0
    policy: StoragePolicy = field(default_factory=StoragePolicy)
    record_tracks: bool = True
    bus_keep: bool = True


@dataclass
class Pallet:
    id: int
    sku: str
    declared: float
    measured: float
    kind: str                    # "in" / "out"
    truck: int
    dock: int
    t_created: float
    lane: LaneState | None = None
    t_done: float = -1.0
    held: bool = False
    marks: dict = field(default_factory=dict)     # 단계별 시각 (분석용)


@dataclass
class AmrTask:
    kind: str                    # "in" / "hold" / "out"
    pallet: Pallet
    pick: Cell
    drop: Cell
    ready: float
    lift: int = -1


@dataclass
class Amr:
    id: int
    pos: Cell
    plan: list[Cell] = field(default_factory=list)
    t0: int = 0
    task: AmrTask | None = None
    t_pick: int = -1
    t_drop: int = -1
    park: Cell | None = None
    battery: float = 100.0
    charging: bool = False
    busy: int = 0                # 작업 중(이동·취급) 초

    def at(self, t: int) -> Cell:
        if not self.plan:
            return self.pos
        i = t - self.t0
        return self.plan[min(max(i, 0), len(self.plan) - 1)]


class D4Sim:
    def __init__(self, cfg: SimConfig, layout: D4Layout | None = None) -> None:
        self.cfg = cfg
        self.L = layout or D4Layout()
        self.rng = random.Random(cfg.seed)
        self.oracle = DistanceOracle(self.L)
        self.res = Reservations()
        self.bus = EventBus(keep=cfg.bus_keep)
        self.t = 0
        self._ev: list = []
        self._seq = itertools.count()
        self._pid = itertools.count()
        # SKU: Zipf 수요, 신고 무게
        w = [1 / (r + 1) for r in range(cfg.n_skus)]
        tot = sum(w)
        self.skus = [f"S{r:03d}" for r in range(cfg.n_skus)]
        self.demand = [x / tot for x in w]
        cum, rank = 0.0, {}
        for s, d in zip(self.skus, self.demand):
            rank[s] = cum
            cum += d
        self.weight = {s: round(self.rng.uniform(250, 1300), 0) for s in self.skus}
        self.store = Storage(self.L, cfg.policy, rank, self.weight, random.Random(cfg.seed + 1))
        self._initial_fill()
        # 리프트·이송점·셔틀
        nr = len(self.L.lifts)
        self.in_buf: list[list[Pallet]] = [[] for _ in range(nr)]
        self.in_incoming = [0] * nr
        self.out_buf: list[list[Pallet]] = [[] for _ in range(nr)]
        self.out_incoming = [0] * nr
        self.lift_busy = [False] * nr
        self.lift_time = [0.0] * nr
        self.lvl_in: dict[tuple[int, int], list[Pallet]] = defaultdict(list)
        self.lvl_in_res: dict[tuple[int, int], int] = defaultdict(int)
        self.lvl_out: dict[tuple[int, int], list[Pallet]] = defaultdict(list)
        self.lvl_out_res: dict[tuple[int, int], int] = defaultdict(int)
        self.sh_busy: dict[tuple[int, int], bool] = defaultdict(bool)
        self.sh_y: dict[tuple[int, int], float] = defaultdict(lambda: self.L.xfer_y)
        self.sh_q: dict[tuple[int, int], list] = defaultdict(list)
        self.sh_time: dict[tuple[int, int], float] = defaultdict(float)
        # AMR
        starts = [self.L.chargers[i] for i in (0, 1, 3, 4, 2, 5)] + self.L.waiting   # Unity 시작 위치와 같게
        self.amrs = [Amr(i, starts[i]) for i in range(cfg.n_amr)]
        for a in self.amrs:
            self.res.hold[a.pos] = (a.id, 0)
            a.park = a.pos
        self.tasks: list[AmrTask] = []
        self.tracks: list[list[Cell]] = [[] for _ in self.amrs]
        # 도크·트럭
        self.station: dict[tuple[str, int], list[Pallet]] = defaultdict(list)   # 계량 끝난 팔레트 (입고)
        self.in_dock_free = [True] * len(self.L.in_docks)
        self.out_dock_free = [True] * len(self.L.out_docks)
        self.in_wait: list[int] = []
        self.out_wait: list[int] = []
        self.trucks: dict[int, dict] = {}
        self.loader_free = defaultdict(float)
        self.staged = [0] * len(self.L.in_docks)      # 도크 적치 칸에 놓인 입고 팔레트 수
        # 기록
        self.done_in: list[Pallet] = []
        self.done_out: list[Pallet] = []
        self.held: list[Pallet] = []
        self.unfilled = 0
        self.consolidations = 0
        self.replans = 0
        self.plan_fail = 0
        self._schedule_trucks()

    # ------------------------------------------------------------ 초기 재고
    def _initial_fill(self) -> None:
        target = int(self.L.capacity * self.cfg.fill)
        for s, d in zip(self.skus, self.demand):
            n = max(2, int(round(target * d)))
            while n > 0:
                ln = self.store.choose_lane(s, self.weight[s], {})
                if ln is None:
                    return
                k = min(DEPTH, n) if ln.sku is None else min(ln.space, n)
                if ln.sku is None:
                    self.store.place_initial(ln.lane.idx, s, k)
                else:
                    ln.count += k
                n -= k

    # ------------------------------------------------------------ 사건
    def at(self, t: float, fn, *args) -> None:
        heapq.heappush(self._ev, (t, next(self._seq), fn, args))

    def _schedule_trucks(self) -> None:
        horizon = self.cfg.hours * 3600
        tid = itertools.count()
        for kind, rate in (("in", self.cfg.in_per_h), ("out", self.cfg.out_per_h)):
            if rate <= 0:
                continue
            gap = 3600 * self.cfg.pallets_per_truck / rate
            t = self.rng.uniform(0, gap) if kind == "in" else self.rng.uniform(gap * .3, gap)
            while t < horizon - 1800:
                self.at(t, self._truck_arrive, next(tid), kind)
                t += self.rng.expovariate(1 / gap)

    def _truck_arrive(self, tid: int, kind: str) -> None:
        self.trucks[tid] = {"kind": kind, "arrive": self.t, "dock": -1, "left": self.cfg.pallets_per_truck,
                            "done": -1.0}
        (self.in_wait if kind == "in" else self.out_wait).append(tid)
        self._assign_docks()

    def _assign_docks(self) -> None:
        for kind, wait, free in (("in", self.in_wait, self.in_dock_free), ("out", self.out_wait, self.out_dock_free)):
            while wait and any(free):
                tid = wait.pop(0)
                d = free.index(True)
                free[d] = False
                tr = self.trucks[tid]
                tr["dock"], tr["dock_t"] = d, self.t
                self.bus.publish(self.t, f"wh/dock/{kind.upper()}{d + 1}/truck", {"truck": tid, "event": "arrive"})
                if kind == "in":
                    self.at(self.t + self.cfg.unload_s, self._unloaded, tid, d)
                else:
                    self._release_order(tid, d)

    # ------------------------------------------------------------ 입고
    def _unloaded(self, tid: int, d: int) -> None:
        if self.staged[d] >= STAGE_CAP:          # 도크 적치 칸(28)이 차면 하차를 멈춘다
            self.at(self.t + 10, self._unloaded, tid, d)
            return
        self.staged[d] += 1
        s = self.rng.choices(self.skus, self.demand)[0]
        dec = self.weight[s]
        dev = self.rng.uniform(.06, .15) * self.rng.choice((-1, 1)) if self.rng.random() < self.cfg.mismatch \
            else self.rng.gauss(0, .012)
        p = Pallet(next(self._pid), s, dec, round(dec * (1 + dev), 1), "in", tid, d, self.t)
        self.at(self.t + WEIGH, self._weighed, p)
        tr = self.trucks[tid]
        tr["left"] -= 1
        if tr["left"] > 0:
            self.at(self.t + self.cfg.unload_s, self._unloaded, tid, d)
        if tr["left"] == 0:
            tr["done"] = self.t
            self.in_dock_free[d] = True
            self.bus.publish(self.t, f"wh/dock/IN{d + 1}/truck", {"truck": tid, "event": "depart"})
            self._assign_docks()

    def _weighed(self, p: Pallet) -> None:
        p.marks["weighed"] = self.t
        ok = abs(p.measured - p.declared) <= .05 * p.declared
        self.bus.publish(self.t, f"wh/pallet/{p.id}/inspect",
                         {"sku": p.sku, "station": f"SC-IN{p.dock + 1}", "declared": p.declared,
                          "measured": p.measured, "pass": ok})
        cell = self.L.in_docks[p.dock].cell
        if not ok:
            p.held = True
            self.tasks.append(AmrTask("hold", p, cell, self.L.hold_cell, self.t))
            return
        ln = self.store.choose_lane(p.sku, p.measured, self._queue_len())
        if ln is None:
            self.unfilled += 1
            p.held = True
            self.tasks.append(AmrTask("hold", p, cell, self.L.hold_cell, self.t))
            return
        self.store.reserve_in(ln, p.sku)
        p.lane = ln
        lf = self.L.lifts[ln.lane.rail]
        self.tasks.append(AmrTask("in", p, cell, lf.in_drop, self.t, lift=ln.lane.rail))

    # ------------------------------------------------------------ 출고
    def _release_order(self, tid: int, d: int) -> None:
        n = self.cfg.pallets_per_truck
        for _ in range(n):
            for _try in range(20):
                s = self.rng.choices(self.skus, self.demand)[0]
                ln = self.store.choose_out_lane(s, self._queue_len())
                if ln is not None:
                    break
            else:
                self.trucks[tid]["left"] -= 1
                continue
            self.store.reserve_out(ln)
            p = Pallet(next(self._pid), s, self.weight[s], self.weight[s], "out", tid, d, self.t, lane=ln)
            self._shuttle_add(("retrieve", p))
        if self.trucks[tid]["left"] <= 0:
            self._out_truck_done(tid)

    def _out_truck_done(self, tid: int) -> None:
        tr = self.trucks[tid]
        tr["done"] = max(self.t, self.loader_free[tr["dock"]])
        self.out_dock_free[tr["dock"]] = True
        self.bus.publish(self.t, f"wh/dock/OUT{tr['dock'] + 1}/truck", {"truck": tid, "event": "depart"})
        self._assign_docks()

    # ------------------------------------------------------------ 셔틀
    def _queue_len(self) -> dict[tuple[int, int], int]:
        return {k: len(q) + (1 if self.sh_busy[k] else 0) for k, q in self.sh_q.items()}

    def _shuttle_add(self, job) -> None:
        p = job[1]
        k = (p.lane.lane.rail, p.lane.lane.level)
        self.sh_q[k].append(job)
        self._shuttle_next(k)

    def _shuttle_next(self, k: tuple[int, int]) -> None:
        if self.sh_busy[k]:
            return
        q = self.sh_q[k]
        job = None
        # 이송점이 차서 리프트가 막히지 않게: 입고 이송점이 찼으면 store 먼저, 아니면 retrieve 먼저
        stores = [j for j in q if j[0] == "store"]
        retrs = [j for j in q if j[0] == "retrieve"]
        if self.cfg.shuttle_fifo:
            if q and (q[0][0] == "store" or len(self.lvl_out[k]) + self.lvl_out_res[k] < LEVEL_SLOT):
                job = q[0]
        elif stores and len(self.lvl_in[k]) >= LEVEL_SLOT:
            job = stores[0]
        elif retrs and len(self.lvl_out[k]) + self.lvl_out_res[k] < LEVEL_SLOT:
            job = retrs[0]
        elif stores:
            job = stores[0]
        if job is None:
            if not q and self.cfg.policy.consolidate:
                self._consolidate(k)
            return
        q.remove(job)
        job[1].marks["sh_start"] = self.t
        self.sh_busy[k] = True
        kind, p = job
        ln = p.lane
        y0 = self.sh_y[k]
        if kind == "store":
            t1 = mother_time(y0, self.L.xfer_y) + SHUTTLE_XFER
            depth = DEPTH - ln.count - 1
            t2 = t1 + mother_time(self.L.xfer_y, ln.lane.y) + sat_time(depth)
            self.at(self.t + t1, self._shuttle_took, k, p)
            self.at(self.t + t2, self._shuttle_stored, k, p)
        else:
            self.lvl_out_res[k] += 1
            depth = DEPTH - ln.count
            t1 = mother_time(y0, ln.lane.y) + sat_time(depth)
            t2 = t1 + mother_time(ln.lane.y, self.L.xfer_y) + SHUTTLE_XFER
            self.at(self.t + t1, self._shuttle_fetched, k, p)
            self.at(self.t + t2, self._shuttle_delivered, k, p)
            t2 = t2
        self.sh_time[k] += t2
        self.bus.publish(self.t, f"wh/shuttle/{self.L.shuttle_id(*k)}/task",
                         {"kind": kind, "pallet": p.id, "lane": self.L.lane_id(ln.lane), "slot": depth + 1,
                          "end": round(self.t + t2, 1)})

    def _shuttle_took(self, k, p: Pallet) -> None:
        p.marks["shuttle"] = self.t
        self.lvl_in[k].remove(p)
        self._lift_next(k[0])

    def _shuttle_stored(self, k, p: Pallet) -> None:
        self.store.commit_in(p.lane)
        self.sh_y[k] = p.lane.lane.y
        p.t_done = self.t
        self.done_in.append(p)
        self._lane_msg(p.lane)
        self.sh_busy[k] = False
        self._shuttle_next(k)

    def _shuttle_fetched(self, k, p: Pallet) -> None:
        self.store.commit_out(p.lane)
        self._lane_msg(p.lane)

    def _shuttle_delivered(self, k, p: Pallet) -> None:
        p.marks["at_level"] = self.t
        self.lvl_out_res[k] -= 1
        self.lvl_out[k].append(p)
        self.sh_y[k] = self.L.xfer_y
        self.sh_busy[k] = False
        self._lift_next(k[0])
        self._shuttle_next(k)

    def _consolidate(self, k: tuple[int, int]) -> None:
        mv = self.store.consolidation_move(*k)
        if mv is None:
            return
        src, dst = mv
        n = src.count
        dur = 0.0
        y = self.sh_y[k]
        for i in range(n):
            dur += mother_time(y, src.lane.y) + sat_time(DEPTH - src.count + i) + \
                mother_time(src.lane.y, dst.lane.y) + sat_time(DEPTH - dst.count - i - 1)
            y = dst.lane.y
        src.reserved_out += n
        dst.reserved_in += n
        self.sh_busy[k] = True
        self.sh_time[k] += dur
        self.bus.publish(self.t, f"wh/shuttle/{self.L.shuttle_id(*k)}/task",
                         {"kind": "consolidate", "from": self.L.lane_id(src.lane), "to": self.L.lane_id(dst.lane),
                          "n": n,
                          "end": round(self.t + dur, 1)})
        self.at(self.t + dur, self._consolidated, k, src, dst, n)

    def _consolidated(self, k, src: LaneState, dst: LaneState, n: int) -> None:
        for _ in range(n):
            self.store.commit_out(src)
            self.store.commit_in(dst)
        self.sh_y[k] = dst.lane.y
        self.consolidations += 1
        self.sh_busy[k] = False
        self._shuttle_next(k)

    # ------------------------------------------------------------ 리프트
    def _lift_next(self, r: int) -> None:
        if self.lift_busy[r]:
            return
        up = next((p for p in self.in_buf[r] if p.marks.get("conv_ready", 0) <= self.t
                   and len(self.lvl_in[(r, p.lane.lane.level)]) + self.lvl_in_res[(r, p.lane.lane.level)] < LEVEL_SLOT),
                  None)
        down = None
        if len(self.out_buf[r]) + self.out_incoming[r] < PD_BUF:
            waiting = [(lv, self.lvl_out[(r, lv)][0]) for lv in range(LEVELS) if self.lvl_out[(r, lv)]]
            if waiting:
                lv_up = up.lane.lane.level if up else 0
                down = min(waiting, key=lambda w: (w[1].t_created, abs(w[0] - lv_up)))
        if up is None and down is None:
            return
        if up is not None and down is not None and not self.cfg.dual_cycle:
            down = None if up.t_created <= down[1].t_created else down
            up = None if down is not None else up
        self.lift_busy[r] = True
        t = LIFT_XFER if up else 0.0
        lv = None                       # None = 버퍼 높이
        t_up = None
        if up:
            self.in_buf[r].remove(up)
            up.marks["lift"] = self.t
            kk = (r, up.lane.lane.level)
            self.lvl_in_res[kk] += 1
            t += lift_time(up.lane.lane.level) + LIFT_XFER
            lv = up.lane.lane.level
            t_up = t
            self.at(self.t + t_up, self._lift_dropped, r, up)
            self.tasks_lift_freed(r)
        if down:
            dlv, dp = down
            self.lvl_out[(r, dlv)].remove(dp)
            self.out_incoming[r] += 1
            t += lift_move(lv, dlv) + LIFT_XFER
            lv = dlv
            self._shuttle_next((r, dlv))
        t += lift_move(lv, None) + (LIFT_XFER if down else 0.0)
        self.lift_time[r] += t
        self.bus.publish(self.t, f"wh/lift/{self.L.lift_id(r)}/cycle",
                         {"up": {"pallet": up.id, "level": up.lane.lane.level + 1} if up else None,
                          "down": {"pallet": down[1].id, "level": down[0] + 1} if down else None,
                          "end": round(self.t + t, 1)})
        self.at(self.t + t, self._lift_done, r, down[1] if down else None)

    def tasks_lift_freed(self, r: int) -> None:
        """입고 버퍼에 자리가 났다: 그 리프트로 가는 AMR 작업이 다시 준비될 수 있다 (배정 때 확인)."""

    def _lift_dropped(self, r: int, p: Pallet) -> None:
        kk = (r, p.lane.lane.level)
        self.lvl_in_res[kk] -= 1
        self.lvl_in[kk].append(p)
        self._shuttle_add(("store", p))

    def _lift_done(self, r: int, p: Pallet | None) -> None:
        if p is not None:
            p.marks["out_buf"] = self.t
            self.out_incoming[r] -= 1
            self.out_buf[r].append(p)
            dock = self.L.out_docks[p.dock].cell
            self.tasks.append(AmrTask("out", p, self.L.lifts[r].out_pick, dock, self.t + CONVEYOR_RUN, lift=r))
        self.lift_busy[r] = False
        self._lift_next(r)

    # ------------------------------------------------------------ AMR
    def _task_ready(self, tk: AmrTask) -> bool:
        if tk.ready > self.t:
            return False
        if tk.kind == "in":
            return len(self.in_buf[tk.lift]) + self.in_incoming[tk.lift] < PD_BUF
        return True

    def _available(self, a: Amr) -> bool:
        return a.task is None and not a.charging

    def _dispatch(self) -> None:
        free = [a for a in self.amrs if self._available(a)]
        if not free or not self.tasks:
            return
        ready = [tk for tk in self.tasks if self._task_ready(tk)]
        if not ready:
            return
        # 같은 도크에서는 먼저 계량된 팔레트부터 (도크 앞 칸 하나를 여러 대가 노리지 않게 1개씩만 후보)
        seen, cand = defaultdict(int), []
        for tk in sorted(ready, key=lambda k: k.ready):
            if seen[tk.pick] >= 2:
                continue
            seen[tk.pick] += 1
            cand.append(tk)
        # 한 접점에 이미 2대가 가는 중이면 그 접점 작업은 미룬다 (두 번째 차는 예약 때문에 앞 차가 떠난 뒤 도착)
        heading = defaultdict(int)
        for b in self.amrs:
            if b.task is not None and b.t_pick > self.t:
                heading[b.task.pick] += 1
        cand = [tk for tk in cand if heading[tk.pick] < 2]
        pairs = []
        for a in free:
            here = a.at(self.t)
            for tk in cand:
                if self.cfg.dispatch == "fifo":
                    cost = tk.ready * 10 + self.oracle.dist(here, tk.pick) * 1e-3
                else:
                    age = self.t - tk.ready
                    press = len(self.out_buf[tk.lift]) * 25 if tk.kind == "out" else 0
                    cost = self.oracle.dist(here, tk.pick) + self.oracle.dist(tk.pick, tk.drop) * .3 - age / 6 - press
                pairs.append((cost, a.id, id(tk), a, tk))
        pairs.sort(key=lambda x: x[:3])
        used_a, used_t = set(), set()
        for _, _, _, a, tk in pairs:
            if a.id in used_a or id(tk) in used_t:
                continue
            if tk.kind == "in" and len(self.in_buf[tk.lift]) + self.in_incoming[tk.lift] >= PD_BUF:
                continue
            if self._plan_trip(a, tk):
                used_a.add(a.id)
                used_t.add(id(tk))
                self.tasks.remove(tk)
                tk.pallet.marks["amr_assigned"] = self.t
                if tk.kind == "in":
                    self.in_incoming[tk.lift] += 1

    def _pick_park(self, a: Amr, frm: Cell, charge: bool) -> list[Cell]:
        taken = {b.park for b in self.amrs if b.id != a.id and b.park is not None}
        spots = self.L.chargers if charge else self.L.waiting + self.L.chargers
        spots = [c for c in spots if c not in taken]
        return sorted(spots, key=lambda c: self.oracle.dist(frm, c))

    def _plan_trip(self, a: Amr, tk: AmrTask | None, charge: bool = False) -> bool:
        """a의 계획을 지금부터 다시 세운다: [집기 → 놓기] → 쉬는 자리. 실패하면 원래 계획을 되살린다."""
        t = self.t
        start = a.at(t)
        old_plan, old_t0, old_park = a.plan, a.t0, a.park
        old_hold = self.res.hold.get(old_park) if old_park else None
        self.res.release(a.id, t)
        if old_park and old_hold and old_hold[0] == a.id:
            del self.res.hold[old_park]
        legs: list[list[Cell]] = []
        cur, tc = start, t
        ok = True
        t_pick = t_drop = -1
        if tk is not None:
            for goal in (tk.pick, tk.drop):
                path = plan_segment(self.L, self.oracle, self.res, a.id, cur, tc, goal, HANDLE, False, HORIZON)
                if path is None:
                    ok = False
                    break
                self.res.reserve(path, tc, a.id)
                legs.append(path)
                tc += len(path) - 1
                cur = goal
                if goal is tk.pick:
                    t_pick = tc
                else:
                    t_drop = tc
        park = None
        if ok:
            ok = False
            low = self.cfg.battery and a.battery < CHARGE_PREF
            for spot in self._pick_park(a, cur, charge or low)[:4]:
                path = plan_segment(self.L, self.oracle, self.res, a.id, cur, tc, spot, 0, True, HORIZON)
                if path is not None:
                    self.res.reserve(path, tc, a.id)
                    legs.append(path)
                    self.res.hold[spot] = (a.id, tc + len(path) - 1)
                    park, ok = spot, True
                    break
        if not ok:
            self.plan_fail += 1
            self.res.release(a.id, t)
            if old_plan:
                rest = old_plan[max(t - old_t0, 0):] or [start]
                self.res.reserve(rest, t, a.id)
            else:
                self.res.reserve([start], t, a.id)
            if old_park and old_hold:
                self.res.hold[old_park] = old_hold
            return False
        plan = [start]
        for leg in legs:
            plan += leg[1:]
        a.plan, a.t0, a.park = plan, t, park
        a.task, a.t_pick, a.t_drop = tk, t_pick, t_drop
        if old_plan:
            self.replans += 1
        if tk is not None:
            self.bus.publish(t, f"wh/amr/{self.L.amr_id(a.id)}/task", {"kind": tk.kind, "pallet": tk.pallet.id, "sku": tk.pallet.sku,
                       "pick": self._place(tk.pick), "drop": self._place(tk.drop)})
        self.bus.publish(t, f"wh/amr/{self.L.amr_id(a.id)}/path", {"points": self._corners(plan, t)})
        return True

    def _place(self, c: Cell) -> dict:
        name = self.L.contacts.get(c, (None,))[0]
        return {"id": name, "pos": self.L.point(c)}

    def _lane_msg(self, ln: LaneState) -> None:
        self.bus.publish(self.t, f"wh/lane/{self.L.lane_id(ln.lane)}", {"sku": ln.sku, "count": ln.count})

    def _corners(self, plan: list[Cell], t0: int) -> list[list[float]]:
        out = []
        for i, c in enumerate(plan):
            if 0 < i < len(plan) - 1:
                p, n = plan[i - 1], plan[i + 1]
                if (c[0] - p[0], c[1] - p[1]) == (n[0] - c[0], n[1] - c[1]) and c != p:
                    continue
            x, _, z = self.L.point(c)
            out.append([t0 + i, x, z])
        return out

    def _amr_events(self) -> None:
        t = self.t
        for a in self.amrs:
            here = a.at(t)
            prev = a.at(t - 1)
            moving = here != prev
            tk = a.task
            if tk is not None or moving:
                a.busy += 1
            if self.cfg.battery:
                docked = tk is None and here == a.park and a.park in self.L.chargers and t >= a.t0 + len(a.plan) - 1
                if docked:      # 쉬는 동안 충전기에 붙어 있으면 언제든 충전 (기회 충전)
                    a.battery = min(100.0, a.battery + BAT_CHARGE)
                    if a.charging and a.battery >= 90:
                        a.charging = False
                        self.bus.publish(t, f"wh/amr/{self.L.amr_id(a.id)}/event", {"event": "charge_end",
                                                                                   "battery": round(a.battery, 1)})
                else:
                    a.battery -= BAT_MOVE if (moving or tk) else BAT_IDLE
            if tk is None:
                continue
            if t == a.t_pick:
                if tk.kind == "out":
                    self.out_buf[tk.lift].remove(tk.pallet)
                    self._lift_next(tk.lift)
                tk.pallet.marks["picked"] = t
                if tk.kind in ("in", "hold") and tk.pallet.kind == "in":
                    self.staged[tk.pallet.dock] -= 1
                self.bus.publish(t, f"wh/amr/{self.L.amr_id(a.id)}/event", {"event": "pick", "pallet": tk.pallet.id})
            if t == a.t_drop:
                self.bus.publish(t, f"wh/amr/{self.L.amr_id(a.id)}/event", {"event": "drop", "pallet": tk.pallet.id})
                p = tk.pallet
                p.marks["dropped"] = t
                if tk.kind == "in":
                    self.in_incoming[tk.lift] -= 1
                    p.marks["conv_ready"] = t + CONVEYOR_RUN        # 끝칸(IN5) → 첫칸(IN1)
                    self.in_buf[tk.lift].append(p)
                    self.at(t + CONVEYOR_RUN, self._lift_next, tk.lift)
                elif tk.kind == "hold":
                    p.t_done = t
                    self.held.append(p)
                else:
                    tr = self.trucks[p.truck]
                    self.loader_free[p.dock] = max(self.loader_free[p.dock], t) + self.cfg.load_s
                    p.t_done = self.loader_free[p.dock]
                    self.done_out.append(p)
                    tr["left"] -= 1
                    if tr["left"] == 0:
                        self._out_truck_done(p.truck)
                a.task = None
                if self.cfg.battery and a.battery < BAT_LOW and not a.charging:
                    a.charging = True
                    self.bus.publish(t, f"wh/amr/{self.L.amr_id(a.id)}/event", {"event": "charge_start",
                                                                 "battery": round(a.battery, 1)})
                    self._plan_trip(a, None, charge=True)

    # ------------------------------------------------------------ 실행
    def run(self) -> dict:
        end = int(self.cfg.hours * 3600)
        for t in range(end):
            self.t = t
            while self._ev and self._ev[0][0] <= t:
                _, _, fn, args = heapq.heappop(self._ev)
                fn(*args)
            self._amr_events()
            self._dispatch()
            if self.cfg.record_tracks:
                for a in self.amrs:
                    self.tracks[a.id].append(a.at(t))
            if t % 600 == 0:
                self.res.prune(t - 2)
        return self.metrics()

    def metrics(self) -> dict:
        H = self.cfg.hours
        d2s = [p.t_done - p.t_created for p in self.done_in]
        out_tr = [tr["done"] - tr["arrive"] for tr in self.trucks.values() if tr["kind"] == "out" and tr["done"] >= 0]
        in_tr = [tr["done"] - tr["arrive"] for tr in self.trucks.values() if tr["kind"] == "in" and tr["done"] >= 0]
        sh = list(self.sh_time.values())

        def p95(v):
            return sorted(v)[int(.95 * (len(v) - 1))] if v else 0.0
        return {
            "in_per_h": len(self.done_in) / H, "out_per_h": len(self.done_out) / H,
            "held": len(self.held), "no_lane": self.unfilled,
            "dock_to_stock_mean_s": statistics.mean(d2s) if d2s else 0.0, "dock_to_stock_p95_s": p95(d2s),
            "out_truck_mean_min": statistics.mean(out_tr) / 60 if out_tr else 0.0,
            "out_truck_p95_min": p95(out_tr) / 60,
            "in_truck_mean_min": statistics.mean(in_tr) / 60 if in_tr else 0.0,
            "trucks_waiting_end": len(self.in_wait) + len(self.out_wait),
            "amr_util": statistics.mean(a.busy for a in self.amrs) / (H * 3600),
            "lift_util": statistics.mean(self.lift_time) / (H * 3600),
            "shuttle_util": (sum(sh) / (len(self.L.rail_x) * LEVELS)) / (H * 3600),
            "used_lanes": self.store.used_lanes(), "honeycomb": self.store.honeycomb(),
            "consolidations": self.consolidations, "replans": self.replans, "plan_fail": self.plan_fail,
            "battery_min": min(a.battery for a in self.amrs),
            "collisions": verify_no_collisions(self.tracks) if self.cfg.record_tracks else -1,
        }
