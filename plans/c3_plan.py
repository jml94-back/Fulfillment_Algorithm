"""배치안 C (중앙 통로 + 리프트 3기) 도면.  python -m plans.c3_plan out → out/c3_plan.png

experiments/shuttle_compare.py 의 "C 중앙 통로 3기 겸용"과 같은 치수.
- 가운데 동서 통로(AMR 4.8m)가 입고 도크 → 리프트 → 출고 도크를 일직선으로 잇는다.
- 리프트는 레일 중간, 통로 북쪽 줄(2.4m)에 레일 옆으로 붙는다. 서쪽 입고 버퍼 5, 동쪽 출고 버퍼 5.
- 2층 이상 레일은 통로 위를 건너 남·북 레인을 잇는다. 1층(바닥) 레일만 통로에서 끊겨 남·북에 셔틀을 따로 둔다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Rectangle

from plans.shuttle_plan import (BUILDING_H, CORR, DEPTH, DOCK_PITCH, DOCK_W, FLOW, H, IN_C, IN_X, INK, INK2, LANE,
                                LEVEL_H, LEVELS, OFF_C, OUT_C, OUT_X, PD_BUF, RACK_C, RACK_F, RACK_H, RAIL, RAIL_C,
                                SLOT, STAGE_DEPTH, STAGE_LANES, STORE_TOP, SURF, W, box, dim)

N_RAILS = 3
MID = 34.8                       # 도크 6개의 가운데 = 통로 중심
AISLE = 4.8
LIFT_ROW = 2.4
A0, A1 = MID - AISLE / 2, MID + AISLE / 2          # 32.4 ~ 37.2 → 시뮬레이션과 맞추기 위해 31.2~36.0 사용
A0, A1 = 31.2, 36.0
L0, L1 = A1, A1 + LIFT_ROW                        # 리프트 줄 36.0 ~ 38.4
SOUTH = (1.2, A0)
NORTH = (L1, 68.4)
DOCK_Y = [19.8, 25.8, 31.8, 37.8, 43.8, 49.8]


def geometry() -> dict:
    sx0, sx1 = IN_X + CORR, OUT_X - CORR
    block = DEPTH * SLOT
    module = 2 * block + RAIL
    margin = (sx1 - sx0 - N_RAILS * module) / 2
    rails, x = [], sx0 + margin
    for _ in range(N_RAILS):
        rails.append({"west_block": (x, x + block), "rail": (x + block, x + block + RAIL),
                      "east_block": (x + block + RAIL, x + module)})
        x += module
    ns = int((SOUTH[1] - SOUTH[0]) // LANE)
    nn = int((NORTH[1] - NORTH[0]) // LANE)
    lanes_side = ns + nn
    return {"rails": rails, "lanes_south": ns, "lanes_north": nn,
            "pallet_positions": N_RAILS * 2 * lanes_side * DEPTH * LEVELS,
            "lanes_total": N_RAILS * 2 * lanes_side * LEVELS,
            "shuttle_sets": N_RAILS * LEVELS + N_RAILS, "lifts": N_RAILS,
            "aisle_y": (A0, A1), "lift_row_y": (L0, L1)}


def draw(path: Path) -> dict:
    g = geometry()
    fig, ax = plt.subplots(figsize=(17, 10.2))
    fig.patch.set_facecolor(SURF)
    ax.set_facecolor(SURF)
    box(ax, 0, 0, W, H, "#ffffff", ec=INK, lw=2.2)
    box(ax, 0, 0, IN_X, STORE_TOP, IN_C)
    box(ax, OUT_X, 0, W - OUT_X, STORE_TOP, OUT_C)
    box(ax, 0, STORE_TOP, W, H - STORE_TOP, OFF_C)
    ax.text(30, STORE_TOP + 5.2, "셔틀·리프트 정비 · 예비 셔틀 보관", ha="center", fontsize=10, color=INK2)
    ax.text(90, STORE_TOP + 5.2, "사무실 · 관제실 · 휴게", ha="center", fontsize=10, color=INK2)

    # 중앙 AMR 통로 (서쪽 연결 통로 ~ 동쪽 연결 통로)
    box(ax, IN_X, A0, OUT_X - IN_X, A1 - A0, "#fff7e0", ec="#e0c27a", lw=.8)
    ax.text(60, A0 + .9, "중앙 AMR 통로 4.8 m · 입고 도크 → 리프트 → 출고 도크 일직선", ha="center", va="center",
            fontsize=8.5, color=INK2)

    # 레인 (남·북)
    for r, rail in enumerate(g["rails"]):
        for side in ("west_block", "east_block"):
            a, b = rail[side]
            for (y0, n) in ((SOUTH[0], g["lanes_south"]), (NORTH[0], g["lanes_north"])):
                for i in range(n):
                    ly = y0 + i * LANE
                    box(ax, a, ly + .1, b - a, LANE - .2, RACK_F, ec=RACK_C, lw=.35)
                    for k in range(1, DEPTH):
                        ax.plot([a + k * SLOT] * 2, [ly + .1, ly + LANE - .1], color=RACK_C, lw=.2)
        ra, rb = rail["rail"]
        for (y0, y1) in (SOUTH, NORTH):
            box(ax, ra, y0, rb - ra, y1 - y0, "#e9e6fb", ec=RAIL_C, lw=.8)
        # 통로 위를 건너는 레일(2층 이상)
        box(ax, ra, A0, rb - ra, L1 - A0, "none", ec=RAIL_C, lw=.9, ls="--")
        ax.text((ra + rb) / 2, 15, f"레일 {r + 1}", rotation=90, ha="center", va="center", fontsize=8, color=RAIL_C,
                bbox=dict(fc="#e9e6fb", ec="none", pad=.3))
        ax.text((ra + rb) / 2, 54, f"레일 {r + 1}", rotation=90, ha="center", va="center", fontsize=8, color=RAIL_C,
                bbox=dict(fc="#e9e6fb", ec="none", pad=.3))
        # 리프트 (레일 바로 동쪽, 리프트 줄) + 버퍼
        lx0 = rb + .2
        box(ax, lx0, L0, 2.4, LIFT_ROW, RAIL_C, ec=INK, lw=.8)
        ax.text(lx0 + 1.2, L0 + 1.2, f"L{r + 1}", ha="center", va="center", fontsize=7, color="white")
        for k in range(PD_BUF):
            box(ax, ra - .2 - (k + 1) * SLOT + .1, L0 + .3, SLOT - .2, LIFT_ROW - .6, "#9fd9c2", ec=FLOW, lw=.6)
            box(ax, lx0 + 2.4 + k * SLOT + .1, L0 + .3, SLOT - .2, LIFT_ROW - .6, "#f6b79c", ec="#eb6834", lw=.6)
        ax.text(ra - .2 - PD_BUF * SLOT / 2, A1 - .9, f"입고 버퍼 {PD_BUF}", ha="center", va="center", fontsize=6.5,
                color=INK2)
        ax.text(lx0 + 2.4 + PD_BUF * SLOT / 2, A1 - .9, f"출고 버퍼 {PD_BUF}", ha="center", va="center", fontsize=6.5,
                color=INK2)

    # 도크·계량·출고 적치
    for i, yc in enumerate(DOCK_Y):
        box(ax, -15.5, yc - 1.25, 14.0, 2.5, "#ffffff", ec=INK2, lw=.9)
        box(ax, -4.0, yc - 1.25, 2.5, 2.5, "#e4e3df", ec=INK2, lw=.9)
        box(ax, W + 1.5, yc - 1.25, 14.0, 2.5, "#ffffff", ec=INK2, lw=.9)
        box(ax, W + 1.5, yc - 1.25, 2.5, 2.5, "#e4e3df", ec=INK2, lw=.9)
        ax.plot([0, 0], [yc - DOCK_W / 2, yc + DOCK_W / 2], color=SURF, lw=4)
        ax.plot([W, W], [yc - DOCK_W / 2, yc + DOCK_W / 2], color=SURF, lw=4)
        box(ax, 0, yc - DOCK_W / 2, 1.2, DOCK_W, "#9fd9c2", ec=FLOW, lw=.8)
        box(ax, W - 1.2, yc - DOCK_W / 2, 1.2, DOCK_W, "#f6b79c", ec="#eb6834", lw=.8)
        ax.text(.6, yc, f"IN{i + 1}", rotation=90, ha="center", va="center", fontsize=6.5)
        ax.text(W - .6, yc, f"OUT{i + 1}", rotation=90, ha="center", va="center", fontsize=6.5)
        for dy in (-1.8, -.6, .6, 1.8):
            for k in range(STAGE_DEPTH):
                box(ax, 1.8 + k * 1.2, yc + dy - .55, 1.1, 1.1, "#ffffff", ec="#7cc8aa", lw=.4)
                box(ax, W - 1.8 - (k + 1) * 1.2 + .1, yc + dy - .55, 1.1, 1.1, "#ffffff", ec="#f1a487", lw=.4)
        box(ax, 10.6, yc - 1.2, 2.4, 2.4, FLOW, ec=INK, lw=.8)
        ax.text(11.8, yc, "계량", ha="center", va="center", fontsize=6.5, color="white")
        box(ax, W - 13.0, yc - 1.2, 2.4, 2.4, "#eb6834", ec=INK, lw=.8)
        ax.text(W - 11.8, yc, "하차", ha="center", va="center", fontsize=6.5, color="white")
    # AMR 충전: 서·동 연결 통로 남쪽 끝 (3기씩)
    for x0 in (IN_X, OUT_X - CORR):
        for k in range(3):
            box(ax, x0 + .6, 2.0 + k * 2.2, 2.4, 1.6, "#eda100", ec="#c98500", lw=.8)
    ax.text(IN_X + CORR / 2, 9.4, "AMR 충전 3", ha="center", fontsize=7, color=INK2)
    ax.text(OUT_X - CORR / 2, 9.4, "AMR 충전 3", ha="center", fontsize=7, color=INK2)
    box(ax, 1.8, 3.0, 9.6, 6.0, "#ffffff", ec=INK2, lw=.8, hatch="//")
    ax.text(6.6, 6.0, "검수 보류 구역\n(무게 불일치)", ha="center", va="center", fontsize=8,
            bbox=dict(fc="#ffffff", ec="none", pad=.5))
    box(ax, W - 11.4, 3.0, 9.6, 6.0, "#ffffff", ec=INK2, lw=.8, hatch="..")
    ax.text(W - 6.6, 6.0, "포장·라벨링\n출고 검수", ha="center", va="center", fontsize=8,
            bbox=dict(fc="#ffffff", ec="none", pad=.5))
    ax.text(7.2, STORE_TOP - 4, "입고·검수\n계량대 6", ha="center", va="center", fontsize=9.5, weight="bold")
    ax.text(W - 7.2, STORE_TOP - 4, "출고\n스테이징", ha="center", va="center", fontsize=9.5, weight="bold")

    # 흐름
    def arrow(p, q, color):
        ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=16, lw=2.4, color=color, zorder=6))
    yf = A0 + 3.0
    arrow((13.2, yf), (g["rails"][0]["rail"][0] - 1.0, yf), FLOW)
    arrow((g["rails"][2]["rail"][1] + 9.0, yf), (OUT_X + 1.0, yf), "#eb6834")
    ax.text(22, yf + 1.0, "입고", fontsize=8.5, color=FLOW, weight="bold")
    ax.text(103, yf + 1.0, "출고", fontsize=8.5, color="#eb6834", weight="bold")

    # 치수
    dim(ax, 0, IN_X, -4.5, "14.4")
    dim(ax, IN_X, IN_X + CORR, -4.5, "3.6")
    dim(ax, IN_X + CORR, OUT_X - CORR, -4.5, "보관 84.0 (레일 3 x 양쪽 레인 13 m)")
    dim(ax, OUT_X - CORR, OUT_X, -4.5, "3.6")
    dim(ax, OUT_X, W, -4.5, "14.4")
    dim(ax, 0, W, -8.0, "120.0 m", fs=10)
    dim(ax, SOUTH[0], SOUTH[1], -20, f"남쪽 레인 {g['lanes_south']}줄", vertical=True, fs=7)
    dim(ax, A0, L1, -20, "7.2", vertical=True, fs=7)
    dim(ax, NORTH[0], STORE_TOP, -20, f"북쪽 레인 {g['lanes_north']}줄", vertical=True, fs=7)
    dim(ax, STORE_TOP, H, -20, "10.4", vertical=True, fs=7)
    dim(ax, 0, H, -24, "80.0 m", vertical=True, fs=10)
    ax.set_xlim(-28, W + 18)
    ax.set_ylim(-10, H + 2)
    ax.set_aspect("equal")
    ax.axis("off")

    fig.text(.5, .93, "물류창고 배치안 C · 중앙 통로 + 리프트 3기 · 셔틀 앤 세틀라이트 · 120 m x 80 m",
             ha="center", fontsize=15, weight="bold")
    spec1 = [f"보관 용량 {g['pallet_positions']:,} 팔레트", f"레인 {g['lanes_total']:,}개 (깊이 {DEPTH})",
             f"랙 {RACK_H:.0f} m · {LEVELS}층 · 건물 {BUILDING_H:.0f} m",
             f"셔틀 {g['shuttle_sets']}세트 (1층만 남·북 분리)"]
    spec2 = [f"리프트 3기 겸용 + 버퍼 입·출 {PD_BUF}칸씩", f"도크 입·출 각 6, 도크별 적치 {STAGE_LANES * STAGE_DEPTH}칸",
             "AMR 4대 기준 처리 한계 약 141 팔레트/h (A안 대비 +41%)"]
    fig.text(.5, .07, "   ·   ".join(spec1) + "\n" + "   ·   ".join(spec2), ha="center", va="center", fontsize=9.5,
             color=INK2, linespacing=1.8)
    fig.savefig(path, dpi=160, bbox_inches="tight", facecolor=SURF)
    plt.close(fig)
    return g


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
    out.mkdir(parents=True, exist_ok=True)
    g = draw(out / "c3_plan.png")
    (out / "c3_plan.json").write_text(json.dumps(g, ensure_ascii=False, indent=1, default=list))
    print({k: g[k] for k in ("lanes_south", "lanes_north", "pallet_positions", "shuttle_sets")})
