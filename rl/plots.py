"""학습 곡선·평가 비교 그림.  python -m rl.plots rl/out"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from . import render  # noqa: F401  (한글 글꼴 설정)

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"]
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def _style(ax):
    ax.set_facecolor(SURF)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#b5b4ae")
    ax.grid(axis="y", color=GRID, lw=.8)
    ax.tick_params(colors=INK2, labelsize=8)


def _read(path):
    with open(path) as f:
        rows = list(csv.DictReader(f))
    return {k: np.array([float(r[k]) for r in rows]) for k in rows[0]}


def _smooth(y, k=60):
    if len(y) < k:
        return y
    c = np.convolve(y, np.ones(k) / k, mode="valid")
    return np.r_[np.full(k - 1, np.nan), c]


def learning_curves(runs: dict[str, Path], out: Path):
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2), facecolor=SURF)
    for ci, (name, p) in enumerate(runs.items()):
        d = _read(p)
        x = d["agent_steps"] / 1e6
        succ = d["arrivals"] / np.maximum(d["arrivals"] + d["collisions"] + d["timeouts"], 1)
        # 다중 단계 지표는 단계 3 구간만 의미 있으므로 전체를 그리되 단계 경계를 표시
        axes[0].plot(x, _smooth(succ), color=SERIES[ci], lw=2, label=name)
        axes[1].plot(x, _smooth(d["arrivals_per_agent_hour"]), color=SERIES[ci], lw=2, label=name)
        axes[2].plot(x, _smooth(d["collisions_per_100_arrivals"]), color=SERIES[ci], lw=2, label=name)
        st = d["stage"]
        for k in np.nonzero(np.diff(st))[0]:
            for ax in axes:
                ax.axvline(x[k + 1], color=SERIES[ci], lw=.8, ls=":", alpha=.7)
            if ci == 0:
                axes[0].text(x[k + 1], 0.02, f" 단계 {int(st[k + 1])}", fontsize=7, color=INK2, va="bottom")
    for ax, t in zip(axes, ["성공률 (도착 / 도착+충돌+정체)", "AMR 1대·1시간당 도착 수", "도착 100건당 충돌"]):
        _style(ax)
        ax.set_title(t, fontsize=10, color=INK, loc="left")
        ax.set_xlabel("에이전트 스텝 (백만)", fontsize=8, color=INK2)
    axes[0].set_ylim(0, 1.02)
    axes[2].set_ylim(bottom=0)
    if len(runs) > 1:
        axes[0].legend(fontsize=8, frameon=False, loc="lower right")
    fig.suptitle("학습 곡선 (이동 평균 60회 갱신, 학습 중 탐색 잡음 포함, 점선 = 커리큘럼 단계 전환)", fontsize=11, color=INK, x=.01, ha="left")
    fig.tight_layout()
    fig.savefig(out, dpi=150, facecolor=SURF)
    plt.close(fig)


def eval_bars(res_path: Path, out: Path):
    res = json.loads(res_path.read_text())
    names = list(res)
    stages = ["stage2", "stage4"]
    metrics = [("arrivals_per_amr_hour", "처리량: AMR 1대·1시간당 도착 수 (높을수록 좋음)"),
               ("collisions_per_100_arrivals", "도착 100건당 충돌 (낮을수록 좋음)"),
               ("timeouts_per_100_arrivals", "도착 100건당 정체·재계획 (낮을수록 좋음)")]
    fig, axes = plt.subplots(3, 1, figsize=(13, 11), facecolor=SURF)
    w = 0.8 / len(names)
    for ax, (m, title) in zip(axes, metrics):
        for ci, n in enumerate(names):
            vals = [res[n].get(s, {}).get(m, np.nan) for s in stages]
            xs = np.arange(len(stages)) + (ci - (len(names) - 1) / 2) * w
            bars = ax.bar(xs, vals, w * 0.92, color=SERIES[ci], label=n, edgecolor=SURF, linewidth=1)
            for b, v in zip(bars, vals):
                if np.isfinite(v):
                    ax.text(b.get_x() + b.get_width() / 2, b.get_height(), f"{v:.0f}" if v >= 10 else f"{v:.1f}",
                            ha="center", va="bottom", fontsize=7, color=INK2)
        _style(ax)
        ax.set_xticks(range(len(stages)))
        ax.set_xticklabels(["단계 2: AMR 4대", "단계 4: + 작업자 4명·임시 팔레트"], fontsize=8)
        ax.set_title(title, fontsize=9.5, color=INK, loc="left")
    axes[0].legend(fontsize=8, frameon=False, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    fig.suptitle("RL vs 고전 방식 (학습에 안 쓴 시드, 150초 x 시드별)", fontsize=11, color=INK, x=.01, ha="left")
    fig.tight_layout()
    fig.savefig(out, dpi=150, facecolor=SURF)
    plt.close(fig)


if __name__ == "__main__":
    o = Path(sys.argv[1] if len(sys.argv) > 1 else "rl/out")
    runs = {n: o / d / "log.csv" for n, d in [("MAPPO (중앙 critic)", "run_mappo"), ("IPPO (지역 critic)", "run_ippo")]
            if (o / d / "log.csv").exists()}
    if runs:
        learning_curves(runs, o / "learning_curves.png")
    if (o / "eval_results.json").exists():
        eval_bars(o / "eval_results.json", o / "eval_compare.png")
