"""배치안 D4 (남쪽 도크 + 사무실 남쪽 중앙 + 도크 사이 예비 구역 + 3레일 + 남쪽 검수 띠) 도면.  python -m plans.d_plan out

- 남쪽 벽: 입고 구역 x 0~40, 사무실 x 40~80 (2층), 출고 구역 x 80~120. 구역마다 도크 3+3 사이 가운데에 예비 구역.
- 남쪽 띠: 도크 적치·계량 14.4m → AMR 통로 4.8m → 리프트·버퍼 줄 2.4m → 검수·지원 띠 9.8m.
- 보관은 벽에서 벽까지 하나로, 모셔틀 레일 3개(가운데 레일이 x 60, 입·출고 경계 바로 위). 레인 깊이 14.
- 레일은 검수 띠를 지나 리프트까지 내려온다. 레인은 검수 띠 북쪽부터 북쪽 벽까지.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Rectangle

from plans.shuttle_plan import (BUILDING_H, DOCK_W, FLOW, H, INK, INK2, LANE, LEVELS, OFF_C, PD_BUF, RACK_C,
                                RACK_F, RACK_H, RAIL_C, STAGE_DEPTH, STAGE_LANES, SURF, W, box, dim)

N_RAILS = 3
STAGE = 14.4
AISLE = (14.4, 19.2)
LIFT_ROW = (19.2, 21.6)
BAND = (21.6, 31.4)              # 검수·지원 띠 (레인 7줄 분량)
LANE_Y0, LANE_Y1 = 31.4, 78.8
SX0, SX1 = 1.0, 119.0             # 보관 구역 (서·동 벽에서 1 m씩 띄움)
IN_ZONE, OUT_ZONE = (0.0, 40.0), (80.0, 120.0)
IN_X = [2.5, 7.5, 12.5, 27.5, 32.5, 37.5]            # 도크 3 + 예비 구역 + 도크 3
OUT_X = [117.5, 112.5, 107.5, 92.5, 87.5, 82.5]
IN_SPARE, OUT_SPARE = (15.5, 24.5), (95.5, 104.5)    # 도크 사이 가운데 예비 구역
DEPTH = 14                        # 3레일로 벽에서 벽까지 채우는 최대 깊이

# Unity 구현(ShuttleRackSystem 기본값 = Blender 상수, 2026-11-19 팀원 명세)에 맞춘 랙 치수
RAIL_CX = (21.8, 60.0, 98.2)      # 통로(레일) 중심 x = 리프트 중심 x
AISLE_W = 2.0                     # 모셔틀 통로 폭 (칸 위치 x = ±(1.0 + (칸-0.5) x 1.25))
SLOT_D4 = 1.25                    # 레인 안 칸 간격 → 깊이 14칸 = 17.5 m
LEVEL_H = 1.45                    # 단 간격 (레일 윗면 0.40 + 1.45 x (단-1))
RAIL_TOP0 = 0.40                  # 1단 레일 윗면 높이 (창고 바닥 윗면 기준)
BUF_PITCH = 1.3                   # 리프트 버퍼 칸 간격
BUF_TOP = 0.50                    # 버퍼 롤러 윗면 높이
SHUTTLE_XFER_Y = LIFT_ROW[0] + 1.2 + 2.0   # 모셔틀이 리프트와 팔레트를 주고받는 위치 (리프트 중심 + 2.0 m)
IN_C, OUT_C = "#d7f0e6", "#fbe1d6"


def geometry() -> dict:
    block = DEPTH * SLOT_D4
    h = AISLE_W / 2
    rails = []
    for c in RAIL_CX:
        rails.append({"west_block": (c - h - block, c - h), "rail": (c - h, c + h),
                      "east_block": (c + h, c + h + block)})
    n = int((LANE_Y1 - LANE_Y0) // LANE)
    return {"rails": rails, "lanes_per_side": n, "pallet_positions": N_RAILS * 2 * n * DEPTH * LEVELS,
            "lanes_total": N_RAILS * 2 * n * LEVELS, "shuttle_sets": N_RAILS * LEVELS, "lifts": N_RAILS,
            "office_x": [(IN_ZONE[1], OUT_ZONE[0])]}


def draw(path: Path) -> dict:
    g = geometry()
    fig, ax = plt.subplots(figsize=(15, 12.5))
    fig.patch.set_facecolor(SURF)
    ax.set_facecolor(SURF)
    box(ax, 0, 0, W, H, "#ffffff", ec=INK, lw=2.2)
    box(ax, IN_ZONE[0], 0, IN_ZONE[1] - IN_ZONE[0], STAGE, IN_C)
    box(ax, OUT_ZONE[0], 0, OUT_ZONE[1] - OUT_ZONE[0], STAGE, OUT_C)
    for (ox0, ox1), title in zip(g["office_x"], ["사무실 · 관제실 · 휴게 (2층)"]):
        box(ax, ox0, 0, ox1 - ox0, STAGE, OFF_C, ec=INK2, lw=1)
        ax.text((ox0 + ox1) / 2, STAGE / 2 + 1.5, title, ha="center", va="center", fontsize=9.5, weight="bold",
                color=INK)
        ax.text((ox0 + ox1) / 2, STAGE / 2 - 1.8,
                f"{ox1 - ox0:.0f} x {STAGE} m = {(ox1 - ox0) * STAGE:.0f}㎡ x 2층 = {(ox1 - ox0) * STAGE * 2:,.0f}㎡",
                ha="center", va="center", fontsize=7.5, color=INK2)

    for (a_, b_), label, ec_ in [(IN_SPARE, "입고 예비 구역\n(초과 적치·\n검수 대기)", FLOW),
                                 (OUT_SPARE, "출고 예비 구역\n(초과 적치·\n합포장 대기)", "#eb6834")]:
        box(ax, a_, 1.8, b_ - a_, 8.4, "#ffffff", ec=ec_, lw=.9, ls="--")
        ax.text((a_ + b_) / 2, 6.0, f"{label}\n{b_ - a_:.0f} x 8.4 m", ha="center", va="center", fontsize=7, color=INK)

    # 도크 (남쪽 벽) + 적치 차선 + 계량/하차 지점
    for i, (xs, kind) in enumerate([(x, "in") for x in IN_X] + [(x, "out") for x in OUT_X]):
        k = i % 6
        fc, ec = ("#9fd9c2", FLOW) if kind == "in" else ("#f6b79c", "#eb6834")
        box(ax, xs - 1.25, -15.5, 2.5, 14.0, "#ffffff", ec=INK2, lw=.9)
        box(ax, xs - 1.25, -4.0, 2.5, 2.5, "#e4e3df", ec=INK2, lw=.9)
        ax.plot([xs - DOCK_W / 2, xs + DOCK_W / 2], [0, 0], color=SURF, lw=4)
        box(ax, xs - DOCK_W / 2, 0, DOCK_W, 1.2, fc, ec=ec, lw=.8)
        ax.text(xs, .6, f"{'IN' if kind == 'in' else 'OUT'}{k + 1}", ha="center", va="center", fontsize=6)
        for dx in (-1.8, -.6, .6, 1.8):
            for j in range(STAGE_DEPTH):
                box(ax, xs + dx - .55, 1.8 + j * 1.2, 1.1, 1.1, "#ffffff", ec=ec, lw=.35)
        box(ax, xs - 1.2, 10.6, 2.4, 2.4, ec, ec=INK, lw=.8)
        ax.text(xs, 11.8, "계량" if kind == "in" else "하차", ha="center", va="center", fontsize=6, color="white")

    # AMR 통로
    box(ax, 0, AISLE[0], W, AISLE[1] - AISLE[0], "#fff7e0", ec="#e0c27a", lw=.8)
    ax.text(60, AISLE[0] + 1.0, "AMR 통로 4.8 m (양방향) · 도크 ↔ 리프트", ha="center", va="center", fontsize=8.5,
            color=INK2)

    # 검수·지원 띠 (리프트 줄과 레인 사이). 레일 사이 6칸에 시설 배치
    box(ax, 0, BAND[0], W, BAND[1] - BAND[0], "#f6f5f2", ec="none")
    edges = [0.0] + [v for rl in g["rails"] for v in rl["rail"]] + [W]
    segs = [(edges[i], edges[i + 1]) for i in range(0, len(edges), 2)]
    seg_use = [("charge", "셔틀·리프트 정비"), ("입고 검수·보류\n(무게 불일치)", "//"),
               ("포장·라벨링\n출고 검수", ".."), ("charge", "예비 셔틀·부품")]
    y0, y1 = BAND[0] + .8, BAND[1] - .8
    for (a_, b_), use in zip(segs, seg_use):
        if use[0] == "charge":
            west = a_ == 0.0
            bx0, bx1 = (a_ + 10.5, b_ - .8) if west else (a_ + .8, b_ - 10.5)
            box(ax, bx0, y0, bx1 - bx0, y1 - y0, "#ffffff", ec=INK2, lw=.8)
            ax.text((bx0 + bx1) / 2, (y0 + y1) / 2,
                    f"{use[1]}\n{bx1 - bx0:.0f} x {y1 - y0:.1f} m = {(bx1 - bx0) * (y1 - y0):.0f}㎡",
                    ha="center", va="center", fontsize=7, bbox=dict(fc="#ffffff", ec="none", pad=.4))
            for k in range(3):
                cy = y0 + .2 + k * 2.9
                if west:
                    box(ax, 0, cy, .6, 2.0, "#eda100", ec="#c98500", lw=.8)
                    box(ax, .6, cy - .1, 1.8, 2.2, "none", ec="#c98500", lw=.7, ls="--")
                else:
                    box(ax, W - .6, cy, .6, 2.0, "#eda100", ec="#c98500", lw=.8)
                    box(ax, W - 2.4, cy - .1, 1.8, 2.2, "none", ec="#c98500", lw=.7, ls="--")
            tx = 3.2 if west else W - 3.2
            ax.text(tx, (BAND[0] + BAND[1]) / 2, "AMR 충전 3\n(벽면)\nAMR 대기", ha="left" if west else "right",
                    va="center", fontsize=7, color=INK2)
            continue
        label, hatch = use
        bx0, bx1 = a_ + .8, b_ - .8
        box(ax, bx0, y0, bx1 - bx0, y1 - y0, "#ffffff", ec=INK2, lw=.8, hatch=hatch)
        ax.text((bx0 + bx1) / 2, (y0 + y1) / 2, f"{label}\n{bx1 - bx0:.0f} x {y1 - y0:.1f} m = {(bx1 - bx0) * (y1 - y0):.0f}㎡",
                ha="center", va="center", fontsize=7, bbox=dict(fc="#ffffff", ec="none", pad=.4))

    # 레인·레일·리프트
    n = g["lanes_per_side"]
    for r, rail in enumerate(g["rails"]):
        for side in ("west_block", "east_block"):
            a, b = rail[side]
            for i in range(n):
                ly = LANE_Y0 + i * LANE
                box(ax, a, ly + .1, b - a, LANE - .2, RACK_F, ec=RACK_C, lw=.35)
                for k in range(1, DEPTH):
                    ax.plot([a + k * SLOT_D4] * 2, [ly + .1, ly + LANE - .1], color=RACK_C, lw=.2)
        ra, rb = rail["rail"]
        box(ax, ra, LIFT_ROW[1], rb - ra, LANE_Y0 + n * LANE - LIFT_ROW[1], "#e9e6fb", ec=RAIL_C, lw=.8)
        ax.text((ra + rb) / 2, 62, f"모셔틀 레일 {r + 1}", rotation=90, ha="center", va="center", fontsize=8,
                color=RAIL_C, bbox=dict(fc="#e9e6fb", ec="none", pad=.3))
        cx = (ra + rb) / 2
        box(ax, cx - 1.2, LIFT_ROW[0], 2.4, 2.4, RAIL_C, ec=INK, lw=.8)
        ax.text(cx, LIFT_ROW[0] + 1.2, f"L{r + 1}", ha="center", va="center", fontsize=7, color="white")
        for k in range(PD_BUF):
            box(ax, cx - 1.2 - (k + 1) * BUF_PITCH + .1, LIFT_ROW[0] + .3, BUF_PITCH - .2, 1.8, "#9fd9c2", ec=FLOW, lw=.6)
            box(ax, cx + 1.2 + k * BUF_PITCH + .1, LIFT_ROW[0] + .3, BUF_PITCH - .2, 1.8, "#f6b79c", ec="#eb6834", lw=.6)
        ax.text(cx - 1.2 - PD_BUF * BUF_PITCH / 2, AISLE[1] - .9, f"입고 버퍼 {PD_BUF}", ha="center", fontsize=6.5,
                color=INK2, va="center")
        ax.text(cx + 1.2 + PD_BUF * BUF_PITCH / 2, AISLE[1] - .9, f"출고 버퍼 {PD_BUF}", ha="center", fontsize=6.5,
                color=INK2, va="center")

    # 흐름
    def arrow(pts, color):
        for p, q in zip(pts, pts[1:]):
            ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>" if q == pts[-1] else "-", mutation_scale=15,
                                         lw=2.2, color=color, zorder=6))
    arrow([(IN_X[2], 13.2), (IN_X[2], 19.3)], FLOW)
    arrow([(OUT_X[2], 19.3), (OUT_X[2], 13.2)], "#eb6834")

    # 치수
    dim(ax, *IN_ZONE, -19, "입고·검수 40")
    dim(ax, IN_ZONE[1], OUT_ZONE[0], -19, "사무실 40")
    dim(ax, *OUT_ZONE, -19, "출고 스테이징 40")
    dim(ax, 0, W, -22.5, "120.0 m", fs=10)
    dim(ax, 0, STAGE, -5, "14.4", vertical=True, fs=7)
    dim(ax, AISLE[0], LIFT_ROW[1], -5, "7.2", vertical=True, fs=7)
    dim(ax, BAND[0], BAND[1], -5, "검수 9.8", vertical=True, fs=7)
    dim(ax, LANE_Y0, LANE_Y0 + n * LANE, -5, f"레인 {n}줄 x {LANE} m", vertical=True, fs=7)
    dim(ax, 0, H, -9, "80.0 m", vertical=True, fs=10)
    ax.set_xlim(-12, W + 4)
    ax.set_ylim(-25, H + 2)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("물류창고 배치안 D4 · 남쪽 도크(가운데 예비 구역) + 사무실 남쪽 중앙 + 3레일 + 남쪽 검수 띠 · 120 m x 80 m",
                 fontsize=14, weight="bold", pad=14)
    spec1 = [f"보관 용량 {g['pallet_positions']:,} 팔레트", f"레인 {g['lanes_total']:,}개 (깊이 {DEPTH})",
             f"랙 {RACK_H:.0f} m · {LEVELS}층 · 건물 {BUILDING_H:.0f} m", f"셔틀 {g['shuttle_sets']}세트"]
    spec2 = [f"리프트 3기 겸용 + 버퍼 입·출 {PD_BUF}칸씩", f"도크 입·출 각 6 (남쪽 벽), 도크별 적치 {STAGE_LANES * STAGE_DEPTH}칸",
             "AMR 4대 기준 처리 한계 약 151 팔레트/h (5대 189, 리프트 한계 628)"]
    fig.text(.5, .045, "   ·   ".join(spec1) + "\n" + "   ·   ".join(spec2), ha="center", va="center", fontsize=9.5,
             color=INK2, linespacing=1.8)
    fig.savefig(path, dpi=160, bbox_inches="tight", facecolor=SURF)
    plt.close(fig)
    return g


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
    out.mkdir(parents=True, exist_ok=True)
    g = draw(out / "d_plan.png")
    (out / "d_plan.json").write_text(json.dumps(g, ensure_ascii=False, indent=1, default=list))
    print({k: g[k] for k in ("lanes_per_side", "pallet_positions", "office_x")})
