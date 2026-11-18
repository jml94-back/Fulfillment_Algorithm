"""RL 정책 vs 고전 방식 비교.  python -m rl.evaluate --ckpt rl/out/run_mappo/best.pt --seeds 20 --out rl/out

학습에 쓰지 않은 시드(1000번대)로 단계 2(AMR 4대), 단계 4(4대 + 작업자 4명 + 임시 팔레트)을 150초씩 돌린다.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import time
from pathlib import Path

import numpy as np
import torch

from .amr_env import AMRFleetEnv
from .baselines import DWA, PurePursuit, SafetyShield


class RLPolicy:
    def __init__(self, ckpt: str, name: str = "RL (MAPPO)") -> None:
        from .mappo import Actor
        self.actor = Actor()
        self.actor.load_state_dict(torch.load(ckpt, map_location="cpu")["actor"])
        self.actor.eval()
        self.name = name

    def act(self, env, scans, obs):
        with torch.no_grad():
            return self.actor.mu(torch.as_tensor(obs)).clamp(-1, 1).numpy()


def make(kind: str, ckpt: str | None, label: str | None = None):
    if kind == "pp":
        return PurePursuit()
    if kind == "dwa":
        return DWA()
    if kind == "dwa_rules":
        return DWA(rules=True)
    if kind == "rl_shield":
        return SafetyShield(RLPolicy(ckpt, label or "RL (MAPPO)"))
    return RLPolicy(ckpt, label or "RL (MAPPO)")


def run_episode(ctrl, stage: int, seed: int, record: bool = False):
    env = AMRFleetEnv(stage, seed=seed)
    obs = env.reset()
    scans = env._scan()
    frames, t_ctrl = [], 0.0
    if record:
        from .render import record_frame
    while True:
        t0 = time.perf_counter()
        a = ctrl.act(env, scans, obs) if isinstance(ctrl, (RLPolicy, SafetyShield)) else ctrl.act(env, scans)
        t_ctrl += time.perf_counter() - t0
        obs, _, _, info = env.step(a)
        scans = env._scan()
        if record:
            frames.append(record_frame(env, scans))
        if info["truncated"]:
            break
    st = dict(env.stats)
    st["ctrl_ms_per_robot"] = 1000 * t_ctrl / max(st["steps"], 1)
    return st, frames


def _job(args):
    kind, ckpt, label, stage, seed = args
    torch.set_num_threads(1)
    st, _ = run_episode(make(kind, ckpt, label), stage, seed)
    return kind, label, stage, st


def summarize(sts: list[dict]) -> dict:
    tot = {k: sum(s[k] for s in sts) for k in sts[0]}
    hours = tot["steps"] * 0.1 / 3600
    arr = max(tot["arrivals"], 1)
    return {
        "episodes": len(sts),
        "arrivals": tot["arrivals"],
        "arrivals_per_amr_hour": round(tot["arrivals"] / hours, 1),
        "collisions": tot["collisions"],
        "collisions_per_100_arrivals": round(100 * tot["collisions"] / arr, 2),
        "coll_robot": tot["coll_robot"], "coll_worker": tot["coll_worker"], "coll_static": tot["coll_static"],
        "timeouts_per_100_arrivals": round(100 * tot["timeouts"] / arr, 2),
        "near_miss_frac": round(tot["near"] / tot["steps"], 4),
        "worker_speeding_frac": round(tot["worker_fast"] / tot["steps"], 4),
        "mean_speed_mps": round(tot["dist"] / (tot["steps"] * 0.1), 3),
        "ctrl_ms_per_robot": round(float(np.mean([s["ctrl_ms_per_robot"] for s in sts])), 3),
    }


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--ckpt", action="append", default=[], help="label=경로 (여러 번 가능)")
    p.add_argument("--shield", action="append", default=[], help="label=경로: 안전 필터를 얹은 RL")
    p.add_argument("--seeds", type=int, default=20)
    p.add_argument("--stages", default="2,4")
    p.add_argument("--procs", type=int, default=4)
    p.add_argument("--out", default="rl/out")
    p.add_argument("--no-baselines", action="store_true")
    a = p.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ctrls = [] if a.no_baselines else [("pp", None, PurePursuit.name), ("dwa", None, "A* + DWA"),
                                       ("dwa_rules", None, "A* + DWA + 우측통행·교착회복")]
    for c in a.ckpt:
        label, path = c.split("=", 1) if "=" in c else ("RL (MAPPO)", c)
        ctrls.append(("rl", path, label))
    for c in a.shield:
        label, path = c.split("=", 1) if "=" in c else ("RL (MAPPO) + 안전 필터", c)
        ctrls.append(("rl_shield", path, label))
    stages = [int(s) for s in a.stages.split(",")]
    jobs = [(k, ck, lb, s, 1000 + sd) for (k, ck, lb) in ctrls for s in stages for sd in range(a.seeds)]
    with mp.get_context("fork").Pool(a.procs) as pool:
        res = pool.map(_job, jobs)
    table: dict = {}
    for (k, ck, lb) in ctrls:
        for s in stages:
            sts = [st for (kk, ll, ss, st) in res if ll == lb and ss == s]
            table.setdefault(lb, {})[f"stage{s}"] = summarize(sts)
    (out / "eval_results.json").write_text(json.dumps(table, ensure_ascii=False, indent=1))
    for lb, v in table.items():
        for s, m in v.items():
            print(f"{lb:32s} {s}: 처리 {m['arrivals_per_amr_hour']:6.1f}/AMR·h  충돌 {m['collisions_per_100_arrivals']:6.2f}/100건 "
                  f"(AMR {m['coll_robot']}, 사람 {m['coll_worker']}, 고정 {m['coll_static']})  "
                  f"시간초과 {m['timeouts_per_100_arrivals']:6.2f}/100건  근접 {m['near_miss_frac']:.3f}  "
                  f"사람옆과속 {m['worker_speeding_frac']:.4f}  {m['ctrl_ms_per_robot']:.2f}ms")
    return table


if __name__ == "__main__":
    main()
