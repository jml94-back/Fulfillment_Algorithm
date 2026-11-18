"""Unity C# 스크립트: MQTTnet 4.3.7 로 컴파일되는지, C# 메시지가 스키마를 통과하고 Python 과 왕복되는지 (dotnet 필요)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from whmqtt import schemas
from whmqtt.client import WhClient, wait_until

TOOLS = Path(__file__).resolve().parents[1] / "tools"
ROOT = TOOLS.parent
pytestmark = pytest.mark.skipif(shutil.which("dotnet") is None, reason="dotnet SDK 없음")


def test_unity_scripts_compile():
    r = subprocess.run(["dotnet", "build", "-v", "q", "-nologo"], cwd=TOOLS / "cs_compile_check",
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-3000:]


def test_cs_messages_match_schema_and_roundtrip(broker, tmp_path):
    host, port = broker
    proj = TOOLS / "cs_interop"
    r = subprocess.run(["dotnet", "build", "-v", "q", "-nologo", "-o", str(tmp_path / "bin")], cwd=proj,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-3000:]
    out = tmp_path / "cs.jsonl"
    p = subprocess.Popen(["dotnet", str(tmp_path / "bin" / "cs_interop.dll"), str(out), str(port)],
                         stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "READY"
        rows = [json.loads(x) for x in out.read_text().splitlines()]
        assert len(rows) == 15
        for d in rows:
            schemas.validate(d)
        c = WhClient("py-sched-cs", "scheduler", host, port, validate=True)
        acks = []
        c.on("amr.evt", lambda env, prm: acks.append(env.data))
        c.start()
        ex = json.loads((ROOT / "examples" / "amr.cmd.mission.json").read_text())
        c.send("amr.cmd.mission", ex["data"], "AMR02")
        line = p.stdout.readline()
        assert line.startswith("MISSION " + ex["data"]["mission_id"]), line
        assert wait_until(lambda: acks, 5) and acks[0]["event"] == "mission_accepted"
        assert c.errors == []
        c.stop()
    finally:
        p.wait(10)
