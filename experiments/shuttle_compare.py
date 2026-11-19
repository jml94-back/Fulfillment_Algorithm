"""셔틀 앤 세틀라이트 배치·정책별 처리 한계 비교.

    python -m experiments.shuttle_compare [out]

처리 한계 = 입고·출고를 감당 못 할 만큼 넣었을 때 실제로 처리되는 팔레트/시간(포화 처리량).
AMR 40대 = AMR이 사실상 무제한일 때, 즉 리프트·셔틀 쪽 한계.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import matplotlib.pyplot as plt

from warehouse import viz
from warehouse.shuttle import Policy, Sim, make_layouts

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
SEEDS = [0, 1, 2]
HOURS = 3.0


def capacity(layout, policy, n_amr, rate=500, hours=2.0) -> float:
    """포화 처리량: 감당 못 할 만큼(한 방향 500/h) 넣고, 0.5~2시간 구간에 끝난 팔레트 수/시간."""
    vals = []
    for s in SEEDS:
        sim = Sim(layout, policy, rate, hours, n_amr=n_amr, seed=s)
        sim.hours_cut = hours
        sim.run_until = hours * 3600
        sim.run()
        done = [j for j in sim.done if 1800 <= j.done <= hours * 3600]
        vals.append(len(done) / (hours - .5))
    return round(statistics.mean(vals))


def at_load(layout, policy, rate, n_amr) -> dict:
    ms = [Sim(layout, policy, rate, HOURS, n_amr=n_amr, seed=s).run() for s in SEEDS]
    keys = ["truck_ready_mean_min", "truck_ready_p95_min", "putaway_mean_s", "lift_util", "amr_util"]
    return {k: statistics.mean(m[k] for m in ms) for k in keys}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    lays = make_layouts()
    res: dict = {"capacity": {}, "at_load": {}, "policy": {}}
    fleets = [4, 8, 12, 40]
    for name, L in lays.items():
        res["capacity"][name] = {}
        for n in fleets:
            c = capacity(L, Policy(), n)
            res["capacity"][name][n] = c
            print(f"{name:16s} AMR {n:2d}대  처리 한계 {c:4d} 팔레트/h", flush=True)
        res["at_load"][name] = at_load(L, Policy(), 50, 4)
        print("   부하 100팔레트/h, AMR 4대:", {k: round(v, 2) for k, v in res["at_load"][name].items()}, flush=True)

    # 정책 비교: C 중앙 통로 6기, AMR 12대 (리프트·셔틀 쪽이 드러나도록)
    Lc = lays["C 중앙 통로 6기 겸용"]
    Lb = lays["B 남입·북출 6기 고정"]
    La = lays["A 남쪽 3기 겸용"]
    for label, L, pol in [("A3 · 전체 정책", La, Policy()),
                          ("A3 · 이중 사이클 끔", La, Policy(dual_cycle=False)),
                          ("A3 · 부하분산 끔", La, Policy(balance=False)),
                          ("A3 · ABC 끔", La, Policy(abc=False))]:
        c = capacity(L, pol, 40)
        res["policy"][label] = c
        print(f"정책 {label:18s} AMR 40대(리프트·셔틀 한계) {c} 팔레트/h", flush=True)

    (OUT / "shuttle_compare.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))

    draw(res, lays, fleets)


KEEP = ["A 남쪽 3기 겸용", "B 남·북 6기 겸용", "C 중앙 통로 3기 겸용", "D4 3레일 깊이 14 + 검수 띠"]
SHORT = {"A": "A  남쪽 리프트 3기", "B-dual": "B  남·북 리프트 6기", "C3": "C  중앙 통로 리프트 3기",
         "D4": "D  남쪽 도크 + 검수 띠"}
# 배치도 없이 읽히도록 쓴 안별 특징 (그래프 아래 열마다 하나씩)
DESC = {
    "A": ["입고 도크는 서쪽 벽, 출고 도크는 동쪽 벽.",
          "레일 3개의 남쪽 끝에 리프트 1기씩",
          "(입고·출고 겸용), 그 앞에 동서 AMR 통로.",
          "AMR이 서쪽 도크 → 남쪽 통로 → 리프트",
          "→ 동쪽 도크로 ㄷ자로 돌아 이동이 길다.",
          "가장 단순한 기준안."],
    "B-dual": ["A에 레일 북쪽 끝 리프트 3기와",
               "북쪽 AMR 통로를 더한 안 (리프트 6기 겸용).",
               "리프트·셔틀 한계는 가장 높지만,",
               "도크가 서·동 벽이라 AMR 이동은 A와",
               "거의 같아 AMR 4대에서는 이득이 작다.",
               "북쪽 통로만큼 용량이 줄어든다."],
    "C3": ["도크는 A와 같이 서·동 벽.",
           "창고 가운데를 동서로 가르는 AMR 통로를",
           "내고, 레일 중간에 리프트 3기를 둔다.",
           "도크 높이와 통로가 맞아 AMR이 직선으로",
           "오가고, 셔틀도 남·북 절반만 움직인다.",
           "용량은 A와 같다."],
    "D4": ["입고·출고 도크를 모두 남쪽 벽에 (입고 서쪽,",
           "출고 동쪽, 가운데 2층 사무실). 도크 3+3 사이",
           "예비 구역. 리프트 3기는 레일 남쪽 끝.",
           "리프트 줄 위 9.8 m 검수 띠 (입고 검수·보류,",
           "포장·출고 검수, 정비, 벽면 충전기).",
           "레인 깊이 14로 벽~벽 채워 용량 최대."],
}


def draw(res: dict, lays: dict, fleets: list[int]) -> None:
    """남긴 배치안의 처리 한계(AMR 대수별 묶음 막대) + 보관 용량 + 안별 특징 설명."""
    names = [nm for nm in KEEP if nm in lays]
    keys = [lays[nm].name for nm in names]
    fig = plt.figure(figsize=(12.5, 10.2))
    ax = fig.add_axes([.07, .50, .91, .43])
    ax2 = fig.add_axes([.07, .30, .91, .15], sharex=ax)
    w = .2
    for j, n in enumerate(fleets):
        xs = [i + (j - 1.5) * w for i in range(len(names))]
        ys = [res["capacity"][nm].get(str(n), res["capacity"][nm].get(n)) for nm in names]
        ax.bar(xs, ys, width=w - .03, color=viz.FLEET[j], label=f"AMR {n}대" if n < 40 else "AMR 무제한(40대)", zorder=2)
        for x, y in zip(xs, ys):
            ax.text(x, y + 6, f"{y}", ha="center", va="bottom", fontsize=8, color=viz.TEXT2)
    ax.set_ylabel("처리 한계 (팔레트/시간)")
    ax.grid(axis="y", color=viz.GRID, lw=.8, zorder=0)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.set_ylim(0, 1080)
    ax.legend(frameon=False, ncol=4, loc="upper left")
    ax.tick_params(axis="x", labelbottom=False, length=0)
    fig.suptitle("배치안별 처리 한계와 보관 용량 (둘 다 높을수록 좋음)", fontsize=13, y=.975)
    cap = [lays[nm].positions for nm in names]
    ax2.bar(range(len(names)), cap, width=.55, color="#a8a69f", zorder=2)
    for i, c in enumerate(cap):
        ax2.text(i, c + 500, f"{c:,}", ha="center", va="bottom", fontsize=9, color=viz.TEXT2)
    ax2.set_ylabel("보관 용량 (팔레트)")
    ax2.set_ylim(0, 34000)
    ax2.grid(axis="y", color=viz.GRID, lw=.8, zorder=0)
    ax2.spines[["top", "right", "left"]].set_visible(False)
    ax2.set_xticks(range(len(names)), [SHORT[k] for k in keys], fontsize=10.5, weight="bold")
    ax2.set_xlim(-.6, len(names) - .4)
    # 설명: 막대 묶음 바로 아래 열에 맞춰 배치
    for i, k in enumerate(keys):
        xf = ax2.transData.transform((i, 0))
        xfig = fig.transFigure.inverted().transform(xf)[0]
        fig.text(xfig, .245, "\n".join(DESC[k]), ha="center", va="top", fontsize=8.6, color=viz.TEXT, linespacing=1.6)
    fig.text(.5, .025, "AMR 4대는 계획 대수. 장비 속도는 표준값 가정, AMR끼리의 혼잡은 반영하지 않음. "
             "AMR 무제한은 리프트·셔틀이 감당할 수 있는 상한.", ha="center", fontsize=8, color=viz.TEXT2)
    fig.savefig(OUT / "shuttle_capacity.png", dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)


if __name__ == "__main__":
    main()
