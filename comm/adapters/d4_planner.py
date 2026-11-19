"""MQTT 스케줄러(comm/sim/scheduler_bridge.py)에 D4 알고리즘을 끼우는 어댑터.

    from adapters.d4_planner import D4Planner           # comm/ 에서 실행
    Scheduler(host, port, planner=D4Planner({}))

의존 방향은 comm → 알고리즘 코어(warehouse/) 한쪽뿐이다. 코어는 이 파일을 모른다.

스케줄러가 가진 레인 상태(lanes: 레인ID → 14칸, index 0 = 위치 1 = 레일 쪽)를 그대로 진실로 쓰고,
고르는 규칙만 warehouse/d4/storage.py 와 같게 바꾼다.
- assign_lane: 같은 SKU 레인 보충(가장 많이 찬 레인) → 없으면 빈 레인 중
  |접근 시간 순위 - SKU 회전율 순위| + 무게→낮은 층 + 가벼운 팔레트는 튼튼한 아래층 비워 두기 + 리프트 부하.
  층별 허용 하중(LEVEL_MAX_KG)은 반드시 지킨다.
- pick_lane: 그 SKU 레인 중 접근 시간 + 리프트 부하가 가장 작은 레인의 맨 앞 팔레트.
- 회전율 순위: 생성자에 sku_rank 를 주거나, 없으면 출고된 횟수로 그때그때 계산.
choose_amr 는 스케줄러 기본(가장 가까운 놀고 있는 AMR)을 쓴다.
"""
from __future__ import annotations

from collections import Counter

from sim.scheduler_bridge import Planner
from warehouse.d4.layout import DEPTH, LEVEL_MAX_KG, D4Layout
from warehouse.d4.storage import Storage, StoragePolicy
from whmqtt import ids


class D4Planner(Planner):
    def __init__(self, lanes: dict[str, list], skus_heavy: set[str] | None = None,
                 sku_rank: dict[str, float] | None = None) -> None:
        super().__init__(lanes, skus_heavy)
        self.L = D4Layout()
        self.by_id = {self.L.lane_id(ln): ln for ln in self.L.lanes}
        st = Storage(self.L, StoragePolicy(), {}, {})
        self.access = {self.L.lane_id(ln): st.access[ln.idx] for ln in self.L.lanes}
        self.access_rank = {self.L.lane_id(ln): st.access_rank[ln.idx] for ln in self.L.lanes}
        self.fixed_rank = sku_rank
        self.out_count: Counter[str] = Counter()

    def _rank(self, sku: str) -> float:
        if self.fixed_rank is not None:
            return self.fixed_rank.get(sku, .5)
        tot = sum(self.out_count.values())
        if not tot or sku not in self.out_count:
            return .5
        above = sum(n for s, n in self.out_count.items() if n > self.out_count[sku])
        return above / tot

    def assign_lane(self, sku: str, weight: float, lift_load: dict[str, int]) -> tuple[str, int]:
        ok = {lv for lv, m in enumerate(LEVEL_MAX_KG) if weight <= m}
        if not ok:
            raise RuntimeError(f"허용 하중 초과 {weight} kg")
        need = min(LEVEL_MAX_KG[lv] for lv in ok)
        best = None
        for lane in self.lanes:
            n = self.count(lane) + self.reserved_in[lane]
            ln = self.by_id[lane]
            if self.lane_sku(lane) == sku and n < DEPTH and self.reserved_out[lane] == 0 and ln.level in ok:
                key = (-n, lift_load.get(f"L{ln.rail + 1}", 0))
                if best is None or key < best[0]:
                    best = (key, lane, DEPTH - n)
        if best is None:
            target, heavy = self._rank(sku), weight / max(LEVEL_MAX_KG)
            cost_best = None
            for lane, ln in self.by_id.items():
                if ln.level not in ok or self.count(lane) or self.reserved_in.get(lane):
                    continue
                cost = abs(self.access_rank[lane] - target) * 10 + heavy * ln.level * .4 \
                    + (LEVEL_MAX_KG[ln.level] - need) / 100 * 1.5 + lift_load.get(f"L{ln.rail + 1}", 0) * .6
                if cost_best is None or cost < cost_best:
                    cost_best, best = cost, ((), lane, DEPTH)
        if best is None:
            raise RuntimeError("빈 레인 없음")
        self.reserved_in[best[1]] += 1
        self.reserved_sku[best[1]] = sku
        self.lanes.setdefault(best[1], [None] * ids.DEPTH)
        return best[1], best[2]

    def pick_lane(self, sku: str, lift_load: dict[str, int]) -> tuple[str, int, str] | None:
        cands = []
        for lane in self.lanes:
            if self.lane_sku(lane) != sku or self.reserved_in[lane]:
                continue
            if self.count(lane) - self.reserved_out[lane] <= 0:
                continue
            r = self.by_id[lane].rail + 1
            cands.append((self.access[lane] + 30 * lift_load.get(f"L{r}", 0), lane))
        if not cands:
            return None
        lane = min(cands)[1]
        occupied = [i for i, s in enumerate(self.lanes[lane]) if s]
        idx = occupied[self.reserved_out[lane]]
        self.reserved_out[lane] += 1
        self.out_count[sku] += 1
        return lane, idx + 1, self.lanes[lane][idx][0]
