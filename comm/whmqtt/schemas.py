"""메시지 본문(data) JSON Schema (draft 2020-12). 봉투 스키마는 ENVELOPE.

규칙
- 단위: 거리 m, 시간 s, 각도 rad, 무게 kg, 속도 m/s · rad/s, 배터리 0~100 %.
- 좌표: 도면 좌표 (geometry.py 참고). x 동쪽, y 북쪽, z 위.
- 시간 필드(t_start, t_end, deadline 등)는 시뮬레이션 시각(초) = 봉투 t 와 같은 시계.
- 보내는 쪽은 스키마를 지킨다(테스트에서 additionalProperties=false 로 검사).
  받는 쪽은 모르는 필드를 무시한다 → 필드 추가는 v1 안에서 가능, 삭제·의미 변경은 v2 토픽으로.

`python -m whmqtt.export` 가 이 정의를 schemas/*.schema.json 으로 내보낸다.
"""
from __future__ import annotations

from .ids import PATTERNS

NUM = {"type": "number"}
NNUM = {"type": "number", "minimum": 0}
STR = {"type": "string"}
INT = {"type": "integer"}
BOOL = {"type": "boolean"}


def nul(s: dict) -> dict:
    return {"anyOf": [s, {"type": "null"}]}


def pat(kind: str) -> dict:
    return {"type": "string", "pattern": PATTERNS[kind]}


def enum(*v: str) -> dict:
    return {"type": "string", "enum": list(v)}


def obj(props: dict, required: list[str] | None = None, desc: str | None = None) -> dict:
    s = {"type": "object", "properties": props, "required": list(props) if required is None else required,
         "additionalProperties": False}
    if desc:
        s["description"] = desc
    return s


PALLET = pat("pallet")
LANE = pat("lane")
POS = {"type": "integer", "minimum": 1, "maximum": 14, "description": "레인 안 위치, 1 = 레일 쪽 입·출구"}
LEVEL = {"type": "integer", "minimum": 0, "maximum": 10, "description": "0 = 바닥 P&D(버퍼), 1~10 = LV01~LV10"}
CMD_ID = {"type": "string", "minLength": 1, "maxLength": 64}
REASON = nul(STR)

ENVELOPE = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "envelope.schema.json",
    "title": "봉투",
    "type": "object",
    "properties": {
        "v": {"const": 1},
        "id": {"type": "string", "pattern": "^[0-9a-f]{8,32}$"},
        "ts": NUM,
        "t": nul(NUM),
        "src": {"type": "string", "minLength": 1},
        "type": STR,
        "corr": nul(STR),
        "data": {"type": "object"},
    },
    "required": ["v", "id", "ts", "t", "src", "type", "corr", "data"],
    "additionalProperties": False,
}

WAYPOINT = obj({
    "node": {"type": "string", "description": "unity/d4_layout.json nav 노드 id"},
    "x": NUM, "y": NUM,
    "t_start": nul(NUM), "t_end": nul(NUM),
}, required=["node", "x", "y", "t_start", "t_end"],
    desc="경유점과 시간 예약 창. 이 노드를 t_start~t_end 사이에 지나간다(점유한다). 예약이 없으면 null.")

MISSION_STEP = obj({
    "seq": {"type": "integer", "minimum": 0},
    "action": enum("move", "pick", "drop", "charge", "wait"),
    "stop": nul({"type": "string", "description": "정지점 ID: SC-INn, DP-OUTn, Ln-IN, Ln-OUT, CH*, QC-IN, QC-OUT"}),
    "pallet": nul(PALLET),
    "path": {"type": "array", "items": WAYPOINT, "description": "move 단계의 경유점 (첫 점 = 현재 위치 노드)"},
    "theta": nul(NUM),
    "duration": nul(NNUM),
    "deadline": nul(NUM),
}, required=["seq", "action", "stop", "pallet", "path", "theta", "duration", "deadline"])

DATA: dict[str, dict] = {
    "sys.online": obj({
        "online": BOOL,
        "role": enum("unity", "scheduler", "rl", "control", "sensor", "monitor", "test"),
        "version": STR,
    }),
    "sim.clock": obj({
        "t": NNUM,
        "scale": {"type": "number", "exclusiveMinimum": 0, "description": "배속 (1 = 실시간)"},
        "paused": BOOL,
    }),
    "sim.control": obj({
        "action": enum("start", "pause", "resume", "reset", "set_scale"),
        "scale": nul({"type": "number", "exclusiveMinimum": 0}),
        "seed": nul(INT),
    }),
    "amr.pose": obj({
        "x": NUM, "y": NUM, "theta": NUM,
        "v": NUM, "w": NUM,
        "battery": {"type": "number", "minimum": 0, "maximum": 100},
        "load_kg": nul(NNUM),
        "node": nul(STR),
    }, required=["x", "y", "theta", "v", "w", "battery", "load_kg"]),
    "amr.scan2d": obj({
        "angle_min": NUM,
        "angle_inc": {"type": "number", "exclusiveMinimum": 0},
        "range_max": {"type": "number", "exclusiveMinimum": 0},
        "ranges": {"type": "array", "items": NNUM, "minItems": 1, "maxItems": 1440},
    }),
    "amr.state": obj({
        "mode": enum("idle", "moving", "picking", "dropping", "charging", "waiting", "paused", "error", "offline"),
        "drive_mode": enum("scripted", "rl"),
        "battery": {"type": "number", "minimum": 0, "maximum": 100},
        "pallet": nul(PALLET),
        "mission_id": nul(STR),
        "step": nul(INT),
        "node": nul(STR),
        "error": REASON,
    }),
    "amr.evt": obj({
        "event": enum("mission_accepted", "mission_rejected", "step_started", "step_done", "mission_done",
                      "mission_failed", "mission_canceled", "control_applied", "battery_low"),
        "mission_id": nul(STR),
        "seq": nul(INT),
        "pallet": nul(PALLET),
        "reason": REASON,
    }),
    "amr.cmd.mission": obj({
        "mission_id": CMD_ID,
        "job_id": nul(STR),
        "priority": {"type": "integer", "minimum": 0, "maximum": 9, "description": "0 = 가장 높음"},
        "steps": {"type": "array", "items": MISSION_STEP, "minItems": 1},
    }),
    "amr.cmd.vel": obj({
        "v": {"type": "number", "minimum": -0.3, "maximum": 1.5},
        "w": {"type": "number", "minimum": -1.2, "maximum": 1.2},
        "seq": {"type": "integer", "minimum": 0},
        "ttl": {"type": "number", "exclusiveMinimum": 0, "maximum": 2,
                "description": "이 시간(초, 벽시계) 안에 다음 명령이 없으면 정지"},
    }),
    "amr.cmd.control": obj({
        "action": enum("pause", "resume", "cancel_mission", "set_drive_mode", "go_charge"),
        "drive_mode": nul(enum("scripted", "rl")),
        "charger": nul(pat("charger")),
    }),
    "lift.state": obj({
        "mode": enum("idle", "moving", "transferring", "paused", "error"),
        "level": {"type": "number", "minimum": 0, "maximum": 10, "description": "현재 높이(층 단위, 이동 중 소수)"},
        "pallet": nul(PALLET),
        "cmd_id": nul(STR),
        "queue": {"type": "integer", "minimum": 0},
        "error": REASON,
    }),
    "lift.cmd": obj({
        "cmd_id": CMD_ID,
        "job_id": nul(STR),
        "action": enum("lift_in", "lift_out", "cancel"),
        "pallet": nul(PALLET),
        "level": {"type": "integer", "minimum": 1, "maximum": 10},
    }),
    "shuttle.state": obj({
        "mode": enum("idle", "moving", "in_lane", "transferring", "paused", "error"),
        "y": {"type": "number", "description": "모셔틀의 레일 방향 위치(도면 y, m). 리프트 이송점 = 20.4"},
        "satellite": obj({
            "where": enum("docked", "in_lane"),
            "lane": nul(LANE),
            "pos": nul({"type": "number", "minimum": 0, "maximum": 14}),
        }),
        "pallet": nul(PALLET),
        "cmd_id": nul(STR),
        "queue": {"type": "integer", "minimum": 0},
        "battery": nul({"type": "number", "minimum": 0, "maximum": 100}),
        "error": REASON,
    }),
    "shuttle.cmd": obj({
        "cmd_id": CMD_ID,
        "job_id": nul(STR),
        "action": enum("store", "retrieve", "relocate", "cancel"),
        "pallet": nul(PALLET),
        "lane": nul(LANE),
        "pos": nul(POS),
        "to_lane": nul(LANE),
        "to_pos": nul(POS),
    }),
    "device.evt": obj({
        "event": enum("accepted", "rejected", "started", "done", "failed", "canceled"),
        "cmd_id": STR,
        "job_id": nul(STR),
        "pallet": nul(PALLET),
        "reason": REASON,
    }),
    "buffer.state": obj({
        "slots": {"type": "array", "items": nul(PALLET), "minItems": 5, "maxItems": 5,
                  "description": "0번 = 1번 칸(리프트 바로 옆), 4번 = 5번 칸(AMR 접점)"},
        "blocked": BOOL,
    }),
    "dock.state": obj({
        "status": enum("empty", "docked", "unloading", "loading", "departing"),
        "truck": nul(STR),
        "order_id": nul(STR),
        "pallets_total": {"type": "integer", "minimum": 0},
        "pallets_done": {"type": "integer", "minimum": 0},
    }),
    "dock.evt": obj({
        "event": enum("truck_arrived", "truck_departed", "pallet_unloaded", "pallet_loaded"),
        "truck": STR,
        "order_id": nul(STR),
        "pallet": nul(PALLET),
        "station": nul({"type": "string", "pattern": r"^(SC-IN[1-6]|DP-OUT[1-6])$"}),
    }),
    "scale.reading": obj({
        "pallet": PALLET,
        "sku": STR,
        "weight_kg": NNUM,
        "declared_kg": nul(NNUM),
        "stable": BOOL,
    }),
    "charger.state": obj({
        "occupied_by": nul(pat("amr")),
        "charging": BOOL,
        "power_kw": NNUM,
    }),
    "wms.order": obj({
        "order_id": CMD_ID,
        "kind": enum("inbound", "outbound"),
        "dock": pat("dock"),
        "truck": STR,
        "lines": {"type": "array", "minItems": 1, "items": obj({
            "sku": STR,
            "qty": {"type": "integer", "minimum": 1, "description": "팔레트 수"},
            "pallets": {"type": "array", "items": obj({"pallet": PALLET, "declared_kg": NNUM}),
                        "description": "입고: 팔레트별 송장 무게 (qty 개). 출고: 빈 배열"},
        })},
        "due": nul(NUM),
    }),
    "wms.job": obj({
        "job_id": CMD_ID,
        "order_id": nul(STR),
        "kind": enum("inbound", "outbound", "relocate"),
        "status": enum("planned", "waiting", "amr_assigned", "lifting", "shuttling", "done", "held", "failed",
                       "canceled"),
        "pallet": nul(PALLET),
        "sku": STR,
        "dock": nul(pat("dock")),
        "lane": nul(LANE),
        "pos": nul(POS),
        "lift": nul(pat("lift")),
        "shuttle": nul(pat("shuttle")),
        "amr": nul(pat("amr")),
        "eta": nul(NUM),
        "created": NUM,
        "reason": REASON,
    }),
    "wms.inventory": obj({
        "event_id": CMD_ID,
        "event": enum("received", "held", "released", "stored", "retrieved", "shipped", "adjusted"),
        "pallet": PALLET,
        "sku": STR,
        "lane": nul(LANE),
        "pos": nul(POS),
        "weight_kg": nul(NNUM),
        "job_id": nul(STR),
    }),
    "wms.snapshot": obj({
        "lanes": {"type": "array", "items": {
            "type": "array", "prefixItems": [LANE, STR, {"type": "integer", "minimum": 0, "maximum": 14}],
            "items": False, "minItems": 3, "description": "[레인 ID, SKU, 팔레트 수]"}},
        "pallets": {"type": "integer", "minimum": 0},
        "capacity": {"type": "integer", "minimum": 0},
        "held": {"type": "integer", "minimum": 0},
    }),
    "traffic.reservations": obj({
        "horizon": NUM,
        "items": {"type": "array", "items": obj({
            "amr": pat("amr"),
            "mission_id": nul(STR),
            "node": STR,
            "t_start": NUM,
            "t_end": NUM,
        })},
    }),
    "fleet.kpi": obj({
        "window_s": {"type": "number", "exclusiveMinimum": 0},
        "inbound_ph": NNUM, "outbound_ph": NNUM,
        "amr_util": {"type": "object", "additionalProperties": {"type": "number", "minimum": 0, "maximum": 1}},
        "jobs_open": {"type": "integer", "minimum": 0},
        "jobs_done": {"type": "integer", "minimum": 0},
        "mean_lead_s": nul(NNUM),
    }),
    "ctrl.cmd": obj({
        "action": enum("pause_all", "resume_all", "set_policy", "request_report", "release_hold", "cancel_job"),
        "args": {"type": "object"},
    }),
    "ctrl.evt": obj({
        "event": enum("ack", "report", "warning", "error"),
        "text": STR,
        "data": {"type": "object"},
    }),
    # 예약 (현재 범위 밖): 최소 형태만
    "sensor.temp": obj({"celsius": NUM}),
    "sensor.cam": obj({"label": STR, "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                       "x": nul(NUM), "y": nul(NUM)}),
    "alert": obj({"kind": enum("fire", "rack_collapse", "rack_tilt", "leak", "injury"),
                  "origin": {"type": "array", "items": NUM, "minItems": 2, "maxItems": 2},
                  "radius_m": NNUM, "active": BOOL}),
}

# lift.evt, shuttle.evt 는 같은 본문
DATA["lift.evt"] = DATA["device.evt"]
DATA["shuttle.evt"] = DATA["device.evt"]


def full_schema(key: str) -> dict:
    """봉투 + type 고정 + data 본문을 합친 독립 스키마."""
    env = {k: v for k, v in ENVELOPE.items() if k not in ("$id", "title")}
    props = dict(env["properties"])
    props["type"] = {"const": key}
    props["data"] = DATA[key]
    return {**env, "$id": f"{key}.schema.json", "title": key, "properties": props}


_validators: dict = {}


def validate(env_dict: dict) -> None:
    """봉투 dict 를 검사. 틀리면 jsonschema.ValidationError."""
    import jsonschema

    key = env_dict.get("type")
    if key not in DATA:
        raise jsonschema.ValidationError(f"모르는 type: {key}")
    if key not in _validators:
        _validators[key] = jsonschema.Draft202012Validator(full_schema(key))
    _validators[key].validate(env_dict)
