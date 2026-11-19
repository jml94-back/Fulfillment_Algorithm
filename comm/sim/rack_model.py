"""Unity ShuttleRackSystem 대역: 통로(레일) 1개의 랙·리프트·모셔틀·새틀라이트·버퍼 규칙.

Unity 팀원 명세(2026-11-19 "물류창고 시뮬레이션 시스템 정리" 4·5장)의 규칙과 속도를 그대로 옮겼다.
가상 장비(virtual_plant.py)가 통로마다 하나씩 만든다. Unity 에서는 ShuttleRackSystem 이 이 일을 한다.

규칙 (명세 5.1)
- 레인(채널 = 면·줄·단) 하나에 SKU 하나, 후입선출.
- 입고 요청(RequestStore): 같은 SKU 채널(빈칸 있음) → 없으면 빈 채널. 그 안에서 비용이 가장 낮은 곳.
  비용 = 리프트 승강 시간 + 모셔틀 주행 시간 + 그 단 대기 작업 x 20 s + 빈칸 수 x 0.1
- 예약은 요청 시점에 virtual 수량으로 (입고 +1, 출고 -1). 실제 칸은 실행 시점: 입고 = 가장 안쪽 빈칸, 출고 = 맨 앞.
  채널이 비고 예약도 0이 되면 SKU 배정 해제.
- 같은 단 작업은 등록 순서대로 (모셔틀 1대). 입고끼리는 버퍼 순서. 출고는 다른 단 입고를 앞지를 수 있다.
- 리프트는 한 번에 한 작업(liftOwner). 입고: 팔레트가 IN1 에 있고 리프트가 비면 점유 → 모셔틀에 넘기면 해제.
  출고: 모셔틀이 리프트 앞에 오면 점유 → OUT1 에 내려놓으면 해제. OUT1 이 차 있으면 리프트를 잡은 채 기다린다.
- 버퍼: 입고는 끝칸 IN5 로만 받고 컨베이어가 IN1 쪽으로 당긴다. 출고는 OUT1 로 나와 OUT5 로 밀린다.

명세에 없어 정한 것 (Unity 와 다를 수 있음)
- 출고 채널 고르기: 그 SKU 채널 중 (리프트 + 모셔틀 시간 + 단 대기 x 20 s) 가 가장 작은 곳.
- 입고는 그 단 모셔틀 차례가 왔을 때만 리프트를 잡는다 (리프트를 잡고 모셔틀을 기다리다 서로 막히는 것 방지).
- 이송 거리 1.95 m (0.6 m/s → 3.25 s): 명세의 "리프트 1회 점유 약 18.5 s" 에서 역산.
"""
from __future__ import annotations

import itertools
import math
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from whmqtt import geometry as G
from whmqtt import ids

# ── Unity 기본값 (명세 4.2·4.3) ─────────────────────────────────────────────
LIFT_V, LIFT_A = 1.5, 0.8
MOTHER_V, MOTHER_A = 2.5, 1.0
SAT_V, SAT_A = 1.2, 0.8
PLATE_S = 0.05 / 0.08
XFER_S = 1.95 / 0.6
CONVEYOR_S = 1.3 / 0.5            # 버퍼 한 칸 (칸 간격 1.3 m, 0.5 m/s)
BUF_TOP, RAIL_TOP0, LEVEL_H = 0.50, 0.40, 1.45
AISLE_HALF, SLOT_M = 1.0, 1.25
QUEUE_S, EMPTY_W = 20.0, 0.1


def trap(d: float, v: float, a: float) -> float:
    d = abs(d)
    if d < 1e-9:
        return 0.0
    return d / v + v / a if d >= v * v / a else 2 * math.sqrt(d / a)


def level_h(lv: int) -> float:
    return RAIL_TOP0 + LEVEL_H * (lv - 1)


def sat_depth(slot: int) -> float:
    return AISLE_HALF + (slot - .5) * SLOT_M


@dataclass
class Channel:
    id: str
    level: int
    y: float
    sku: str | None = None
    slots: list = field(default_factory=lambda: [None] * ids.DEPTH)   # index 0 = 칸 01 (맨 앞)
    virtual: int = 0

    @property
    def count(self) -> int:
        return sum(1 for s in self.slots if s)


@dataclass
class Mother:
    id: str
    level: int
    y: float
    queue: deque = field(default_factory=deque)
    busy: bool = False
    pallet: str | None = None
    sat_channel: str | None = None
    sat_depth: float | None = None
    status: str = "대기"
    last: tuple | None = None


@dataclass
class Lift:
    id: str
    height: float = BUF_TOP
    owner: str | None = None
    pallet: str | None = None
    status: str = "대기"
    last: tuple | None = None


@dataclass
class Task:
    id: str
    kind: str                     # store / retrieve
    sku: str
    pallet: str | None
    channel: Channel
    cmd: dict
    corr: str | None
    gen: object = None
    wake: float = 0.0
    until: Callable[[], bool] | None = None
    t0: float = 0.0


class RackModel:
    def __init__(self, rail: int, buffers: dict[str, list], send: Callable, now: Callable[[], float]) -> None:
        self.rail, self.rid = rail, f"R{rail}"
        self.buf_in, self.buf_out = buffers[f"R{rail}-IN"], buffers[f"R{rail}-OUT"]
        self.send, self.now = send, now
        self.xfer_y = G.shuttle_xfer_y(self.rid)
        self.channels: dict[str, Channel] = {}
        for side in "WE":
            for row in range(1, ids.ROWS + 1):
                y = G.channel_xyz(ids.channel_id(rail, side, row, 1))[1]
                for lv in range(1, ids.LEVELS + 1):
                    ch = ids.channel_id(rail, side, row, lv)
                    self.channels[ch] = Channel(ch, lv, y)
        self.mothers = {lv: Mother(ids.shuttle_id(rail, lv), lv, self.xfer_y) for lv in range(1, ids.LEVELS + 1)}
        self.lift = Lift(f"L{rail}")
        self.tasks: list[Task] = []
        self._ids = itertools.count(1)
        self._pid = itertools.count(1)
        self.total_stored = self.total_retrieved = 0
        self.cycles: list[float] = []
        self.dirty_inv = True
        self.dirty_buf = {f"R{rail}-IN", f"R{rail}-OUT"}

    # ── 조회 ───────────────────────────────────────────────────────────────
    @property
    def capacity(self) -> int:
        return len(self.channels) * ids.DEPTH

    @property
    def stored(self) -> int:
        return sum(c.count for c in self.channels.values())

    @property
    def inbound_free(self) -> bool:
        return self.buf_in[4] is None

    def stock(self, sku: str) -> int:
        return sum(c.virtual for c in self.channels.values() if c.sku == sku)

    def _cost(self, c: Channel) -> float:
        return (trap(level_h(c.level) - BUF_TOP, LIFT_V, LIFT_A) + trap(c.y - self.xfer_y, MOTHER_V, MOTHER_A)
                + QUEUE_S * len(self.mothers[c.level].queue))

    # ── 시작 재고 (PrefillRandom 대역) ─────────────────────────────────────
    def prefill(self, rng: random.Random, skus: list[str], n_channels: int) -> None:
        for ch in rng.sample(sorted(self.channels), n_channels):
            c = self.channels[ch]
            c.sku = rng.choice(skus)
            n = rng.randint(4, ids.DEPTH)
            for i in range(n):                       # 가장 안쪽(14)부터
                c.slots[ids.DEPTH - 1 - i] = ids.rack_pallet_id(self.rail, next(self._pid))
            c.virtual = n
        self.dirty_inv = True

    # ── 요청 (RequestStore / RequestRetrieve) ─────────────────────────────
    def request(self, cmd: dict, corr: str | None) -> None:
        act = cmd["action"]
        if act == "store":
            if cmd["pallet"] not in self.buf_in:
                return self._evt("rejected", cmd, corr, None, reason=f"{cmd['pallet']} 가 {self.rid}-IN 버퍼에 없음")
            same = [c for c in self.channels.values() if c.sku == cmd["sku"] and c.virtual < ids.DEPTH]
            cand = same or [c for c in self.channels.values() if c.sku is None and c.virtual == 0]
            if not cand:
                return self._evt("rejected", cmd, corr, None, reason="만재")
            c = min(cand, key=lambda c: self._cost(c) + EMPTY_W * (ids.DEPTH - c.virtual))
            c.sku, c.virtual = cmd["sku"], c.virtual + 1
        else:
            cand = [c for c in self.channels.values() if c.sku == cmd["sku"] and c.virtual > 0]
            if not cand:
                return self._evt("rejected", cmd, corr, None, reason="재고 없음")
            c = min(cand, key=self._cost)
            c.virtual -= 1
        t = Task(f"{self.rid}-T{next(self._ids):05d}", act, cmd["sku"], cmd.get("pallet"), c, cmd, corr, t0=self.now())
        t.gen = self._store(t) if act == "store" else self._retrieve(t)
        self.mothers[c.level].queue.append(t)
        self.tasks.append(t)
        self.dirty_inv = True
        self._evt("accepted", cmd, corr, t)

    def _evt(self, event: str, cmd: dict, corr, t: Task | None, slot: int | None = None, reason: str | None = None):
        self.send("rack.evt", {"event": event, "cmd_id": cmd.get("cmd_id"), "job_id": cmd.get("job_id"),
                               "action": cmd["action"], "pallet": t.pallet if t else cmd.get("pallet"),
                               "sku": cmd["sku"], "channel": t.channel.id if t else None, "slot": slot,
                               "reason": reason}, self.rid, corr=corr)

    # ── 실행 ───────────────────────────────────────────────────────────────
    def step(self) -> None:
        now = self.now()
        for t in list(self.tasks):
            while True:
                if t.until is not None:
                    if not t.until():
                        break
                    t.until = None
                elif now < t.wake:
                    break
                try:
                    kind, arg = next(t.gen)
                except StopIteration:
                    self.tasks.remove(t)
                    break
                if kind == "sleep":
                    t.wake = now + arg
                else:
                    t.until = arg

    def _store(self, t: Task):
        m, c, lf = self.mothers[t.channel.level], t.channel, self.lift
        # 팔레트가 IN1 에 오고, 리프트가 비고, 그 단 모셔틀 차례가 됐을 때
        yield "until", lambda: self.buf_in[0] == t.pallet and lf.owner is None and m.queue[0] is t and not m.busy
        lf.owner, m.busy = t.id, True
        self._evt("started", t.cmd, t.corr, t)
        lf.status = f"{t.pallet} 받으러 버퍼로"
        yield "sleep", trap(lf.height - BUF_TOP, LIFT_V, LIFT_A)
        lf.height = BUF_TOP
        yield "sleep", XFER_S
        self.buf_in[0], lf.pallet = None, t.pallet
        self.dirty_buf.add(f"{self.rid}-IN")
        h = level_h(c.level)
        lf.status, m.status = f"{c.level}단으로 승강", "리프트 앞으로"
        dt = max(trap(h - BUF_TOP, LIFT_V, LIFT_A), trap(m.y - self.xfer_y, MOTHER_V, MOTHER_A))
        yield "sleep", dt
        lf.height, m.y = h, self.xfer_y
        lf.status = "모셔틀에 이송"
        yield "sleep", XFER_S
        lf.pallet, m.pallet, lf.owner, lf.status = None, t.pallet, None, "대기"
        idx = max(i for i, s in enumerate(c.slots) if s is None)        # 가장 안쪽 빈칸
        m.status = f"{c.id} 로 주행"
        yield "sleep", trap(c.y - m.y, MOTHER_V, MOTHER_A)
        m.y = c.y
        d = sat_depth(idx + 1)
        m.sat_channel, m.sat_depth, m.status = c.id, d, f"새틀라이트 {c.id}-{idx + 1:02d} 적재"
        yield "sleep", trap(d, SAT_V, SAT_A) + PLATE_S
        c.slots[idx], m.pallet = t.pallet, None
        yield "sleep", trap(d, SAT_V, SAT_A)
        m.sat_channel = m.sat_depth = None
        self._finish(t, m, idx + 1)
        self.total_stored += 1

    def _retrieve(self, t: Task):
        m, c, lf = self.mothers[t.channel.level], t.channel, self.lift
        yield "until", lambda: m.queue[0] is t and not m.busy
        m.busy = True
        self._evt("started", t.cmd, t.corr, t)
        m.status = f"{c.id} 로 주행"
        yield "sleep", trap(c.y - m.y, MOTHER_V, MOTHER_A)
        m.y = c.y
        idx = min(i for i, s in enumerate(c.slots) if s)                 # 맨 앞 팔레트
        d = sat_depth(idx + 1)
        m.sat_channel, m.sat_depth, m.status = c.id, d, f"새틀라이트 {c.id}-{idx + 1:02d} 인출"
        yield "sleep", trap(d, SAT_V, SAT_A) + PLATE_S
        t.pallet, c.slots[idx] = c.slots[idx], None
        m.pallet = t.pallet
        if c.count == 0 and c.virtual == 0:
            c.sku = None
        self.dirty_inv = True
        yield "sleep", trap(d, SAT_V, SAT_A)
        m.sat_channel = m.sat_depth = None
        m.status = "리프트 앞으로"
        yield "sleep", trap(self.xfer_y - m.y, MOTHER_V, MOTHER_A)
        m.y = self.xfer_y
        m.status = "리프트 대기"
        yield "until", lambda: lf.owner is None
        lf.owner = t.id
        h = level_h(c.level)
        lf.status = f"{c.level}단으로 승강"
        yield "sleep", trap(lf.height - h, LIFT_V, LIFT_A)
        lf.height = h
        yield "sleep", XFER_S
        m.pallet, lf.pallet = None, t.pallet
        m.busy, m.status = False, "대기"
        m.queue.popleft()
        lf.status = "버퍼로 하강"
        yield "sleep", trap(h - BUF_TOP, LIFT_V, LIFT_A)
        lf.height = BUF_TOP
        lf.status = "OUT1 비기를 기다림"
        yield "until", lambda: self.buf_out[0] is None
        yield "sleep", XFER_S
        self.buf_out[0], lf.pallet, lf.owner, lf.status = t.pallet, None, None, "대기"
        self.dirty_buf.add(f"{self.rid}-OUT")
        self._evt("done", t.cmd, t.corr, t, slot=idx + 1)
        self.total_retrieved += 1
        self.cycles.append(self.now() - t.t0)

    def _finish(self, t: Task, m: Mother, slot: int) -> None:
        m.busy, m.status = False, "대기"
        m.queue.popleft()
        self.dirty_inv = True
        self._evt("done", t.cmd, t.corr, t, slot=slot)
        self.cycles.append(self.now() - t.t0)

    # ── 상태 메시지 ────────────────────────────────────────────────────────
    def inventory(self) -> dict:
        chs = [[c.id, c.sku, c.count, c.virtual] for c in self.channels.values() if c.sku]
        return {"channels": chs, "stored": self.stored, "capacity": self.capacity}

    def state(self) -> dict:
        return {"stored": self.stored, "capacity": self.capacity, "pending": len(self.tasks),
                "inbound_free": self.inbound_free, "total_stored": self.total_stored,
                "total_retrieved": self.total_retrieved,
                "avg_cycle_s": round(sum(self.cycles) / len(self.cycles), 1) if self.cycles else None}

    def lift_state(self) -> dict:
        lf = self.lift
        lv = next((n for n in range(1, ids.LEVELS + 1) if abs(level_h(n) - lf.height) < 1e-6), None)
        return {"status": lf.status, "height": round(lf.height, 3), "level": lv, "pallet": lf.pallet,
                "owner": lf.owner}

    def shuttle_state(self, m: Mother) -> dict:
        return {"busy": m.busy, "status": m.status, "level": m.level, "y": round(m.y, 2),
                "satellite": {"where": "in_lane" if m.sat_channel else "docked", "channel": m.sat_channel,
                              "depth_m": None if m.sat_depth is None else round(m.sat_depth, 3)},
                "pallet": m.pallet}
