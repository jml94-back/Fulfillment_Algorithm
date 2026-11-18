"""matplotlib 시각화: 히트맵, 경로 비교, 전략 비교 막대, AMR 애니메이션."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import animation
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import Rectangle

from .layout import Cell, WarehouseLayout
from .pathfinding import astar

# 검증된 기본 팔레트(라이트 모드) 슬롯 1~3
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
TEXT, TEXT2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
RACK, FLOOR = "#d9d8d3", "#fcfcfb"
SEQ = LinearSegmentedColormap.from_list("seq", ["#eef4fc", "#2a78d6", "#0d3a73"])

from matplotlib import font_manager

_KO_FONTS = ["Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR", "WenQuanYi Zen Hei"]
_available = {f.name for f in font_manager.fontManager.ttflist}
_font = next((f for f in _KO_FONTS if f in _available), "DejaVu Sans")

plt.rcParams.update({
    "font.family": _font,
    "axes.unicode_minus": False,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "axes.edgecolor": GRID,
    "axes.labelcolor": TEXT2,
    "xtick.color": TEXT2,
    "ytick.color": TEXT2,
    "text.color": TEXT,
})


def _draw_floor(ax, L: WarehouseLayout) -> None:
    for x in range(L.width):
        for y in range(L.height):
            ax.add_patch(Rectangle((x - .5, y - .5), 1, 1, fc=RACK if L.is_rack((x, y)) else FLOOR,
                                   ec="white", lw=1))
    ax.add_patch(Rectangle((L.dock[0] - .5, L.dock[1] - .5), 1, 1, fc=TEXT, ec="white"))
    ax.text(*L.dock, "D", ha="center", va="center", color="white", fontsize=8, weight="bold")
    ax.set_xlim(-.5, L.width - .5)
    ax.set_ylim(-.5, L.height - .5)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)


def heatmaps(L: WarehouseLayout, visits_by_name: dict[str, dict[Cell, int]], path: str) -> None:
    """전략별 랙 셀 방문 빈도. 도크 근처가 진할수록 좋은 배치."""
    vmax = max(max(v.values(), default=1) for v in visits_by_name.values())
    fig, axes = plt.subplots(1, len(visits_by_name), figsize=(4.2 * len(visits_by_name), 4.6))
    for ax, (name, visits) in zip(axes, visits_by_name.items()):
        _draw_floor(ax, L)
        for c, n in visits.items():
            ax.add_patch(Rectangle((c[0] - .5, c[1] - .5), 1, 1, fc=SEQ(n / vmax), ec="white", lw=1))
        ax.set_title(name, fontsize=11, color=TEXT)
    sm = plt.cm.ScalarMappable(cmap=SEQ, norm=plt.Normalize(0, vmax))
    cb = fig.colorbar(sm, ax=axes, shrink=.7, pad=.02)
    cb.set_label("랙 셀 방문 횟수", color=TEXT2)
    cb.outline.set_visible(False)
    fig.suptitle("적재 전략별 방문 히트맵 (D = 입출고 도크)", fontsize=13)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def route_compare(L: WarehouseLayout, routes: dict[str, tuple[list[Cell], int]], targets: list[Cell], path: str) -> None:
    """같은 피킹 목록을 서로 다른 순서로 돌았을 때의 실제 경로."""
    fig, axes = plt.subplots(1, len(routes), figsize=(4.4 * len(routes), 4.8))
    for i, (ax, (name, (order, dist))) in enumerate(zip(axes, routes.items())):
        _draw_floor(ax, L)
        pts = [L.dock]
        for c in [*order, L.dock]:
            pts += astar(L, pts[-1], c)[1:]
        xs, ys = zip(*pts)
        ax.plot(xs, ys, color=SERIES[i], lw=2, solid_capstyle="round", zorder=3)
        for t in targets:
            ax.add_patch(Rectangle((t[0] - .5, t[1] - .5), 1, 1, fc=SERIES[i], alpha=.35, ec="white", zorder=2))
        for k, c in enumerate(order, 1):
            ax.scatter(*c, s=90, color=SERIES[i], edgecolor=SURFACE, linewidth=2, zorder=4)
            ax.text(c[0], c[1], str(k), ha="center", va="center", fontsize=6, color="white", zorder=5)
        ax.set_title(f"{name}  ·  {dist}칸", fontsize=11)
    fig.suptitle("피킹 경로 비교 (같은 주문, 다른 방문 순서)", fontsize=13)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def bar_compare(table: dict[str, dict[str, float]], path: str, ylabel: str) -> None:
    """table[slotting][routing] = 값. 묶음 막대."""
    slots = list(table)
    routes = list(next(iter(table.values())))
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    w = .8 / len(routes)
    for j, r in enumerate(routes):
        xs = [i + (j - (len(routes) - 1) / 2) * w for i in range(len(slots))]
        vals = [table[s][r] for s in slots]
        ax.bar(xs, vals, width=w - .03, color=SERIES[j], label=r, zorder=2)
        for x, v in zip(xs, vals):
            ax.text(x, v + .1, f"{v:.2f}", ha="center", va="bottom", fontsize=7.5, color=TEXT2)
    ax.set_xticks(range(len(slots)), slots)
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", color=GRID, lw=.8, zorder=0)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.legend(title="경로 전략", frameon=False, ncol=len(routes), loc="upper right")
    ax.set_title("적재 × 경로 전략별 작업 1건당 비용 (낮을수록 좋음)", fontsize=12)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def animate_trips(L: WarehouseLayout, trips: list[dict], path: str, fps: int = 12) -> None:
    """Unity 내보내기용 trip(dict) 목록을 그대로 재생하는 GIF."""
    frames: list[tuple[Cell, int, set[Cell]]] = []
    for ti, t in enumerate(trips):
        stops = {tuple(s["access_point"]) for s in t["stops"]}
        for wp in t["waypoints"]:
            frames.append((tuple(wp), ti, stops))
    fig, ax = plt.subplots(figsize=(5, 5))
    _draw_floor(ax, L)
    marks = []
    amr = ax.scatter([], [], s=140, color=SERIES[0], edgecolor=SURFACE, linewidth=2, zorder=5)
    title = ax.set_title("")

    def update(i):
        nonlocal marks
        pos, ti, stops = frames[i]
        for m in marks:
            m.remove()
        color = SERIES[0] if trips[ti]["kind"] == "outbound" else SERIES[2]
        marks = [ax.scatter(*c, s=60, marker="s", color=color, alpha=.45, zorder=3) for c in stops]
        amr.set_offsets([pos])
        amr.set_color(color)
        kind = "출고(피킹)" if trips[ti]["kind"] == "outbound" else "입고(적재)"
        title.set_text(f"trip {ti + 1}/{len(trips)} · {kind}")
        return [amr, title, *marks]

    anim = animation.FuncAnimation(fig, update, frames=len(frames), interval=1000 / fps)
    anim.save(path, writer=animation.PillowWriter(fps=fps))
    plt.close(fig)


FLEET = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]


CRITICAL = "#e34948"


def animate_fleet(L: WarehouseLayout, tracks: list[list[Cell]], path: str, t0: int = 0, t1: int = 240,
                  fps: int = 10, tick_seconds: float = 1.2, zones: list[tuple[set[Cell], int, str]] | None = None) -> None:
    """다중 AMR 위치를 시간축으로 재생. 꼬리는 최근 6 tick 이동 궤적.
    zones: [(칸 집합, 발생 tick, 라벨)] 비상 구역을 빨간 빗금으로 표시."""
    fig, ax = plt.subplots(figsize=(5.8, 5.6))
    _draw_floor(ax, L)
    zone_patches = []
    for cells, start, label in zones or []:
        ps = [ax.add_patch(Rectangle((c[0] - .5, c[1] - .5), 1, 1, fc=CRITICAL, alpha=.28, ec=CRITICAL,
                                     hatch="///", lw=.5, zorder=2, visible=False)) for c in cells]
        cx = sum(c[0] for c in cells) / len(cells)
        cy = sum(c[1] for c in cells) / len(cells)
        txt = ax.text(cx, cy, label, ha="center", fontsize=8, color=TEXT, zorder=6, visible=False,
                      bbox=dict(fc=SURFACE, ec=CRITICAL, boxstyle="round,pad=.2"))
        zone_patches.append((start, [*ps, txt]))
    for p in L.parking:
        ax.add_patch(Rectangle((p[0] - .5, p[1] - .5), 1, 1, fc=GRID, ec="white"))
    ax.add_patch(Rectangle((L.inbound_dock[0] - .5, -.5), 1, 1, fc=TEXT2, ec="white"))
    ax.text(*L.inbound_dock, "I", ha="center", va="center", color="white", fontsize=8, weight="bold")
    dots = [ax.scatter([], [], s=120, color=FLEET[a % 6], edgecolor=SURFACE, linewidth=2, zorder=5,
                       label=f"AMR {a + 1}") for a in range(len(tracks))]
    tails = [ax.plot([], [], color=FLEET[a % 6], lw=2, alpha=.5, zorder=4)[0] for a in range(len(tracks))]
    ax.legend(loc="upper center", bbox_to_anchor=(.5, -.01), ncol=len(tracks), frameon=False, fontsize=8)
    title = ax.set_title("")

    def update(t):
        for start, items in zone_patches:
            for it in items:
                it.set_visible(t >= start)
        for a, tr in enumerate(tracks):
            dots[a].set_offsets([tr[t]])
            seg = tr[max(0, t - 6): t + 1]
            tails[a].set_data([c[0] for c in seg], [c[1] for c in seg])
        title.set_text(f"AMR {len(tracks)}대 · t = {t * tick_seconds:5.0f}초")
        return [*dots, *tails, title]

    t1 = min(t1, len(tracks[0]))
    anim = animation.FuncAnimation(fig, update, frames=range(t0, t1), interval=1000 / fps)
    anim.save(path, writer=animation.PillowWriter(fps=fps))
    plt.close(fig)


def line_compare(xs: list, series: dict[str, list[float]], path: str, xlabel: str, ylabel: str, title: str) -> None:
    fig, ax = plt.subplots(figsize=(6.8, 4))
    for i, (name, ys) in enumerate(series.items()):
        ax.plot(xs, ys, color=FLEET[i], lw=2, marker="o", ms=7, mec=SURFACE, mew=2, label=name, zorder=3)
        ax.annotate(f"{ys[-1]:.0f}", (xs[-1], ys[-1]), xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=8, color=TEXT2)
    ax.set_xticks(xs)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", color=GRID, lw=.8, zorder=0)
    ax.spines[["top", "right", "left"]].set_visible(False)
    if len(series) > 1:
        ax.legend(frameon=False)
    ax.set_title(title, fontsize=12)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def layout_gallery(layouts: dict[str, WarehouseLayout], path: str, cols: int = 4) -> None:
    rows = (len(layouts) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(3.6 * cols, 3.9 * rows))
    axes = axes.ravel() if hasattr(axes, "ravel") else [axes]
    for ax, (name, L) in zip(axes, layouts.items()):
        _draw_floor(ax, L)
        ax.add_patch(Rectangle((L.inbound_dock[0] - .5, -.5), 1, 1, fc=TEXT2, ec="white"))
        for p in L.parking[:4]:
            ax.add_patch(Rectangle((p[0] - .5, p[1] - .5), 1, 1, fc=GRID, ec="white"))
        ax.set_title(name, fontsize=10)
    for ax in list(axes)[len(layouts):]:
        ax.axis("off")
    fig.suptitle("랙 배치 후보 (D 출고 도크 · I 입고 도크 · 회색 = AMR 대기 칸)", fontsize=13)
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
