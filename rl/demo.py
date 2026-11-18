"""같은 시나리오(단계 4)를 RL 과 A*+DWA 로 돌려 애니메이션으로 저장.  python -m rl.demo <ckpt> rl/out [seed]"""
from __future__ import annotations

import sys
from pathlib import Path

from .baselines import DWA, SafetyShield
from .evaluate import RLPolicy, run_episode
from .render import animate


def main():
    ckpt, out = sys.argv[1], Path(sys.argv[2] if len(sys.argv) > 2 else "rl/out")
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 1003
    for ctrl, fname in [(SafetyShield(RLPolicy(ckpt)), "demo_rl.gif"), (DWA(rules=True), "demo_dwa.gif")]:
        st, frames = run_episode(ctrl, 4, seed, record=True)
        frames = frames[:900:4]                  # 앞 90초, 4배속
        animate(frames, out / fname, title=ctrl.name, fps=10)
        print(fname, st)


if __name__ == "__main__":
    main()
