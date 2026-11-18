"""환경 그림·애니메이션 (matplotlib)."""
from __future__ import annotations

import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.patches import Circle, Rectangle

from .geometry import H, ROBOT_R, W, WORKER_R, static_rects

COLORS = ["#2a6fdb", "#e0582b", "#1f9e6a", "#8a4fd8"]
from matplotlib import font_manager

_ko = next((f for f in ["Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR", "WenQuanYi Zen Hei"]
            if f in {x.name for x in font_manager.fontManager.ttflist}), "DejaVu Sans")
plt.rcParams.update({"font.family": _ko, "axes.unicode_minus": False})


def _base(ax, xlim=(0, W)):
    ax.add_patch(Rectangle((0, 0), W, H, fc="#ffffff", ec="#333", lw=1.5))
    for x0, y0, x1, y1 in static_rects():
        ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fc="#d9d6cf", ec="#8a877f", lw=.6))
    ax.set_xlim(*xlim)
    ax.set_ylim(-0.5, H + 0.5)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])


def draw_state(ax, env, scans=None, show_beams=True):
    arts = []
    for x0, y0, x1, y1 in env.pallets:
        arts.append(ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fc="#c58b3c", ec="#7a5216", lw=.8)))
    for w in env.workers:
        arts.append(ax.add_patch(Circle((w[0], w[1]), WORKER_R, fc="#f2c200", ec="#333", lw=.8)))
    for i, r in enumerate(env.robots):
        if not r.active:
            continue
        col = COLORS[i % 4]
        if show_beams and scans is not None:
            for k in range(0, len(scans[i]), 2):
                a = r.th + k * 2 * math.pi / len(scans[i])
                d = scans[i][k]
                arts += ax.plot([r.x, r.x + d * math.cos(a)], [r.y, r.y + d * math.sin(a)], color=col, lw=.3,
                                alpha=.35)
        xs = [r.x] + [p[0] for p in r.wps[r.wp_i:]]
        ys = [r.y] + [p[1] for p in r.wps[r.wp_i:]]
        arts += ax.plot(xs, ys, ls="--", lw=.8, color=col, marker=".", ms=3)
        arts.append(ax.add_patch(Circle((r.x, r.y), ROBOT_R, fc=col if r.load > 0 else "#ffffff", ec=col, lw=1.6)))
        arts += ax.plot([r.x, r.x + ROBOT_R * math.cos(r.th)], [r.y, r.y + ROBOT_R * math.sin(r.th)], color="#111",
                        lw=1.2)
        arts.append(ax.text(r.x, r.y + 1.0, f"AMR{i + 1:02d}", ha="center", fontsize=6, color=col))
    return arts


def snapshot(env, path, title=""):
    fig, ax = plt.subplots(figsize=(16, 3.6))
    _base(ax)
    draw_state(ax, env, env._scan())
    for s in env.stations:
        ax.plot(s.x, s.y, marker="x", color="#555", ms=4)
        ax.text(s.x, s.y - 0.9, s.name, fontsize=5.5, ha="center", color="#555")
    ax.set_title(title, fontsize=10)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def animate(frames, path, title="", fps=10, xlim=(0, W)):
    """frames: [(env 상태 사본 dict)] — record_frame() 결과 목록."""
    fig, ax = plt.subplots(figsize=(14 * (xlim[1] - xlim[0]) / W + 2, 3.4))
    _base(ax, xlim)
    ttl = ax.set_title(title, fontsize=9)
    arts: list = []

    class _E:  # draw_state 가 기대하는 최소 속성
        pass

    def upd(k):
        nonlocal arts
        for a in arts:
            a.remove()
        f = frames[k]
        e = _E()
        e.robots, e.workers, e.pallets = f["robots"], f["workers"], f["pallets"]
        arts = draw_state(ax, e, f["scans"])
        ttl.set_text(f"{title}   t = {f['t'] * 0.1:5.1f}s   도착 {f['arrivals']}   충돌 {f['collisions']}")
        return arts

    anim = FuncAnimation(fig, upd, frames=len(frames), blit=False)
    anim.save(path, writer=PillowWriter(fps=fps), dpi=60)
    plt.close(fig)


def record_frame(env, scans):
    import copy
    return {"robots": copy.deepcopy(env.robots), "workers": env.workers.copy(), "pallets": list(env.pallets),
            "scans": scans.copy(), "t": env.t, "arrivals": env.stats["arrivals"],
            "collisions": env.stats["collisions"]}
