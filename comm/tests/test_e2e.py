"""브로커를 띄운 끝에서 끝까지 검사."""
import json
import math
import socket
import time

from sim.run_demo import run
from sim.virtual_plant import Plant
from whmqtt.client import WhClient, wait_until


def test_demo_end_to_end(broker, tmp_path):
    host, port = broker
    s = run(host, port, scale=40.0, out=tmp_path, timeout=180)
    assert s["jobs_done"] == s["jobs_expected"] == 11, s["jobs"]
    assert s["schema_violations"] == [] and s["handler_errors"] == []
    st = {j["pallet"]: j for j in s["jobs"]}
    assert st["P00000003"]["status"] == "done"                     # 보류 → 관제 해제 → 적재
    held = [json.loads(l) for l in (tmp_path / "demo_log.jsonl").read_text().splitlines()]
    assert any(r["type"] == "wms.job" and r["data"]["status"] == "held" and r["data"]["pallet"] == "P00000003"
               for r in held)
    assert any(r["type"] == "ctrl.evt" and r["data"]["event"] == "report" for r in held)
    # 같은 SKU 입고는 같은 채널에 모인다 (채널은 SKU 1종 전용, Unity 랙이 같은 SKU 채널을 먼저 고름)
    chans = {j["channel"] for j in s["jobs"] if j["sku"] == "SKU101"}
    assert len(chans) == 1
    # 랙 응답은 corr 로 원래 rack.cmd 를 가리키고, done 에 채널·칸이 온다
    cmd_ids = {r["id"] for r in held if r["type"] == "rack.cmd"}
    acks = [r for r in held if r["type"] == "rack.evt" and r["data"]["event"] == "accepted"]
    assert acks and all(r["corr"] in cmd_ids for r in acks)
    done = [r["data"] for r in held if r["type"] == "rack.evt" and r["data"]["event"] == "done"]
    assert len(done) == 11 and all(d["channel"] and d["slot"] for d in done)
    # 후입선출: 같은 채널에 넣은 순서대로 안쪽(14)부터 한 칸씩 앞으로
    slots = [d["slot"] for d in done if d["action"] == "store" and d["sku"] == "SKU101"]
    assert slots == list(range(14, 14 - len(slots), -1))


def test_rl_velocity_and_expiry(broker):
    host, port = broker
    plant = Plant(host, port, scale=1.0, tick=0.05, validate=True).start()
    rl = WhClient("rl-test", "rl", host, port, validate=True)
    poses = []
    rl.on("amr.pose", lambda env, p: p["amr"] == "AMR03" and poses.append(env.data))
    rl.start()
    try:
        rl.send("amr.cmd.control", {"action": "set_drive_mode", "drive_mode": "rl", "charger": None}, "AMR03")
        assert wait_until(lambda: plant.amrs["AMR03"].drive_mode == "rl", 3)
        x0, y0 = plant.amrs["AMR03"].x, plant.amrs["AMR03"].y
        for k in range(15):                                 # 1.5초 동안 1 m/s
            rl.send("amr.cmd.vel", {"v": 1.0, "w": 0.0, "seq": k, "ttl": 0.5}, "AMR03")
            time.sleep(0.1)
        moved = math.dist((x0, y0), (plant.amrs["AMR03"].x, plant.amrs["AMR03"].y))
        assert 0.8 < moved < 2.5, moved
        time.sleep(0.8)                                     # 명령 끊김 → ttl 지나면 정지
        x1 = plant.amrs["AMR03"].x
        time.sleep(0.5)
        assert abs(plant.amrs["AMR03"].x - x1) < 1e-6 and plant.amrs["AMR03"].v == 0.0
        assert poses and all("load_kg" in p for p in poses)
    finally:
        rl.stop()
        plant.stop()


def test_last_will_marks_offline(broker):
    host, port = broker
    watcher = WhClient("watch", "monitor", host, port, validate=True)
    seen = {}
    watcher.on("sys.online", lambda env, p: seen.__setitem__(p["client"], env.data["online"]))
    watcher.start()
    c = WhClient("crashy", "rl", host, port, validate=True, keepalive=5).start()
    assert wait_until(lambda: seen.get("crashy") is True, 3)
    c.mqtt.loop_stop()
    c.mqtt.socket().shutdown(socket.SHUT_RDWR)                # 비정상 종료 → 브로커가 LWT 발행
    assert wait_until(lambda: seen.get("crashy") is False, 5)
    watcher.stop()


def test_retained_state_for_late_joiner(broker):
    host, port = broker
    plant = Plant(host, port, scale=1.0, validate=True).start()
    time.sleep(0.5)
    late = WhClient("late", "monitor", host, port, validate=True)
    states = {}
    late.on("amr.state", lambda env, p: states.__setitem__(p["amr"], env.data["mode"]))
    late.on("buffer.state", lambda env, p: states.__setitem__(p["buffer"], env.data["slots"]))
    late.start()
    try:
        assert wait_until(lambda: len([k for k in states if k.startswith("AMR")]) == 4 and "R2-IN" in states, 3)
    finally:
        late.stop()
        plant.stop()


def test_rl_dropout_falls_back_to_scripted(broker):
    """RL 프로세스가 비정상 종료되면 Last Will → 스케줄러가 해당 AMR 을 scripted 로 돌린다."""
    from sim.scheduler_bridge import Scheduler
    host, port = broker
    plant = Plant(host, port, scale=1.0, validate=True, client_id="unity-fb").start()
    sched = Scheduler(host, port, validate=True, client_id="sched-fb").start()
    rl = WhClient("rl-fb", "rl", host, port, validate=True).start()
    try:
        rl.send("amr.cmd.control", {"action": "set_drive_mode", "drive_mode": "rl", "charger": None}, "AMR02")
        assert wait_until(lambda: sched.amrs["AMR02"].drive_mode == "rl", 3)
        rl.mqtt.loop_stop()
        rl.mqtt.socket().shutdown(socket.SHUT_RDWR)
        assert wait_until(lambda: plant.amrs["AMR02"].drive_mode == "scripted", 5)
    finally:
        sched.stop()
        plant.stop()


def test_demo_with_algorithm_planner(broker, tmp_path, monkeypatch):
    """알고리즘 스레드의 D4Planner(warehouse/d4/comm_planner.py)를 끼워도 끝까지 돈다."""
    import sys
    from pathlib import Path

    import pytest
    algo = Path(__file__).resolve().parents[2]
    if not (algo / "warehouse" / "d4" / "comm_planner.py").exists():
        pytest.skip("warehouse/d4/comm_planner.py 없음")
    monkeypatch.syspath_prepend(str(algo))
    from warehouse.d4.comm_planner import D4Planner

    import sim.run_demo as rd
    from sim.scheduler_bridge import Scheduler

    class D4Scheduler(Scheduler):
        def __init__(self, *a, **k):
            super().__init__(*a, planner=D4Planner(), **k)

    monkeypatch.setattr(rd, "Scheduler", D4Scheduler)
    host, port = broker
    s = rd.run(host, port, scale=40.0, out=tmp_path, timeout=180)
    assert s["jobs_done"] == s["jobs_expected"], s["jobs"]
    assert s["schema_violations"] == [] and s["handler_errors"] == []
