"""비상 구역 회피 테스트 (보류 중인 코드지만 동작은 유지)."""
import pytest

from emergency import zone_for
from warehouse import EmergencyEvent, FleetConfig, FleetSimulator, NearestSlotting, SimConfig, Simulator, WarehouseLayout


@pytest.fixture(scope="module")
def L():
    return WarehouseLayout(rack_blocks=3, rack_length=6)


def test_emergency_zone_avoided(L):
    sim = Simulator(L, SimConfig(n_skus=12, n_orders=200, record_trips=10**6))
    r = sim.run(NearestSlotting(L, sim.oracle))
    z = zone_for("leak", (L.dock[0] + 1, 3), L)
    f = FleetSimulator(L, FleetConfig(n_amr=4, order_interval=10), sim.oracle).run(
        r.recorded, [EmergencyEvent(60, z.cells, kind="leak")])
    assert f.collisions == 0
    # 구역 발생 후, 구역 안에 있다가 빠져나오는 경우를 제외하면 진입 없음
    for tr in f.tracks:
        for t in range(61, len(tr)):
            if tr[t] in z.cells:
                assert tr[t - 1] in z.cells, "entered a blocked zone"
