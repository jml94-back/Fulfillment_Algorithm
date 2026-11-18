"""레인 재고 상태와 적재(레인 배정)·출고(레인 선택)·레인 정리 알고리즘.

셔틀 앤 세틀라이트 레인의 규칙
- 레인 하나에는 SKU 한 종만 (깊이 14팔레트).
- 입구가 하나라 나중에 넣은 팔레트를 먼저 꺼낸다(선입후출). 그래서 같은 SKU끼리만 섞이고, 날짜 순서가
  중요한 SKU는 레인을 따로 쓴다(여기서는 SKU 단위로만 관리).
- 레인 안에서는 안쪽부터 채운다. 세틀라이트가 들어가는 거리 = 비어 있는 칸 수.

적재 전략 (StoragePolicy.slotting)
- "abc"    : 같은 SKU 레인 보충 우선 → 빈 레인은 회전율 순위와 레인 접근 시간 순위를 맞춘다(빠른 SKU를 리프트 가까이·낮은 층에).
             무거운 팔레트는 층 허용 하중을 넘지 않게 하고(필수), 무거울수록 낮은 층을 선호한다.
             가벼운 팔레트는 허용 하중이 큰 아래층을 되도록 비워 둔다(무거운 팔레트 자리 확보).
             셔틀 대기열이 긴 레일·층은 피한다(부하 분산).
- "random" : 비교 기준. 허용 하중만 지키고 아무 빈 레인.
출고 전략 (StoragePolicy.retrieval)
- "nearest": 접근 시간이 가장 짧은 레인부터 (기본)
- "fewest" : 그 SKU 레인 중 팔레트가 가장 적은 레인부터 (레인을 빨리 비워 빈 레인을 늘림) + 셔틀 대기 반영
레인 정리 (consolidate): 셔틀이 놀 때, 같은 레일·같은 층에서 같은 SKU의 덜 찬 레인 두 개를 하나로 합친다.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .layout import DEPTH, LEVEL_H, LEVEL_MAX_KG, D4Layout, Lane

# 장비 성능 (가정값, warehouse/shuttle.py 와 같음)
LIFT_V, LIFT_OVERHEAD, LIFT_XFER = 1.5, 2.0, 5.0
MOTHER_V, MOTHER_OVERHEAD = 2.0, 3.0
SAT_V, SAT_XFER = 1.0, 4.0
SHUTTLE_XFER = 4.0
SLOT_M = 1.3


def lift_time(level: int) -> float:
    return 0.0 if level == 0 else level * LEVEL_H / LIFT_V + LIFT_OVERHEAD


def mother_time(y0: float, y1: float) -> float:
    d = abs(y0 - y1)
    return 0.0 if d < 1e-6 else d / MOTHER_V + MOTHER_OVERHEAD


def sat_time(depth_pos: int) -> float:
    """세틀라이트가 입구에서 depth_pos 칸 들어갔다 나오는 시간 (+ 들기·놓기)."""
    return 2 * depth_pos * SLOT_M / SAT_V + 2 * SAT_XFER


@dataclass
class LaneState:
    lane: Lane
    sku: str | None = None
    count: int = 0            # 실제 적재 수
    reserved_in: int = 0      # 들어오기로 배정된 수
    reserved_out: int = 0     # 꺼내기로 배정된 수

    @property
    def space(self) -> int:
        return DEPTH - self.count - self.reserved_in

    @property
    def available(self) -> int:
        return self.count - self.reserved_out

    @property
    def idle(self) -> bool:
        return self.reserved_in == 0 and self.reserved_out == 0


@dataclass
class StoragePolicy:
    slotting: str = "abc"          # "abc" / "random"
    retrieval: str = "nearest"     # "nearest" / "fewest" (8시간 비교에서 nearest가 출고 트럭 시간이 짧았다)
    balance: bool = True           # 셔틀 대기열 반영
    consolidate: bool = True       # 셔틀이 놀 때 레인 합치기


@dataclass
class Storage:
    layout: D4Layout
    policy: StoragePolicy
    sku_rank: dict[str, float]                 # 0 = 가장 많이 나가는 SKU, 1 = 가장 적게
    sku_weight: dict[str, float]               # 신고 무게 (kg)
    rng: random.Random = field(default_factory=lambda: random.Random(0))

    def __post_init__(self) -> None:
        self.lanes = [LaneState(ln) for ln in self.layout.lanes]
        self.by_sku: dict[str, set[int]] = {}
        self.access = {s.lane.idx: self.access_time(s.lane) for s in self.lanes}
        order = sorted(self.lanes, key=lambda s: self.access[s.lane.idx])
        self.access_rank = {s.lane.idx: i / len(order) for i, s in enumerate(order)}

    # ---- 시간 추정 -----------------------------------------------------------
    def access_time(self, lane: Lane) -> float:
        """리프트 바닥 → 레인 입구 → 레인 가운데 깊이까지 한 번 다녀오는 대표 시간."""
        return (2 * lift_time(lane.level) + 2 * mother_time(self.layout.lift_y, lane.y)
                + sat_time(DEPTH // 2))

    # ---- 재고 ---------------------------------------------------------------
    def place_initial(self, idx: int, sku: str, n: int) -> None:
        s = self.lanes[idx]
        s.sku, s.count = sku, n
        self.by_sku.setdefault(sku, set()).add(idx)

    def stock(self, sku: str) -> int:
        return sum(self.lanes[i].available for i in self.by_sku.get(sku, ()))

    def used_lanes(self) -> int:
        return sum(1 for s in self.lanes if s.sku is not None)

    def honeycomb(self) -> float:
        """쓰고 있는 레인에서 비어 있는 칸 비율 (SKU 전용 레인이라 생기는 낭비)."""
        used = [s for s in self.lanes if s.sku is not None]
        if not used:
            return 0.0
        return 1 - sum(s.count for s in used) / (len(used) * DEPTH)

    # ---- 적재: 레인 배정 ----------------------------------------------------
    def choose_lane(self, sku: str, weight: float, queue_len: dict[tuple[int, int], int]) -> LaneState | None:
        ok_level = [lv for lv, m in enumerate(LEVEL_MAX_KG) if weight <= m]
        if not ok_level:
            return None
        lv_ok = set(ok_level)
        if self.policy.slotting == "random":
            cand = [s for s in self.lanes if s.lane.level in lv_ok and
                    ((s.sku == sku and s.space > 0 and s.reserved_out == 0) or (s.sku is None and s.idle))]
            return self.rng.choice(cand) if cand else None

        # 1) 같은 SKU 레인 보충: 꺼내는 중이 아닌 레인 중 가장 많이 찬 레인 (레인 수를 늘리지 않음)
        same = [self.lanes[i] for i in self.by_sku.get(sku, ())
                if self.lanes[i].space > 0 and self.lanes[i].reserved_out == 0 and self.lanes[i].lane.level in lv_ok]
        if same:
            return min(same, key=lambda s: (-(s.count + s.reserved_in), self._q(s, queue_len)))
        # 2) 빈 레인: 회전율 순위 ↔ 접근 시간 순위, 무게 → 낮은 층, 셔틀 대기열
        target = self.sku_rank.get(sku, .5)
        heavy = weight / max(LEVEL_MAX_KG)
        need = min(LEVEL_MAX_KG[lv] for lv in ok_level)     # 이 팔레트에 충분한 가장 약한 층 기준
        best, best_cost = None, float("inf")
        for s in self.lanes:
            if s.sku is not None or not s.idle or s.lane.level not in lv_ok:
                continue
            cost = abs(self.access_rank[s.lane.idx] - target) * 10
            cost += heavy * s.lane.level * .4
            # 가벼운 팔레트가 튼튼한 아래층을 차지하면 무거운 팔레트 자리가 모자란다
            cost += (LEVEL_MAX_KG[s.lane.level] - need) / 100 * 1.5
            if self.policy.balance:
                cost += self._q(s, queue_len) * .6
            if cost < best_cost:
                best, best_cost = s, cost
        return best

    def _q(self, s: LaneState, queue_len: dict[tuple[int, int], int]) -> int:
        return queue_len.get((s.lane.rail, s.lane.level), 0)

    def reserve_in(self, s: LaneState, sku: str) -> None:
        if s.sku is None:
            s.sku = sku
            self.by_sku.setdefault(sku, set()).add(s.lane.idx)
        s.reserved_in += 1

    def commit_in(self, s: LaneState) -> int:
        """팔레트를 레인에 넣는다. 세틀라이트가 들어간 깊이(입구=0)를 돌려준다."""
        depth_pos = DEPTH - s.count - 1
        s.reserved_in -= 1
        s.count += 1
        return depth_pos

    # ---- 출고: 레인 선택 ----------------------------------------------------
    def choose_out_lane(self, sku: str, queue_len: dict[tuple[int, int], int]) -> LaneState | None:
        cand = [self.lanes[i] for i in self.by_sku.get(sku, ()) if self.lanes[i].available > 0
                and self.lanes[i].reserved_in == 0]
        if not cand:   # 들어오는 중인 레인밖에 없으면 그거라도
            cand = [self.lanes[i] for i in self.by_sku.get(sku, ()) if self.lanes[i].available > 0]
        if not cand:
            return None
        if self.policy.retrieval == "nearest":
            return min(cand, key=lambda s: self.access[s.lane.idx])
        w = 30.0 if self.policy.balance else 0.0
        return min(cand, key=lambda s: s.available * 20 + self.access[s.lane.idx] + w * self._q(s, queue_len))

    def reserve_out(self, s: LaneState) -> None:
        s.reserved_out += 1

    def commit_out(self, s: LaneState) -> int:
        """맨 앞 팔레트를 꺼낸다. 세틀라이트 깊이를 돌려주고, 레인이 비면 SKU 표시를 지운다."""
        depth_pos = DEPTH - s.count
        s.count -= 1
        s.reserved_out -= 1
        if s.count == 0 and s.reserved_in == 0 and s.reserved_out == 0:
            self.by_sku[s.sku].discard(s.lane.idx)
            s.sku = None
        return depth_pos

    # ---- 레인 정리 ---------------------------------------------------------
    def consolidation_move(self, rail: int, level: int) -> tuple[LaneState, LaneState] | None:
        """같은 레일·층에서 같은 SKU 레인 둘을 합칠 수 있으면 (꺼낼 레인, 넣을 레인)."""
        by: dict[str, list[LaneState]] = {}
        for s in self.lanes:
            if s.lane.rail == rail and s.lane.level == level and s.sku and s.idle and 0 < s.count < DEPTH:
                by.setdefault(s.sku, []).append(s)
        best = None
        for group in by.values():
            if len(group) < 2:
                continue
            group.sort(key=lambda s: s.count)
            src, dst = group[0], group[-1]
            if src is dst or dst.count + src.count > DEPTH:
                continue
            if best is None or src.count < best[0].count:
                best = (src, dst)
        return best
