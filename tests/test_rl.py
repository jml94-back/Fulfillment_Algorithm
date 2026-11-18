"""rl/ 프로토타입 기본 동작 확인 (학습 없이 빠르게)."""
import math

import numpy as np
import pytest

pytest.importorskip("torch")

from rl.amr_env import OBS_DIM, STATE_DIM, AMRFleetEnv
from rl.baselines import DWA, SafetyShield
from rl.geometry import ROBOT_R
from rl.planner import LANE_GAP, LANE_MID


def test_env_shapes_and_step():
    e = AMRFleetEnv(4, seed=0)
    o = e.reset()
    assert o.shape == (4, OBS_DIM)
    assert e.critic_obs(o).shape == (4, OBS_DIM + STATE_DIM)
    for _ in range(50):
        o, r, d, info = e.step(np.zeros((4, 2)))
    assert np.isfinite(o).all() and np.isfinite(r).all()


def test_lidar_sees_other_robot():
    e = AMRFleetEnv(2, seed=0)
    e.reset()
    a, b = e.robots[0], e.robots[1]
    for r in e.robots[2:]:
        r.active = False
    a.x, a.y, a.th = 50.0, 16.8, 0.0
    b.x, b.y, b.th = 55.0, 16.8, math.pi
    sc = e._scan()
    assert abs(sc[0][0] - (5.0 - ROBOT_R)) < 1e-6


def test_planner_keeps_right():
    e = AMRFleetEnv(4, seed=3)
    e.reset()
    st = {s.name: s for s in e.stations}
    east = e.planner.plan((30.0, 16.8), st["L3out"])     # 동쪽으로
    west = e.planner.plan((110.0, 16.8), st["IN1"])      # 서쪽으로
    mid_e = [y for x, y in east if 45 < x < 75]
    mid_w = [y for x, y in west if 45 < x < 75]
    assert mid_e and max(mid_e) <= LANE_MID - LANE_GAP + 0.15
    assert mid_w and min(mid_w) >= LANE_MID + LANE_GAP - 0.15


def test_shield_caps_speed_near_wall():
    e = AMRFleetEnv(1, seed=0)
    e.reset()
    r = e.robots[0]
    r.x, r.y, r.th = 50.0, 20.5, math.pi / 2                # 북쪽 벽(21.6)을 1.1 m 앞에 두고 바라봄 (리프트 없는 곳)

    class Full:
        name = "full"

        def act(self, env, scans):
            return np.ones((4, 2)) * [1, 0]

    sc = e._scan()
    a = SafetyShield(Full()).act(e, sc)
    assert a[0, 0] < 1.0


def test_dwa_runs():
    e = AMRFleetEnv(2, seed=1)
    e.reset()
    c = DWA(rules=True)
    for _ in range(20):
        e.step(c.act(e, e._scan()))
    assert e.stats["collisions"] == 0
