"""적재 위치 할당(Slotting) 전략.

모든 전략은 같은 인터페이스를 따른다::

    choose(pallet, inventory) -> Slot

weight_aware=True(기본)이면 검수 하중 센서로 잰 무게로
층·랙 칸 허용 하중을 넘는 슬롯을 후보에서 뺀다.

슬롯 비용 = 도크→접근점 이동거리 + level * level_cost
(왕복이므로 실제 이동은 대략 2배지만, 순위 비교에는 편도로 충분하다.)
"""
from __future__ import annotations

import random
from typing import Protocol

from .inventory import Inventory, Pallet
from .layout import Slot, WarehouseLayout
from .pathfinding import DistanceOracle


def slot_costs(layout: WarehouseLayout, oracle: DistanceOracle) -> dict[Slot, float]:
    return {
        s: oracle.dist(layout.dock, layout.access_point(s)) + s[2] * layout.level_cost
        for s in layout.slots
    }


class SlottingStrategy(Protocol):
    name: str

    def choose(self, pallet: Pallet, inventory: Inventory) -> Slot: ...


def _ok(inventory: Inventory, s: Slot, pallet: Pallet, weight_aware: bool) -> bool:
    return inventory.is_empty(s) and (not weight_aware or inventory.fits(s, pallet.weight_kg))


class RandomSlotting:
    """기준선: 빈 슬롯 중 무작위."""

    name = "random"

    def __init__(self, seed: int = 0, weight_aware: bool = True) -> None:
        self.rng = random.Random(seed)
        self.weight_aware = weight_aware

    def choose(self, pallet: Pallet, inventory: Inventory) -> Slot:
        empties = [s for s in inventory.empty_slots() if _ok(inventory, s, pallet, self.weight_aware)]
        if not empties:
            raise RuntimeError("warehouse full")
        return self.rng.choice(empties)


class NearestSlotting:
    """도크에서 가장 가까운(비용이 가장 낮은) 빈 슬롯."""

    name = "nearest"

    def __init__(self, layout: WarehouseLayout, oracle: DistanceOracle, weight_aware: bool = True) -> None:
        cost = slot_costs(layout, oracle)
        self.order = sorted(layout.slots, key=lambda s: (cost[s], s))
        self.weight_aware = weight_aware

    def choose(self, pallet: Pallet, inventory: Inventory) -> Slot:
        for s in self.order:
            if _ok(inventory, s, pallet, self.weight_aware):
                return s
        raise RuntimeError("warehouse full")


class ClassBasedSlotting:
    """ABC 회전율 기반 구역 할당 + 구역 내 최근접.

    1) SKU를 회전율(출고 빈도) 순으로 정렬해 누적 비중 기준으로 A/B/C 등급 부여
       (기본: 상위 누적 수요 80% = A, 다음 15% = B, 나머지 = C).
    2) 슬롯을 비용 순으로 정렬해 앞에서부터 A, B, C 구역으로 나눈다.
       구역 크기는 각 등급 SKU 수에 비례 (SKU별 재고량이 비슷하다고 가정).
    3) 해당 등급 구역의 가장 가까운 빈 슬롯에 넣는다.
       구역이 꽉 차면 바로 다음 구역(더 먼 쪽) → 그래도 없으면 더 가까운 쪽 순으로 넘친다.
    4) (weight_aware) 허용 하중을 넘는 슬롯은 제외하고, 무거울수록 위층에 벌점을 줘서
       같은 구역 안에서 아래층을 먼저 고른다:
           점수 = 슬롯 비용 + 층 × heavy_penalty × (무게 / 1000kg)
    """

    name = "abc"

    def __init__(
        self,
        layout: WarehouseLayout,
        oracle: DistanceOracle,
        demand: dict[str, float],
        cutoffs: tuple[float, float] = (0.80, 0.95),
        weight_aware: bool = True,
        heavy_penalty: float = 3.0,
    ) -> None:
        self.sku_class = classify_abc(demand, cutoffs)
        self.weight_aware = weight_aware
        self.heavy_penalty = heavy_penalty
        cost = self.cost = slot_costs(layout, oracle)
        ordered = sorted(layout.slots, key=lambda s: (cost[s], s))

        counts = {c: 0 for c in "ABC"}
        for c in self.sku_class.values():
            counts[c] += 1
        total = sum(counts.values())
        self.zones: dict[str, list[Slot]] = {}
        start = 0
        for i, c in enumerate("ABC"):
            end = len(ordered) if i == 2 else start + round(len(ordered) * counts[c] / total)
            self.zones[c] = ordered[start:end]
            start = end
        self.zone_of = {s: c for c, ss in self.zones.items() for s in ss}

    _FALLBACK = {"A": "ABC", "B": "BCA", "C": "CBA"}

    def choose(self, pallet: Pallet, inventory: Inventory) -> Slot:
        cls = self.sku_class.get(pallet.sku, "C")
        w = pallet.weight_kg / 1000
        for zone in self._FALLBACK[cls]:
            cands = [s for s in self.zones[zone] if _ok(inventory, s, pallet, self.weight_aware)]
            if not cands:
                continue
            if not self.weight_aware:
                return cands[0]
            return min(cands, key=lambda s: (self.cost[s] + s[2] * self.heavy_penalty * w, s))
        raise RuntimeError("warehouse full")


def classify_abc(
    demand: dict[str, float], cutoffs: tuple[float, float] = (0.80, 0.95)
) -> dict[str, str]:
    total = sum(demand.values())
    out: dict[str, str] = {}
    acc = 0.0
    for sku, d in sorted(demand.items(), key=lambda kv: -kv[1]):
        # 이 SKU를 넣기 전 누적 비중으로 판단 → 최상위 SKU는 항상 A
        out[sku] = "A" if acc < cutoffs[0] else ("B" if acc < cutoffs[1] else "C")
        acc += d / total
    return out
