"""120m x 80m 창고 배치안 v3 — 셔틀 앤 세틀라이트(모셔틀 + 세틀라이트) 고밀도 보관.

    python -m plans.shuttle_plan out   → out/shuttle_plan.png, out/shuttle_plan.json

구성
- 보관: 모셔틀 레일(남북 방향) 3개. 레일 양옆으로 동서 방향 레인이 붙는다.
  레인 1개 = 팔레트 10개 깊이, 한 SKU 전용(선입후출, 한쪽에서만 넣고 뺌).
- 각 레일·층마다 모셔틀 1대가 레일을 따라 움직이고, 세틀라이트(자셔틀)가 레인 안으로 들어가 팔레트를 넣고 뺀다.
- 각 레일 남쪽 끝에 수직 리프트 1기와 입고/출고 컨베이어(P&D) 스테이션.
- AMR 4대는 도크·계량대 ↔ 리프트 P&D 사이만 오간다 (남쪽 이송 통로).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, Rectangle

W, H = 120.0, 80.0
BUILDING_H = 25.0           # 창고 내부 높이(m)
RACK_H = 15.0               # 랙 높이(m)
LEVEL_H = 1.5               # 층고: 적재 높이 1.2m + 셔틀 레일·여유 0.3m
LEVELS = int(RACK_H // LEVEL_H)   # 10층
PALLET = 1.1                 # T11 팔레트 1.1 x 1.1 m
SLOT = 1.3                   # 레인 길이 방향 팔레트 간격 (팔레트 + 여유)
LANE = 1.4                   # 레인 폭 (팔레트 + 레일·기둥)
DEPTH = 10                   # 레인 깊이(팔레트 수)
RAIL = 1.8                   # 모셔틀 레일 폭
N_RAILS = 3
IN_X, OUT_X = 14.4, 105.6    # 입고/출고 구역 경계
CORR = 3.6                   # 서·동 AMR 연결 통로
CHG_H, AISLE_H, PD_H = 1.8, 4.8, 2.4   # 남쪽: 충전 대기열, AMR 이송 통로, P&D 스테이션
LANE_Y0 = CHG_H + AISLE_H + PD_H       # 9.0
LANE_Y1 = 68.4
STORE_TOP = 69.6
SPLIT = False               # True: 리프트 입구는 남쪽, 출구는 북쪽 (레일 양 끝에 리프트)
N_DOCKS, DOCK_W, DOCK_PITCH = 6, 3.6, 6.0
PD_BUF = 5                  # 리프트별 입고/출고 적치 컨베이어 칸 수
STAGE_LANES, STAGE_DEPTH = 4, 7   # 도크별 적치 차선: 4줄 x 7 = 28칸 (트럭 1대분)


def geometry() -> dict:
    sx0, sx1 = IN_X + CORR, OUT_X - CORR                    # 18.0 ~ 102.0
    block = DEPTH * SLOT
    module = 2 * block + RAIL
    margin = (sx1 - sx0 - N_RAILS * module) / 2
    rails = []
    x = sx0 + margin
    for _ in range(N_RAILS):
        rails.append({"west_block": (round(x, 2), round(x + block, 2)),
                      "rail": (round(x + block, 2), round(x + block + RAIL, 2)),
                      "east_block": (round(x + block + RAIL, 2), round(x + module, 2))})
        x += module
    lane_top = STORE_TOP - AISLE_H - PD_H if SPLIT else LANE_Y1
    n_lanes = int((lane_top - LANE_Y0) // LANE)
    positions = N_RAILS * 2 * n_lanes * DEPTH * LEVELS
    mid = (LANE_Y0 + STORE_TOP) / 2
    dock_y = [round(mid + (i - (N_DOCKS - 1) / 2) * DOCK_PITCH, 2) for i in range(N_DOCKS)]
    return {"rails": rails, "lanes_per_side": n_lanes, "lane_depth": DEPTH, "levels": LEVELS, "rack_h": RACK_H, "building_h": BUILDING_H,
            "lanes_total": N_RAILS * 2 * n_lanes * LEVELS, "pallet_positions": positions,
            "store_x": (sx0, sx1), "lane_y": (LANE_Y0, round(LANE_Y0 + n_lanes * LANE, 2)), "dock_y": dock_y,
            "shuttle_sets": N_RAILS * LEVELS, "lifts": N_RAILS * (2 if SPLIT else 1), "split": SPLIT,
            "lift_buffer_each_side": PD_BUF, "dock_staging_per_dock": STAGE_LANES * STAGE_DEPTH}


_ko = next((f for f in ["Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR", "WenQuanYi Zen Hei"]
            if f in {x.name for x in font_manager.fontManager.ttflist}), "DejaVu Sans")
plt.rcParams.update({"font.family": _ko, "axes.unicode_minus": False})
INK, INK2, LINE, SURF = "#0b0b0b", "#52514e", "#c9c8c2", "#fcfcfb"
RACK_C, RACK_F = "#2a78d6", "#dce8f8"
IN_C, OUT_C, CHG_C, OFF_C = "#d7f0e6", "#fbe1d6", "#fcefc9", "#ecebe7"
FLOW = "#1baf7a"
RAIL_C = "#6250d6"


def box(ax, x0, y0, w, h, fc, ec=LINE, lw=.6, **kw):
    ax.add_patch(Rectangle((x0, y0), w, h, fc=fc, ec=ec, lw=lw, **kw))


def dim(ax, a, b, at, text, vertical=False, fs=8, off=0.0):
    kw = dict(arrowstyle="<|-|>", mutation_scale=7, lw=.8, color=INK2, shrinkA=0, shrinkB=0)
    if vertical:
        ax.add_patch(FancyArrowPatch((at, a), (at, b), **kw))
        ax.text(at + off, (a + b) / 2, text, rotation=90, ha="center", va="center", fontsize=fs, color=INK2,
                bbox=dict(fc=SURF, ec="none", pad=.5))
    else:
        ax.add_patch(FancyArrowPatch((a, at), (b, at), **kw))
        ax.text((a + b) / 2, at + off, text, ha="center", va="center", fontsize=fs, color=INK2,
                bbox=dict(fc=SURF, ec="none", pad=.5))


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

    # 남쪽: AMR 충전 대기열 + 이송 통로
    box(ax, IN_X, 0, OUT_X - IN_X, CHG_H, "#f6f5f2")
    for i in range(6):
        x = 54 + i * 2.4
        box(ax, x + .3, .2, 1.8, 1.4, "#eda100", ec="#c98500", lw=.8)
        ax.text(x + 1.2, .9, f"C{i + 1}", ha="center", va="center", fontsize=6.5)
    ax.text(51.5, .9, "AMR 충전·대기 6기 →", ha="right", va="center", fontsize=8, color=INK2)
    ax.text(30, CHG_H + AISLE_H / 2, "AMR 이송 통로 4.8 m (양방향)", ha="center", va="center", fontsize=9,
            color=INK2, bbox=dict(fc="#ffffff", ec="none", pad=.4))

    # 랙 레인 + 레일 + 리프트
    y0, y1 = g["lane_y"]
    n = g["lanes_per_side"]
    for r, rail in enumerate(g["rails"]):
        for side in ("west_block", "east_block"):
            a, b = rail[side]
            for i in range(n):
                ly = y0 + i * LANE
                box(ax, a, ly + .1, b - a, LANE - .2, RACK_F, ec=RACK_C, lw=.35)
                for k in range(1, DEPTH):
                    ax.plot([a + k * SLOT] * 2, [ly + .1, ly + LANE - .1], color=RACK_C, lw=.2)
        ra, rb = rail["rail"]
        box(ax, ra, y0, rb - ra, y1 - y0, "#e9e6fb", ec=RAIL_C, lw=.8)
        ax.text((ra + rb) / 2, (y0 + y1) / 2, f"모셔틀 레일 {r + 1}", rotation=90, ha="center", va="center",
                fontsize=8, color=RAIL_C, bbox=dict(fc="#e9e6fb", ec="none", pad=.3))
        # 모셔틀/세틀라이트 예시
        sy = y0 + (8 + 13 * r) * LANE
        box(ax, ra + .15, sy + .15, rb - ra - .3, LANE - .3, RAIL_C, ec=INK, lw=.6)
        sat_x = rail["west_block"][1] - 4 * SLOT if r != 1 else rail["east_block"][0] + 3 * SLOT
        box(ax, sat_x + .2, sy + .3, SLOT - .4, LANE - .6, "#9085e9", ec=INK, lw=.6)
        # 리프트 + P&D: (위치 y, 라벨, 서쪽 버퍼 종류, 동쪽 버퍼 종류, 라벨 위/아래)
        cx = (ra + rb) / 2
        if SPLIT:
            stations = [(CHG_H + AISLE_H, f"L{r + 1}입", "in", "in", -1), (y1, f"L{r + 1}출", "out", "out", +1)]
        else:
            stations = [(CHG_H + AISLE_H, f"L{r + 1}", "in", "out", -1)]
        style = {"in": ("#9fd9c2", FLOW, "입고"), "out": ("#f6b79c", "#eb6834", "출고")}
        for py, lab, wk, ek, side in stations:
            box(ax, cx - 1.2, py, 2.4, PD_H, RAIL_C, ec=INK, lw=.8)
            ax.text(cx, py + PD_H / 2, lab, ha="center", va="center", fontsize=6 if SPLIT else 7, color="white")
            yb = py + .3
            for k in range(PD_BUF):
                fc, ec, _ = style[wk]
                box(ax, cx - 1.2 - (k + 1) * SLOT + .1, yb, SLOT - .2, PD_H - .6, fc, ec=ec, lw=.6)
                fc, ec, _ = style[ek]
                box(ax, cx + 1.2 + k * SLOT + .1, yb, SLOT - .2, PD_H - .6, fc, ec=ec, lw=.6)
            ty = yb - .9 if side < 0 else py + PD_H + .6
            ax.text(cx - 1.2 - PD_BUF * SLOT / 2, ty, f"{style[wk][2]} 버퍼 {PD_BUF}", ha="center", va="center",
                    fontsize=6.5, color=INK2)
            ax.text(cx + 1.2 + PD_BUF * SLOT / 2, ty, f"{style[ek][2]} 버퍼 {PD_BUF}", ha="center", va="center",
                    fontsize=6.5, color=INK2)
    if SPLIT:
        box(ax, IN_X + CORR, y1 + PD_H, OUT_X - IN_X - 2 * CORR, AISLE_H, "#ffffff", ec="none")
        ax.text(30, y1 + PD_H + AISLE_H / 2 + .4, "북쪽 AMR 이송 통로 4.8 m (출고)", ha="center", va="center",
                fontsize=9, color=INK2, bbox=dict(fc="#ffffff", ec="none", pad=.4))

    # 도크·계량·출고 적치 (v2와 동일)
    for i, yc in enumerate(g["dock_y"]):
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
    box(ax, 1.8, 3.0, 9.6, 6.0, "#ffffff", ec=INK2, lw=.8, hatch="//")
    ax.text(6.6, 6.0, "검수 보류 구역\n(무게 불일치)", ha="center", va="center", fontsize=8,
            bbox=dict(fc="#ffffff", ec="none", pad=.5))
    box(ax, W - 11.4, 3.0, 9.6, 6.0, "#ffffff", ec=INK2, lw=.8, hatch="..")
    ax.text(W - 6.6, 6.0, "포장·라벨링\n출고 검수", ha="center", va="center", fontsize=8,
            bbox=dict(fc="#ffffff", ec="none", pad=.5))
    ax.text(7.2, STORE_TOP - 4, "입고·검수\n계량대 6", ha="center", va="center", fontsize=9.5, weight="bold")
    ax.text(W - 7.2, STORE_TOP - 4, "출고\n스테이징", ha="center", va="center", fontsize=9.5, weight="bold")
    for xa, nm in ((IN_X, "서쪽 연결 통로 3.6m"), (OUT_X - CORR, "동쪽 연결 통로 3.6m")):
        ax.text(xa + CORR / 2, 40, nm, rotation=90, ha="center", va="center", fontsize=8, color=INK2,
                bbox=dict(fc="#ffffff", ec="none", pad=.3))

    # AMR 동선
    def flow(pts, color=FLOW):
        for p, q in zip(pts, pts[1:]):
            last = q == pts[-1]
            ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>" if last else "-", mutation_scale=15, lw=2.2,
                                         color=color, zorder=6))
    ya = CHG_H + AISLE_H * .3
    yb = CHG_H + AISLE_H * .7
    flow([(13.0, g["dock_y"][0]), (16.2, g["dock_y"][0]), (16.2, yb), (g["rails"][1]["rail"][0] - 3.2, yb),
          (g["rails"][1]["rail"][0] - 3.2, CHG_H + AISLE_H + .2)])
    if SPLIT:
        yn = y1 + PD_H + AISLE_H * .5
        flow([(g["rails"][1]["rail"][1] + 3.2, y1 + PD_H - .2), (g["rails"][1]["rail"][1] + 3.2, yn),
              (103.8, yn), (103.8, g["dock_y"][-1]), (107.2, g["dock_y"][-1])], color="#eb6834")
    else:
      flow([(g["rails"][1]["rail"][1] + 3.2, CHG_H + AISLE_H + .2), (g["rails"][1]["rail"][1] + 3.2, ya),
          (103.8, ya), (103.8, g["dock_y"][0]), (107.2, g["dock_y"][0])], color="#eb6834")

    # 치수
    sx0 = g["rails"][0]["west_block"][0]
    rr = g["rails"][0]
    dim(ax, rr["west_block"][0], rr["west_block"][1], STORE_TOP + .9, f"레인 {DEPTH * SLOT:.0f}m ({DEPTH}팔레트)", fs=7)
    dim(ax, rr["rail"][0], rr["rail"][1], STORE_TOP + 2.6, f"레일 {RAIL}", fs=7)
    dim(ax, 0, IN_X, -4.5, "14.4")
    dim(ax, IN_X, IN_X + CORR, -4.5, "3.6")
    dim(ax, IN_X + CORR, OUT_X - CORR, -4.5, f"보관 {OUT_X - IN_X - 2 * CORR:.1f} (모셔틀 레일 3 x 양쪽 레인)")
    dim(ax, OUT_X - CORR, OUT_X, -4.5, "3.6")
    dim(ax, OUT_X, W, -4.5, "14.4")
    dim(ax, 0, W, -8.0, "120.0 m", fs=10)
    dim(ax, 0, CHG_H + AISLE_H + PD_H, -20, "9.0", vertical=True, fs=7)
    dim(ax, y0, y1, -20, f"레인 {n}줄 x {LANE} m", vertical=True, fs=7)
    if SPLIT:
        dim(ax, y1, STORE_TOP, -20, "7.2", vertical=True, fs=7)
    dim(ax, STORE_TOP, H, -20, "10.4", vertical=True, fs=7)
    dim(ax, 0, H, -24, "80.0 m", vertical=True, fs=10)

    ax.set_xlim(-28, W + 52)
    ax.set_ylim(-10, H + 2)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.text(.5, .93, "물류창고 배치안 v3.1" + (" B안: 리프트 입구 남·출구 북" if SPLIT else " A안: 리프트 입·출구 남쪽") + " · 셔틀 앤 세틀라이트 · 120 m x 80 m", ha="center", fontsize=15,
             weight="bold")
    spec1 = [f"보관 용량 {g['pallet_positions']:,} 팔레트",
             f"레인 {g['lanes_total']:,}개 (깊이 {DEPTH}, SKU 1종 전용)",
             f"모셔틀+세틀라이트 {g['shuttle_sets']}세트 (레일 {N_RAILS} x {LEVELS}층)",
             (f"리프트 {2 * N_RAILS}기 (입고 전용 {N_RAILS} 남쪽 · 출고 전용 {N_RAILS} 북쪽), 버퍼 {PD_BUF}칸씩" if SPLIT else f"리프트 {N_RAILS}기 + 리프트별 입·출고 버퍼 컨베이어 {PD_BUF}칸씩")]
    spec2 = [f"입·출고 도크 각 6 (트럭 6대 동시), 도크별 적치 {STAGE_LANES * STAGE_DEPTH}칸(트럭 1대분)", "AMR 4대: 도크·계량대 ↔ 리프트",
             f"랙 {RACK_H:.0f} m · {LEVELS}층(층고 {LEVEL_H} m) · 건물 높이 {BUILDING_H:.0f} m", f"팔레트 T11 1.1 m"]
    fig.text(.5, .07, "   ·   ".join(spec1) + "\n" + "   ·   ".join(spec2), ha="center", va="center", fontsize=9.5,
             color=INK2, linespacing=1.8)
    # 범례
    lx, ly = W + 2, 60
    for i, (fc, ec, t) in enumerate([(RAIL_C, INK, "모셔틀"), ("#9085e9", INK, "세틀라이트"),
                                     (RAIL_C, INK, "리프트 L"), (FLOW, FLOW, "입고 AMR 동선"),
                                     ("#eb6834", "#eb6834", "출고 AMR 동선")]):
        pass
    # 단면도 (남북 방향으로 자른 모습: 레인 끝에서 본 랙 + 리프트)
    ins = fig.add_axes([.785, .60, .19, .30])
    ins.set_facecolor(SURF)
    ins.add_patch(Rectangle((0, 0), 30, BUILDING_H, fc="#ffffff", ec=INK, lw=1.2))
    for lv in range(LEVELS):
        for side_x in (4, 15.8):
            ins.add_patch(Rectangle((side_x, lv * LEVEL_H + .15), 10.2, LEVEL_H - .3, fc=RACK_F, ec=RACK_C, lw=.4))
        ins.add_patch(Rectangle((14.2, lv * LEVEL_H + .5), 1.6, .5, fc=RAIL_C, ec="none"))
    ins.add_patch(Rectangle((14.2, 0), 1.6, RACK_H, fc="none", ec=RAIL_C, lw=.8, ls="--"))
    ins.plot([0, 30], [RACK_H, RACK_H], color=INK2, lw=.6, ls=":")
    ins.annotate("", (2, 0), (2, RACK_H), arrowprops=dict(arrowstyle="<|-|>", color=INK2, lw=.8))
    ins.text(1.2, RACK_H / 2, f"랙 {RACK_H:.0f} m\n{LEVELS}층", ha="right", va="center", fontsize=7, color=INK2)
    ins.annotate("", (28.5, 0), (28.5, BUILDING_H), arrowprops=dict(arrowstyle="<|-|>", color=INK2, lw=.8))
    ins.text(30.3, BUILDING_H / 2, f"건물 {BUILDING_H:.0f} m", rotation=90, ha="left", va="center", fontsize=7,
             color=INK2)
    ins.text(15, (RACK_H + BUILDING_H) / 2, "상부 여유 10 m\n(스프링클러·조명·환기)", ha="center", va="center",
             fontsize=7, color=INK2)
    ins.text(15, -1.6, "단면: 레인 | 모셔틀 레일 | 레인", ha="center", va="top", fontsize=7.5, color=INK)
    ins.set_xlim(-5, 34)
    ins.set_ylim(-4, BUILDING_H + 1)
    ins.set_aspect("equal")
    ins.axis("off")
    fig.savefig(path, dpi=160, bbox_inches="tight", facecolor=SURF)
    plt.close(fig)
    return g


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
    out.mkdir(parents=True, exist_ok=True)
    SPLIT = len(sys.argv) > 2 and sys.argv[2] == "split"
    name = "shuttle_plan_split" if SPLIT else "shuttle_plan"
    g = draw(out / f"{name}.png")
    (out / f"{name}.json").write_text(json.dumps(g, ensure_ascii=False, indent=1))
    print({k: g[k] for k in ("lanes_per_side", "lanes_total", "pallet_positions", "shuttle_sets", "lane_y")})
