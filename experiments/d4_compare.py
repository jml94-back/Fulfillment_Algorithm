"""D4 확정 배치에서 적재·출고 알고리즘 전략 비교 + 부하별 성능 + Unity·MQTT용 작업 데이터.

python -m experiments.d4_compare
→ out/d4_compare.png, out/d4_compare.json, out/d4_events.json (기본 전략 첫 1시간 MQTT 메시지),
  out/d4_nav_grid.json (AMR 바닥 격자·접점)
"""
from __future__ import annotations

import json
import statistics
from dataclasses import replace
from pathlib import Path

import matplotlib.pyplot as plt

from warehouse import viz  # noqa: F401  (한글 글꼴 설정)
from warehouse.d4.layout import D4Layout, cx
from warehouse.d4.sim import D4Sim, SimConfig
from warehouse.d4.storage import StoragePolicy

OUT = Path("out")
SEEDS = [0, 1, 2]
RATE = 40                       # 방향별 팔레트/시간 (입고 40 + 출고 40)
KEYS = ["dock_to_stock_mean_s", "dock_to_stock_p95_s", "out_truck_mean_min", "out_truck_p95_min", "amr_util",
        "lift_util", "shuttle_util", "honeycomb", "used_lanes", "in_per_h", "out_per_h", "collisions", "held"]

BASE = SimConfig(hours=8, in_per_h=RATE, out_per_h=RATE, record_tracks=True, bus_keep=False)
VARIANTS = {
    "기본 (Unity 랙 규칙)": BASE,
    "적재: 회전율·하중 비용식": replace(BASE, policy=StoragePolicy(slotting="abc")),
    "적재: 무작위 레인": replace(BASE, policy=StoragePolicy(slotting="random")),
    "출고: 적은 레인 먼저": replace(BASE, policy=StoragePolicy(retrieval="fewest")),
    "AMR: 오래된 작업 먼저": replace(BASE, dispatch="fifo"),
    "리프트 이중 사이클 켬": replace(BASE, dual_cycle=True),
}
LOADS = [25, 35, 45, 55]


def run(cfg: SimConfig) -> dict:
    ms = [D4Sim(replace(cfg, seed=s)).run() for s in SEEDS]
    return {k: statistics.mean(m[k] for m in ms) for k in KEYS}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    L = D4Layout()
    res: dict = {"rate_per_direction": RATE, "seeds": SEEDS, "variants": {}, "loads": {}}
    for name, cfg in VARIANTS.items():
        res["variants"][name] = r = run(cfg)
        print(f"{name:22s} 입고→적재 {r['dock_to_stock_mean_s'] / 60:5.1f}분  출고 트럭 {r['out_truck_mean_min']:5.1f}분  "
              f"셔틀 {r['shuttle_util']:.3f}  리프트 {r['lift_util']:.3f}  빈칸 {r['honeycomb']:.3f}  "
              f"레인 {r['used_lanes']:.0f}  충돌 {r['collisions']:.0f}", flush=True)
    for rate in LOADS:
        res["loads"][rate] = r = run(replace(BASE, in_per_h=rate, out_per_h=rate))
        print(f"부하 {rate * 2:3d}/h  입고→적재 {r['dock_to_stock_mean_s'] / 60:5.1f}분 (p95 {r['dock_to_stock_p95_s'] / 60:5.1f})"
              f"  출고 트럭 {r['out_truck_mean_min']:5.1f}분  AMR {r['amr_util']:.2f}", flush=True)
    (OUT / "d4_compare.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))

    # 기본 전략 한 번 더 (기록용): MQTT 메시지 첫 1시간 + AMR 궤적
    sim = D4Sim(replace(BASE, bus_keep=True))
    sim.run()
    n = sim.bus.dump(OUT / "d4_events.json", until=3600)
    print("MQTT 메시지", n, "개 (첫 1시간) → out/d4_events.json")
    nav = {"cell_size": 1.2, "cols": L.cols, "rows": L.rows,
           "walkable": sorted([list(c) for c in L._walk]),
           "contacts": [{"id": v[0], "cell": list(c), "pos": v[1]} for c, v in L.contacts.items()]}
    (OUT / "d4_nav_grid.json").write_text(json.dumps(nav, ensure_ascii=False))
    draw(res, sim, L)


def draw(res: dict, sim: D4Sim, L: D4Layout) -> None:
    fig = plt.figure(figsize=(14, 15))
    gs = fig.add_gridspec(3, 2, height_ratios=[1, .9, .62], hspace=.45, wspace=.28)
    names = list(res["variants"])
    ys = list(range(len(names)))[::-1]
    # (1) 전략별 시간
    ax = fig.add_subplot(gs[0, 0])
    a = [res["variants"][n]["dock_to_stock_mean_s"] / 60 for n in names]
    b = [res["variants"][n]["out_truck_mean_min"] for n in names]
    ax.barh([y + .2 for y in ys], a, height=.38, color=viz.FLEET[0], label="입고: 하차 → 레인 적재 (분)")
    ax.barh([y - .2 for y in ys], b, height=.38, color=viz.FLEET[1], label="출고: 트럭 도착 → 상차 완료 (분)")
    for y, v in zip(ys, a):
        ax.text(v + .3, y + .2, f"{v:.1f}", va="center", fontsize=8, color=viz.TEXT2)
    for y, v in zip(ys, b):
        ax.text(v + .3, y - .2, f"{v:.1f}", va="center", fontsize=8, color=viz.TEXT2)
    ax.set_yticks(ys, names, fontsize=9)
    ax.set_xlabel("평균 시간 (분, 짧을수록 좋음)")
    ax.legend(frameon=False, fontsize=8, loc="lower center", bbox_to_anchor=(.5, 1.06), ncol=2)
    ax.set_title(f"전략별 처리 시간 (입고·출고 각 {res['rate_per_direction']}/h, AMR 4대)", fontsize=11, pad=34)
    ax.spines[["top", "right"]].set_visible(False)
    # (2) 전략별 장비·레인
    ax = fig.add_subplot(gs[0, 1])
    sh = [res["variants"][n]["shuttle_util"] * 100 for n in names]
    lf = [res["variants"][n]["lift_util"] * 100 for n in names]
    hc = [res["variants"][n]["honeycomb"] * 100 for n in names]
    w = .26
    ax.barh([y + w for y in ys], lf, height=w - .02, color=viz.FLEET[2], label="리프트 가동률 %")
    ax.barh(ys, sh, height=w - .02, color=viz.FLEET[3], label="셔틀 가동률 %")
    ax.barh([y - w for y in ys], hc, height=w - .02, color="#a8a69f", label="쓰는 레인 속 빈 칸 %")
    for y, vs in zip(ys, zip(lf, sh, hc)):
        for dy, v in zip((w, 0, -w), vs):
            ax.text(v + .3, y + dy, f"{v:.1f}", va="center", fontsize=7, color=viz.TEXT2)
    ax.set_yticks(ys, [""] * len(names))
    ax.set_xlabel("% (낮을수록 여유 있음)")
    ax.legend(frameon=False, fontsize=8, loc="lower center", bbox_to_anchor=(.5, 1.06), ncol=3)
    ax.set_title("전략별 장비 사용과 레인 낭비", fontsize=11, pad=34)
    ax.spines[["top", "right"]].set_visible(False)
    # (3) 부하별
    ax = fig.add_subplot(gs[1, 0])
    rates = sorted(res["loads"], key=float)
    xs = [float(r) * 2 for r in rates]
    ax.plot(xs, [res["loads"][r]["dock_to_stock_mean_s"] / 60 for r in rates], "o-", color=viz.FLEET[0],
            label="입고→적재 평균")
    ax.plot(xs, [res["loads"][r]["dock_to_stock_p95_s"] / 60 for r in rates], "o--", color=viz.FLEET[0], alpha=.6,
            label="입고→적재 95%")
    ax.plot(xs, [res["loads"][r]["out_truck_mean_min"] for r in rates], "s-", color=viz.FLEET[1], label="출고 트럭 평균")
    ax2 = ax.twinx()
    ax2.plot(xs, [res["loads"][r]["amr_util"] * 100 for r in rates], "^:", color=viz.TEXT2, label="AMR 가동률 %")
    ax2.set_ylim(0, 100)
    ax2.set_ylabel("AMR 가동률 %", color=viz.TEXT2)
    ax.set_xlabel("처리량 (입고+출고 팔레트/시간)")
    ax.set_ylabel("분")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, frameon=False, fontsize=8, loc="upper left")
    ax.set_title("부하가 늘면 어디서 막히나 (AMR 4대)", fontsize=11)
    ax.grid(color=viz.GRID, lw=.6)
    ax.spines[["top"]].set_visible(False)
    # 요약 글
    ax = fig.add_subplot(gs[1, 1])
    ax.axis("off")
    base, rnd = res["variants"][names[0]], res["variants"]["적재: 무작위 레인"]
    fifo = res["variants"]["AMR: 오래된 작업 먼저"]
    lines = [
        "읽는 법",
        "",
        f"- 제안 알고리즘: 입고 팔레트는 평균 {base['dock_to_stock_mean_s'] / 60:.0f}분 만에 레인에 들어가고,",
        f"  출고 트럭은 도착부터 상차 완료까지 평균 {base['out_truck_mean_min']:.0f}분 (24팔레트 상차만 12분).",
        f"- 무작위 레인 배정과 비교: 쓰는 레인 {rnd['used_lanes'] - base['used_lanes']:.0f}개 더,",
        f"  레인 속 빈 칸 {rnd['honeycomb'] * 100:.0f}% → {base['honeycomb'] * 100:.0f}%, 셔틀 일 {(1 - base['shuttle_util'] / rnd['shuttle_util']) * 100:.0f}% 감소.",
        f"- AMR을 오래된 작업 순으로만 보내면 출고 트럭이 {fifo['out_truck_mean_min']:.0f}분까지 늘어난다.",
        "  제안 방식은 거리·대기 시간·출고 버퍼 압박을 함께 본다.",
        f"- 리프트 가동률 {base['lift_util'] * 100:.0f}%, 셔틀 {base['shuttle_util'] * 100:.0f}%: 병목은 AMR 4대.",
        "  그래서 리프트 이중 사이클·셔틀 부하 분산은 지금 부하에서는 차이가 작다.",
        "- 왼쪽 그래프: 입고+출고 90/h를 넘으면 대기 시간이 빠르게 늘어난다.",
        "  그 이상이 필요하면 AMR을 늘리는 것이 가장 효과가 크다.",
        f"- 8시간 x 전 조건에서 AMR끼리 충돌 {base['collisions']:.0f}건 (시공간 예약).",
    ]
    ax.text(0, 1, "\n".join(lines), va="top", fontsize=9.5, color=viz.TEXT, linespacing=1.55)
    # (4) AMR 궤적 10분
    ax = fig.add_subplot(gs[2, :])
    walk = L._walk
    for (c, r) in walk:
        ax.add_patch(plt.Rectangle((c * 1.2, r * 1.2), 1.2, 1.2, fc="#f1f0ec", ec="none"))
    t0, t1 = 2 * 3600, 2 * 3600 + 600
    for i, tr in enumerate(sim.tracks):
        pts = tr[t0:t1]
        ax.plot([cx(c) for c, _ in pts], [cx(r) for _, r in pts], color=viz.FLEET[i], lw=1.4, alpha=.85,
                label=L.amr_id(i))
        ax.plot(cx(pts[-1][0]), cx(pts[-1][1]), "o", color=viz.FLEET[i], ms=5)
    for c, (name, _) in L.contacts.items():
        if name.startswith(("L", "IN", "OUT", "QC", "CH")):
            ax.text(cx(c[0]), cx(c[1]) + (1.3 if c[1] >= 15 else -1.5), name, ha="center", fontsize=7,
                    color=viz.TEXT2)
    ax.set_xlim(0, 120)
    ax.set_ylim(11.5, 31.2)
    ax.set_aspect("equal")
    ax.set_xlabel("x (m)")
    ax.set_title(f"AMR 4대 경로 10분 (시간 예약으로 충돌 {res['variants'][names[0]]['collisions']:.0f}건)", fontsize=11)
    ax.set_ylabel("y (m)")
    ax.legend(frameon=False, fontsize=8, ncol=4, loc="upper center", bbox_to_anchor=(.5, -.22))
    ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("D4 적재·출고 알고리즘 비교 (8시간 x 3회 평균)", fontsize=13, y=.98)
    fig.savefig(OUT / "d4_compare.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    main()
