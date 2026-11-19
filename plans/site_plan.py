"""120m x 80m 창고 배치안 (v1) — 치수 정의 + 도면 그리기.

    python -m plans.site_plan out      → out/site_plan.png, out/site_plan.json

흐름: 왼쪽 입고(서쪽 벽 도크 6) → 가운데 보관 → 오른쪽 출고(동쪽 벽 도크 6).
랙 통로는 동-서 방향이라 AMR이 입고 쪽에서 들어가 출고 쪽으로 그대로 빠져나갈 수 있다.
모든 치수는 1.2m 격자(팔레트 1개 폭) 배수다.
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

C = 1.2                     # 격자 1칸 (m)
W, H = 120.0, 80.0          # 건물 내부 치수
AISLE = 2 * C               # 랙 사이 통로 2.4m: AMR 1대 폭. 진입 방향은 알고리즘(시공간 예약)이 그때그때 정한다
RACK = 2 * C                # 양면 랙 깊이 2.4m (한쪽 1.2m x 2)
MAIN = 4 * C                # 중앙 메인 통로 4.8m
LEVELS = 4
LEVEL_H = 1.5               # 층 높이(m, 팔레트+여유) → 랙 높이 약 6m

# x 방향 구역 (서→동)
ZONES_X = [
    ("입고·검수", 12 * C),
    ("서쪽 이송 통로", 3 * C),
    ("보관 서쪽 블록", 33 * C),
    ("중앙 메인 통로", MAIN),
    ("보관 동쪽 블록", 33 * C),
    ("동쪽 이송 통로", 3 * C),
    ("출고 스테이징", 12 * C),
]
N_RACKS = 14                # 양면 랙 줄 수
STORE_H = AISLE + N_RACKS * (RACK + AISLE)   # 69.6m
N_DOCKS = 6
DOCK_W = 3.6
DOCK_PITCH = 6.0
N_CHARGERS = 6              # AMR 4대 + 예비 2


def geometry() -> dict:
    xs, x = {}, 0.0
    for name, w in ZONES_X:
        xs[name] = (round(x, 2), round(x + w, 2))
        x += w
    assert abs(x - W) < 1e-6, x
    rack_rows = []
    for k in range(N_RACKS):
        y0 = AISLE + k * (RACK + AISLE)
        rack_rows.append((round(y0, 2), round(y0 + RACK, 2)))
    mid = STORE_H / 2
    dock_y = [round(mid + (i - (N_DOCKS - 1) / 2) * DOCK_PITCH, 2) for i in range(N_DOCKS)]
    bays_per_row = int(round((xs["보관 서쪽 블록"][1] - xs["보관 서쪽 블록"][0]) / C)) * 2
    positions = N_RACKS * 2 * bays_per_row * LEVELS
    return {"zones_x": xs, "rack_rows": rack_rows, "dock_y": dock_y, "store_h": STORE_H,
            "pallet_positions": positions, "bays_per_row": bays_per_row}


# ---------------------------------------------------------------- 그리기
_ko = next((f for f in ["Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR", "WenQuanYi Zen Hei"]
            if f in {x.name for x in font_manager.fontManager.ttflist}), "DejaVu Sans")
plt.rcParams.update({"font.family": _ko, "axes.unicode_minus": False})

INK, INK2, MUTED, LINE = "#0b0b0b", "#52514e", "#8a8984", "#c9c8c2"
SURF = "#fcfcfb"
RACK_C = "#2a78d6"
IN_C, OUT_C, CHG_C, OFF_C, AISLE_C = "#d7f0e6", "#fbe1d6", "#fcefc9", "#ecebe7", "#ffffff"
FLOW = "#1baf7a"


def dim(ax, x0, x1, y, text, vertical=False, off=0.0, fs=8):
    kw = dict(arrowstyle="<|-|>", mutation_scale=7, lw=.8, color=INK2, shrinkA=0, shrinkB=0)
    if vertical:
        ax.add_patch(FancyArrowPatch((y, x0), (y, x1), **kw))
        ax.text(y + off, (x0 + x1) / 2, text, rotation=90, ha="center", va="center", fontsize=fs, color=INK2,
                bbox=dict(fc=SURF, ec="none", pad=.6))
    else:
        ax.add_patch(FancyArrowPatch((x0, y), (x1, y), **kw))
        ax.text((x0 + x1) / 2, y + off, text, ha="center", va="center", fontsize=fs, color=INK2,
                bbox=dict(fc=SURF, ec="none", pad=.6))


def box(ax, x0, y0, w, h, fc, ec=LINE, lw=.6, **kw):
    ax.add_patch(Rectangle((x0, y0), w, h, fc=fc, ec=ec, lw=lw, **kw))


def draw(path: Path) -> dict:
    g = geometry()
    zx = g["zones_x"]
    fig, ax = plt.subplots(figsize=(17, 10.2))
    fig.patch.set_facecolor(SURF)
    ax.set_facecolor(SURF)

    # 구역 바탕
    box(ax, 0, 0, W, H, AISLE_C, ec=INK, lw=2.2)
    rx0, rx1 = zx["입고·검수"]
    sx0, sx1 = zx["출고 스테이징"]
    box(ax, rx0, 0, rx1 - rx0, STORE_H, IN_C)
    box(ax, sx0, 0, sx1 - sx0, STORE_H, OUT_C)

    # 북쪽 지원 구역 (y 69.6~80)
    top = STORE_H
    box(ax, 0, top, W, H - top, OFF_C)
    m0, m1 = zx["중앙 메인 통로"]
    chg_w = N_CHARGERS * 2.4
    cx0 = (m0 + m1) / 2 - chg_w / 2
    box(ax, cx0 - 1.2, top, chg_w + 2.4, H - top, CHG_C)
    for i in range(N_CHARGERS):
        box(ax, cx0 + i * 2.4 + .3, top + .3, 1.8, 2.4, "#eda100", ec="#c98500", lw=.8)
        ax.text(cx0 + i * 2.4 + 1.2, top + 1.5, f"C{i + 1}", ha="center", va="center", fontsize=7, color=INK)
    ax.text((m0 + m1) / 2, top + 6.4, "AMR 충전·대기\n충전기 6기 (AMR 4 + 예비 2)", ha="center", va="center",
            fontsize=9, color=INK)
    ax.text((zx["입고·검수"][0] + cx0 - 1.2) / 2, top + 5.2, "장비 정비 · 팔레트/자재 보관", ha="center",
            fontsize=10, color=INK2)
    ax.text((cx0 + chg_w + 1.2 + W) / 2, top + 5.2, "사무실 · 관제실 · 휴게", ha="center", fontsize=10, color=INK2)

    # 랙
    for (y0, y1) in g["rack_rows"]:
        for blk in ["보관 서쪽 블록", "보관 동쪽 블록"]:
            a, b = zx[blk]
            box(ax, a, y0, b - a, y1 - y0, "#cfe0f6", ec=RACK_C, lw=.8)
            ax.plot([a, b], [(y0 + y1) / 2] * 2, color=RACK_C, lw=.4)
            # 1.2m 칸 눈금 (팔레트 1개 = 1.2m x 1.2m 정사각형)
            n = int(round((b - a) / C))
            for i in range(0, n + 1):
                ax.plot([a + i * C] * 2, [y0, y1], color=RACK_C, lw=.25)
    ax.text((zx["보관 서쪽 블록"][0] + zx["보관 동쪽 블록"][1]) / 2, -2.2,
            "", ha="center")

    # 입고 도크·차선·계량대
    for i, yc in enumerate(g["dock_y"]):
        # 트럭 (건물 밖)
        box(ax, -15.5, yc - 1.25, 14.0, 2.5, "#ffffff", ec=INK2, lw=.9)
        box(ax, -4.0, yc - 1.25, 2.5, 2.5, "#e4e3df", ec=INK2, lw=.9)
        box(ax, W + 1.5, yc - 1.25, 14.0, 2.5, "#ffffff", ec=INK2, lw=.9)
        box(ax, W + 1.5, yc - 1.25, 2.5, 2.5, "#e4e3df", ec=INK2, lw=.9)
        # 도크 문
        ax.plot([0, 0], [yc - DOCK_W / 2, yc + DOCK_W / 2], color=SURF, lw=4)
        ax.plot([W, W], [yc - DOCK_W / 2, yc + DOCK_W / 2], color=SURF, lw=4)
        box(ax, 0, yc - DOCK_W / 2, 1.2, DOCK_W, "#9fd9c2", ec=FLOW, lw=.8)
        box(ax, W - 1.2, yc - DOCK_W / 2, 1.2, DOCK_W, "#f6b79c", ec="#eb6834", lw=.8)
        ax.text(.6, yc, f"IN{i + 1}", rotation=90, ha="center", va="center", fontsize=6.5)
        ax.text(W - .6, yc, f"OUT{i + 1}", rotation=90, ha="center", va="center", fontsize=6.5)
        # 입고 하차 차선(팔레트 7개 x 2줄) + 계량대
        for dy in (-.6, .6):
            for k in range(7):
                box(ax, 1.8 + k * 1.2, yc + dy - .55, 1.1, 1.1, "#ffffff", ec="#7cc8aa", lw=.4)
        box(ax, 10.6, yc - 1.2, 2.4, 2.4, "#1baf7a", ec=INK, lw=.8)
        ax.text(11.8, yc, "계량", ha="center", va="center", fontsize=6.5, color="white")
        # 출고 적치 차선 + AMR 하차 지점
        for dy in (-.6, .6):
            for k in range(7):
                box(ax, W - 1.8 - (k + 1) * 1.2 + .1, yc + dy - .55, 1.1, 1.1, "#ffffff", ec="#f1a487", lw=.4)
        box(ax, W - 13.0, yc - 1.2, 2.4, 2.4, "#eb6834", ec=INK, lw=.8)
        ax.text(W - 11.8, yc, "하차", ha="center", va="center", fontsize=6.5, color="white")

    # 보류 구역 (무게 불일치)
    box(ax, 1.8, 2.4, 9.6, 6.0, "#ffffff", ec=INK2, lw=.8, hatch="//")
    ax.text(6.6, 5.4, "검수 보류 구역\n(무게 불일치)", ha="center", va="center", fontsize=8,
            bbox=dict(fc="#ffffff", ec="none", pad=.5))
    box(ax, W - 11.4, 2.4, 9.6, 6.0, "#ffffff", ec=INK2, lw=.8, hatch="..")
    ax.text(W - 6.6, 5.4, "포장·라벨링\n출고 검수", ha="center", va="center", fontsize=8,
            bbox=dict(fc="#ffffff", ec="none", pad=.5))
    ax.text(7.2, STORE_H - 4, "입고·검수\n계량대 6", ha="center", va="center", fontsize=9.5, weight="bold")
    ax.text(W - 7.2, STORE_H - 4, "출고\n스테이징", ha="center", va="center", fontsize=9.5, weight="bold")

    # 통로 라벨
    ax.text((m0 + m1) / 2, STORE_H / 2 - 6, "중앙 메인 통로 4.8m", rotation=90, ha="center", va="center",
            fontsize=9, color=INK2, bbox=dict(fc=SURF, ec="none", pad=.4))
    for nm in ["서쪽 이송 통로", "동쪽 이송 통로"]:
        a, b = zx[nm]
        ax.text((a + b) / 2, STORE_H / 2 - 6, f"{nm} 3.6m", rotation=90, ha="center", va="center", fontsize=8,
                color=INK2, bbox=dict(fc=SURF, ec="none", pad=.3))

    # 흐름 화살표
    def flow(p, q, rad=0.0):
        ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=16, lw=2.4, color=FLOW,
                                     connectionstyle=f"arc3,rad={rad}", zorder=6))
    ay = AISLE + 2 * (RACK + AISLE) + AISLE / 2 - (RACK + AISLE) + RACK  # 통로 하나 중앙
    lane = [AISLE / 2 + k * (RACK + AISLE) for k in range(N_RACKS + 1)]
    for yy in (lane[4], lane[10]):
        flow((14.0, yy), (56.5, yy))
        flow((63.5, yy), (106.0, yy))

    # 치수선
    x = 0.0
    for name, w in ZONES_X:
        dim(ax, x, x + w, -4.5, f"{w:.1f}", off=0)
        x += w
    dim(ax, 0, W, -8.0, "120.0 m", fs=10)
    dim(ax, 0, STORE_H, -22.5, f"{STORE_H:.1f}", vertical=True)
    dim(ax, STORE_H, H, -22.5, f"{H - STORE_H:.1f}", vertical=True)
    dim(ax, 0, H, -26.0, "80.0 m", vertical=True, fs=10)
    # 랙 모듈 상세 치수 (동쪽 블록 위쪽 두 줄)
    xd = (m0 + m1) / 2
    y0 = g["rack_rows"][-2][0]
    dim(ax, y0 - AISLE, y0, xd, "통로 2.4", vertical=True, off=-1.2, fs=7)
    dim(ax, y0, y0 + RACK, xd, "랙 2.4", vertical=True, off=-1.2, fs=7)
    yd = g["dock_y"]
    dim(ax, yd[0], yd[1], -17.5, "6.0", vertical=True, off=-.9, fs=7)

    ax.set_xlim(-28, W + 18)
    ax.set_ylim(-10, H + 2)
    ax.set_aspect("equal")
    ax.axis("off")

    # 명세 표
    spec = [
        ("건물", "120 m x 80 m (격자 1.2 m)"),
        ("보관 랙", f"양면 랙 {N_RACKS}줄 x 2블록, 한 줄 39.6 m (33칸)"),
        ("랙 규격", f"깊이 1.2 m x 2(양면), 칸 폭 1.2 m, {LEVELS}층 (층고 {LEVEL_H} m)"),
        ("보관 용량", f"{g['pallet_positions']:,} 팔레트 위치"),
        ("통로", "랙 사이 2.4 m 단일 차선(방향은 알고리즘이 동적 배정), 교행은 메인 4.8 m·이송 3.6 m"),
        ("입고", "도크 6 (트럭 6대 동시 접안), 도크별 계량대"),
        ("출고", "도크 6 (트럭 6대 동시 접안), 도크별 적치 차선"),
        ("AMR", "팔레트 AMR 4대, 1회 1팔레트, 충전기 6기"),
    ]
    tx, ty = W + 3, H - 1
    ax.text(tx - 1, H + 1.2, "배치 명세", fontsize=11, weight="bold", color=INK)
    fig.text(.5, .93, "물류창고 배치안 v2 · 120 m x 80 m · 입고(서) → 보관 → 출고(동)", ha="center", fontsize=15,
             weight="bold", color=INK)
    ax.texts[-1].remove()
    tbl = "\n".join(f"{k:<6}  {v}" for k, v in spec)
    fig.text(.5, .07, "   ·   ".join(f"{k}: {v}" for k, v in spec[:4]) + "\n" +
             "   ·   ".join(f"{k}: {v}" for k, v in spec[4:]), ha="center", va="center", fontsize=9.5,
             color=INK2, linespacing=1.8)
    fig.savefig(path, dpi=160, bbox_inches="tight", facecolor=SURF)
    plt.close(fig)
    return g


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
    out.mkdir(parents=True, exist_ok=True)
    g = draw(out / "site_plan.png")
    (out / "site_plan.json").write_text(json.dumps(g, ensure_ascii=False, indent=1))
    print(json.dumps({k: g[k] for k in ("store_h", "pallet_positions", "bays_per_row", "dock_y")}, ensure_ascii=False))
