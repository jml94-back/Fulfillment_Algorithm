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

from .layout import DEPTH, LEVEL_H, LEVEL_MAX_KG, LEVELS, D4Layout, Lane

# 장비 성능: Unity 구현(ShuttleRackSystem·LiftController·ShuttleController 기본값, 2026-11-19 팀원 명세)
# 이동은 사다리꼴 속도(가속 → 최고 속도 → 감속). 이송은 등속.
LIFT_V, LIFT_A = 1.5, 0.8              # 리프트 m/s, m/s²
MOTHER_V, MOTHER_A = 2.5, 1.0          # 모셔틀
SAT_V, SAT_A = 1.2, 0.8                # 새틀라이트
PLATE_S = 0.05 / 0.08                  # 새틀라이트 판 0.05 m 승강 (0.08 m/s) = 0.625 s
XFER_V = 0.6                           # 이송 (버퍼 ↔ 리프트, 리프트 ↔ 모셔틀) 등속 m/s
XFER_DIST = 1.95                       # (추정) 이송 거리. 명세의 "리프트 1회 점유 약 18.5 s"(승강 2 x 6.0 s + 이송 2회)에서 역산
CONVEYOR_V, BUF_PITCH = 0.5, 1.3       # 버퍼 컨베이어 0.5 m/s, 칸 간격 1.3 m → 한 칸 2.6 s
BUF_TOP = 0.50                         # 버퍼 롤러 윗면 (리프트 바닥 위치)
RAIL_TOP0 = 0.40                       # 1단 레일 윗면, 단마다 +LEVEL_H(1.45)
SLOT_M = 1.25                          # 레인 칸 간격
AISLE_HALF = 1.0                       # 통로 중심 → 레인 입구 (칸 위치 x = ±(1.0 + (칸-0.5) x 1.25))
LIFT_XFER = XFER_DIST / XFER_V         # 3.25 s
SHUTTLE_XFER = XFER_DIST / XFER_V
CONVEYOR_SLOT = BUF_PITCH / CONVEYOR_V  # 2.6 s
# Unity 레인 배정 비용의 가중치
UNITY_QUEUE_S = 20.0                   # 단별 대기 작업 1개당
UNITY_EMPTY_W = 0.1                    # 빈칸 1개당


def trap_time(d: float, v: float, a: float) -> float:
    """정지 → 정지 이동 시간 (사다리꼴, 짧으면 삼각형)."""
    d = abs(d)
    if d < 1e-9:
        return 0.0
    if d >= v * v / a:
        return d / v + v / a
    return 2 * (d / a) ** .5


def level_height(level: int | None) -> float:
    """리프트 위치 높이. level None = 버퍼(바닥), 0~9 = 1~10단 레일 윗면."""
    return BUF_TOP if level is None else RAIL_TOP0 + LEVEL_H * level


def lift_move(a: int | None, b: int | None) -> float:
    return trap_time(level_height(a) - level_height(b), LIFT_V, LIFT_A)


def lift_time(level: int) -> float:
    """버퍼 높이 ↔ level(0~9) 단 편도."""
    return lift_move(None, level)


def mother_time(y0: float, y1: float) -> float:
    return trap_time(y1 - y0, MOTHER_V, MOTHER_A)


def sat_distance(depth_pos: int) -> float:
    """통로 중심에서 레인 칸 중심까지. depth_pos 는 0부터 (0 = 칸 01 맨 앞, 13 = 칸 14 가장 안쪽)."""
    return AISLE_HALF + (max(depth_pos, 0) + .5) * SLOT_M


def sat_time(depth_pos: int) -> float:
    """새틀라이트가 칸(0부터)까지 들어갔다 나오는 시간 + 판 올리기·내리기."""
    return 2 * trap_time(sat_distance(depth_pos), SAT_V, SAT_A) + 2 * PLATE_S


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
    slotting: str = "unity"        # "unity" (Unity ShuttleRackSystem 비용식, 기본) / "abc" / "random"
    weight_limits: bool = True     # 층별 허용 하중 (안전 규칙. Unity 에는 아직 없음 → Unity 쪽 미구현 목록에 올림)
    retrieval: str = "nearest"     # "nearest" / "fewest" (8시간 비교에서 nearest가 출고 트럭 시간이 짧았다)
    balance: bool = True           # 셔틀 대기열 반영
    consolidate: bool = False      # 셔틀이 놀 때 레인 합치기 (Unity 에는 없음 → 기본 끔)


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
        return (2 * lift_time(lane.level) + 2 * mother_time(self.layout.xfer_y, lane.y)
                + sat_time(DEPTH // 2))

    def unity_cost(self, s: "LaneState", queue_len: dict[tuple[int, int], int]) -> float:
        """Unity ShuttleRackSystem 레인 배정 비용 = 리프트 승강 + 모셔틀 주행 + 단별 대기 작업 x 20 s + 빈칸 x 0.1."""
        return (lift_time(s.lane.level) + mother_time(self.layout.xfer_y, s.lane.y)
                + UNITY_QUEUE_S * self._q(s, queue_len) + UNITY_EMPTY_W * s.space)

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
    def choose_rail(self, sku: str, queue_len: dict[tuple[int, int], int]) -> int:
        """통로(레일) 고르기 = 우리(WMS) 몫. Unity 는 통로 안에서만 레인을 고른다.
        같은 SKU 레인에 빈칸이 있는 통로 우선, 그다음 대기 작업이 적은 통로."""
        def key(r: int):
            has_same = any(self.lanes[i].lane.rail == r and self.lanes[i].space > 0 and self.lanes[i].reserved_out == 0
                           for i in self.by_sku.get(sku, ()))
            load = sum(n for (rr, _), n in queue_len.items() if rr == r)
            return (0 if has_same else 1, load, r)
        return min(range(len(self.layout.rails)), key=key)

    def choose_lane(self, sku: str, weight: float, queue_len: dict[tuple[int, int], int]) -> LaneState | None:
        limits = self.policy.weight_limits or self.policy.slotting == "abc"
        ok_level = [lv for lv, m in enumerate(LEVEL_MAX_KG) if weight <= m] if limits else list(range(LEVELS))
        if not ok_level:
            return None
        lv_ok = set(ok_level)
        if self.policy.slotting == "unity":
            # Unity: 같은 SKU 레인(빈칸 있음) → 없으면 빈 레인. 그 안에서 비용 최소. 통로는 choose_rail.
            r = self.choose_rail(sku, queue_len)
            same = [self.lanes[i] for i in self.by_sku.get(sku, ()) if self.lanes[i].lane.rail == r
                    and self.lanes[i].space > 0 and self.lanes[i].reserved_out == 0 and self.lanes[i].lane.level in lv_ok]
            cand = same or [s for s in self.lanes if s.lane.rail == r and s.sku is None and s.idle
                            and s.lane.level in lv_ok]
            if not cand:      # 그 통로가 꽉 찼으면 다른 통로
                cand = [s for s in self.lanes if s.sku is None and s.idle and s.lane.level in lv_ok]
            return min(cand, key=lambda s: self.unity_cost(s, queue_len)) if cand else None
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
