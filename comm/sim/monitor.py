"""토픽 모니터: 모든 메시지를 한 줄씩 보여 주고(고빈도 pose/scan/clock 제외), 원하면 JSONL로 기록한다.

    python -m sim.monitor --host localhost            # 화면 출력
    python -m sim.monitor --record out/log.jsonl      # 기록 (pose 포함 전부)
"""
from __future__ import annotations

import argparse
import json
import threading
import time

from whmqtt import schemas
from whmqtt.client import WhClient
from whmqtt.topics import ROOT, match

QUIET = {"amr.pose", "amr.scan2d", "sim.clock", "fleet.kpi", "traffic.reservations", "wms.snapshot"}


def brief(key: str, d: dict) -> str:
    if key == "amr.cmd.mission":
        return f"{d['mission_id']} job={d['job_id']} " + " → ".join(
            f"{s['action']}:{s['stop']}" for s in d["steps"])
    if key in ("amr.evt",):
        return f"{d['event']} {d['mission_id'] or ''} seq={d['seq']} {d.get('reason') or ''}"
    if key in ("lift.cmd", "shuttle.cmd"):
        return f"{d['cmd_id']} {d['action']} {d.get('pallet')} {d.get('lane') or ''} lv/pos={d.get('level') or d.get('pos')}"
    if key in ("lift.evt", "shuttle.evt"):
        return f"{d['event']} {d['cmd_id']} {d.get('reason') or ''}"
    if key == "wms.job":
        return f"{d['job_id']} {d['kind']} {d['status']} {d['pallet']} {d['sku']} lane={d['lane']} amr={d['amr']}"
    if key == "wms.inventory":
        return f"{d['event']} {d['pallet']} {d['sku']} {d['lane'] or ''}"
    s = json.dumps(d, ensure_ascii=False)
    return s if len(s) < 140 else s[:137] + "..."


class Recorder:
    """wh/# 전체를 받아 메모리·파일에 쌓고, 스키마 위반을 모은다."""

    def __init__(self, host: str, port: int, path: str | None = None, echo: bool = False) -> None:
        self.c = WhClient(f"monitor-{int(time.time()) % 100000}", "monitor", host, port)
        self.rows: list[dict] = []
        self.invalid: list[str] = []
        self.counts: dict[str, int] = {}
        self.bytes: dict[str, int] = {}
        self.echo, self.path = echo, path
        self.lock = threading.Lock()
        self.c.on_raw(f"{ROOT}/#", self._on)

    def _on(self, topic: str, payload: bytes) -> None:
        if not payload:
            return
        m = match(topic)
        key = m[0].key if m else "?"
        try:
            d = json.loads(payload)
            schemas.validate(d)
            if d["type"] != key:
                raise ValueError(f"type {d['type']} != {key}")
        except Exception as e:
            with self.lock:
                self.invalid.append(f"{topic}: {str(e).splitlines()[0]}")
            return
        with self.lock:
            self.counts[key] = self.counts.get(key, 0) + 1
            self.bytes[key] = self.bytes.get(key, 0) + len(payload)
            self.rows.append({"topic": topic, **d})
        if self.echo and key not in QUIET:
            t = d["t"]
            print(f"[{t if t is None else f'{t:8.1f}'}] {topic.removeprefix(ROOT + '/'):32s} {brief(key, d['data'])}")

    def start(self) -> "Recorder":
        self.c.start()
        return self

    def stop(self) -> None:
        self.c.stop()
        if self.path:
            with open(self.path, "w", encoding="utf-8") as f:
                for r in self.rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--record")
    args = ap.parse_args()
    r = Recorder(args.host, args.port, args.record, echo=True).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        r.stop()
        if r.invalid:
            print("스키마 위반:", *r.invalid[:20], sep="\n  ")


if __name__ == "__main__":
    main()
