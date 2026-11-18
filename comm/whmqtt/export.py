"""스펙 파일 생성: schemas/*.schema.json, spec/topics.json, docs/topics.md, examples/*.json

    python -m whmqtt.export                  # 스키마·토픽 표
    python -m whmqtt.export --log out/demo_log.jsonl   # 데모 기록에서 type 별 예제 1개씩 + 수동 예제
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import ids, schemas
from .envelope import make
from .topics import ROLES, SUBSCRIPTIONS, TOPICS, as_dicts

HERE = Path(__file__).resolve().parents[1]

# 데모에서 안 나오는 토픽의 수동 예제
MANUAL = {
    "sim.control": {"action": "set_scale", "scale": 5.0, "seed": None},
    "amr.scan2d": {"angle_min": 0.0, "angle_inc": 0.087266, "range_max": 10.0,
                   "ranges": [10.0] * 30 + [3.52, 3.41, 3.37, 3.41, 3.52] + [10.0] * 37},
    "amr.cmd.vel": {"v": 0.8, "w": -0.15, "seq": 1532, "ttl": 0.5},
    "amr.cmd.control": {"action": "set_drive_mode", "drive_mode": "rl", "charger": None},
    "ctrl.cmd": {"action": "request_report", "args": {}},
    "sensor.temp": {"celsius": 21.4},
    "sensor.cam": {"label": "smoke", "confidence": 0.87, "x": 63.5, "y": 44.2},
    "alert": {"kind": "fire", "origin": [63.5, 44.2], "radius_m": 3.6, "active": True},
}
MANUAL_ARGS = {"sim.control": [], "amr.scan2d": ["AMR01"], "amr.cmd.vel": ["AMR01"], "amr.cmd.control": ["AMR01"],
               "ctrl.cmd": [], "sensor.temp": ["T-R2-N20"], "sensor.cam": ["CAM03"], "alert": ["fire"]}


def export_schemas(dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "envelope.schema.json").write_text(json.dumps(schemas.ENVELOPE, ensure_ascii=False, indent=1))
    for key in TOPICS:
        (dst / f"{key}.schema.json").write_text(json.dumps(schemas.full_schema(key), ensure_ascii=False, indent=1))


def export_topics(spec_dir: Path, doc: Path) -> None:
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "topics.json").write_text(json.dumps({
        "root": "wh/v1", "roles": ROLES, "id_patterns": ids.PATTERNS, "topics": as_dicts(),
        "subscriptions": SUBSCRIPTIONS}, ensure_ascii=False, indent=1))
    rows = ["| 토픽 (`wh/v1/` 뒤) | type | 발행 → 구독 | QoS | retain | 주기 | 만료 | 내용 |",
            "|---|---|---|---|---|---|---|---|"]
    for t in TOPICS.values():
        rows.append(f"| `{t.pattern}` | `{t.key}` | {' '.join(t.pub)} → {' '.join(t.sub)} | {t.qos} | "
                    f"{'O' if t.retain else ''} | {t.rate} | {f'{t.expiry_s}s' if t.expiry_s else ''} | "
                    f"{'(예약) ' if t.reserved else ''}{t.desc} |")
    subs = ["| 역할 | 하는 일 | 구독 |", "|---|---|---|"]
    for r, desc in ROLES.items():
        subs.append(f"| `{r}` | {desc} | {'<br>'.join(f'`{s}`' for s in SUBSCRIPTIONS[r]) or '-'} |")
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("# 토픽 표 (자동 생성: `python -m whmqtt.export`)\n\n" + "\n".join(rows) +
                   "\n\n## 역할별 구독\n\n" + "\n".join(subs) + "\n", encoding="utf-8")


def export_acl(dst: Path) -> None:
    """토픽 레지스트리의 발행자 정보로 mosquitto ACL 을 만든다 (사용자명 = 역할)."""
    lines = ["# 자동 생성: python -m whmqtt.export  (사용자명 = 역할, 토픽 레지스트리의 발행자 기준)",
             "# 모든 클라이언트: 자기 접속 상태(LWT) 토픽만 쓰기",
             "pattern readwrite wh/v1/sys/%c/online", ""]
    for role in ROLES:
        lines.append(f"user {role}")
        lines.append("topic read wh/v1/#")
        for t in TOPICS.values():
            if role in t.pub:
                lines.append(f"topic write {t.wildcard()}")
        lines.append("")
    lines += ["# 개발용 관리자 (mosquitto_sub/pub 로 디버깅)", "user admin", "topic readwrite wh/v1/#",
              "topic read $SYS/#", ""]
    dst.write_text("\n".join(lines))


def export_examples(log: Path | None, dst: Path) -> int:
    dst.mkdir(parents=True, exist_ok=True)
    got: dict[str, dict] = {}
    if log and log.exists():
        prefer = {"amr.evt": "mission_done", "wms.job": "held", "lift.evt": "done", "shuttle.evt": "done",
                  "dock.evt": "pallet_unloaded", "wms.inventory": "stored", "ctrl.evt": "report"}
        for line in log.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            k = r["type"]
            want = prefer.get(k)
            ok = want is None or want in (r["data"].get("event"), r["data"].get("status"))
            if k == "amr.cmd.mission" and k in got:
                continue
            if k not in got or (ok and not got[k]["_ok"]):
                got[k] = {**r, "_ok": ok}
    for k, data in MANUAL.items():
        if k not in got:
            env = make(k, data, {"amr": "rl-amr", "ctrl": "control", "sim": "control"}.get(k.split(".")[0], "sensor"),
                       t=1234.5).to_dict()
            from .topics import topic as _topic
            got[k] = {"topic": _topic(k, *MANUAL_ARGS[k]), **env, "_ok": True}
    for k, r in got.items():
        r = {kk: vv for kk, vv in r.items() if kk != "_ok"}
        schemas.validate({kk: vv for kk, vv in r.items() if kk != "topic"})
        (dst / f"{k}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1))
    return len(got)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log")
    args = ap.parse_args()
    export_schemas(HERE / "schemas")
    export_topics(HERE / "spec", HERE / "docs" / "topics.md")
    export_acl(HERE / "broker" / "acl")
    n = export_examples(Path(args.log) if args.log else None, HERE / "examples")
    print(f"스키마 {len(TOPICS) + 1}개, 토픽 {len(TOPICS)}개, 예제 {n}개")


if __name__ == "__main__":
    main()
