"""스케줄러 브리지: 적재·출고 알고리즘을 MQTT에 붙이는 쪽 (예제 구현).

    python -m sim.scheduler_bridge --host localhost

하는 일
1. 입고: scale/+/reading 을 받으면 팔레트 작업(job)을 만든다. 송장 무게 ±5% 초과 → 보류(QC-IN으로 운반).
   정상 → 통로(R1~R3) 선택 → AMR 미션(계량대 → 그 통로 입고 버퍼 R?-IN) → rack/R?/cmd store
   → 랙(Unity ShuttleRackSystem)이 채널·칸을 정해 넣고 rack/R?/evt done → 재고 stored.
2. 출고: wms/order(outbound) 를 받으면 SKU별로 꺼낼 통로를 고르고 rack/R?/cmd retrieve
   → 랙이 채널·맨 앞 팔레트를 정해 출고 버퍼 R?-OUT 으로 → AMR 미션(R?-OUT → 출고 하차대) → 재고 shipped.
   레인·칸 선택, 리프트·모셔틀 제어는 Unity 랙 몫이다 (2026-11-19 Unity 명세 5.1). 통로 간 배분은 이쪽(WMS) 몫.
3. AMR 배정: 가장 가까운 유휴 AMR. 경로는 nav 그래프 최단 경로, 노드별 시간 예약표로 겹치면 출발을 늦춘다.
   배터리 30% 미만이면 빈 충전기로 보낸다.
4. 상태 공유: wms/job, wms/inventory/evt, wms/inventory/snapshot, traffic/reservations, fleet/kpi.
5. 관제 명령(ctrl/cmd): pause_all, resume_all, request_report, release_hold.

통로 선택(입고)·통로 선택(출고)·AMR 배정은 Planner 클래스 하나에 모았다.
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
    rail: str | None = None
    channel: str | None = None
    slot: int | None = None
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
    """알고리즘 교체 지점. 기본 구현은 간단한 규칙.

    stock = {통로: {SKU: 꺼낼 수 있는 수(예약 반영)}} (Unity 랙의 rack/+/inventory 와 rack/+/evt 로 맞춘다).
    load = {통로: 진행 중 작업 수}."""

    def __init__(self, lanes: dict | None = None, skus_heavy: set[str] | None = None) -> None:
        pass

    def choose_rail(self, sku: str, weight: float, load: dict[str, int], stock: dict[str, dict[str, int]]) -> str:
        """입고 통로: 같은 SKU 가 이미 있는 통로 우선(채널 보충), 그다음 작업이 적은 통로."""
        return min(ids.RAILS, key=lambda r: (0 if stock.get(r, {}).get(sku) else 1, load.get(r, 0), r))

    def pick_rail(self, sku: str, load: dict[str, int], stock: dict[str, dict[str, int]]) -> str | None:
        """출고 통로: 그 SKU 재고가 있는 통로 중 작업이 적은 곳, 같으면 재고가 많은 곳."""
        cands = [r for r in ids.RAILS if stock.get(r, {}).get(sku, 0) > 0]
        if not cands:
            return None
        return min(cands, key=lambda r: (load.get(r, 0), -stock[r][sku], r))

    def choose_amr(self, amrs: list[AmrView], src_stop: str) -> AmrView | None:
        free = [a for a in amrs if a.mission is None and a.busy_job is None and a.battery >= BATTERY_LOW
                and a.mode in ("idle", "charging")]
        if not free:
            return None
        return min(free, key=lambda a: G.path_length(G.shortest_path(a.node, src_stop)) if a.node else 1e9)


class Scheduler:
    def __init__(self, host: str, port: int, *, validate: bool = False, seed: int = 0,
                 client_id: str = "scheduler", planner: Planner | None = None) -> None:
        self.c = WhClient(client_id, "scheduler", host, port, validate=validate, persistent=True)
        self.rng = random.Random(seed)
        self.lock = threading.RLock()
        self.jobs: dict[str, Job] = {}
        self.jobs_by_pallet: dict[str, Job] = {}
        self.amrs = {a: AmrView(a) for a in ids.AMRS}
        self.missions: dict[str, tuple[str, str]] = {}      # mission_id → (amr, job_id | "charge")
        self.rack_cmds: dict[str, str] = {}                  # rack.cmd cmd_id → job_id
        self.reservations: list[dict] = []
        self.chargers: dict[str, str | None] = {c: None for c in ids.CHARGERS}
        self.stock: dict[str, dict[str, int]] = {r: {} for r in ids.RAILS}        # 통로 → SKU → 수 (예약 반영)
        self.channels: dict[str, list] = {r: [] for r in ids.RAILS}            # 통로 → [[채널, SKU, 수, 가상 수]]
        self.planner = planner or Planner()
        self.held: list[str] = []
        self.paused = False
        self._ids = itertools.count(1)
        self._evt_ids = itertools.count(1)
        self.done_log: list[Job] = []
        c = self.c
        c.on("scale.reading", self._on_scale)
        c.on("wms.order", self._on_order)
        c.on("amr.state", self._on_amr_state)
        c.on("amr.pose", self._on_amr_pose)
        c.on("amr.evt", self._on_amr_evt)
        c.on("rack.evt", self._on_rack_evt)
        c.on("rack.inventory", self._on_rack_inventory)
        c.on("charger.state", self._on_charger)
        c.on("ctrl.cmd", self._on_ctrl)
        c.on("sys.online", self._on_online)
        self._stop = threading.Event()

    def now(self) -> float:
        return self.c.now() or 0.0

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
                j.rail = self.planner.choose_rail(j.sku, j.weight or 0, self._rail_load(), self.stock)
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
                    rail = self.planner.pick_rail(ln["sku"], self._rail_load(), self.stock)
                    if not rail:
                        j.status, j.reason = "failed", "재고 없음"
                        self._job_msg(j)
                        continue
                    j.rail = rail
                    self.stock[rail][j.sku] -= 1                    # 랙이 수락하기 전까지 임시로 빼 둔다
                    j.stage, j.status = "retrieve", "in_rack"
                    self._rack_cmd(j, "retrieve")
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
                    j.stage, j.status = "store", "in_rack"          # 팔레트가 IN5 에 놓임 (= AcceptInbound)
                    self._rack_cmd(j, "store")
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

    def _on_rack_inventory(self, env: Envelope, p: dict) -> None:
        """Unity 랙의 채널별 재고(가상 수량 포함)로 통로별 SKU 재고를 맞춘다."""
        with self.lock:
            st: dict[str, int] = defaultdict(int)
            for ch, sku, _n, virtual in env.data["channels"]:
                st[sku] += virtual
            self.stock[p["rail"]] = dict(st)
            self.channels[p["rail"]] = env.data["channels"]

    def _on_rack_evt(self, env: Envelope, p: dict) -> None:
        d = env.data
        with self.lock:
            job_id = self.rack_cmds.get(d["cmd_id"])
            if not job_id or job_id not in self.jobs:
                return
            j = self.jobs[job_id]
            ev, rail = d["event"], p["rail"]
            if ev == "accepted":
                j.channel = d["channel"]
                if d["action"] == "store":
                    self.stock[rail][j.sku] = self.stock[rail].get(j.sku, 0) + 1
            elif ev == "rejected" or ev == "failed":
                if d["action"] == "retrieve" and ev == "rejected":
                    self.stock[rail][j.sku] = self.stock[rail].get(j.sku, 0) + 1
                j.status, j.reason = "failed", f"랙 {rail}: {d['reason']}"
            elif ev == "started":
                if d["action"] == "store":
                    j.stage = "storing"                              # 리프트가 IN1 에서 가져감
            elif ev == "done":
                j.slot = d["slot"]
                if d["action"] == "store":
                    j.status, j.stage, j.done_t = "done", "done", self.now()
                    self._inv("stored", j)
                    self.done_log.append(j)
                else:
                    j.pallet = d["pallet"]
                    self.jobs_by_pallet[j.pallet] = j
                    self._inv("retrieved", j)
                    j.stage, j.status = "at_buffer", "waiting"      # OUT1 에 놓임 → 컨베이어로 OUT5
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
                    j.rail = self.planner.choose_rail(j.sku, j.weight or 0, self._rail_load(), self.stock)
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
    def _rail_load(self) -> dict[str, int]:
        load: dict[str, int] = defaultdict(int)
        for j in self.jobs.values():
            if j.rail and j.status not in ("done", "failed", "held"):
                load[j.rail] += 1
        return load

    def _buffer_count(self, rail: str) -> int:
        """입고 버퍼 5칸에 있거나 가는 중인 팔레트 수 (리프트가 가져가기 전까지)."""
        return sum(1 for j in self.jobs.values() if j.rail == rail and j.stage in ("to_lift", "store"))

    def dispatch(self) -> None:
        # 1) 출고 버퍼에 나온 팔레트를 먼저 (리프트를 막지 않게)
        order = sorted(self.jobs.values(), key=lambda j: (j.stage != "at_buffer", j.created))
        for j in order:
            if j.stage == "at_buffer":
                self._send_amr(j, f"{j.rail}-OUT", f"DP-{j.dock}", "to_dock")
            elif j.stage == "at_scale" and j.status == "waiting":
                if self._buffer_count(j.rail) >= 5:
                    continue
                self._send_amr(j, f"SC-{j.dock}", f"{j.rail}-IN", "to_lift")
            elif j.stage == "to_qc" and j.amr is None:
                self._send_amr(j, f"SC-{j.dock}", "QC-IN", "to_qc")
            elif j.stage == "at_qc_ready":
                if self._buffer_count(j.rail) >= 5:
                    continue
                self._send_amr(j, "QC-IN", f"{j.rail}-IN", "to_lift")
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
    def _rack_cmd(self, j: Job, action: str) -> None:
        cid = f"C{next(self._ids):05d}"
        self.rack_cmds[cid] = j.job_id
        self.c.send("rack.cmd", {"cmd_id": cid, "job_id": j.job_id, "action": action,
                                 "pallet": j.pallet if action == "store" else None, "sku": j.sku,
                                 "weight_kg": j.weight if action == "store" else None}, j.rail)

    def _new_job(self, kind: str, sku: str, pallet: str | None, **kw) -> Job:
        j = Job(f"J{next(self._ids):05d}", kind, sku, pallet, self.now(), **kw)
        self.jobs[j.job_id] = j
        if pallet:
            self.jobs_by_pallet[pallet] = j
        return j

    def _job_msg(self, j: Job) -> None:
        self.c.send("wms.job", {"job_id": j.job_id, "order_id": j.order_id, "kind": j.kind, "status": j.status,
                                "pallet": j.pallet, "sku": j.sku, "dock": j.dock, "rail": j.rail,
                                "channel": j.channel, "slot": j.slot, "amr": j.amr, "eta": j.eta,
                                "created": round(j.created, 2), "reason": j.reason})

    def _inv(self, event: str, j: Job, weight: float | None = None) -> None:
        self.c.send("wms.inventory", {"event_id": f"E{next(self._evt_ids):06d}", "event": event, "pallet": j.pallet,
                                      "sku": j.sku, "channel": j.channel if event in ("stored", "retrieved") else None,
                                      "slot": j.slot if event in ("stored", "retrieved") else None,
                                      "weight_kg": weight, "job_id": j.job_id})

    def publish_snapshot(self) -> None:
        chs = sorted([ch, sku, n] for r in ids.RAILS for ch, sku, n, _v in self.channels[r] if n)
        self.c.send("wms.snapshot", {"channels": chs, "pallets": sum(x[2] for x in chs),
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
