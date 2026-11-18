"""broker/mosquitto.conf + acl 을 실제 mosquitto 로 띄워 인증·권한을 확인."""
import shutil
import subprocess
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import pytest

from sim.run_demo import free_port
from whmqtt.client import WhClient, wait_until

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def secure_broker(tmp_path_factory):
    exe = shutil.which("mosquitto") or "/usr/sbin/mosquitto"
    pw = shutil.which("mosquitto_passwd")
    if not Path(exe).exists() or not pw:
        pytest.skip("mosquitto 없음")
    d = tmp_path_factory.mktemp("broker")
    shutil.copy(ROOT / "broker" / "acl", d / "acl")
    (d / "passwd").write_text("")
    for u in ("unity", "scheduler", "rl", "control", "monitor"):
        subprocess.run([pw, "-b", str(d / "passwd"), u, f"pw-{u}"], check=True)
    port = free_port()
    conf = (ROOT / "broker" / "mosquitto.conf").read_text().replace("__DIR__", str(d))
    conf = conf.replace("listener 1883 0.0.0.0", f"listener {port} 127.0.0.1")
    conf = conf.replace("listener 9001 0.0.0.0\nprotocol websockets\n", "")
    import os
    if os.geteuid() == 0:                      # 테스트 컨테이너(root): 권한 낮추지 않고 실행
        conf += "\nuser root\n"
    (d / "data").mkdir()
    (d / "m.conf").write_text(conf)
    p = subprocess.Popen([exe, "-c", str(d / "m.conf")], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    time.sleep(0.5)
    assert p.poll() is None, p.stdout.read().decode()
    yield port
    p.terminate()
    p.wait(5)


def _client(role, port, password=None):
    c = WhClient(f"{role}-acl", role, "127.0.0.1", port, username=role, password=password or f"pw-{role}")
    return c


def test_wrong_password_rejected(secure_broker):
    with pytest.raises(TimeoutError):
        _client("rl", secure_broker, "nope").start(wait=2)


def test_acl_allows_own_topics_blocks_others(secure_broker):
    port = secure_broker
    unity = _client("unity", port)
    got = []
    unity.on("amr.cmd.vel", lambda env, p: got.append(("vel", env.src)))
    unity.on("amr.cmd.mission", lambda env, p: got.append(("mission", env.src)))
    unity.start()
    rl = _client("rl", port).start()
    sched = _client("scheduler", port).start()
    vel = {"v": 0.5, "w": 0.0, "seq": 1, "ttl": 0.5}
    mission = {"mission_id": "M1", "job_id": None, "priority": 5,
               "steps": [{"seq": 0, "action": "wait", "stop": None, "pallet": None, "path": [], "theta": None,
                          "duration": 1.0, "deadline": None}]}
    rl.send("amr.cmd.vel", vel, "AMR01")                 # 허용
    rl.send("amr.cmd.mission", mission, "AMR01")         # rl 은 미션 발행 권한 없음 → 브로커가 버림
    sched.send("amr.cmd.mission", mission, "AMR01")      # 허용
    assert wait_until(lambda: ("vel", "rl-acl") in got and ("mission", "scheduler-acl") in got, 3)
    time.sleep(0.5)
    assert ("mission", "rl-acl") not in got
    for c in (unity, rl, sched):
        c.stop()


def test_cannot_spoof_other_clients_online(secure_broker):
    port = secure_broker
    mon = _client("monitor", port)
    seen = []
    mon.on("sys.online", lambda env, p: seen.append(p["client"]))
    mon.start()
    rl = _client("rl", port).start()
    rl.send("sys.online", {"online": False, "role": "unity", "version": "1"}, "unity-sim")   # 남의 상태 위조 시도
    time.sleep(0.5)
    assert "rl-acl" in seen and "unity-sim" not in seen
    rl.stop()
    mon.stop()
