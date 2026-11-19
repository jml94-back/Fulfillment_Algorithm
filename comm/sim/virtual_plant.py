"""가상 장비 (Unity 대역). Unity 시뮬레이션이 아직 없을 때 같은 토픽으로 장비를 흉내 낸다.

    python -m sim.virtual_plant --host localhost --scale 20

Unity 쪽이 구현해야 할 동작이 이 파일에 그대로 들어 있다 (Unity C# 예제와 같은 규칙):
- AMR: cmd/mission 의 단계를 순서대로 수행. move 는 경유점을 따라 1.2 m/s, 경유점의 t_start 전에는 그 노드 앞에서 기다린다.
  pick/drop 8초. 전제 조건(팔레트가 있음/자리가 빔)이 안 되면 될 때까지 기다린다. 한 번에 미션 1개, 바쁘면 거부.
  drive_mode = rl 이면 cmd/vel 로 움직이고 0.5초 안에 새 명령이 없으면 정지.
- 랙(통로 R1~R3): Unity ShuttleRackSystem 과 같은 규칙으로 rack/+/cmd 의 store·retrieve 를 처리한다
  (레인 배정 비용식, virtual 예약, 단별 순서, 리프트 단일 점유, 사다리꼴 속도). 자세한 규칙은 sim/rack_model.py.
  시작 재고는 PrefillRandom 처럼 무작위로 채우고 rack/+/inventory(retain)로 알린다.
- 버퍼: 컨베이어 5칸. 입고는 IN5(AMR)→IN1(리프트), 출고는 OUT1→OUT5. 한 칸 1.3 m / 0.5 m/s = 2.6초.
- 도크: wms/order 를 보고 트럭을 세운다(시뮬레이션 전용). 입고 트럭은 30초마다 팔레트 1개를 계량대에 내린다.
  출고 트럭은 DP-OUT 에 내려진 팔레트를 싣고, 다 실으면 떠난다.
- 셔틀·리프트 속도는 Unity 스크립트 기본값(명세 4.3)과 같다.

AMR끼리의 물리 충돌은 흉내 내지 않는다 (충돌 회피는 스케줄러의 시간 예약과 RL 정책 몫).
"""
from __future__ import annotations

import argparse
import logging
import math
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from whmqtt import geometry as G
from whmqtt import ids
from whmqtt.client import WhClient
from whmqtt.envelope import Envelope

from .rack_model import CONVEYOR_S, RackModel

AMR_V, AMR_HANDLE = 1.2, 8.0
UNLOAD_S, LOAD_S = 30.0, 30.0
PRECOND_TIMEOUT = 600.0
DRAIN_MOVE, DRAIN_IDLE, CHARGE_RATE = 0.02, 0.003, 0.5      # %/s
log = logging.getLogger("plant")


# ───────────────────────────────────────────────────────────────── 장비 상태
@dataclass
class Amr:
    id: str
    x: float
    y: float
    theta: float
    node: str
    battery: float = 90.0
    drive_mode: str = "scripted"
    mode: str = "idle"
    pallet: str | None = None
    load_kg: float | None = None
    mission: dict | None = None
    step_i: int = 0
    wp_i: int = 0
    busy_until: float = 0.0
    waited: float = 0.0
    paused: bool = False
    v: float = 0.0
    w: float = 0.0
    vel_cmd: tuple[float, float, float] | None = None   # (v, w, 벽시계 만료)
    charger: str | None = None
    last_state: tuple | None = None
    last_state_t: float = -1e9
    error: str | None = None


@dataclass
class Dock:
    id: str
    status: str = "empty"
    truck: str | None = None
    order_id: str | None = None
    total: int = 0
    done: int = 0
    pending: deque = field(default_factory=deque)      # 입고: 내릴 팔레트 (pallet, sku, declared, actual)
    next_t: float = 0.0
    depart_t: float | None = None


class Plant:
    def __init__(self, host: str, port: int, scale: float = 10.0, tick: float = 0.1, scan: bool = False,
                 validate: bool = False, seed: int = 0, client_id: str = "unity-sim",
                 heavy: set[str] | None = None, prefill_channels: int = 14,
                 skus: list[str] | None = None) -> None:
        self.heavy = heavy or set()          # 시뮬레이션용: 실측 무게를 송장보다 12% 무겁게 만들 팔레트
        self.rng = random.Random(seed)
        self.scale, self.tick, self.scan = scale, tick, scan
        self.t = 0.0
        self.paused = False
        self.c = WhClient(client_id, "unity", host, port, validate=validate)
        self.lock = threading.RLock()
        self.amrs: dict[str, Amr] = {}
        for a in ids.AMRS:
            x, y, ch = G.amr_spawn(a)
            self.amrs[a] = Amr(a, x, y, G.stop_theta(ch), G.STOP_NODES[ch], charger=ch,
                               battery=self.rng.uniform(70, 95))
        self.chargers = {c: next((a.id for a in self.amrs.values() if a.charger == c), None) for c in ids.CHARGERS}
        self.buffers: dict[str, list[str | None]] = {b: [None] * 5 for b in ids.BUFFERS}
        self.buf_move: dict[str, float] = {b: 0.0 for b in ids.BUFFERS}
        self.racks = {f"R{r}": RackModel(r, self.buffers, self._rack_send, lambda: self.t) for r in range(1, 4)}
        skus = skus or [f"SKU{k:03d}" for k in range(1, 21)]
        for rk in self.racks.values():             # 시작 재고 (Unity PrefillRandom 대역)
            rk.prefill(self.rng, skus, prefill_channels)
        self._inv_next = 0.0
        self.weights: dict[str, float] = {}
        self.stations: dict[str, deque] = {s: deque() for s in ids.SCALES + ids.DROPS + ["QC-IN", "QC-OUT"]}
        self.docks = {d: Dock(d) for d in ids.DOCKS}
        self._stop = threading.Event()
        self._pose_every = max(1, round(0.1 / tick))
        self._n = 0
        c = self.c
        c.on("amr.cmd.mission", self._on_mission)
        c.on("amr.cmd.vel", self._on_vel)
        c.on("amr.cmd.control", self._on_amr_control)
        c.on("rack.cmd", self._on_rack_cmd)
        c.on("wms.order", self._on_order)
        c.on("sim.control", self._on_sim_control)

    # ── 수신 ────────────────────────────────────────────────────────────────
    def _on_mission(self, env: Envelope, p: dict) -> None:
        with self.lock:
            a = self.amrs.get(p["amr"])
            m = env.data
            if a is None:
                return
            if a.mission is not None or a.error:
                self._amr_evt(a, "mission_rejected", m["mission_id"], None, reason="busy", corr=env.id)
                return
            a.mission, a.step_i, a.wp_i, a.busy_until, a.waited = m, 0, 0, 0.0, 0.0
            if a.charger:
                self.chargers[a.charger] = None
                self._charger_state(a.charger)
                a.charger = None
            self._amr_evt(a, "mission_accepted", m["mission_id"], None, corr=env.id)
            self._start_step(a)

    def _on_vel(self, env: Envelope, p: dict) -> None:
        a = self.amrs.get(p["amr"])
        if a:
            a.vel_cmd = (env.data["v"], env.data["w"], time.time() + env.data["ttl"])

    def _on_amr_control(self, env: Envelope, p: dict) -> None:
        with self.lock:
            a = self.amrs.get(p["amr"])
            if not a:
                return
            act = env.data["action"]
            if act == "pause":
                a.paused = True
            elif act == "resume":
                a.paused = False
            elif act == "set_drive_mode":
                a.drive_mode = env.data["drive_mode"] or "scripted"
            elif act == "cancel_mission" and a.mission:
                mid = a.mission["mission_id"]
                a.mission = None
                self._amr_evt(a, "mission_canceled", mid, None)
            self._amr_evt(a, "control_applied", a.mission["mission_id"] if a.mission else None, None, corr=env.id)
            self._amr_state(a, force=True)

    def _on_rack_cmd(self, env: Envelope, p: dict) -> None:
        with self.lock:
            rk = self.racks.get(p["rail"])
            if rk:
                rk.request(env.data, env.id)

    def _rack_send(self, key: str, data: dict, rail: str, corr: str | None = None) -> None:
        self.c.send(key, data, rail, corr=corr, t=self.t)

    def _on_order(self, env: Envelope, _p) -> None:
        """시뮬레이션 전용: 주문에 맞춰 트럭을 도크에 세운다 (실제로는 트럭이 그냥 온다)."""
        o = env.data
        with self.lock:
            dk = self.docks[o["dock"]]
            if dk.status != "empty":
                log.warning("도크 %s 사용 중, 주문 %s 무시", dk.id, o["order_id"])
                return
            dk.status, dk.truck, dk.order_id, dk.done = "docked", o["truck"], o["order_id"], 0
            dk.total = sum(ln["qty"] for ln in o["lines"])
            dk.next_t = self.t + 10.0
            if o["kind"] == "inbound":
                for ln in o["lines"]:
                    for pl in ln["pallets"]:
                        decl = pl["declared_kg"]
                        actual = decl * self.rng.gauss(1.0, 0.01)
                        if pl["pallet"] in self.heavy:
                            actual = decl * 1.12
                        dk.pending.append((pl["pallet"], ln["sku"], decl, actual))
            self.c.send("dock.evt", {"event": "truck_arrived", "truck": dk.truck, "order_id": dk.order_id,
                                     "pallet": None, "station": None}, dk.id, t=self.t)
            self._dock_state(dk)

    def _on_sim_control(self, env: Envelope, _p) -> None:
        a = env.data["action"]
        if a == "pause":
            self.paused = True
        elif a in ("resume", "start"):
            self.paused = False
        elif a == "set_scale" and env.data.get("scale"):
            self.scale = env.data["scale"]
        self._clock(force=True)

    # ── 발행 도우미 ────────────────────────────────────────────────────────
    def _amr_evt(self, a: Amr, event: str, mid, seq, reason=None, corr=None, pallet=None) -> None:
        self.c.send("amr.evt", {"event": event, "mission_id": mid, "seq": seq, "pallet": pallet, "reason": reason},
                    a.id, corr=corr, t=self.t)

    def _amr_state(self, a: Amr, force: bool = False) -> None:
        mode = "paused" if a.paused else a.mode
        key = (mode, a.drive_mode, a.pallet, a.mission["mission_id"] if a.mission else None, a.step_i, a.node,
               a.error)
        if not force and key == a.last_state and self.t - a.last_state_t < 5.0 * self.scale:
            return
        a.last_state, a.last_state_t = key, self.t
        self.c.send("amr.state", {"mode": mode, "drive_mode": a.drive_mode, "battery": round(a.battery, 1),
                                  "pallet": a.pallet, "mission_id": key[3],
                                  "step": a.step_i if a.mission else None, "node": a.node, "error": a.error},
                    a.id, t=self.t)

    def _rack_states(self, force: bool = False) -> None:
        for rid, rk in self.racks.items():
            st = rk.state()
            if force or st != getattr(rk, "_last_state", None):
                rk._last_state = st
                self.c.send("rack.state", st, rid, t=self.t)
            ls = rk.lift_state()
            if force or ls != rk.lift.last:
                rk.lift.last = ls
                self.c.send("lift.state", ls, rk.lift.id, t=self.t)
            for m in rk.mothers.values():
                ms = rk.shuttle_state(m)
                if force or ms != m.last:
                    m.last = ms
                    self.c.send("shuttle.state", ms, m.id, t=self.t)
            for b in list(rk.dirty_buf):
                self._buffer_state(b)
            rk.dirty_buf.clear()
            if force or (rk.dirty_inv and self.t >= self._inv_next):
                rk.dirty_inv = False
                self.c.send("rack.inventory", rk.inventory(), rid, t=self.t)
        if self.t >= self._inv_next:
            self._inv_next = self.t + 60.0

    def _buffer_state(self, b: str) -> None:
        self.c.send("buffer.state", {"slots": list(self.buffers[b]), "blocked": False}, b, t=self.t)

    def _dock_state(self, dk: Dock) -> None:
        self.c.send("dock.state", {"status": dk.status, "truck": dk.truck, "order_id": dk.order_id,
                                   "pallets_total": dk.total, "pallets_done": dk.done}, dk.id, t=self.t)

    def _charger_state(self, ch: str) -> None:
        occ = self.chargers[ch]
        self.c.send("charger.state", {"occupied_by": occ, "charging": occ is not None,
                                      "power_kw": 3.0 if occ else 0.0}, ch, t=self.t)

    def _clock(self, force: bool = False) -> None:
        self.c.send("sim.clock", {"t": round(self.t, 3), "scale": self.scale, "paused": self.paused}, t=self.t)

    # ── 실행 ────────────────────────────────────────────────────────────────
    def start(self) -> "Plant":
        self.c.start()
        with self.lock:
            self._clock(force=True)
            for a in self.amrs.values():
                self._amr_state(a, force=True)
            self._rack_states(force=True)
            for b in self.buffers:
                self._buffer_state(b)
            for dk in self.docks.values():
                self._dock_state(dk)
            for ch in self.chargers:
                self._charger_state(ch)
        self._th = threading.Thread(target=self._loop, daemon=True, name="plant")
        self._th.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._th.join(5)
        self.c.stop()

    def _loop(self) -> None:
        next_clock = 0.0
        while not self._stop.is_set():
            w0 = time.time()
            if not self.paused:
                with self.lock:
                    self.step(self.tick * self.scale)
            if w0 >= next_clock:
                self._clock()
                next_clock = w0 + 1.0
            time.sleep(max(0.0, self.tick - (time.time() - w0)))

    def step(self, dt: float) -> None:
        self.t += dt
        self._n += 1
        for a in self.amrs.values():
            self._amr_step(a, dt)
        for b in self.buffers:
            self._conveyor(b)
        for rk in self.racks.values():
            rk.step()
        self._rack_states()
        for dk in self.docks.values():
            self._dock_step(dk)
        if self._n % self._pose_every == 0:
            for a in self.amrs.values():
                self.c.send("amr.pose", {"x": round(a.x, 3), "y": round(a.y, 3), "theta": round(a.theta, 4),
                                         "v": round(a.v, 3), "w": round(a.w, 3), "battery": round(a.battery, 1),
                                         "load_kg": None if a.load_kg is None else round(a.load_kg, 1),
                                         "node": a.node}, a.id, t=self.t)
                if self.scan:
                    self.c.send("amr.scan2d", self.scan2d(a), a.id, t=self.t)

    # ── AMR ────────────────────────────────────────────────────────────────
    def _start_step(self, a: Amr) -> None:
        s = a.mission["steps"][a.step_i]
        a.wp_i, a.busy_until, a.waited = 0, 0.0, 0.0
        a.mode = {"move": "moving", "pick": "picking", "drop": "dropping", "charge": "charging",
                  "wait": "waiting"}[s["action"]]
        self._amr_evt(a, "step_started", a.mission["mission_id"], s["seq"], pallet=s.get("pallet"))
        self._amr_state(a, force=True)

    def _finish_step(self, a: Amr) -> None:
        m = a.mission
        s = m["steps"][a.step_i]
        self._amr_evt(a, "step_done", m["mission_id"], s["seq"], pallet=s.get("pallet"))
        a.step_i += 1
        if a.step_i >= len(m["steps"]):
            a.mission, a.mode, a.v, a.w = None, "charging" if a.charger else "idle", 0.0, 0.0
            self._amr_evt(a, "mission_done", m["mission_id"], None)
            self._amr_state(a, force=True)
        else:
            self._start_step(a)

    def _fail(self, a: Amr, reason: str) -> None:
        m = a.mission
        self._amr_evt(a, "mission_failed", m["mission_id"], m["steps"][a.step_i]["seq"], reason=reason)
        a.mission, a.mode = None, "idle"
        self._amr_state(a, force=True)

    def _amr_step(self, a: Amr, dt: float) -> None:
        a.v = a.w = 0.0 if a.mode != "moving" else a.v
        if a.charger and a.mode in ("idle", "charging"):
            a.battery = min(100.0, a.battery + CHARGE_RATE * dt)
        else:
            a.battery = max(0.0, a.battery - (DRAIN_MOVE if a.mode == "moving" else DRAIN_IDLE) * dt)
        if a.paused:
            a.v = a.w = 0.0
            self._amr_state(a)
            return
        if a.drive_mode == "rl" and a.vel_cmd:
            v, w, exp = a.vel_cmd
            if time.time() > exp:
                v = w = 0.0
            a.theta = (a.theta + w * dt + math.pi) % (2 * math.pi) - math.pi
            a.x = min(119.6, max(0.4, a.x + v * math.cos(a.theta) * dt))
            a.y = min(79.6, max(0.4, a.y + v * math.sin(a.theta) * dt))
            a.v, a.w = v, w
        if a.mission is None:
            self._amr_state(a)
            return
        s = a.mission["steps"][a.step_i]
        act = s["action"]
        if act == "move":
            if a.drive_mode == "rl":       # RL 모드: 위치는 cmd/vel 이 움직이고, 경유점 도달만 판정
                path = s["path"]
                wp = path[min(a.wp_i, len(path) - 1)]
                if math.dist((a.x, a.y), (wp["x"], wp["y"])) < 0.3:
                    a.node = wp["node"]
                    a.wp_i += 1
                    if a.wp_i >= len(path):
                        self._finish_step(a)
                return
            self._follow(a, s, dt)
        elif act in ("pick", "drop"):
            self._handle(a, s, act, dt)
        elif act == "charge":
            ch = s["stop"]
            if self.chargers.get(ch) not in (None, a.id):
                return self._fail(a, f"{ch} 사용 중")
            self.chargers[ch], a.charger = a.id, ch
            self._charger_state(ch)
            self._finish_step(a)
        elif act == "wait":
            if a.busy_until == 0.0:
                a.busy_until = self.t + (s.get("duration") or 0.0)
            if self.t >= a.busy_until:
                self._finish_step(a)
        self._amr_state(a)

    def _follow(self, a: Amr, s: dict, dt: float) -> None:
        path, budget = s["path"], AMR_V * dt
        while budget > 1e-9 and a.wp_i < len(path):
            wp = path[a.wp_i]
            if wp.get("t_start") is not None and self.t < wp["t_start"] and a.node != wp["node"]:
                a.v = 0.0     # 예약 시각 전: 노드 앞에서 대기
                return
            d = math.dist((a.x, a.y), (wp["x"], wp["y"]))
            if d > 1e-6:
                a.theta = math.atan2(wp["y"] - a.y, wp["x"] - a.x)
            if d <= budget:
                a.x, a.y, a.node = wp["x"], wp["y"], wp["node"]
                budget -= d
                a.wp_i += 1
            else:
                a.x += (wp["x"] - a.x) / d * budget
                a.y += (wp["y"] - a.y) / d * budget
                budget = 0.0
            a.v = AMR_V
        if a.wp_i >= len(path):
            if s.get("theta") is not None:
                a.theta = s["theta"]
            a.v = 0.0
            self._finish_step(a)

    def _handle(self, a: Amr, s: dict, act: str, dt: float) -> None:
        stop, pallet = s["stop"], s["pallet"]
        if a.busy_until == 0.0:                       # 전제 조건 확인
            if act == "pick":
                ok = self._pallet_at(stop) == pallet
            else:
                ok = self._space_at(stop) and a.pallet == pallet
            if not ok:
                a.waited += dt
                a.mode = "waiting"
                if a.waited > PRECOND_TIMEOUT:
                    self._fail(a, f"{stop} 전제 조건 시간 초과 ({act} {pallet})")
                return
            a.mode = "picking" if act == "pick" else "dropping"
            a.busy_until = self.t + AMR_HANDLE
            return
        if self.t < a.busy_until:
            return
        if act == "pick":
            self._take_from(stop)
            a.pallet, a.load_kg = pallet, self.weights.get(pallet)
        else:
            self._put_to(stop, pallet)
            a.pallet = a.load_kg = None
        self._finish_step(a)

    def _pallet_at(self, stop: str) -> str | None:
        if stop in self.buffers:
            return self.buffers[stop][4]
        q = self.stations.get(stop)
        return q[0] if q else None

    def _space_at(self, stop: str) -> bool:
        if stop in self.buffers:
            return self.buffers[stop][4] is None
        return True

    def _take_from(self, stop: str) -> None:
        if stop in self.buffers:
            self.buffers[stop][4] = None
            self._buffer_state(stop)
        else:
            self.stations[stop].popleft()

    def _put_to(self, stop: str, pallet: str) -> None:
        if stop in self.buffers:
            self.buffers[stop][4] = pallet
            self._buffer_state(stop)
        elif stop.startswith("DP-OUT"):
            dk = self.docks[stop[3:]]
            dk.done += 1
            self.c.send("dock.evt", {"event": "pallet_loaded", "truck": dk.truck or "?", "order_id": dk.order_id,
                                     "pallet": pallet, "station": stop}, dk.id, t=self.t)
            if dk.total and dk.done >= dk.total:
                dk.status, dk.depart_t = "departing", self.t + 30.0
            else:
                dk.status = "loading"
            self._dock_state(dk)
        else:
            self.stations[stop].append(pallet)

    # ── 컨베이어 · 도크 ────────────────────────────────────────────────────
    def _conveyor(self, b: str) -> None:
        if self.t < self.buf_move[b]:
            return
        s, moved = self.buffers[b], False
        if b.endswith("-IN"):        # 5번 → 1번
            for i in range(1, 5):
                if s[i] and s[i - 1] is None:
                    s[i - 1], s[i], moved = s[i], None, True
                    break
        else:                        # 1번 → 5번
            for i in range(3, -1, -1):
                if s[i] and s[i + 1] is None:
                    s[i + 1], s[i], moved = s[i], None, True
                    break
        if moved:
            self.buf_move[b] = self.t + CONVEYOR_S
            self._buffer_state(b)

    def _dock_step(self, dk: Dock) -> None:
        if dk.status == "departing" and dk.depart_t is not None and self.t >= dk.depart_t:
            self.c.send("dock.evt", {"event": "truck_departed", "truck": dk.truck or "?", "order_id": dk.order_id,
                                     "pallet": None, "station": None}, dk.id, t=self.t)
            self.docks[dk.id] = Dock(dk.id)
            self._dock_state(self.docks[dk.id])
            return
        if not dk.id.startswith("IN") or not dk.pending or self.t < dk.next_t:
            return
        st = f"SC-{dk.id}"
        if len(self.stations[st]) >= 28:          # 적치 28칸이 차면 하차를 멈춤
            return
        pallet, sku, decl, actual = dk.pending.popleft()
        self.weights[pallet] = actual
        self.stations[st].append(pallet)
        dk.done += 1
        dk.status = "unloading" if dk.pending else "departing"
        if not dk.pending:
            dk.depart_t = self.t + 30.0
        dk.next_t = self.t + UNLOAD_S
        self.c.send("dock.evt", {"event": "pallet_unloaded", "truck": dk.truck or "?", "order_id": dk.order_id,
                                 "pallet": pallet, "station": st}, dk.id, t=self.t)
        self.c.send("scale.reading", {"pallet": pallet, "sku": sku, "weight_kg": round(actual, 1),
                                      "declared_kg": decl, "stable": True}, st, t=self.t)
        self._dock_state(dk)

    # ── 리프트 ──────────────────────────────────────────────────────────────
    # ── 2D LiDAR (건물 벽만) ───────────────────────────────────────────────
    def scan2d(self, a: Amr, beams: int = 72, rmax: float = 10.0) -> dict:
        out = []
        for k in range(beams):
            ang = a.theta + 2 * math.pi * k / beams
            c, s = math.cos(ang), math.sin(ang)
            d = rmax
            if c > 1e-9:
                d = min(d, (120 - a.x) / c)
            elif c < -1e-9:
                d = min(d, -a.x / c)
            if s > 1e-9:
                d = min(d, (80 - a.y) / s)
            elif s < -1e-9:
                d = min(d, -a.y / s)
            out.append(round(max(0.0, d), 3))
        return {"angle_min": 0.0, "angle_inc": round(2 * math.pi / beams, 6), "range_max": rmax, "ranges": out}


def main() -> None:
    ap = argparse.ArgumentParser(description="가상 장비 (Unity 대역)")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--scale", type=float, default=10.0, help="배속")
    ap.add_argument("--scan", action="store_true", help="2D LiDAR 발행")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    p = Plant(args.host, args.port, scale=args.scale, scan=args.scan).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        p.stop()


if __name__ == "__main__":
    main()
