"""랙 배치 후보 비교 (AMR 4대, ABC 적재 + nn2opt 경로).

    python -m experiments.layout_compare [out]

슬롯 수(랙 셀 120칸 × 4층 = 480)를 고정하고 다음을 바꾼다.
- 형태: 블록 수 × 랙 길이 (4×15, 5×12, 6×10, 10×6)
- 도크 위치: 왼쪽 끝(corner) / 앞면 가운데(center)
- 중간 교차 통로: 0 / 1개
"""
from __future__ import annotations

import json
import statistics
import sys
from itertools import product
from pathlib import Path

from warehouse import ClassBasedSlotting, SimConfig, Simulator, WarehouseLayout, viz
from warehouse.fleet import FleetConfig, FleetSimulator

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
SHAPES = [(4, 15), (5, 12), (6, 10), (10, 6)]
DOCKS = ["corner", "center"]
CROSS = [0, 1]
SEEDS = [0, 1, 2]
ORDER_INTERVAL = 24.0     # 기준 배치에서 AMR 4대 가동률 ~75%가 되도록 정한 주문 간격


def evaluate(L: WarehouseLayout) -> dict:
    cost, lead, p95, cong, util = [], [], [], [], []
    for seed in SEEDS:
        sim = Simulator(L, SimConfig(n_orders=800, seed=seed, record_trips=10**6))
        r = sim.run(ClassBasedSlotting(L, sim.oracle, sim.demand))
        f = FleetSimulator(L, FleetConfig(n_amr=4, order_interval=ORDER_INTERVAL), sim.oracle).run(r.recorded)
        assert f.collisions == 0
        cost.append(r.cost_per_line)
        lead.append(f.mean_lead * 1.2)
        p95.append(f.p95_lead * 1.2)
        cong.append(f.congestion_wait)
        util.append(f.utilization)
    m = statistics.mean
    return {"cost_per_line": m(cost), "lead_s": m(lead), "p95_lead_s": m(p95),
            "congestion": m(cong), "utilization": m(util),
            "area_m2": L.width * L.height * L.cell_size ** 2}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    results, gallery = [], {}
    for (b, l), dock, cross in product(SHAPES, DOCKS, CROSS):
        L = WarehouseLayout(rack_blocks=b, rack_length=l, dock_position=dock, cross_aisles=cross)
        name = f"{b}x{l} · {'가운데' if dock == 'center' else '모서리'} 도크 · 교차통로 {cross}"
        row = {"name": name, "blocks": b, "length": l, "dock": dock, "cross": cross, **evaluate(L)}
        results.append(row)
        print(f"{name:28s} 건당비용 {row['cost_per_line']:5.2f}  평균 리드타임 {row['lead_s']:6.0f}s  "
              f"p95 {row['p95_lead_s']:6.0f}s  혼잡 {row['congestion']*100:4.1f}%  가동률 {row['utilization']*100:3.0f}%  "
              f"면적 {row['area_m2']:.0f}㎡")
        if (dock, cross) in [("corner", 0), ("center", 1)] or (b, l) == (5, 12):
            gallery[name] = L
    (OUT / "layout_compare.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))

    best = min(results, key=lambda r: r["lead_s"])
    print("\n평균 리드타임 최소:", best["name"])
    pick = {k: v for k, v in gallery.items() if "5x12" in k}
    viz.layout_gallery(pick, str(OUT / "layout_candidates.png"))

    # 형태별 막대 대신 꺾은선: x = 형태, 시리즈 = (도크, 교차통로)
    xs = [f"{b}x{l}" for b, l in SHAPES]
    series = {}
    for dock, cross in product(DOCKS, CROSS):
        key = f"{'가운데' if dock == 'center' else '모서리'} 도크 · 교차통로 {cross}"
        series[key] = [next(r["lead_s"] for r in results if r["blocks"] == b and r["length"] == l
                            and r["dock"] == dock and r["cross"] == cross) for b, l in SHAPES]
    viz.line_compare(xs, series, str(OUT / "layout_lead_time.png"), "형태 (랙 블록 수 x 랙 길이)",
                     "평균 작업 리드타임 (초)", "랙 배치별 평균 리드타임 · AMR 4대 (낮을수록 좋음)")


if __name__ == "__main__":
    main()
