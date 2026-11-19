"""의존 방향 검사: 알고리즘 코어는 MQTT 쪽(comm/)을 모른다 (comm → 코어 단방향).

1) 코어 소스에 MQTT·comm 쪽 import 가 없는지 정적으로 본다.
2) paho·whmqtt·sim·adapters·jsonschema 를 막은 새 프로세스에서 코어를 import 하고 짧은 D4 시뮬을 돌린다.
"""
import ast
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ("warehouse", "plans")
FORBIDDEN = ("paho", "whmqtt", "sim", "adapters", "comm", "jsonschema")


def _imports(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield node.lineno, a.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module


def test_core_source_has_no_comm_imports():
    bad = []
    for pkg in CORE:
        for f in sorted((ROOT / pkg).rglob("*.py")):
            for line, mod in _imports(f):
                if mod.split(".")[0] in FORBIDDEN:
                    bad.append(f"{f.relative_to(ROOT)}:{line} import {mod}")
    assert not bad, "코어가 MQTT 쪽을 import 함:\n" + "\n".join(bad)


def test_core_runs_without_mqtt_packages():
    code = textwrap.dedent(f"""
        import importlib, pkgutil, sys

        class Block:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in {FORBIDDEN!r}:
                    raise ImportError("blocked: " + name)

        sys.meta_path.insert(0, Block())
        import matplotlib
        matplotlib.use("Agg")
        import warehouse
        for m in pkgutil.walk_packages(warehouse.__path__, "warehouse."):
            importlib.import_module(m.name)
        from warehouse.d4.sim import D4Sim, SimConfig
        sim = D4Sim(SimConfig(hours=1, in_per_h=60, out_per_h=0, seed=1))
        m = sim.run()
        assert m["in_per_h"] > 0 and sim.bus.counts, m
        print("ok")
    """)
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr[-3000:]
