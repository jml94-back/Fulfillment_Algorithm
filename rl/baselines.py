"""고전 방식 비교 대상.

- PurePursuit: 상위 계획기 경유점만 따라감. 회피 없음 (하한선).
- DWA: 같은 경유점(= A* 전역 경로) + Dynamic Window Approach 국소 회피. LiDAR 점만 보고, 장애물은 정지해 있다고 본다.
  흔한 산업용 안전 규칙도 넣었다: 1.5 m 안에 무언가 있으면 0.6 m/s 로 감속.
- DWA(rules=True): 현장에서 흔히 덧붙이는 규칙 두 가지를 더한 강화판.
  우측 통행(중간 경유점을 진행 방향 오른쪽으로 0.9 m 옮김) + 교착 회복(2초 넘게 막히면 1.5초 후진하며 넓은 쪽으로 회전).

둘 다 RL 정책과 똑같은 관측 정보(LiDAR, 경유점, 자기 속도)만 쓰고, 같은 행동 형식(-1~1 의 목표 v, w)을 낸다.
"""
from __future__ import annotations

import math

import numpy as np

from .amr_env import DT, LIDAR_MAX, N_BEAMS, V_BACK, V_MAX, W_MAX, AMRFleetEnv
from .geometry import ROBOT_R


def _limits(r):
    lf = r.load / 1500.0
    return V_MAX - 0.5 * lf, 1.0 - 0.4 * lf, 1.5 - 0.5 * lf


def _to_action(vt, wt, vmax):
    return np.array([vt / vmax if vt >= 0 else vt / V_BACK, wt / W_MAX])


class PurePursuit:
    name = "경유점 추종만 (회피 없음)"

    def act(self, env: AMRFleetEnv, scans: np.ndarray) -> np.ndarray:
        a = np.zeros((env.n_agents, 2))
        for i, r in enumerate(env.robots):
            if not r.active:
                continue
            vmax, _, brake = _limits(r)
            gx, gy = r.wps[r.wp_i]
            err = (math.atan2(gy - r.y, gx - r.x) - r.th + math.pi) % (2 * math.pi) - math.pi
            d = math.hypot(gx - r.x, gy - r.y)
            vt = vmax * max(0.0, math.cos(err)) ** 2
            if r.wp_i == len(r.wps) - 1:
                vt = min(vt, math.sqrt(max(0.0, 2 * brake * 0.8 * (d - 0.15))))
            a[i] = _to_action(vt, float(np.clip(2.0 * err, -W_MAX, W_MAX)), vmax)
        return a


class DWA:
    def __init__(self, rules: bool = False, horizon: float = 2.0, nv: int = 7, nw: int = 13, w_head: float = 1.0, w_clear: float = 0.8,
                 w_vel: float = 0.3, w_prog: float = 1.0, safety_dist: float = 1.5, safety_v: float = 0.6) -> None:
        self.h, self.nv, self.nw = horizon, nv, nw
        self.wh, self.wc, self.wv, self.wp = w_head, w_clear, w_vel, w_prog
        self.sd, self.sv = safety_dist, safety_v
        self.rules = rules
        self.name = "A* + DWA + 우측통행·교착회복" if rules else "A* + DWA"
        self.stuck: dict[int, int] = {}
        self.recover: dict[int, int] = {}
        self.ts = np.arange(1, int(horizon / 0.2) + 1) * 0.2
        ang = np.arange(N_BEAMS) * 2 * np.pi / N_BEAMS
        self.bc, self.bs = np.cos(ang), np.sin(ang)

    def act(self, env: AMRFleetEnv, scans: np.ndarray) -> np.ndarray:
        a = np.zeros((env.n_agents, 2))
        for i, r in enumerate(env.robots):
            if r.active:
                a[i] = self._rules(env, i, scans[i]) if self.rules else self._one(r, scans[i])
        return a

    def _rules(self, env, i, scan):
        r = env.robots[i]
        vmax = _limits(r)[0]
        if self.recover.get(i, 0) > 0:
            self.recover[i] -= 1
            left = scan[: N_BEAMS // 2].mean() > scan[N_BEAMS // 2:].mean()
            rear = np.r_[scan[N_BEAMS // 2 - 6: N_BEAMS // 2 + 7]].min()
            return _to_action(-V_BACK if rear > ROBOT_R + 0.25 else 0.0, -0.8 if left else 0.8, vmax)
        self.stuck[i] = self.stuck.get(i, 0) + 1 if (abs(r.v) < 0.05 and scan.min() < 1.0) else 0
        if self.stuck[i] > 20:
            self.stuck[i] = 0
            self.recover[i] = 15
        goal = None
        if r.wp_i < len(r.wps) - 1:
            gx, gy = r.wps[r.wp_i]
            px, py = r.wps[r.wp_i - 1] if r.wp_i > 0 else (r.x, r.y)
            dx, dy = gx - px, gy - py
            n = math.hypot(dx, dy) or 1.0
            sx, sy = gx + 0.9 * dy / n, gy - 0.9 * dx / n          # 진행 방향 오른쪽
            if env.grid.free(sx, sy):
                goal = (sx, sy)
        return self._one(r, scan, goal)

    def _one(self, r, scan, goal=None):
        vmax, acc, brake = _limits(r)
        hit = scan < LIDAR_MAX - 1e-6
        # 로봇 좌표계 장애물 점
        px, py = (scan * self.bc)[hit], (scan * self.bs)[hit]
        near = scan.min()
        vcap = vmax if near > self.sd else min(vmax, self.sv)
        gx, gy = goal or r.wps[r.wp_i]
        c, s = math.cos(r.th), math.sin(r.th)
        lx, ly = c * (gx - r.x) + s * (gy - r.y), -s * (gx - r.x) + c * (gy - r.y)    # 목표(로봇 좌표계)
        d0 = math.hypot(lx, ly)
        final = r.wp_i == len(r.wps) - 1
        if final:
            vcap = min(vcap, math.sqrt(max(0.0, 2 * brake * 0.8 * (d0 - 0.15))))
        win = 0.5
        vs = np.linspace(max(-V_BACK, r.v - brake * win), min(vcap, r.v + acc * win), self.nv)
        ws = np.linspace(max(-W_MAX, r.w - 3.0 * win), min(W_MAX, r.w + 3.0 * win), self.nw)
        V, Wg = np.meshgrid(vs, ws, indexing="ij")
        V, Wg = V.ravel(), Wg.ravel()
        T = self.ts[None, :]
        th = Wg[:, None] * T
        safe_w = np.where(np.abs(Wg) < 1e-6, 1e-6, Wg)[:, None]
        X = np.where(np.abs(Wg[:, None]) < 1e-6, V[:, None] * T, V[:, None] / safe_w * np.sin(th))
        Y = np.where(np.abs(Wg[:, None]) < 1e-6, 0.0, V[:, None] / safe_w * (1 - np.cos(th)))
        if len(px):
            dist = np.hypot(X[:, :, None] - px[None, None], Y[:, :, None] - py[None, None]).min(axis=2)
            clear = dist.min(axis=1) - ROBOT_R
        else:
            clear = np.full(len(V), 5.0)
        # 허용 속도: 남은 여유 안에서 멈출 수 있어야 함
        ok = (clear > 0.05) & (np.abs(V) <= np.sqrt(2 * brake * np.maximum(clear, 0)) + 1e-6)
        if not ok.any():
            err = math.atan2(ly, lx)
            return _to_action(0.0, float(np.clip(1.5 * err, -W_MAX, W_MAX)) if near > ROBOT_R + 0.1 else 0.0, vmax)
        ex, ey, eth = X[:, -1], Y[:, -1], th[:, -1]
        head = np.abs((np.arctan2(ly - ey, lx - ex) - eth + np.pi) % (2 * np.pi) - np.pi)
        prog = (d0 - np.hypot(lx - ex, ly - ey)) / (vmax * self.h)
        score = (self.wh * (1 - head / np.pi) + self.wc * np.minimum(clear, 2.0) / 2.0 + self.wv * V / vmax
                 + self.wp * prog)
        score[~ok] = -np.inf
        k = int(np.argmax(score))
        return _to_action(float(V[k]), float(Wg[k]), vmax)


class SafetyShield:
    """학습 정책 출력 위에 얹는 규칙 기반 안전 필터 (실물의 안전 스캐너 보호 영역과 같은 역할).

    진행 방향 ±40° 안의 LiDAR 최소 거리로 "지금 멈추면 닿지 않는" 속도 상한을 구해 v 를 깎는다.
    정책이 효율(경로·속도 선택)을 맡고, 충돌 직전 상황은 규칙이 막는 구조.
    """

    def __init__(self, inner, margin: float = 0.2, cone_deg: float = 40.0) -> None:
        self.inner, self.margin = inner, margin
        self.name = f"{inner.name} + 안전 필터"
        k = int(round(cone_deg / (360 / N_BEAMS)))
        self.front = np.r_[np.arange(0, k + 1), np.arange(N_BEAMS - k, N_BEAMS)]
        self.rear = (self.front + N_BEAMS // 2) % N_BEAMS

    def act(self, env, scans, obs=None):
        a = self.inner.act(env, scans, obs) if obs is not None else self.inner.act(env, scans)
        a = np.array(a, dtype=np.float64)
        for i, r in enumerate(env.robots):
            if not r.active:
                continue
            vmax, _, brake = _limits(r)
            vt = a[i, 0] * vmax if a[i, 0] >= 0 else a[i, 0] * V_BACK
            cone = self.front if vt >= 0 else self.rear
            free = scans[i][cone].min() - ROBOT_R - self.margin
            cap = math.sqrt(2 * brake * max(free, 0.0))
            if abs(vt) > cap:
                vt = math.copysign(cap, vt)
                a[i, 0] = vt / vmax if vt >= 0 else vt / V_BACK
        return a
