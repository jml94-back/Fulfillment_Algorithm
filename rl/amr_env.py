"""AMR 4대 국소 주행·회피 강화학습 환경 (Unity 없이 도는 2D 프로토타입).

- 지도: D4 남쪽 운행 구역 120 m x 21.6 m (rl/geometry.py).
- 로봇: 차동 구동, 10 Hz 제어(MQTT cmd/vel 주기와 같음). 행동 = 목표 선속도·각속도 (연속 2차원, -1~1).
  싣고 있는 팔레트 무게가 크면 최고 속도·가속도가 줄어든다.
- 관측: 2D LiDAR 72빔(현재 + 직전 프레임), 다음 경유점 2개, 자기 속도·적재 무게, 직전 행동,
  가까운 AMR 3대의 상대 위치·속도(MQTT pose 공유를 가정).
- 상위 계획기(planner.py)가 고정 장애물만 보고 경유점을 준다. 다른 AMR, 작업자, 임시 팔레트는 정책이 피한다.

API 두 가지:
- AMRFleetEnv: 학습용 배열 API. reset() → obs[n, D], step(actions[n, 2]) → obs, reward, done, info.
  done[i] 는 "그 로봇의 생애 종료(충돌)" 이고 로봇은 그 자리에서 새 위치·새 임무로 다시 시작한다
  (ML-Agents 의 에이전트별 EndEpisode 와 같은 방식). 임무 완료는 종료가 아니라 다음 임무로 이어진다.
- ParallelAMREnv: PettingZoo ParallelEnv 래퍼 (같은 학습 코드를 mlagents_envs 의 UnityParallelEnv 로 바꿔 끼우는 용도).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .geometry import (AISLE, H, ROBOT_R, W, WORKER_R, GridMap, Station, static_rects, stations, wall_rects)
from .planner import WaypointPlanner

N_BEAMS, LIDAR_MAX = 72, 10.0
DT = 0.1
V_MAX, V_BACK, W_MAX = 1.5, 0.3, 1.2
K_NEIGH = 3
FRONT = 7                     # 정면 ±30° (빔 0~6, 66~71)
OBS_DIM = 2 * N_BEAMS + 3 + 3 + 1 + 3 + 2 + 5 * K_NEIGH        # 171
N_WORKERS_MAX = 4
STATE_DIM = 4 * 10 + N_WORKERS_MAX * 4 + 4                   # 중앙 critic 용 전역 상태 (60)


@dataclass
class StageCfg:
    n_active: int
    missions: bool          # False = 근거리 무작위 목표, True = 실제 도크↔리프트 임무 + 경유점
    n_workers: int
    max_pallets: int
    max_steps: int


STAGES = [
    StageCfg(1, False, 0, 0, 400),     # 0: 혼자, 빈 통로에서 3~10 m 목표
    StageCfg(1, True, 0, 0, 1500),     # 1: 혼자, 실제 임무(경유점 따라가기)
    StageCfg(4, True, 0, 0, 1500),     # 2: 4대 동시
    StageCfg(4, True, 2, 0, 1500),     # 3: 4대 + 작업자 2명
    StageCfg(4, True, 4, 3, 1500),     # 4: 4대 + 작업자 4명 + 지도에 없는 임시 팔레트 0~3개 (평가 기준)
]


@dataclass
class RewardCfg:
    progress: float = 1.0          # 경유점까지 1 m 가까워질 때
    waypoint: float = 0.5
    arrive: float = 10.0
    collision: float = -30.0
    pass_close: float = -0.3       # 다른 AMR 중심 거리 2.2 m 안(틈 0.8 m 미만)에서 서로 다가갈 때, m 당
    safety: float = -0.3           # 속도 의존 안전 영역 침범(m)당. 정지 거리 + 0.3 m 안에 전방 장애물
    near: float = -0.2             # LiDAR 최소 거리 1.2 m → 0.7 m 사이 선형 (틈 0.5 m 미만)
    worker_speed: float = -0.3     # 작업자 2.5 m 안에서 0.5 m/s 초과분(m/s)당
    time: float = -0.01
    idle: float = -0.02            # 앞이 2.5 m 이상 비어 있는데 서 있을 때 (서로 눈치 보며 멈추는 교착 방지)
    smooth: float = -0.02
    timeout: float = -5.0


@dataclass
class Robot:
    x: float = 0.0
    y: float = 0.0
    th: float = 0.0
    v: float = 0.0
    w: float = 0.0
    load: float = 0.0
    active: bool = False
    wps: list = field(default_factory=list)
    wp_i: int = 0
    goal: Station | None = None
    next_leg: tuple | None = None      # (station, load) 다음 구간
    prev_a: np.ndarray = field(default_factory=lambda: np.zeros(2))
    prev_scan: np.ndarray = field(default_factory=lambda: np.full(N_BEAMS, LIDAR_MAX))
    d_prev: float = 0.0
    since_wp: int = 0


_PLANNER_CACHE: dict = {}


def _shared_planner() -> tuple[GridMap, WaypointPlanner, list[Station]]:
    if "p" not in _PLANNER_CACHE:
        grid = GridMap()
        st = stations()
        _PLANNER_CACHE["p"] = (grid, WaypointPlanner(grid, st), st)
    return _PLANNER_CACHE["p"]


class AMRFleetEnv:
    n_agents = 4

    def __init__(self, stage: int = 3, seed: int | None = None, reward: RewardCfg | None = None) -> None:
        self.rng = np.random.default_rng(seed)
        self.rc = reward or RewardCfg()
        self.grid, self.planner, self.stations = _shared_planner()
        self.by_kind: dict[str, list[Station]] = {}
        for s in self.stations:
            self.by_kind.setdefault(s.kind, []).append(s)
        self.static = np.array(static_rects() + wall_rects(), dtype=np.float64)
        ang = np.arange(N_BEAMS) * 2 * np.pi / N_BEAMS
        self.beam_cos, self.beam_sin = np.cos(ang), np.sin(ang)
        self.set_stage(stage)

    # ------------------------------------------------------------------ 설정
    def set_stage(self, stage: int) -> None:
        self.stage = stage
        self.cfg = STAGES[stage]

    # ------------------------------------------------------------------ 배치
    def _clear(self, x: float, y: float, skip: int = -1, margin: float = 2.2) -> bool:
        if not self.grid.free(x, y):
            return False
        for j, r in enumerate(self.robots):
            if j != skip and r.active and math.hypot(r.x - x, r.y - y) < margin:
                return False
        for w in self.workers:
            if math.hypot(w[0] - x, w[1] - y) < 1.5:
                return False
        for x0, y0, x1, y1 in self.pallets:
            if x0 - ROBOT_R - .3 < x < x1 + ROBOT_R + .3 and y0 - ROBOT_R - .3 < y < y1 + ROBOT_R + .3:
                return False
        return True

    def _random_aisle_point(self, skip: int = -1) -> tuple[float, float]:
        for _ in range(500):
            x = self.rng.uniform(1.5, W - 1.5)
            y = self.rng.uniform(AISLE[0] - 0.4, AISLE[1] - 0.6)
            if self._clear(x, y, skip):
                return x, y
        return x, y

    def _spawn(self, i: int) -> None:
        r = self.robots[i]
        r.x, r.y = self._random_aisle_point(skip=i)
        r.th = self.rng.uniform(-np.pi, np.pi)
        r.v = r.w = 0.0
        r.prev_a = np.zeros(2)
        r.load = 0.0
        self._new_mission(i)
        r.prev_scan = None

    def _targets_taken(self, i: int) -> set[str]:
        out = set()
        for j, r in enumerate(self.robots):
            if j != i and r.active:
                if r.goal is not None:
                    out.add(r.goal.name)
                if r.next_leg is not None:
                    out.add(r.next_leg[0].name)
        return out

    def _pick(self, kind: str, taken: set[str]) -> Station:
        cand = [s for s in self.by_kind[kind] if s.name not in taken] or self.by_kind[kind]
        return cand[self.rng.integers(len(cand))]

    def _new_mission(self, i: int) -> None:
        r = self.robots[i]
        if not self.cfg.missions:
            for _ in range(200):
                d = self.rng.uniform(3, 10)
                a = self.rng.uniform(-np.pi, np.pi)
                gx, gy = r.x + d * math.cos(a), r.y + d * math.sin(a)
                if 0 < gx < W and 0 < gy < H and self._clear(gx, gy, skip=i, margin=1.6):
                    break
            r.goal = Station("rand", gx, gy, "rand")
            r.wps, r.load, r.next_leg = [(gx, gy)], 0.0, None
        else:
            taken = self._targets_taken(i)
            if self.rng.random() < 0.5:     # 입고: 계량대에서 집어 리프트 입고 버퍼로
                pick, drop = self._pick("in_pick", taken), self._pick("lift_in", taken)
            else:                           # 출고: 리프트 출고 버퍼에서 집어 출고 하차대로
                pick, drop = self._pick("lift_out", taken), self._pick("out_drop", taken)
            r.goal, r.load = pick, 0.0
            r.next_leg = (drop, float(self.rng.uniform(200, 1500)))
            r.wps = self.planner.plan((r.x, r.y), pick)
        r.wp_i, r.since_wp = 0, 0
        r.d_prev = self._wp_dist(r)

    def _advance_leg(self, i: int) -> None:
        r = self.robots[i]
        if r.next_leg is None:
            self._new_mission(i)
            return
        drop, load = r.next_leg
        r.goal, r.load, r.next_leg = drop, load, None
        r.wps = self.planner.plan((r.x, r.y), drop)
        r.wp_i, r.since_wp = 0, 0
        r.d_prev = self._wp_dist(r)

    @staticmethod
    def _wp_dist(r: Robot) -> float:
        wx, wy = r.wps[r.wp_i]
        return math.hypot(wx - r.x, wy - r.y)

    # ------------------------------------------------------------------ reset / step
    def reset(self, seed: int | None = None) -> np.ndarray:
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        self.t = 0
        self.robots = [Robot() for _ in range(self.n_agents)]
        self.workers = np.zeros((0, 6))          # x, y, vx, vy, tx, ty
        self.pallets: list[tuple[float, float, float, float]] = []
        n_p = self.rng.integers(0, self.cfg.max_pallets + 1) if self.cfg.max_pallets else 0
        for _ in range(n_p):
            for _ in range(100):
                cx = self.rng.uniform(3, W - 3)
                cy = self.rng.uniform(AISLE[0] + 0.6, AISLE[1] - 0.6)
                if (all(math.hypot(cx - s.x, cy - s.y) > 3.0 for s in self.stations)
                        and all(abs(cx - (p[0] + .55)) > 5.0 for p in self.pallets)):   # 팔레트끼리 5 m 이상 (통로 막힘 방지)
                    self.pallets.append((cx - .55, cy - .55, cx + .55, cy + .55))
                    break
        ws = []
        for _ in range(self.cfg.n_workers):
            x, y = self._random_aisle_point()
            ws.append([x, y, 0, 0, x, y])
        self.workers = np.array(ws, dtype=np.float64).reshape(-1, 6)
        self.worker_wait = np.zeros(len(self.workers), dtype=int)
        for i in range(self.n_agents):
            self.robots[i].active = i < self.cfg.n_active
            if self.robots[i].active:
                self._spawn(i)
            else:
                self.robots[i].x = self.robots[i].y = -100.0
        self._rects = np.vstack([self.static, np.array(self.pallets).reshape(-1, 4)])
        self.stats = dict(arrivals=0, collisions=0, coll_robot=0, coll_worker=0, coll_static=0, timeouts=0,
                          near=0, worker_fast=0, steps=0, dist=0.0)
        scans = self._scan()
        for i, r in enumerate(self.robots):
            r.prev_scan = scans[i]
        return self._obs(scans)

    def active_mask(self) -> np.ndarray:
        return np.array([r.active for r in self.robots])

    def step(self, actions: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
        a = np.clip(np.asarray(actions, dtype=np.float64), -1, 1)
        rc = self.rc
        rew = np.zeros(self.n_agents)
        done = np.zeros(self.n_agents, dtype=bool)
        # 1) 운동
        for i, r in enumerate(self.robots):
            if not r.active:
                continue
            lf = r.load / 1500.0
            vmax, acc, brake = V_MAX - 0.5 * lf, 1.0 - 0.4 * lf, 1.5 - 0.5 * lf
            vt = a[i, 0] * vmax if a[i, 0] >= 0 else a[i, 0] * V_BACK
            wt = a[i, 1] * W_MAX
            lim = (acc if abs(vt) > abs(r.v) else brake) * DT
            r.v += float(np.clip(vt - r.v, -lim, lim))
            r.w += float(np.clip(wt - r.w, -3.0 * DT, 3.0 * DT))
            r.th = (r.th + r.w * DT + np.pi) % (2 * np.pi) - np.pi
            r.x += r.v * math.cos(r.th) * DT
            r.y += r.v * math.sin(r.th) * DT
            self.stats["dist"] += abs(r.v) * DT
            rew[i] += rc.smooth * float(np.sum((a[i] - r.prev_a) ** 2)) + rc.time
            r.prev_a = a[i].copy()
        self._move_workers()
        # 2) 충돌
        coll = self._collisions()
        # 3) 보상·경유점
        for i, r in enumerate(self.robots):
            if not r.active:
                continue
            if coll[i]:
                rew[i] += rc.collision
                done[i] = True
                continue
            d = self._wp_dist(r)
            rew[i] += rc.progress * (r.d_prev - d)
            r.since_wp += 1
            final = r.wp_i == len(r.wps) - 1
            if final and d < 0.5 and abs(r.v) < 0.3:
                rew[i] += rc.arrive
                self.stats["arrivals"] += 1
                self._advance_leg(i)
            elif not final and d < 1.2:
                rew[i] += rc.waypoint
                r.wp_i += 1
                r.since_wp = 0
                r.d_prev = self._wp_dist(r)
            else:
                r.d_prev = d
            if r.since_wp > 400:                       # 경유점 하나에 40초 넘게 못 감 → 재계획
                rew[i] += rc.timeout
                self.stats["timeouts"] += 1
                if self.cfg.missions and r.goal is not None and r.goal.kind != "rand":
                    r.wps = self.planner.plan((r.x, r.y), r.goal)
                    r.wp_i, r.since_wp = 0, 0
                    r.d_prev = self._wp_dist(r)
                else:
                    self._new_mission(i)
        for i in np.nonzero(done)[0]:
            self._spawn(int(i))
        self._rects = np.vstack([self.static, np.array(self.pallets).reshape(-1, 4)])
        scans = self._scan()
        # 4) 안전 거리 보상 (스캔 기준)
        for i, r in enumerate(self.robots):
            if not r.active or done[i]:
                continue
            m = scans[i].min()
            # 속도에 따라 커지는 전방 보호 영역 (실제 안전 스캐너의 필드 전환과 같은 개념)
            if r.v > 0.05:
                brake = 1.5 - 0.5 * r.load / 1500.0
                field_len = r.v ** 2 / (2 * brake) + 0.3
                front = min(scans[i][:FRONT].min(), scans[i][-FRONT:].min()) - ROBOT_R
                if front < field_len:
                    rew[i] += rc.safety * (field_len - front)
            if abs(r.v) < 0.1 and min(scans[i][:FRONT].min(), scans[i][-FRONT:].min()) > 2.5:
                rew[i] += rc.idle
            if m < 1.2:
                rew[i] += rc.near * min(1.0, (1.2 - m) / 0.5)
            for j, q in enumerate(self.robots):
                if j != i and q.active:
                    dx, dy = q.x - r.x, q.y - r.y
                    dd = math.hypot(dx, dy)
                    if dd < 2.2:
                        closing = -((q.v * math.cos(q.th) - r.v * math.cos(r.th)) * dx
                                    + (q.v * math.sin(q.th) - r.v * math.sin(r.th)) * dy) / max(dd, 1e-6)
                        if closing > 0:
                            rew[i] += rc.pass_close * (2.2 - dd) * min(closing, 3.0)
            if m < 0.9:
                self.stats["near"] += 1
            if len(self.workers):
                dw = np.hypot(self.workers[:, 0] - r.x, self.workers[:, 1] - r.y).min()
                if dw < 2.5 and abs(r.v) > 0.5:
                    rew[i] += rc.worker_speed * (abs(r.v) - 0.5)
                    self.stats["worker_fast"] += 1
        obs = self._obs(scans)
        for i, r in enumerate(self.robots):
            r.prev_scan = scans[i]
        self.t += 1
        self.stats["steps"] += int(self.active_mask().sum())
        trunc = self.t >= self.cfg.max_steps
        return obs, rew, done, {"truncated": trunc, "collided": coll}

    # ------------------------------------------------------------------ 작업자
    def _move_workers(self) -> None:
        """작업자: 목표 지점으로 걷되 AMR 을 사회적 힘(social force)으로 피해 간다.
        움직이는 AMR 이 정면에서 다가오면 멈춰서 양보하고, 서 있는 AMR 은 돌아서 지나간다."""
        if not len(self.workers):
            return
        act = [r for r in self.robots if r.active]
        for k in range(len(self.workers)):
            x, y, _, _, tx, ty = self.workers[k]
            d = math.hypot(tx - x, ty - y)
            if d < 0.4:
                _, ty = self._random_aisle_point()
                tx = float(np.clip(x + self.rng.uniform(-25, 25), 2, W - 2))
                self.workers[k, 4:] = tx, ty
                d = math.hypot(tx - x, ty - y)
            sp = 1.0 + 0.3 * math.sin(0.01 * self.t + k)
            fx, fy = (tx - x) / max(d, 1e-6), (ty - y) / max(d, 1e-6)
            stop = False
            for r in act:
                dx, dy = x - r.x, y - r.y
                dd = max(math.hypot(dx, dy), 1e-6)
                if dd < 2.5:                                   # AMR 에서 밀어내는 힘
                    mag = 1.5 * math.exp(-(dd - 1.0) / 0.4)
                    fx += mag * dx / dd
                    fy += mag * dy / dd
                # 1.8 m 안에서 AMR 이 나를 향해 움직이면 멈춘다 (양보)
                if dd < 1.8 and abs(r.v) > 0.2 and (math.cos(r.th) * dx + math.sin(r.th) * dy) * r.v > 0.6 * dd * abs(r.v):
                    stop = True
            n = math.hypot(fx, fy)
            vx, vy = (0.0, 0.0) if stop or n < 1e-6 else (sp * fx / n, sp * fy / n)
            nx, ny = x + vx * DT, y + vy * DT
            if not self.grid.free(nx, ny) and self.grid.free(x, y):
                vx = vy = 0.0
                nx, ny = x, y
                self.worker_wait[k] += 1
                if self.worker_wait[k] > 20:
                    self.worker_wait[k] = 0
                    self.workers[k, 4:] = self._random_aisle_point()
            self.workers[k, :4] = nx, ny, vx, vy

    # ------------------------------------------------------------------ 충돌·스캔
    def _collisions(self) -> np.ndarray:
        n = self.n_agents
        coll = np.zeros(n, dtype=bool)
        R = self._rects if hasattr(self, "_rects") else self.static
        for i, r in enumerate(self.robots):
            if not r.active:
                continue
            dx = np.maximum(np.maximum(R[:, 0] - r.x, 0), r.x - R[:, 2])
            dy = np.maximum(np.maximum(R[:, 1] - r.y, 0), r.y - R[:, 3])
            if (np.hypot(dx, dy) < ROBOT_R).any():
                coll[i] = True
                self.stats["coll_static"] += 1
            for j in range(i + 1, n):
                q = self.robots[j]
                if q.active and math.hypot(r.x - q.x, r.y - q.y) < 2 * ROBOT_R:
                    coll[i] = coll[j] = True
                    self.stats["coll_robot"] += 1
            if len(self.workers) and (np.hypot(self.workers[:, 0] - r.x, self.workers[:, 1] - r.y)
                                      < ROBOT_R + WORKER_R).any():
                coll[i] = True
                self.stats["coll_worker"] += 1
        self.stats["collisions"] += int(coll.sum())
        return coll

    def _scan(self) -> np.ndarray:
        """모든 로봇의 2D LiDAR (n, 72), 단위 m. 0번 빔 = 정면, 반시계."""
        n = self.n_agents
        out = np.full((n, N_BEAMS), LIDAR_MAX)
        act = [i for i, r in enumerate(self.robots) if r.active]
        if not act:
            return out
        R = self._rects
        circ = [(r.x, r.y, ROBOT_R, j) for j, r in enumerate(self.robots) if r.active]
        circ += [(w[0], w[1], WORKER_R, -1) for w in self.workers]
        C = np.array(circ, dtype=np.float64).reshape(-1, 4)
        for i in act:
            r = self.robots[i]
            c, s = math.cos(r.th), math.sin(r.th)
            dx = c * self.beam_cos - s * self.beam_sin
            dy = s * self.beam_cos + c * self.beam_sin
            ix = 1.0 / np.where(np.abs(dx) < 1e-9, 1e-9, dx)
            iy = 1.0 / np.where(np.abs(dy) < 1e-9, 1e-9, dy)
            near = ((R[:, 0] - r.x < LIDAR_MAX) & (r.x - R[:, 2] < LIDAR_MAX) &
                    (R[:, 1] - r.y < LIDAR_MAX) & (r.y - R[:, 3] < LIDAR_MAX))
            Rn = R[near]
            t1 = (Rn[None, :, 0] - r.x) * ix[:, None]
            t2 = (Rn[None, :, 2] - r.x) * ix[:, None]
            t3 = (Rn[None, :, 1] - r.y) * iy[:, None]
            t4 = (Rn[None, :, 3] - r.y) * iy[:, None]
            tmin = np.maximum(np.minimum(t1, t2), np.minimum(t3, t4))
            tmax = np.minimum(np.maximum(t1, t2), np.maximum(t3, t4))
            hit = (tmax >= np.maximum(tmin, 0))
            t = np.where(hit, np.maximum(tmin, 0), LIDAR_MAX)
            best = t.min(axis=1) if t.shape[1] else np.full(N_BEAMS, LIDAR_MAX)
            Ci = C[C[:, 3] != i]
            if len(Ci):
                ox, oy = r.x - Ci[:, 0], r.y - Ci[:, 1]
                b = dx[:, None] * ox[None] + dy[:, None] * oy[None]
                cc = (ox ** 2 + oy ** 2 - Ci[:, 2] ** 2)[None]
                disc = b ** 2 - cc
                tc = np.where(disc >= 0, -b - np.sqrt(np.maximum(disc, 0)), LIDAR_MAX)
                tc = np.where(tc >= 0, tc, LIDAR_MAX)
                best = np.minimum(best, tc.min(axis=1))
            out[i] = np.minimum(best, LIDAR_MAX)
        return out

    # ------------------------------------------------------------------ 관측
    def _goal_feat(self, r: Robot, p: tuple[float, float]) -> list[float]:
        dx, dy = p[0] - r.x, p[1] - r.y
        d = math.hypot(dx, dy)
        b = math.atan2(dy, dx) - r.th
        return [min(d, LIDAR_MAX) / LIDAR_MAX, math.cos(b), math.sin(b)]

    def _obs(self, scans: np.ndarray) -> np.ndarray:
        obs = np.zeros((self.n_agents, OBS_DIM), dtype=np.float32)
        for i, r in enumerate(self.robots):
            if not r.active:
                continue
            prev = r.prev_scan if r.prev_scan is not None else scans[i]
            g1 = r.wps[r.wp_i]
            g2 = r.wps[min(r.wp_i + 1, len(r.wps) - 1)]
            c, s = math.cos(r.th), math.sin(r.th)
            neigh = []
            for j, q in enumerate(self.robots):
                if j == i or not q.active:
                    continue
                dx, dy = q.x - r.x, q.y - r.y
                d = math.hypot(dx, dy)
                if d < LIDAR_MAX:
                    rvx = q.v * math.cos(q.th) - r.v * c
                    rvy = q.v * math.sin(q.th) - r.v * s
                    neigh.append((d, [(c * dx + s * dy) / LIDAR_MAX, (-s * dx + c * dy) / LIDAR_MAX,
                                      (c * rvx + s * rvy) / V_MAX, (-s * rvx + c * rvy) / V_MAX, 1.0]))
            neigh.sort(key=lambda z: z[0])
            nf = [v for _, f in neigh[:K_NEIGH] for v in f] + [0.0] * 5 * (K_NEIGH - min(len(neigh), K_NEIGH))
            obs[i] = np.concatenate([
                scans[i] / LIDAR_MAX, prev / LIDAR_MAX,
                self._goal_feat(r, g1), self._goal_feat(r, g2), [float(r.wp_i == len(r.wps) - 1)],
                [r.v / V_MAX, r.w / W_MAX, r.load / 1500.0], r.prev_a, nf])
        return obs

    def state(self) -> np.ndarray:
        """중앙 critic 용 전역 상태 (학습 때만 사용, 실행 시 정책은 쓰지 않음)."""
        f = []
        for r in self.robots:
            if r.active:
                g = r.wps[r.wp_i]
                f += [1.0, r.x / W, r.y / H, math.cos(r.th), math.sin(r.th), r.v / V_MAX, r.w / W_MAX,
                      g[0] / W, g[1] / H, r.load / 1500.0]
            else:
                f += [0.0] * 10
        for k in range(N_WORKERS_MAX):
            if k < len(self.workers):
                w = self.workers[k]
                f += [w[0] / W, w[1] / H, w[2], w[3]]
            else:
                f += [0.0] * 4
        return np.array(f, dtype=np.float32)

    def critic_obs(self, obs: np.ndarray) -> np.ndarray:
        s = self.state()
        eye = np.eye(self.n_agents, dtype=np.float32)
        return np.concatenate([obs, np.repeat(s[None, :STATE_DIM - 4], self.n_agents, 0), eye], axis=1)


# ---------------------------------------------------------------------- PettingZoo 래퍼
try:
    from gymnasium import spaces
    from pettingzoo import ParallelEnv

    class ParallelAMREnv(ParallelEnv):
        metadata = {"name": "amr_fleet_v0", "render_modes": []}

        def __init__(self, stage: int = 3, seed: int | None = None) -> None:
            self.env = AMRFleetEnv(stage, seed)
            self.possible_agents = [f"AMR{i + 1:02d}" for i in range(AMRFleetEnv.n_agents)]
            self._obs_space = spaces.Box(-1.0, 1.0, (OBS_DIM,), np.float32)
            self._act_space = spaces.Box(-1.0, 1.0, (2,), np.float32)

        def observation_space(self, agent):
            return self._obs_space

        def action_space(self, agent):
            return self._act_space

        def _ids(self):
            return [a for a, m in zip(self.possible_agents, self.env.active_mask()) if m]

        def reset(self, seed=None, options=None):
            o = self.env.reset(seed)
            self.agents = self._ids()
            idx = {a: i for i, a in enumerate(self.possible_agents)}
            return {a: o[idx[a]] for a in self.agents}, {a: {} for a in self.agents}

        def step(self, actions):
            idx = {a: i for i, a in enumerate(self.possible_agents)}
            arr = np.zeros((AMRFleetEnv.n_agents, 2))
            for a, v in actions.items():
                arr[idx[a]] = v
            o, r, d, info = self.env.step(arr)
            ids = self.agents
            obs = {a: o[idx[a]] for a in ids}
            rew = {a: float(r[idx[a]]) for a in ids}
            # 충돌은 그 로봇의 생애 종료지만 같은 이름으로 곧장 다시 시작하므로 info 로 알린다
            term = {a: False for a in ids}
            trunc = {a: bool(info["truncated"]) for a in ids}
            infos = {a: {"collided": bool(d[idx[a]])} for a in ids}
            if info["truncated"]:
                self.agents = []
            return obs, rew, term, trunc, infos
except ImportError:            # gymnasium / pettingzoo 없이도 배열 API 는 쓸 수 있게
    ParallelAMREnv = None
