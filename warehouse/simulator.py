"""적재·출고 이산 시뮬레이션 (단일 AMR).

모델
----
- 토트(bin) 단위 창고: 슬롯 1개 = 토트 1개 = SKU 1종.
- AMR은 한 번에 최대 `capacity`개 토트를 싣고 도크 → 슬롯들 → 도크로 돈다(trip).
- 출고 주문: 주문마다 1~max_lines개 라인, 라인 1개 = 토트 1개 꺼내기.
  SKU는 파레토(80/20) 수요 분포에서 뽑힌다.
- 출고 대상 토트 선택: FIFO(가장 오래된 것) 또는 nearest(가장 가까운 것).
- 재고가 재주문점 이하로 떨어지면 lot_size개 토트가 입고되고, 적재 전략이 위치를 정한다.

외부 의사결정(RL)
-----------------
핵심 루프 `_process()`는 제너레이터다. 적재 위치가 필요할 때마다
`StorageRequest`를 yield 하고 슬롯을 send 받는다.
- `run()` 은 이 요청에 적재 전략으로 답한다.
- `env.SlottingEnv` 는 이 요청을 RL 에이전트의 행동으로 답한다.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Generator, Literal

from .inventory import Inventory, Pallet
from .layout import Cell, Slot, WarehouseLayout
from .pathfinding import DistanceOracle, astar
from .routing import ROUTERS, tour_length
from .slotting import SlottingStrategy


@dataclass
class SimConfig:
    n_skus: int = 40
    pareto_s: float = 1.2            # 수요 분포 기울기 (클수록 상위 SKU 쏠림)
    capacity: int = 6                # AMR 1회 적재 토트 수
    max_lines: int = 3               # 주문당 최대 라인 수
    lot_size: int = 6                # 입고 1회 토트 수
    reorder_point: int = 2
    initial_fill: float = 0.55       # 시작 시 창고 점유율 목표
    n_orders: int = 1500
    retrieval: Literal["fifo", "nearest"] = "fifo"
    routing: str = "nn2opt"
    seed: int = 0
    record_trips: int = 0            # Unity 내보내기용으로 앞 N개 trip의 상세 기록
    # 검수(하중 센서)
    weight_range: tuple[float, float] = (80, 1100)   # SKU별 팔레트 기준 무게 범위(kg)
    mismatch_rate: float = 0.02      # 송장과 실제 무게가 다른 팔레트 비율
    weight_tolerance: float = 0.05   # 송장 대비 허용 오차 (넘으면 보류)
    hold_orders: int = 10            # 보류 팔레트 재검수까지 걸리는 주문 수
    # 비상 구역: (발생 주문 번호, emergency.Zone, 해제 주문 번호 or None)
    emergencies: list = field(default_factory=list)


@dataclass
class Stop:
    slot: Slot
    action: Literal["store", "pick"]
    sku: str
    pallet_id: int


@dataclass
class Trip:
    kind: Literal["inbound", "outbound"]
    stops: list[Stop]
    distance: int
    lift: float
    released_at: float = 0.0       # 이 작업이 생긴 시각(주문 번호 기준), 다중 AMR 실행 계층이 사용


@dataclass
class StorageRequest:
    pallet: Pallet


@dataclass
class SimResult:
    trips: int = 0
    lines_picked: int = 0
    lines_stored: int = 0
    stockouts: int = 0
    held: int = 0                    # 무게 불일치로 검수 보류된 팔레트 수
    weight_violations: int = 0       # 허용 하중 초과 적재 수
    distance_out: int = 0
    distance_in: int = 0
    lift_cost: float = 0.0
    visits: dict[Cell, int] = field(default_factory=dict)
    recorded: list[Trip] = field(default_factory=list)

    @property
    def distance(self) -> int:
        return self.distance_out + self.distance_in

    @property
    def total_cost(self) -> float:
        """이동거리(칸) + 층 승강 비용(칸 환산)."""
        return self.distance + self.lift_cost

    @property
    def cost_per_line(self) -> float:
        return self.total_cost / max(self.lines_picked + self.lines_stored, 1)

    @property
    def dist_per_pick(self) -> float:
        return self.distance_out / max(self.lines_picked, 1)

    @property
    def dist_per_line(self) -> float:
        return self.distance / max(self.lines_picked + self.lines_stored, 1)


def zipf_demand(n: int, s: float) -> dict[str, float]:
    w = [1 / (r + 1) ** s for r in range(n)]
    tot = sum(w)
    return {f"SKU{r:03d}": w[r] / tot for r in range(n)}


class Simulator:
    def __init__(self, layout: WarehouseLayout, cfg: SimConfig, oracle: DistanceOracle | None = None) -> None:
        self.layout = layout
        self.cfg = cfg
        self.oracle = oracle or DistanceOracle(layout)
        self.demand = zipf_demand(cfg.n_skus, cfg.pareto_s)
        wrng = random.Random(12345)    # SKU 무게는 시드와 무관하게 고정 (수요 순위와 무관)
        self.sku_weight = {s: wrng.uniform(*cfg.weight_range) for s in self.demand}
        self.route_fn = ROUTERS[cfg.routing]

    # ---- 공개 API -----------------------------------------------------
    def run(self, strategy: SlottingStrategy) -> SimResult:
        gen = self.process()
        try:
            req = next(gen)
            while True:
                req = gen.send(strategy.choose(req.pallet, self.inventory))
        except StopIteration as stop:
            return stop.value

    def process(self) -> Generator[StorageRequest, Slot, SimResult]:
        """적재 결정마다 StorageRequest를 yield 하는 핵심 루프."""
        cfg = self.cfg
        self.rng = random.Random(cfg.seed)
        self.inventory = Inventory(self.layout)
        self.clock = 0.0
        self._next_id = 0
        self.result = SimResult()
        self._pending: set[str] = set()   # 입고 대기 중인 SKU
        skus = list(self.demand)
        weights = list(self.demand.values())

        # 초기 재고: 수요와 무관하게 SKU마다 같은 수의 토트 (워밍업, 통계 미포함)
        per_sku = max(1, int(len(self.layout.slots) * cfg.initial_fill / cfg.n_skus))
        warm = [self._new_pallet(s) for s in skus for _ in range(per_sku)]
        self.rng.shuffle(warm)
        for p in warm:
            slot = yield StorageRequest(p)
            self.inventory.store(p, slot)
        self._warm_violations = self.inventory.violations
        self.result = SimResult()

        inbound_queue: list[Pallet] = []
        pick_queue: list[str] = []
        on_hold: list[tuple[float, Pallet]] = []    # (재검수 완료 시각, 팔레트)
        for _ in range(cfg.n_orders):
            self.clock += 1
            # 재검수 끝난 보류 팔레트는 실측 무게로 송장을 정정해 입고 대기열로
            done = [p for t, p in on_hold if t <= self.clock]
            on_hold = [(t, p) for t, p in on_hold if t > self.clock]
            inbound_queue += [Pallet(p.pallet_id, p.sku, p.arrived_at, p.weight_kg, p.weight_kg) for p in done]
            for start, zone, until in cfg.emergencies:
                if self.clock == start:
                    self.inventory.block_slots(zone.slots)
                if until is not None and self.clock == until:
                    self.inventory.unblock_slots(zone.slots)
            for _ in range(self.rng.randint(1, cfg.max_lines)):
                pick_queue.append(self.rng.choices(skus, weights)[0])

            while len(pick_queue) >= cfg.capacity:
                batch, pick_queue = pick_queue[: cfg.capacity], pick_queue[cfg.capacity :]
                for p in self._outbound_trip(batch):
                    if self.inspect(p):
                        inbound_queue.append(p)
                    else:
                        self.result.held += 1
                        on_hold.append((self.clock + cfg.hold_orders, p))
            while len(inbound_queue) >= cfg.capacity:
                batch, inbound_queue = inbound_queue[: cfg.capacity], inbound_queue[cfg.capacity :]
                yield from self._inbound_trip(batch)
        self.result.weight_violations = self.inventory.violations - self._warm_violations
        return self.result

    # ---- 내부 ---------------------------------------------------------
    def _new_pallet(self, sku: str) -> Pallet:
        """입고 팔레트 생성 + 검수대 하중 센서 측정 시뮬레이션."""
        self._next_id += 1
        declared = self.sku_weight[sku] * self.rng.uniform(0.95, 1.05)
        measured = declared * self.rng.uniform(0.99, 1.01)            # 센서 오차 ±1%
        if self.rng.random() < self.cfg.mismatch_rate:                # 오배송·파손·수량 오류
            measured = declared * self.rng.choice([-1, 1]) * self.rng.uniform(0.15, 0.30) + declared
        return Pallet(self._next_id, sku, self.clock, round(measured, 1), round(declared, 1))

    def inspect(self, p: Pallet) -> bool:
        """검수: 송장 대비 무게 차이가 허용 오차 안이면 통과."""
        return abs(p.weight_kg - p.declared_kg) <= self.cfg.weight_tolerance * p.declared_kg

    def _pick_target(self, sku: str, taken: set[int]) -> Pallet | None:
        cands = [p for p in self.inventory.pallets_of(sku) if p.pallet_id not in taken]
        if not cands:
            return None
        if self.cfg.retrieval == "fifo":
            return min(cands, key=lambda p: (p.arrived_at, p.pallet_id))
        L, inv = self.layout, self.inventory
        return min(
            cands,
            key=lambda p: (self.oracle.dist(L.dock, L.access_point(inv.slot_of[p.pallet_id]))
                           + inv.slot_of[p.pallet_id][2] * L.level_cost, p.pallet_id),
        )

    def _outbound_trip(self, batch: list[str]) -> list[Pallet]:
        stops: list[Stop] = []
        taken: set[int] = set()
        for sku in batch:
            p = self._pick_target(sku, taken)
            if p is None:
                self.result.stockouts += 1
                continue
            taken.add(p.pallet_id)
            stops.append(Stop(self.inventory.slot_of[p.pallet_id], "pick", sku, p.pallet_id))
        self._execute("outbound", stops)

        replenish: list[Pallet] = []
        for st in stops:
            self.inventory.remove(st.pallet_id)
            self.result.lines_picked += 1
        for sku in sorted({st.sku for st in stops}):  # 정렬: 실행마다 결과가 같도록
            if self.inventory.stock(sku) <= self.cfg.reorder_point and sku not in self._pending:
                replenish += [self._new_pallet(sku) for _ in range(self.cfg.lot_size)]
                self._pending.add(sku)
        return replenish

    def _inbound_trip(self, batch: list[Pallet]) -> Generator[StorageRequest, Slot, None]:
        stops: list[Stop] = []
        for p in batch:
            slot = yield StorageRequest(p)
            self.inventory.store(p, slot)   # 슬롯 예약 (같은 trip 안에서 중복 방지)
            stops.append(Stop(slot, "store", p.sku, p.pallet_id))
            self.result.lines_stored += 1
            self._pending.discard(p.sku)
        self._execute("inbound", stops)

    def _execute(self, kind: str, stops: list[Stop]) -> None:
        if not stops:
            return
        L = self.layout
        by_ap: dict[Cell, list[Stop]] = {}
        for st in stops:
            by_ap.setdefault(L.access_point(st.slot), []).append(st)
        order = self.route_fn(L, self.oracle, L.dock, list(by_ap))
        ordered_stops = [st for ap in order for st in by_ap[ap]]
        dist = tour_length(self.oracle, L.dock, order)
        lift = sum(st.slot[2] * L.level_cost for st in stops)

        r = self.result
        r.trips += 1
        if kind == "outbound":
            r.distance_out += dist
        else:
            r.distance_in += dist
        r.lift_cost += lift
        for st in stops:
            c = (st.slot[0], st.slot[1])
            r.visits[c] = r.visits.get(c, 0) + 1
        if len(r.recorded) < self.cfg.record_trips:
            r.recorded.append(Trip(kind, ordered_stops, dist, lift, self.clock))

    # ---- Unity 내보내기 ----------------------------------------------------
    def trip_to_dict(self, trip: Trip) -> dict:
        """Unity에서 AMR이 그대로 따라갈 수 있는 작업 명세."""
        L = self.layout
        start = L.inbound_dock if trip.kind == "inbound" else L.dock
        waypoints: list[Cell] = [start]
        aps = []
        for st in trip.stops:
            ap = L.access_point(st.slot)
            if not aps or aps[-1] != ap:
                aps.append(ap)
        for ap in [*aps, L.dock]:
            seg = astar(L, waypoints[-1], ap)
            waypoints += seg[1:]
        return {
            "kind": trip.kind,
            "distance_cells": trip.distance,
            "stops": [
                {
                    "action": st.action,
                    "sku": st.sku,
                    "pallet_id": st.pallet_id,
                    "slot": list(st.slot),
                    "weight_kg": self.inventory.pallet_at[st.slot].weight_kg
                    if st.slot in self.inventory.pallet_at and self.inventory.pallet_at[st.slot].pallet_id == st.pallet_id
                    else None,
                    "access_point": list(L.access_point(st.slot)),
                    "world": L.to_world(st.slot),
                }
                for st in trip.stops
            ],
            "waypoints": [list(c) for c in waypoints],
            "waypoints_world": [L.to_world(c) for c in waypoints],
        }
