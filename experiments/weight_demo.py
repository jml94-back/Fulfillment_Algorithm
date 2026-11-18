"""검수 하중 센서 반영 효과.

    python -m experiments.weight_demo [out]

- 무게 무시 ABC vs 무게 반영 ABC: 허용 하중 초과 적재 수, 층별 평균 무게, 작업 비용
- 송장 무게 불일치 → 보류 건수
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import matplotlib.pyplot as plt

from warehouse import ClassBasedSlotting, SimConfig, Simulator, WarehouseLayout, viz

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    L = WarehouseLayout()
    rows, by_level = {}, {}
    for label, wa in [("무게 무시", False), ("무게 반영", True)]:
        cost, viol, held, levels = [], [], [], {lv: [] for lv in range(L.levels)}
        for seed in range(5):
            sim = Simulator(L, SimConfig(seed=seed))
            r = sim.run(ClassBasedSlotting(L, sim.oracle, sim.demand, weight_aware=wa))
            cost.append(r.cost_per_line); viol.append(r.weight_violations); held.append(r.held)
            for slot, p in sim.inventory.pallet_at.items():
                levels[slot[2]].append(p.weight_kg)
        rows[label] = {"cost_per_line": statistics.mean(cost), "violations": statistics.mean(viol),
                       "held": statistics.mean(held)}
        by_level[label] = [statistics.mean(levels[lv]) for lv in range(L.levels)]
        print(f"{label}: 건당 비용 {rows[label]['cost_per_line']:.2f}, 허용 하중 초과 {rows[label]['violations']:.0f}건, "
              f"검수 보류 {rows[label]['held']:.0f}건, 층별 평균 무게 {[round(x) for x in by_level[label]]}kg")

    fig, ax = plt.subplots(figsize=(6.8, 4))
    w = .38
    for i, (label, ys) in enumerate(by_level.items()):
        xs = [lv + (i - .5) * w for lv in range(L.levels)]
        ax.bar(xs, ys, width=w - .03, color=viz.SERIES[i], label=label, zorder=2)
        for x, y in zip(xs, ys):
            ax.text(x, y + 10, f"{y:.0f}", ha="center", va="bottom", fontsize=8, color=viz.TEXT2)
    ax.plot(range(L.levels), L.level_max_kg[: L.levels], color=viz.TEXT2, lw=1.5, ls="--", marker="_",
            ms=18, label="층 허용 하중", zorder=3)
    ax.set_xticks(range(L.levels), [f"{lv + 1}층" for lv in range(L.levels)])
    ax.set_ylabel("팔레트 평균 무게 (kg)")
    ax.grid(axis="y", color=viz.GRID, lw=.8, zorder=0)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.legend(frameon=False)
    ax.set_title("층별 평균 팔레트 무게: 무게 반영 시 무거운 것은 아래층으로", fontsize=12)
    fig.savefig(OUT / "weight_by_level.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    (OUT / "weight_results.json").write_text(json.dumps({"summary": rows, "mean_weight_by_level": by_level},
                                                       ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
