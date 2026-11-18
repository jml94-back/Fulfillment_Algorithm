"""끝에서 끝까지 데모: 브로커 + 가상 장비(Unity 대역) + 스케줄러 브리지 + 관제 역할 주문 투입 + 기록.

    python -m sim.run_demo                       # mosquitto 를 직접 띄워서 (설치돼 있으면)
    python -m sim.run_demo --host 192.168.0.10   # 이미 떠 있는 브로커 사용
    python -m sim.run_demo --scale 40 --out out  # 배속, 결과 폴더

시나리오 (D4)
- 입고 트럭 2대: IN2 (SKU101 x 4, 그중 1개는 송장보다 12% 무거움 → 보류), IN5 (SKU102 x 3)
- 출고 트럭 1대: OUT3 (시작 재고에 있는 SKU 2종 x 2)
- 관제(LangGraph 자리)가 중간에 보고를 요청하고, 보류 팔레트를 해제한다.

결과: out/demo_log.jsonl (모든 메시지), out/demo_summary.json (토픽별 수·대역폭·작업 결과), 스키마 위반 수.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from sim.monitor import Recorder
from sim.scheduler_bridge import Scheduler
from sim.virtual_plant import Plant
from whmqtt import ids
from whmqtt.client import WhClient, wait_until

HERE = Path(__file__).resolve().parents[1]


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start_broker(port: int) -> subprocess.Popen:
    exe = shutil.which("mosquitto") or "/usr/sbin/mosquitto"
    if not os.path.exists(exe):
        raise RuntimeError("mosquitto 가 없습니다. --host 로 떠 있는 브로커를 지정하세요.")
    d = tempfile.mkdtemp()
    conf = Path(d) / "m.conf"
    conf.write_text(f"listener {port} 127.0.0.1\nallow_anonymous true\npersistence false\n"
                    "max_queued_messages 10000\n")
    p = subprocess.Popen([exe, "-c", str(conf)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            return p
        except OSError:
            time.sleep(0.1)
    p.kill()
    raise RuntimeError("mosquitto 시작 실패")


def inbound(order_id, dock, truck, sku, n, start, kg=650.0):
    pallets = [{"pallet": ids.pallet_id(start + i), "declared_kg": kg} for i in range(n)]
    return {"order_id": order_id, "kind": "inbound", "dock": dock, "truck": truck,
            "lines": [{"sku": sku, "qty": n, "pallets": pallets}], "due": None}


def run(host: str | None, port: int | None, scale: float, out: Path, timeout: float = 240.0,
        quiet: bool = True) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    broker = None
    if host is None:
        port = port or free_port()
        broker = start_broker(port)
        host = "127.0.0.1"
    port = port or 1883
    rec = Recorder(host, port, str(out / "demo_log.jsonl"), echo=not quiet).start()
    sched = Scheduler(host, port, validate=True, seed=3).start()
    time.sleep(0.3)                                          # 스냅샷(retain) → 가상 장비가 시작 재고를 받음
    plant = Plant(host, port, scale=scale, validate=True, seed=3,
                  heavy={ids.pallet_id(3)}).start()          # P00000003: 송장보다 12% 무거움 → 보류
    ctrl = WhClient("control-demo", "control", host, port, validate=True)
    reports: list[dict] = []
    ctrl.on("ctrl.evt", lambda env, p: reports.append(env.data))
    held: list[str] = []
    ctrl.on("wms.job", lambda env, p: env.data["status"] == "held" and env.data["job_id"] not in held
            and held.append(env.data["job_id"]))
    ctrl.start()
    wall0 = time.time()
    try:
        wait_until(lambda: ctrl.sim_t is not None, 5)
        # 출고할 SKU: 시작 재고에서 수량이 많은 2종
        stock: dict[str, int] = {}
        for lane, slots in sched.lane_pallets.items():
            for s in slots:
                if s:
                    stock[s[1]] = stock.get(s[1], 0) + 1
        out_skus = sorted(stock, key=lambda k: -stock[k])[:2]
        orders = [
            inbound("ASN-0001", "IN2", "TRK-IN-01", "SKU101", 4, 1),
            inbound("ASN-0002", "IN5", "TRK-IN-02", "SKU102", 3, 101, kg=900.0),
            {"order_id": "SO-0001", "kind": "outbound", "dock": "OUT3", "truck": "TRK-OUT-01",
             "lines": [{"sku": s, "qty": 2, "pallets": []} for s in out_skus], "due": None},
        ]
        for o in orders:
            ctrl.send("wms.order", o)
        total_jobs = 4 + 3 + 2 * len(out_skus)
        released = False
        asked = False

        def finished() -> bool:
            return len(sched.done_log) >= total_jobs

        end = time.time() + timeout
        while time.time() < end and not finished():
            time.sleep(0.2)
            if not asked and len(sched.done_log) >= 3:
                ctrl.send("ctrl.cmd", {"action": "request_report", "args": {}})
                asked = True
            with sched.lock:
                ready = [j for j in held if sched.jobs[j].stage == "at_qc"]
            if ready and not released:
                ctrl.send("ctrl.cmd", {"action": "release_hold", "args": {"job_id": ready[0]}})
                released = True
        time.sleep(1.0)
        sim_t = plant.t
    finally:
        ctrl.stop()
        plant.stop()
        sched.stop()
        time.sleep(0.3)
        rec.stop()
        if broker:
            broker.terminate()
            broker.wait(5)

    jobs = sorted(sched.jobs.values(), key=lambda j: j.job_id)
    sim_dur = max(sim_t, 1e-6)
    summary = {
        "ok": finished() and not rec.invalid and not plant.c.errors and not sched.c.errors,
        "sim_seconds": round(sim_t, 1),
        "wall_seconds": round(time.time() - wall0, 1),
        "scale": scale,
        "jobs": [{"job_id": j.job_id, "kind": j.kind, "pallet": j.pallet, "sku": j.sku, "status": j.status,
                  "lane": j.lane, "pos": j.pos, "amr": j.amr, "lead_s": None if j.done_t is None
                  else round(j.done_t - j.created, 1), "reason": j.reason} for j in jobs],
        "jobs_done": len(sched.done_log), "jobs_expected": total_jobs,
        "reports": reports,
        "messages": sum(rec.counts.values()),
        "schema_violations": rec.invalid[:50],
        "handler_errors": (plant.c.errors + sched.c.errors)[:20],
        "per_topic": {k: {"count": rec.counts[k], "bytes": rec.bytes[k],
                          "avg_bytes": round(rec.bytes[k] / rec.counts[k]),
                          "per_sim_s": round(rec.counts[k] / sim_dur, 2)}
                      for k in sorted(rec.counts)},
    }
    (out / "demo_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--scale", type=float, default=30.0)
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    s = run(args.host, args.port, args.scale, Path(args.out), args.timeout, quiet=not args.verbose)
    print(f"완료 {s['jobs_done']}/{s['jobs_expected']} 작업, 시뮬레이션 {s['sim_seconds']}초 "
          f"(벽시계 {s['wall_seconds']}초), 메시지 {s['messages']}개, 스키마 위반 {len(s['schema_violations'])}건")
    for j in s["jobs"]:
        print(f"  {j['job_id']} {j['kind']:8s} {j['pallet']} {j['sku']} {j['status']:6s} {j['lane'] or '':14s} "
              f"{j['amr'] or '':5s} {j['lead_s']}s {j['reason'] or ''}")
    for r in s["reports"]:
        print("  관제 응답:", r["event"], r["text"])
    if s["schema_violations"] or s["handler_errors"]:
        print("문제:", *(s["schema_violations"] + s["handler_errors"])[:10], sep="\n  ")


if __name__ == "__main__":
    main()
