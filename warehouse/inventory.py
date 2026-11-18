"""재고 상태: 어떤 슬롯에 어떤 SKU가 언제 들어왔는지."""
from __future__ import annotations

from dataclasses import dataclass

from .layout import Slot, WarehouseLayout


@dataclass(frozen=True)
class Pallet:
    pallet_id: int
    sku: str
    arrived_at: float
    weight_kg: float = 0.0       # 검수 하중 센서 측정값
    declared_kg: float = 0.0     # 송장(ASN) 기재 무게


class Inventory:
    def __init__(self, layout: WarehouseLayout) -> None:
        self.layout = layout
        self.slot_of: dict[int, Slot] = {}          # pallet_id -> slot
        self.pallet_at: dict[Slot, Pallet] = {}     # slot -> pallet
        self._by_sku: dict[str, set[int]] = {}
        self._pallets: dict[int, Pallet] = {}
        self.blocked: set[Slot] = set()             # 비상 구역 등으로 사용 중지된 슬롯
        self.bay_load: dict[tuple[int, int], float] = {}   # 랙 칸별 현재 하중
        self.violations = 0                         # 허용 하중 초과 적재 수 (무게 무시 전략 비교용)

    # ---- 질의 ---------------------------------------------------------
    def is_empty(self, slot: Slot) -> bool:
        """새로 적재할 수 있는 슬롯인지 (비어 있고 차단되지 않음)."""
        return slot not in self.pallet_at and slot not in self.blocked

    def empty_slots(self) -> list[Slot]:
        return [s for s in self.layout.slots if self.is_empty(s)]

    def pallets_of(self, sku: str, include_blocked: bool = False) -> list[Pallet]:
        """출고 가능한 토트. 차단된 슬롯의 토트는 기본적으로 제외."""
        out = [self._pallets[pid] for pid in self._by_sku.get(sku, ())]
        if include_blocked:
            return out
        return [p for p in out if self.slot_of[p.pallet_id] not in self.blocked]

    def fits(self, slot: Slot, weight: float) -> bool:
        """층 허용 하중과 랙 칸 합계 허용 하중을 모두 만족하는지."""
        L = self.layout
        lv_max = L.level_max_kg[min(slot[2], len(L.level_max_kg) - 1)]
        return weight <= lv_max and self.bay_load.get(slot[:2], 0.0) + weight <= L.bay_max_kg

    def block_slots(self, slots) -> None:
        self.blocked |= set(slots)

    def unblock_slots(self, slots) -> None:
        self.blocked -= set(slots)

    def stock(self, sku: str) -> int:
        """출고 가능한 재고 수 (차단 슬롯 제외 → 부족하면 재입고가 걸린다)."""
        return len(self.pallets_of(sku))

    @property
    def utilization(self) -> float:
        return len(self.pallet_at) / len(self.layout.slots)

    # ---- 변경 ---------------------------------------------------------
    def store(self, pallet: Pallet, slot: Slot) -> None:
        if not self.is_empty(slot):
            raise ValueError(f"slot {slot} is occupied")
        if not self.fits(slot, pallet.weight_kg):
            self.violations += 1
        self.bay_load[slot[:2]] = self.bay_load.get(slot[:2], 0.0) + pallet.weight_kg
        self.pallet_at[slot] = pallet
        self.slot_of[pallet.pallet_id] = slot
        self._pallets[pallet.pallet_id] = pallet
        self._by_sku.setdefault(pallet.sku, set()).add(pallet.pallet_id)

    def remove(self, pallet_id: int) -> Slot:
        slot = self.slot_of.pop(pallet_id)
        pallet = self.pallet_at.pop(slot)
        self.bay_load[slot[:2]] -= pallet.weight_kg
        self._pallets.pop(pallet_id)
        self._by_sku[pallet.sku].discard(pallet_id)
        return slot
