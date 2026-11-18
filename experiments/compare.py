"""전략 비교 실험.

    python -m experiments.compare            # 결과: out/ 폴더

- 적재 전략 3종 × 경로 전략 3종 × 시드 5개
- 출고 대상 선택 FIFO vs nearest 비교
- 히트맵, 경로 비교 그림, Unity용 레이아웃/작업 JSON, AMR 애니메이션 GIF 생성
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path

from warehouse import (ClassBasedSlotting, DistanceOracle, NearestSlotting, RandomSlotting, ROUTERS,
                       SimConfig, Simulator, WarehouseLayout)
from warehouse import viz
from warehouse.routing import tour_length

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
SEEDS = [0, 1, 2, 3, 4]
SLOTTING_LABEL = {"random": "랜덤", "nearest": "최근접 빈 칸", "abc": "ABC 회전율"}


def make_strategy(name, layout, sim, seed):
    if name == "random":
        return RandomSlotting(seed)
    if name == "nearest":
        return NearestSlotting(layout, sim.oracle)
    return ClassBasedSlotting(layout, sim.oracle, sim.demand)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    layout = WarehouseLayout()   # 추천 배치: 6x10, 가운데 도크, 중간 교차 통로 1, 4층
    oracle = DistanceOracle(layout)
    print(layout.ascii(), f"\n슬롯 {len(layout.slots)}개\n")

    # 1) 적재 × 경로 전략
    table: dict[str, dict[str, float]] = {}
    rows = []
    for sname in ["random", "nearest", "abc"]:
        table[SLOTTING_LABEL[sname]] = {}
        for rname in ROUTERS:
            vals = []
            for seed in SEEDS:
                sim = Simulator(layout, SimConfig(routing=rname, seed=seed), oracle)
                res = sim.run(make_strategy(sname, layout, sim, seed))
                vals.append(res.cost_per_line)
                rows.append({"slotting": sname, "routing": rname, "seed": seed,
                             "cost_per_line": res.cost_per_line, "dist_per_pick": res.dist_per_pick,
                             "trips": res.trips, "picks": res.lines_picked, "stores": res.lines_stored,
                             "stockouts": res.stockouts})
            table[SLOTTING_LABEL[sname]][rname] = statistics.mean(vals)
            print(f"{sname:8s} {rname:8s} 건당 비용 {statistics.mean(vals):6.2f} ± {statistics.stdev(vals):.2f}")
    viz.bar_compare(table, str(OUT / "strategy_compare.png"), "작업 1건당 비용 (칸)")

    # 2) FIFO vs nearest 출고
    print()
    retrieval = {}
    for rv in ["fifo", "nearest"]:
        vals = []
        for seed in SEEDS:
            sim = Simulator(layout, SimConfig(retrieval=rv, seed=seed), oracle)
            vals.append(sim.run(make_strategy("abc", layout, sim, seed)).cost_per_line)
        retrieval[rv] = statistics.mean(vals)
        print(f"출고 선택 {rv:8s} 건당 비용 {retrieval[rv]:.2f}")

    # 3) 히트맵 (seed 0, nn2opt)
    visits = {}
    for sname in ["random", "nearest", "abc"]:
        sim = Simulator(layout, SimConfig(seed=0), oracle)
        visits[SLOTTING_LABEL[sname]] = sim.run(make_strategy(sname, layout, sim, 0)).visits
    viz.heatmaps(layout, visits, str(OUT / "heatmap.png"))

    # 4) 같은 피킹 목록, 경로 전략 비교 (랜덤 10개 지점)
    rng = random.Random(7)
    targets = sorted({layout.access_point(s) for s in rng.sample(layout.slots, 10)})
    routes = {}
    for rname, fn in ROUTERS.items():
        order = fn(layout, oracle, layout.dock, targets)
        routes[rname] = (order, tour_length(oracle, layout.dock, order))
    viz.route_compare(layout, routes, targets, str(OUT / "route_compare.png"))

    # 5) Unity 내보내기 + 애니메이션
    sim = Simulator(layout, SimConfig(seed=0, record_trips=8), oracle)
    res = sim.run(make_strategy("abc", layout, sim, 0))
    trips = [sim.trip_to_dict(t) for t in res.recorded]
    (OUT / "layout.json").write_text(json.dumps(layout.to_dict(), ensure_ascii=False, indent=1))
    (OUT / "tasks_sample.json").write_text(json.dumps(trips, ensure_ascii=False, indent=1))
    viz.animate_trips(layout, trips, str(OUT / "amr_trips.gif"))

    summary = {"cost_per_line": table, "retrieval": retrieval,
               "route_example": {k: v[1] for k, v in routes.items()}, "runs": rows}
    (OUT / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"\n결과 저장: {OUT.resolve()}")


if __name__ == "__main__":
    main()
