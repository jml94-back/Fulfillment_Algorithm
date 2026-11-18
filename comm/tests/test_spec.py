"""토픽·스키마·ID·좌표 정적 검사 (브로커 불필요)."""
import json
import math
from pathlib import Path

import jsonschema
import pytest

from whmqtt import geometry as G
from whmqtt import ids, schemas
from whmqtt.topics import TOPICS, T, match, topic

ROOT = Path(__file__).resolve().parents[1]


def test_every_topic_has_schema_and_example():
    for key in TOPICS:
        assert key in schemas.DATA, key
        ex = json.loads((ROOT / "examples" / f"{key}.json").read_text())
        t = ex.pop("topic")
        m = match(t)
        assert m and m[0].key == key, t
        schemas.validate(ex)


def test_topic_builder_and_match_roundtrip():
    assert T.amr_pose("AMR01") == "wh/v1/amr/AMR01/pose"
    assert topic("shuttle.cmd", "MS-R2-LV07") == "wh/v1/shuttle/MS-R2-LV07/cmd"
    tp, p = match("wh/v1/buffer/L3-OUT/state")
    assert tp.key == "buffer.state" and p == {"buffer": "L3-OUT"}
    assert match("wh/v1/amr/AMR01/unknown") is None
    # 서로 다른 토픽 패턴이 같은 문자열에 겹치지 않는다
    seen = {}
    for k, t in TOPICS.items():
        sample = t.full
        for prm in t.params:
            sample = sample.replace("{" + prm + "}", "X1")
        assert match(sample)[0].key == k
        assert sample not in seen
        seen[sample] = k


def test_qos_policy():
    for t in TOPICS.values():
        if ".cmd" in t.key or t.key.endswith(".evt") or t.key in ("wms.order", "wms.inventory", "wms.job"):
            if t.key != "amr.cmd.vel":
                assert t.qos == 1, t.key          # 명령·사건은 잃으면 안 됨
        if "cmd" in t.key:
            assert not t.retain, t.key            # 재접속 때 옛 명령이 다시 실행되면 안 됨
        if t.key.endswith(".state"):
            assert t.retain, t.key                # 늦게 붙은 구독자도 현재 상태를 받음
    assert TOPICS["amr.pose"].qos == 0 and TOPICS["amr.cmd.vel"].expiry_s


def test_schema_rejects_bad_messages():
    ok = json.loads((ROOT / "examples" / "amr.cmd.vel.json").read_text())
    ok.pop("topic")
    schemas.validate(ok)
    for bad in ({"v": 2.0}, {"w": 3.0}, {"extra": 1}):
        m = json.loads(json.dumps(ok))
        m["data"].update(bad)
        with pytest.raises(jsonschema.ValidationError):
            schemas.validate(m)
    lane = json.loads((ROOT / "examples" / "shuttle.cmd.json").read_text())
    lane.pop("topic")
    lane["data"]["lane"] = "R4-W-LV01-N01"
    with pytest.raises(jsonschema.ValidationError):
        schemas.validate(lane)


def test_ids():
    assert len(ids.SHUTTLES) == 30 and ids.shuttle_for_lane("R2-E-LV07-N33") == "MS-R2-LV07"
    assert ids.parse_lane("R1-W-LV10-N01") == (1, "W", 10, 1)
    for bad in ("R1-W-LV11-N01", "R1-X-LV01-N01", "R1-W-LV01-N34", "R1-W-LV1-N01"):
        with pytest.raises(ValueError):
            ids.parse_lane(bad)


def test_geometry_matches_layouts():
    lay = G.layout()
    assert lay["params"]["lanes_total"] == 1980 and lay["params"]["lane_depth"] == ids.DEPTH
    assert lay["params"]["lanes_per_side"] == ids.ROWS
    # 모든 정지점 쌍이 nav 그래프로 이어진다
    stops = list(G.STOP_NODES)
    for a in stops:
        for b in stops:
            if a != b:
                assert G.path_length(G.shortest_path(a, b)) > 0
    # 레인 위치: 레일 쪽 1번이 레일에 붙어 있고 14번이 레일에서 가장 멀다
    for lane in ("R1-W-LV01-N01", "R2-E-LV05-N17", "R3-W-LV10-N33"):
        r = ids.parse_lane(lane)[0]
        rail = next(x for x in lay["rails"] if x["id"] == f"R{r}")
        x1, y1, _ = G.lane_xyz(lane, 1)
        x14, _, _ = G.lane_xyz(lane, 14)
        assert abs(x1 - rail["center_x"]) < abs(x14 - rail["center_x"])
        assert 31.4 < y1 < 80
    assert G.lane_xyz("R1-W-LV03-N01")[2] == pytest.approx(3.0)
    # Unity 변환
    assert G.to_unity(10, 20, 3) == (10, 3, 20)
    assert G.theta_to_unity_yaw_deg(math.pi / 2) == pytest.approx(0.0)


def test_geometry_matches_d_plan():
    """도면 코드(plans/d_plan.py)가 있으면 레일·도크 좌표가 같은지 확인."""
    import sys
    algo = ROOT.parent
    if not (algo / "plans" / "d_plan.py").exists():
        pytest.skip("plans/d_plan.py 없음")
    sys.path.insert(0, str(algo))
    from plans import d_plan
    g = d_plan.geometry()
    for i, r in enumerate(g["rails"]):
        cx = (r["rail"][0] + r["rail"][1]) / 2
        assert G.lift_xy(f"L{i + 1}")[0] == pytest.approx(cx, abs=0.05)
    for i, x in enumerate(d_plan.IN_X):
        assert G.stop_xy(f"SC-IN{i + 1}")[0] == pytest.approx(x)
