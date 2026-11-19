"""데모 기록(JSONL)으로 장비별 작업 타임라인 그림을 만든다.

    python -m sim.timeline out/demo_log.jsonl out/demo_timeline.png
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import logging

import matplotlib.pyplot as plt

logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

COLORS = {"inbound": "#2a78d6", "outbound": "#eb6834", "charge": "#1baf7a", "held": "#888780"}
LABELS = {"inbound": "입고 작업", "outbound": "출고 작업", "charge": "충전 이동", "held": "보류(검수대 운반)"}
INK, INK2, GRID = "#1f1e1c", "#6b6a64", "#e4e3df"


def intervals(rows: list[dict]):
    job_kind, held_jobs, mission_job, cmd_job = {}, set(), {}, {}
    for r in rows:
        d = r["data"]
        if r["type"] == "wms.job":
            job_kind[d["job_id"]] = d["kind"]
            if d["status"] == "held":
                held_jobs.add(d["job_id"])
        elif r["type"] == "amr.cmd.mission":
            mission_job[d["mission_id"]] = (d["job_id"], [s["stop"] for s in d["steps"]])
        elif r["type"] == "rack.cmd":
            cmd_job[d["cmd_id"]] = d["job_id"]
    open_, bars = {}, []
    for r in rows:
        d, dev = r["data"], r["topic"].split("/")[3]
        if r["type"] == "amr.evt":
            mid = d["mission_id"]
            if d["event"] == "mission_accepted":
                open_[mid] = r["t"]
            elif d["event"] in ("mission_done", "mission_failed") and mid in open_:
                job, stops = mission_job.get(mid, (None, []))
                kind = "charge" if job is None else ("held" if "QC-IN" in stops[-1:] else job_kind.get(job, "inbound"))
                bars.append((dev, open_.pop(mid), r["t"], kind))
        elif r["type"] == "rack.evt":
            key = (dev, d["cmd_id"])
            if d["event"] == "started":
                open_[key] = r["t"]
            elif d["event"] in ("done", "failed") and key in open_:
                bars.append((dev, open_.pop(key), r["t"], job_kind.get(cmd_job.get(d["cmd_id"]), "inbound")))
    return bars


def draw(log: Path, png: Path) -> None:
    rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    bars = intervals(rows)
    devs = sorted({b[0] for b in bars}, key=lambda s: (0 if s.startswith("AMR") else 1, s))
    y = {d: i for i, d in enumerate(reversed(devs))}
    plt.rcParams["font.family"] = ["NanumGothic", "DejaVu Sans"]
    fig, ax = plt.subplots(figsize=(12, 0.42 * len(devs) + 1.6), dpi=160)
    for dev, t0, t1, kind in bars:
        ax.barh(y[dev], max(t1 - t0, 0.5), left=t0, height=0.62, color=COLORS[kind], edgecolor="white", linewidth=1)
    ax.set_yticks(list(y.values()), list(y.keys()), fontsize=8.5, color=INK)
    ax.set_xlabel("시뮬레이션 시각 (초)", color=INK2, fontsize=9)
    ax.grid(axis="x", color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK2, length=0)
    used = [k for k in COLORS if any(b[3] == k for b in bars)]
    handles = [plt.Rectangle((0, 0), 1, 1, color=COLORS[k]) for k in used]
    ax.legend(handles, [LABELS[k] for k in used], ncol=len(used), loc="lower left", bbox_to_anchor=(0, 1.0),
              frameon=False, fontsize=9)
    ax.set_title("MQTT 데모: 장비별 작업 구간 (AMR 미션, 랙 R1~R3 보관·출고 작업)", loc="left", fontsize=11.5, color=INK,
                 pad=26, weight="bold")
    fig.tight_layout()
    fig.savefig(png, facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    draw(Path(sys.argv[1]), Path(sys.argv[2]))
