"""D4 알고리즘 테스트: 배치 데이터, 레인 규칙, 짧은 통합 시뮬레이션."""
import random

import pytest

from warehouse.d4.layout import DEPTH, LEVEL_MAX_KG, D4Layout
from warehouse.d4.sim import D4Sim, SimConfig
from warehouse.d4.storage import Storage, StoragePolicy
from warehouse.pathfinding import DistanceOracle


@pytest.fixture(scope="module")
def L():
    return D4Layout()


def test_layout_matches_plan(L):
    assert len(L.lanes) == 3 * 2 * 33 * 10
    assert L.capacity == 27720
    assert len(L.in_docks) == len(L.out_docks) == 6
    assert len(L.chargers) == 6


def test_contacts_reachable(L):
    o = DistanceOracle(L)
    cells = list(L.contacts)
    for c in cells:
        assert L.is_walkable(c)
        assert o.dist(cells[0], c) < 10 ** 6


def test_ids(L):
    assert L.amr_id(0) == "AMR01"
    assert L.lane_id(L.lanes[0]) == "R1-W-LV01-N01"
    assert L.shuttle_id(2, 9) == "MS-R3-LV10"
    names = {v[0] for v in L.contacts.values()}
    assert {"IN1", "OUT6", "L2-IN", "L3-OUT", "QC-IN", "CHW1", "CHE3"} <= names


def _storage(L, **kw):
    return Storage(L, StoragePolicy(**kw), {"A": 0.0, "B": .5}, {"A": 600, "B": 1400}, random.Random(0))


def test_weight_limit_respected(L):
    st = _storage(L)
    for _ in range(50):
        ln = st.choose_lane("B", 1400, {})
        assert LEVEL_MAX_KG[ln.lane.level] >= 1400
        st.reserve_in(ln, "B")
        st.commit_in(ln)


def test_one_sku_per_lane_and_lifo_depth(L):
    st = _storage(L)
    ln = st.choose_lane("A", 600, {})
    st.reserve_in(ln, "A")
    assert st.commit_in(ln) == DEPTH - 1           # 첫 팔레트는 가장 안쪽
    ln2 = st.choose_lane("A", 600, {})
    assert ln2 is ln                                # 같은 SKU 레인 보충
    st.reserve_in(ln2, "A")
    assert st.commit_in(ln2) == DEPTH - 2
    other = st.choose_lane("B", 1400, {})
    assert other is not ln                          # 다른 SKU는 다른 레인
    st.reserve_out(ln)
    assert st.commit_out(ln) == DEPTH - 2           # 마지막에 넣은 것을 먼저 꺼냄


def test_sim_short_run_no_collisions():
    sim = D4Sim(SimConfig(hours=1.5, in_per_h=40, out_per_h=40, seed=3))
    m = sim.run()
    assert m["collisions"] == 0
    assert m["in_per_h"] > 10 and m["out_per_h"] > 10
    assert all(p.lane.lane.level <= 9 for p in sim.done_in)
    for p in sim.done_in:
        assert LEVEL_MAX_KG[p.lane.lane.level] >= p.measured
    assert sim.bus.counts.get("amr", 0) > 0


def test_mismatch_goes_to_hold():
    sim = D4Sim(SimConfig(hours=1, in_per_h=60, out_per_h=0, mismatch=1.0, seed=1))
    m = sim.run()
    assert m["in_per_h"] == 0 and m["held"] > 0
