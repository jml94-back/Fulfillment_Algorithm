"""강화학습용 환경 래퍼 (Gymnasium 스타일, 외부 의존성 없음).

SlottingEnv: 입고 토트마다 "어느 슬롯에 넣을지"를 에이전트가 고른다.
- observation: dict
    occupancy : [levels][height][width] 0/1 점유 격자
    sku_rank  : 들어온 SKU의 수요 순위(0 = 가장 잘 나감) / n_skus  (0~1)
    action_mask: 슬롯별 사용 가능 여부 (len = n_slots)
- action: layout.slots 인덱스
- reward: 직전 결정 이후 발생한 이동거리+승강 비용의 음수
  → 지금 넣은 위치가 나중에 꺼낼 때 비용으로 돌아오는 구조라 장기 보상 학습이 필요하다.

AMR 주행 RL은 별도 환경에서 waypoint 대신 저수준 제어를 학습하면 되고,
이 환경은 상위 의사결정(어디에 넣고 꺼낼지)을 맡는다.
"""
from __future__ import annotations

from .layout import WarehouseLayout
from .simulator import SimConfig, Simulator


class SlottingEnv:
    def __init__(self, layout: WarehouseLayout, cfg: SimConfig) -> None:
        self.layout = layout
        self.cfg = cfg
        self.slots = layout.slots
        self.slot_index = {s: i for i, s in enumerate(self.slots)}
        self.n_actions = len(self.slots)
        self._rank = {f"SKU{r:03d}": r for r in range(cfg.n_skus)}

    def reset(self, seed: int | None = None) -> tuple[dict, dict]:
        if seed is not None:
            self.cfg.seed = seed
        self.sim = Simulator(self.layout, self.cfg)
        self._gen = self.sim.process()
        self._req = next(self._gen)
        self._last_cost = 0.0
        return self._obs(), {}

    def step(self, action: int) -> tuple[dict, float, bool, bool, dict]:
        slot = self.slots[action]
        if not self.sim.inventory.is_empty(slot):
            raise ValueError("invalid action: slot occupied (use action_mask)")
        done = False
        try:
            self._req = self._gen.send(slot)
        except StopIteration:
            done = True
        cost = self.sim.result.total_cost
        reward = -(cost - self._last_cost) if cost >= self._last_cost else 0.0
        self._last_cost = cost
        info = {"utilization": self.sim.inventory.utilization}
        return (None if done else self._obs()), reward, done, False, info

    def _obs(self) -> dict:
        L, inv = self.layout, self.sim.inventory
        occ = [[[0] * L.width for _ in range(L.height)] for _ in range(L.levels)]
        for (x, y, lv) in inv.pallet_at:
            occ[lv][y][x] = 1
        p = self._req.pallet
        return {
            "occupancy": occ,
            "sku_rank": self._rank[p.sku] / self.cfg.n_skus,
            "weight": p.weight_kg / 1000,
            "action_mask": [inv.is_empty(s) and inv.fits(s, p.weight_kg) for s in self.slots],
        }
