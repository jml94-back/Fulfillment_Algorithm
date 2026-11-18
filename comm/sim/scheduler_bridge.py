"""스케줄러 브리지: 적재·출고 알고리즘을 MQTT에 붙이는 쪽 (예제 구현).

    python -m sim.scheduler_bridge --host localhost

하는 일
1. 입고: scale/+/reading 을 받으면 팔레트 작업(job)을 만든다. 송장 무게 ±5% 초과 → 보류(QC-IN으로 운반).
   정상 → 레인 배정 → AMR 미션(계량대 → 리프트 입고 버퍼) → lift_in → 셔틀 store → 재고 stored.
2. 출고: wms/order(outbound) 를 받으면 SKU별로 꺼낼 레인을 고르고 셔틀 retrieve → lift_out
   → AMR 미션(리프트 출고 버퍼 → 출고 하차대) → 재고 shipped.
3. AMR 배정: 가장 가까운 유휴 AMR. 경로는 nav 그래프 최단 경로, 노드별 시간 예약표로 겹치면 출발을 늦춘다.
   배터리 30% 미만이면 빈 충전기로 보낸다.
4. 상태 공유: wms/job, wms/inventory/evt, wms/inventory/snapshot, traffic/reservations, fleet/kpi.
5. 관제 명령(ctrl/cmd): pause_all, resume_all, request_report, release_hold.

레인 배정·출고 레인 선택·AMR 배정은 Planner 클래스 하나에 모았다.
알고리즘 스레드의 D4 구현(warehouse/d4)이 나오면 Planner 를 그쪽 함수로 바꾸면 MQTT 쪽은 그대로 쓴다.
"""
from __future__ import annotations

import argparse
import itertools
import logging
import math
import random
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field

from whmqtt import geometry as G
from whmqtt import ids
from whmqtt.client import WhClient
from whmqtt.envelope import Envelope

AMR_V, AMR_HANDLE = 1.2, 8.0
WEIGHT_TOL = 0.05
BATTERY_LOW = 30.0
RES_MARGIN = 2.0          # 노드 예약 앞뒤 여유 (초)
log = logging.getLogger("scheduler")


@dataclass
class Job:
    job_id: str
    kind: str
    sku: str
    pallet: str | None
    created: float
    order_id: str | None = None
    dock: str | None = None
    status: str = "planned"
    lane: str | None = None
    pos: int | None = None
    lift: str | None = None
    shuttle: str | None = None
    amr: str | None = None
    weight: float | None = None
    eta: float | None = None
    reason: str | None = None
    done_t: float | None = None
    stage: str = "new"


@dataclass
class AmrView:
    id: str
    node: str = ""
    x: float = 0.0
    y: float = 0.0
    battery: float = 100.0
    mode: str = "idle"
    mission: str | None = None
    busy_job: str | None = None
    busy_since: float = 0.0
    busy_total: float = 0.0
    charging: bool = False
    drive_mode: str = "scripted"


class Planner:
    """알고리즘 교체 지점. 기본 구현은 간단한 규칙."""

    def __init__(self, lanes: dict[str, list], skus_heavy: set[str] | None = None) -> None:
        self.lanes = lanes              # 레인 → [(pallet, sku) | None] * 14 (index 0 = pos 1)
        self.reserved_in: dict[str, int] = defaultdict(int)
        self.reserved_out: dict[str, int] = defaultdict(int)
        self.reserved_sku: dict[str, str] = {}      # 들어올 예정 팔레트의 SKU (아직 비어 있는 레인도 SKU 전용)

    def lane_sku(self, lane: str) -> str | None:
        for s in self.lanes.get(lane, []):
            if s:
                return s[1]
        return self.reserved_sku.get(lane) if self.reserved_in.get(lane) else None

    def count(self, lane: str) -> int:
        return sum(1 for s in self.lanes.get(lane, []) if s)

    def assign_lane(self, sku: str, weight: float, lift_load: dict[str, int]) -> tuple[str, int]:
        """같은 SKU 레인에 빈 자리가 있으면 거기, 없으면 빈 레인 중 리프트에 가깝고(줄 번호 작음) 무거우면 아래층.
        레인은 안쪽(14)부터 채운다: 다음 위치 = 14 - (현재 수 + 들어올 예정 수)."""
        best = None
        for lane, slots in self.lanes.items():
            n = self.count(lane) + self.reserved_in[lane]
            if self.lane_sku(lane) == sku and n < ids.DEPTH and self.reserved_out[lane] == 0:
                r, _, lv, row = ids.parse_lane(lane)
                score = (0, row + lv, lift_load.get(f"L{r}", 0))
                if best is None or score < best[0]:
                    best = (score, lane, ids.DEPTH - n)
        if best:
            self.reserved_in[best[1]] += 1
            self.reserved_sku[best[1]] = sku
            return best[1], best[2]
        for r, side, lv, row in itertools.product(range(1, 4), "WE", range(1, 11), range(1, ids.ROWS + 1)):
            lane = ids.lane_id(r, side, lv, row)
            if self.count(lane) or self.reserved_in.get(lane):
                continue
            lv_cost = lv * (2.0 if weight > 800 else 0.5)
            score = (1, row + lv_cost + 3 * lift_load.get(f"L{r}", 0), r)
            if best is None or score < best[0]:
                best = (score, lane, ids.DEPTH)
        if not best:
            raise RuntimeError("빈 레인 없음")
        self.reserved_in[best[1]] += 1
        self.reserved_sku[best[1]] = sku
        self.lanes.setdefault(best[1], [None] * ids.DEPTH)
        return best[1], best[2]

    def pick_lane(self, sku: str, lift_load: dict[str, int]) -> tuple[str, int, str] | None:
        """출고: 그 SKU가 있는 레인 중 남은 수가 가장 적은 레인(레인을 빨리 비움)의 맨 앞 팔레트."""
        cands = []
        for lane, slots in self.lanes.items():
            if self.lane_sku(lane) != sku or self.reserved_in[lane]:
                continue
            avail = self.count(lane) - self.reserved_out[lane]
            if avail <= 0:
                continue
            r = ids.parse_lane(lane)[0]
            cands.append(((avail, lift_load.get(f"L{r}", 0)), lane))
        if not cands:
            return None
        lane = min(cands)[1]
        occupied = [i for i, s in enumerate(self.lanes[lane]) if s]
        idx = occupied[self.reserved_out[lane]]           # 앞에서부터 차례로
        self.reserved_out[lane] += 1
        return lane, idx + 1, self.lanes[lane][idx][0]

    def choose_amr(self, amrs: list[AmrView], src_stop: str) -> AmrView | None:
        free = [a for a in amrs if a.mission is None and a.busy_job is None and a.battery >= BATTERY_LOW
                and a.mode in ("idle", "charging")]
        if not free:
            return None
        return min(free, key=lambda a: G.path_length(G.shortest_path(a.node, src_stop)) if a.node else 1e9)


class Scheduler:
    def __init__(self, host: str, port: int, *, validate: bool = False, seed: int = 0, initial_fill: int = 40,
                 client_id: str = "scheduler", planner: Planner | None = None) -> None:
        self.c = WhClient(client_id, "scheduler", host, port, validate=validate, persistent=True)
        self.rng = random.Random(seed)
        self.lock = threading.RLock()
        self.jobs: dict[str, Job] = {}
        self.jobs_by_pallet: dict[str, Job] = {}
        self.amrs = {a: AmrView(a) for a in ids.AMRS}
        self.missions: dict[str, tuple[str, str]] = {}      # mission_id → (amr, job_id | "charge")
        self.dev_cmds: dict[str, str] = {}                   # cmd_id → job_id
        self.reservations: list[dict] = []
        self.chargers: dict[str, str | None] = {c: None for c in ids.CHARGERS}
        self.lane_pallets: dict[str, list] = {}
        self.planner = planner or Planner(self.lane_pallets)
        self.planner.lanes = self.lane_pallets
        self.held: list[str] = []
        self.paused = False
        self._ids = itertools.count(1)
        self._evt_ids = itertools.count(1)
        self.done_log: list[Job] = []
        self._seed_inventory(initial_fill)
        c = self.c
        c.on("scale.reading", self._on_scale)
        c.on("wms.order", self._on_order)
        c.on("amr.state", self._on_amr_state)
        c.on("amr.pose", self._on_amr_pose)
        c.on("amr.evt", self._on_amr_evt)
        c.on("lift.evt", self._on_dev_evt)
        c.on("shuttle.evt", self._on_dev_evt)
        c.on("charger.state", self._on_charger)
        c.on("ctrl.cmd", self._on_ctrl)
        c.on("sys.online", self._on_online)
        self._stop = threading.Event()

    def now(self) -> float:
        return self.c.now() or 0.0

    def _seed_inventory(self, n_lanes: int) -> None:
        """시작 재고 (시뮬레이션용): SKU 20종을 n_lanes 개 레인에 4~14개씩."""
        n = itertools.count(90_000_000)
        for k in range(n_lanes):
            r, side, lv, row = self.rng.randint(1, 3), self.rng.choice("WE"), self.rng.randint(1, 10), \
                self.rng.randint(1, ids.ROWS)
            lane = ids.lane_id(r, side, lv, row)
            if lane in self.lane_pallets:
                continue
            sku = f"SKU{self.rng.randint(1, 20):03d}"
            cnt = self.rng.randint(4, ids.DEPTH)
            slots = [None] * ids.DEPTH
            for i in range(cnt):
                slots[ids.DEPTH - 1 - i] = (ids.pallet_id(next(n)), sku)
            self.lane_pallets[lane] = slots

    # ── 시작 ────────────────────────────────────────────────────────────────
    def start(self) -> "Scheduler":
        self.c.start()
        self.publish_snapshot()
        self._th = threading.Thread(target=self._loop, daemon=True, name="scheduler")
        self._th.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._th.join(5)
        self.c.stop()

    def _loop(self) -> None:
        last_kpi = last_snap = 0.0
        while not self._stop.is_set():
            with self.lock:
                if not self.paused:
                    self.dispatch()
                w = time.time()
                if w - last_kpi > 2.0:
                    self.publish_kpi()
                    last_kpi = w
                if w - last_snap > 60.0 / max(1.0, self.c.sim_scale):
                    self.publish_snapshot()
                    last_snap = w
            time.sleep(0.05)

    # ── 수신 ────────────────────────────────────────────────────────────────
    def _on_amr_state(self, env: Envelope, p: dict) -> None:
        with self.lock:
            a = self.amrs[p["amr"]]
            d = env.data
            a.mode, a.battery, a.node, a.mission = d["mode"], d["battery"], d["node"] or a.node, d["mission_id"]
            a.drive_mode = d["drive_mode"]

    def _on_amr_pose(self, env: Envelope, p: dict) -> None:
        a = self.amrs.get(p["amr"])
        if a:
            a.x, a.y, a.battery = env.data["x"], env.data["y"], env.data["battery"]
            if env.data.get("node"):
                a.node = env.data["node"]

    def _on_charger(self, env: Envelope, p: dict) -> None:
        with self.lock:
            self.chargers[p["charger"]] = env.data["occupied_by"]

    def _on_scale(self, env: Envelope, p: dict) -> None:
        d = env.data
        with self.lock:
            if d["pallet"] in self.jobs_by_pallet:
                return
            j = self._new_job("inbound", d["sku"], d["pallet"], dock=p["scale"][3:])
            j.weight = d["weight_kg"]
            j.stage = "at_scale"
            self._inv("received", j, weight=d["weight_kg"])
            decl = d["declared_kg"]
            if decl and abs(d["weight_kg"] - decl) / decl > WEIGHT_TOL:
                j.status, j.stage = "held", "to_qc"
                j.reason = f"무게 불일치: 실측 {d['weight_kg']:.0f} kg / 송장 {decl:.0f} kg"
                self._inv("held", j, weight=d["weight_kg"])
            else:
                lift_load = self._lift_load()
                j.lane, j.pos = self.planner.assign_lane(j.sku, j.weight or 0, lift_load)
                j.lift, j.shuttle = ids.lift_for_lane(j.lane), ids.shuttle_for_lane(j.lane)
                j.status = "waiting"
            self._job_msg(j)

    def _on_order(self, env: Envelope, _p) -> None:
        o = env.data
        if o["kind"] != "outbound":
            return               # 입고는 계량 시점에 작업이 생긴다
        with self.lock:
            for ln in o["lines"]:
                for _ in range(ln["qty"]):
                    j = self._new_job("outbound", ln["sku"], None, order_id=o["order_id"], dock=o["dock"])
                    got = self.planner.pick_lane(ln["sku"], self._lift_load())
                    if not got:
                        j.status, j.reason = "failed", "재고 없음"
                        self._job_msg(j)
                        continue
                    j.lane, j.pos, j.pallet = got
                    j.lift, j.shuttle = ids.lift_for_lane(j.lane), ids.shuttle_for_lane(j.lane)
                    self.jobs_by_pallet[j.pallet] = j
                    j.stage, j.status = "retrieve", "shuttling"
                    self._dev_cmd("shuttle.cmd", j.shuttle, j, "retrieve", lane=j.lane, pos=j.pos)
                    self._job_msg(j)

    def _on_amr_evt(self, env: Envelope, p: dict) -> None:
        d = env.data
        with self.lock:
            mid = d["mission_id"]
            if mid not in self.missions:
                return
            amr, job_id = self.missions[mid]
            a = self.amrs[amr]
            ev = d["event"]
            if ev in ("mission_done", "mission_failed", "mission_rejected", "mission_canceled"):
                self.missions.pop(mid, None)
                a.busy_total += self.now() - a.busy_since
                a.busy_job, a.mission = None, None
                self.reservations = [r for r in self.reservations if r["mission_id"] != mid]
                self._publish_reservations()
            if job_id == "charge" or job_id not in self.jobs:
                return
            j = self.jobs[job_id]
            if ev == "mission_done":
                if j.stage == "to_lift":
                    j.stage, j.status = "lift_in", "lifting"
                    self._dev_cmd("lift.cmd", j.lift, j, "lift_in", level=ids.parse_lane(j.lane)[2])
                elif j.stage == "to_dock":
                    j.status, j.stage, j.done_t = "done", "done", self.now()
                    self._inv("shipped", j)
                    self.done_log.append(j)
                elif j.stage == "to_qc":
                    j.stage = "at_qc"
                    self.held.append(j.job_id)
                self._job_msg(j)
            elif ev in ("mission_failed", "mission_rejected"):
                j.reason = d.get("reason")
                j.stage = {"to_lift": "at_scale", "to_dock": "at_buffer", "to_qc": "to_qc"}.get(j.stage, j.stage)
                j.status = "held" if j.stage == "to_qc" else "waiting"
                j.amr = None
                self._job_msg(j)

    def _on_dev_evt(self, env: Envelope, p: dict) -> None:
        d = env.data
        with self.lock:
            job_id = self.dev_cmds.get(d["cmd_id"])
            if not job_id or job_id not in self.jobs:
                return
            j = self.jobs[job_id]
            if d["event"] == "failed":
                j.status, j.reason = "failed", d["reason"]
                self._job_msg(j)
                return
            if d["event"] != "done":
                return
            if j.stage == "lift_in":
                j.stage, j.status = "store", "shuttling"
                self._dev_cmd("shuttle.cmd", j.shuttle, j, "store", lane=j.lane, pos=j.pos)
            elif j.stage == "store":
                slots = self.lane_pallets.setdefault(j.lane, [None] * ids.DEPTH)
                slots[j.pos - 1] = (j.pallet, j.sku)
                self.planner.reserved_in[j.lane] -= 1
                j.status, j.stage, j.done_t = "done", "done", self.now()
                self._inv("stored", j)
                self.done_log.append(j)
            elif j.stage == "retrieve":
                self.lane_pallets[j.lane][j.pos - 1] = None
                self.planner.reserved_out[j.lane] -= 1
                self._inv("retrieved", j)
                j.stage, j.status = "lift_out", "lifting"
                self._dev_cmd("lift.cmd", j.lift, j, "lift_out", level=ids.parse_lane(j.lane)[2])
            elif j.stage == "lift_out":
                j.stage, j.status = "at_buffer", "waiting"
            self._job_msg(j)

    def _on_online(self, env: Envelope, p: dict) -> None:
        """RL 정책이 끊기면(Last Will) rl 모드 AMR 을 scripted 로 돌려 미션을 Unity 경로 추종으로 계속한다."""
        if env.data["role"] != "rl" or env.data["online"]:
            return
        with self.lock:
            for a in self.amrs.values():
                if a.drive_mode == "rl":
                    self.c.send("amr.cmd.control", {"action": "set_drive_mode", "drive_mode": "scripted",
                                                    "charger": None}, a.id)
            self.c.send("ctrl.evt", {"event": "warning", "text": f"RL 정책 {p['client']} 끊김: scripted 주행으로 전환",
                                     "data": {"client": p["client"]}})

    def _on_ctrl(self, env: Envelope, _p) -> None:
        d = env.data
        act = d["action"]
        with self.lock:
            if act in ("pause_all", "resume_all"):
                self.paused = act == "pause_all"
                for a in ids.AMRS:
                    self.c.send("amr.cmd.control", {"action": "pause" if self.paused else "resume",
                                                    "drive_mode": None, "charger": None}, a)
                self.c.send("ctrl.evt", {"event": "ack", "text": f"{act} 적용", "data": {}}, corr=env.id)
            elif act == "request_report":
                self.c.send("ctrl.evt", {"event": "report", "text": self.report_text(), "data": self.summary()},
                            corr=env.id)
            elif act == "release_hold":
                jid = d["args"].get("job_id")
                j = self.jobs.get(jid)
                if j and j.stage == "at_qc":
                    self.held.remove(jid)
                    j.lane, j.pos = self.planner.assign_lane(j.sku, j.weight or 0, self._lift_load())
                    j.lift, j.shuttle = ids.lift_for_lane(j.lane), ids.shuttle_for_lane(j.lane)
                    j.stage, j.status, j.reason = "at_qc_ready", "waiting", None
                    self._inv("released", j, weight=j.weight)
                    self._job_msg(j)
                    self.c.send("ctrl.evt", {"event": "ack", "text": f"{jid} 보류 해제", "data": {}}, corr=env.id)
                else:
                    self.c.send("ctrl.evt", {"event": "error", "text": f"보류 중인 작업 아님: {jid}", "data": {}},
                                corr=env.id)
            else:
                self.c.send("ctrl.evt", {"event": "error", "text": f"지원하지 않는 명령: {act}", "data": {}},
                            corr=env.id)

    # ── 배차 ────────────────────────────────────────────────────────────────
    def _lift_load(self) -> dict[str, int]:
        load: dict[str, int] = defaultdict(int)
        for j in self.jobs.values():
            if j.lift and j.status not in ("done", "failed", "held"):
                load[j.lift] += 1
        return load

    def _buffer_count(self, lift: str) -> int:
        return sum(1 for j in self.jobs.values() if j.lift == lift and j.stage in ("to_lift", "lift_in"))

    def dispatch(self) -> None:
        # 1) 출고 버퍼에 나온 팔레트를 먼저 (리프트를 막지 않게)
        order = sorted(self.jobs.values(), key=lambda j: (j.stage != "at_buffer", j.created))
        for j in order:
            if j.stage == "at_buffer":
                self._send_amr(j, f"{j.lift}-OUT", f"DP-{j.dock}", "to_dock")
            elif j.stage == "at_scale" and j.status == "waiting":
                if self._buffer_count(j.lift) >= 5:
                    continue
                self._send_amr(j, f"SC-{j.dock}", f"{j.lift}-IN", "to_lift")
            elif j.stage == "to_qc" and j.amr is None:
                self._send_amr(j, f"SC-{j.dock}", "QC-IN", "to_qc")
            elif j.stage == "at_qc_ready":
                if self._buffer_count(j.lift) >= 5:
                    continue
                self._send_amr(j, "QC-IN", f"{j.lift}-IN", "to_lift")
        # 2) 배터리 부족 AMR은 충전
        for a in self.amrs.values():
            if a.mission is None and a.busy_job is None and a.battery < BATTERY_LOW and a.mode == "idle":
                free = [c for c, occ in self.chargers.items() if occ in (None, a.id)]
                if free:
                    ch = min(free, key=lambda c: G.path_length(G.shortest_path(a.node, c)))
                    self.chargers[ch] = a.id
                    self._mission(a, None, [("move", ch, None), ("charge", ch, None)], "charge")

    def _send_amr(self, j: Job, src: str, dst: str, stage: str) -> None:
        a = self.planner.choose_amr(list(self.amrs.values()), src)
        if a is None:
            return
        j.amr, j.stage = a.id, stage
        if j.status not in ("held",):
            j.status = "amr_assigned"
        self._mission(a, j, [("move", src, None), ("pick", src, j.pallet), ("move", dst, None),
                             ("drop", dst, j.pallet)], j.job_id)
        self._job_msg(j)

    def _mission(self, a: AmrView, j: Job | None, plan: list[tuple[str, str, str | None]], tag: str) -> None:
        """미션 단계를 만들고 nav 노드 시간 예약을 잡는다. 다른 AMR 예약과 겹치면 출발을 2초씩 늦춘다."""
        mid = f"M{next(self._ids):05d}"
        t0 = self.now() + 1.0
        nodes_xy = G.nav()[0]
        for delay in range(0, 300, 2):
            steps, items, t, cur = [], [], t0 + delay, a.node
            ok = True
            for seq, (act, stop, pallet) in enumerate(plan):
                path = []
                if act == "move":
                    route = G.shortest_path(cur, stop) if cur else [G.STOP_NODES[stop]]
                    prev = None
                    for n in route:
                        if prev is not None:
                            t += math.dist(nodes_xy[prev], nodes_xy[n]) / AMR_V
                        ts, te = round(t - RES_MARGIN, 2), round(t + RES_MARGIN, 2)
                        if self._conflict(n, ts, te, a.id):
                            ok = False
                            break
                        path.append({"node": n, "x": nodes_xy[n][0], "y": nodes_xy[n][1],
                                     "t_start": ts if prev is not None else None, "t_end": te})
                        items.append({"amr": a.id, "mission_id": mid, "node": n, "t_start": ts, "t_end": te})
                        prev = n
                    cur = route[-1]
                    if not ok:
                        break
                elif act in ("pick", "drop"):
                    t += AMR_HANDLE
                    items[-1]["t_end"] = round(t + RES_MARGIN, 2)      # 상·하역 동안 그 노드를 점유
                steps.append({"seq": seq, "action": act, "stop": stop, "pallet": pallet, "path": path,
                              "theta": round(G.stop_theta(stop), 4) if act == "move" else None,
                              "duration": AMR_HANDLE if act in ("pick", "drop") else None,
                              "deadline": round(t + 60, 1) if act != "charge" else None})
            if ok:
                break
        else:
            log.warning("예약 실패: %s", mid)
            return
        if j:
            j.eta = round(t, 1)
        self.reservations += items
        self.missions[mid] = (a.id, tag)
        a.busy_job, a.mission, a.busy_since = tag, mid, self.now()
        self.c.send("amr.cmd.mission", {"mission_id": mid, "job_id": j.job_id if j else None, "priority": 5,
                                        "steps": steps}, a.id)
        self._publish_reservations()

    def _conflict(self, node: str, ts: float, te: float, amr: str) -> bool:
        if node.startswith("n_CH"):     # 충전기 칸은 배정으로 관리
            return False
        return any(r["node"] == node and r["amr"] != amr and r["t_start"] < te and ts < r["t_end"]
                   for r in self.reservations)

    def _publish_reservations(self) -> None:
        now = self.now()
        self.reservations = [r for r in self.reservations if r["t_end"] >= now - 5]
        self.c.send("traffic.reservations", {"horizon": round(max([r["t_end"] for r in self.reservations],
                                                                  default=now), 1),
                                             "items": self.reservations[:500]})

    # ── 장비 명령 · 발행 ───────────────────────────────────────────────────
    def _dev_cmd(self, key: str, dev: str, j: Job, action: str, **kw) -> None:
        cid = f"C{next(self._ids):05d}"
        self.dev_cmds[cid] = j.job_id
        data = {"cmd_id": cid, "job_id": j.job_id, "action": action, "pallet": j.pallet}
        if key == "shuttle.cmd":
            data.update({"lane": kw["lane"], "pos": kw["pos"], "to_lane": None, "to_pos": None})
        else:
            data["level"] = kw["level"]
        self.c.send(key, data, dev)

    def _new_job(self, kind: str, sku: str, pallet: str | None, **kw) -> Job:
        j = Job(f"J{next(self._ids):05d}", kind, sku, pallet, self.now(), **kw)
        self.jobs[j.job_id] = j
        if pallet:
            self.jobs_by_pallet[pallet] = j
        return j

    def _job_msg(self, j: Job) -> None:
        self.c.send("wms.job", {"job_id": j.job_id, "order_id": j.order_id, "kind": j.kind, "status": j.status,
                                "pallet": j.pallet, "sku": j.sku, "dock": j.dock, "lane": j.lane, "pos": j.pos,
                                "lift": j.lift, "shuttle": j.shuttle, "amr": j.amr, "eta": j.eta,
                                "created": round(j.created, 2), "reason": j.reason})

    def _inv(self, event: str, j: Job, weight: float | None = None) -> None:
        self.c.send("wms.inventory", {"event_id": f"E{next(self._evt_ids):06d}", "event": event, "pallet": j.pallet,
                                      "sku": j.sku, "lane": j.lane if event in ("stored", "retrieved") else None,
                                      "pos": j.pos if event in ("stored", "retrieved") else None,
                                      "weight_kg": weight, "job_id": j.job_id})

    def publish_snapshot(self) -> None:
        lanes = [[ln, self.planner.lane_sku(ln), self.planner.count(ln)] for ln in sorted(self.lane_pallets)
                 if self.planner.count(ln)]
        self.c.send("wms.snapshot", {"lanes": lanes, "pallets": sum(x[2] for x in lanes),
                                     "capacity": 1980 * ids.DEPTH, "held": len(self.held)})

    def summary(self) -> dict:
        done = [j for j in self.done_log]
        leads = [j.done_t - j.created for j in done if j.done_t is not None]
        return {"jobs_done": len(done), "jobs_open": sum(1 for j in self.jobs.values()
                                                         if j.status not in ("done", "failed")),
                "held": len(self.held), "failed": sum(1 for j in self.jobs.values() if j.status == "failed"),
                "mean_lead_s": round(sum(leads) / len(leads), 1) if leads else None}

    def report_text(self) -> str:
        s = self.summary()
        return (f"완료 {s['jobs_done']}건, 진행 {s['jobs_open']}건, 보류 {s['held']}건, 실패 {s['failed']}건, "
                f"평균 리드타임 {s['mean_lead_s']}초")

    def publish_kpi(self) -> None:
        now = self.now()
        win = 600.0
        recent = [j for j in self.done_log if j.done_t and j.done_t >= now - win]
        s = self.summary()
        util = {}
        for a in self.amrs.values():
            busy = a.busy_total + ((now - a.busy_since) if a.busy_job else 0.0)
            util[a.id] = round(min(1.0, busy / now), 3) if now > 0 else 0.0
        self.c.send("fleet.kpi", {"window_s": win,
                                  "inbound_ph": round(sum(j.kind == "inbound" for j in recent) * 3600 / win, 1),
                                  "outbound_ph": round(sum(j.kind == "outbound" for j in recent) * 3600 / win, 1),
                                  "amr_util": util, "jobs_open": s["jobs_open"], "jobs_done": s["jobs_done"],
                                  "mean_lead_s": s["mean_lead_s"]})


def main() -> None:
    ap = argparse.ArgumentParser(description="스케줄러 브리지 (적재·출고 알고리즘 ↔ MQTT)")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    s = Scheduler(args.host, args.port).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        s.stop()


if __name__ == "__main__":
    main()
