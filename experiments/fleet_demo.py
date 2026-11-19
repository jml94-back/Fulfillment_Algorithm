"""다중 AMR(기본 4대) 실험.

    python -m experiments.fleet_demo [out]

1) AMR 대수(1~6)별 리드타임 → 4대가 적정한지 확인
2) 충돌 회피 on/off 충돌 수 비교
3) 비상 상황(랙 기울어짐 → 화재) 발생 시 재경로·재배정 데모 + GIF
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

from warehouse import (ClassBasedSlotting, EmergencyEvent, FleetConfig, FleetSimulator, SimConfig, Simulator,
                       WarehouseLayout, viz)
from emergency import zone_for  # 비상 대응은 보류 중 (emergency/README.md)

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
INTERVAL = 24.0          # 주문 간격(tick) = 28.8초
SEEDS = [0, 1, 2]


def plan_trips(L, seed, n_orders=800, emergencies=()):
    sim = Simulator(L, SimConfig(n_orders=n_orders, seed=seed, record_trips=10**6, emergencies=list(emergencies)))
    r = sim.run(ClassBasedSlotting(L, sim.oracle, sim.demand))
    return sim, r.recorded


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    L = WarehouseLayout()
    summary: dict = {}

    # 1) 대수별
    ns = [1, 2, 3, 4, 5, 6]
    lead, p95, util = [], [], []
    for n in ns:
        ls, ps, us = [], [], []
        for seed in SEEDS:
            sim, trips = plan_trips(L, seed)
            f = FleetSimulator(L, FleetConfig(n_amr=n, order_interval=INTERVAL), sim.oracle).run(trips)
            assert f.collisions == 0
            ls.append(f.mean_lead * 1.2); ps.append(f.p95_lead * 1.2); us.append(f.utilization)
        lead.append(statistics.mean(ls)); p95.append(statistics.mean(ps)); util.append(statistics.mean(us))
        print(f"AMR {n}대  평균 리드타임 {lead[-1]:7.0f}s  p95 {p95[-1]:7.0f}s  가동률 {util[-1]*100:3.0f}%")
    summary["fleet_size"] = {"n": ns, "mean_lead_s": lead, "p95_lead_s": p95, "utilization": util}
    viz.line_compare(ns, {"평균": lead, "p95": p95}, str(OUT / "fleet_size.png"), "AMR 대수",
                     "작업 리드타임 (초, 로그 축)", "AMR 대수별 작업 리드타임 (주문 28.8초마다 1건)")
    # 로그 축으로 다시 저장 (1~2대에서 대기열이 폭증해서)
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6.8, 4))
    for i, (name, ys) in enumerate({"평균": lead, "p95": p95}.items()):
        ax.plot(ns, ys, color=viz.FLEET[i], lw=2, marker="o", ms=7, mec=viz.SURFACE, mew=2, label=name)
    ax.set_yscale("log"); ax.set_xticks(ns); ax.set_xlabel("AMR 대수"); ax.set_ylabel("작업 리드타임 (초, 로그 축)")
    ax.grid(axis="y", color=viz.GRID, lw=.8); ax.spines[["top", "right", "left"]].set_visible(False)
    ax.legend(frameon=False); ax.set_title("AMR 대수별 작업 리드타임 (주문 28.8초마다 1건)", fontsize=12)
    fig.savefig(OUT / "fleet_size.png", dpi=150, bbox_inches="tight"); plt.close(fig)

    # 2) 충돌 회피 on/off
    sim, trips = plan_trips(L, 0)
    on = FleetSimulator(L, FleetConfig(avoidance=True, order_interval=INTERVAL), sim.oracle).run(trips)
    off = FleetSimulator(L, FleetConfig(avoidance=False, order_interval=INTERVAL), sim.oracle).run(trips)
    print(f"\n충돌 회피 ON  충돌 {on.collisions}건, 평균 리드타임 {on.mean_lead*1.2:.0f}s, 혼잡 대기 {on.congestion_wait*100:.1f}%")
    print(f"충돌 회피 OFF 충돌 {off.collisions}건 (실제라면 사고)")
    summary["avoidance"] = {"on_collisions": on.collisions, "off_collisions": off.collisions,
                            "on_mean_lead_s": on.mean_lead * 1.2, "congestion_wait": on.congestion_wait}

    # 3) 비상 상황 데모
    tilt_cell = (L.width // 2 + 4, 4)          # 도크 오른쪽 앞 구역 랙
    fire_cell = (4, L.height - 4)              # 왼쪽 뒤 구역
    z1 = zone_for("rack_tilt", tilt_cell, L)
    z2 = zone_for("fire", fire_cell, L)
    t1, t2 = 150, 300
    emerg_plan = [(int(t1 / INTERVAL), z1, None), (int(t2 / INTERVAL), z2, None)]
    sim, trips = plan_trips(L, 0, n_orders=400, emergencies=emerg_plan)
    events = [EmergencyEvent(t1, z1.cells, kind="rack_tilt"), EmergencyEvent(t2, z2.cells, kind="fire")]
    f = FleetSimulator(L, FleetConfig(order_interval=INTERVAL), sim.oracle).run(trips, events)
    print(f"\n비상 데모: 충돌 {f.collisions}건, 재계획 {f.events_log}, 건너뛴 정류장 {len(f.skipped_stops)}개")
    entered = sum(1 for tr in f.tracks for t, c in enumerate(tr)
                  if (t > t1 and c in z1.cells) or (t > t2 and c in z2.cells))
    print(f"차단 구역 진입 tick 수(탈출 이동 포함): {entered}")
    summary["emergency"] = {"events": f.events_log, "collisions": f.collisions,
                            "skipped_stops": len(f.skipped_stops), "zone_ticks": entered,
                            "zones": [z1.to_dict(), z2.to_dict()]}
    viz.animate_fleet(L, f.tracks, str(Path("emergency/out") / "fleet_emergency.gif"), t0=100, t1=420,
                      zones=[(z1.cells, t1, "랙 기울어짐"), (z2.cells, t2, "화재")])
    viz.animate_fleet(L, on.tracks, str(OUT / "fleet_4amr.gif"), t0=200, t1=440)

    (OUT / "fleet_results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    print(f"\n결과 저장: {OUT.resolve()}")


if __name__ == "__main__":
    main()
