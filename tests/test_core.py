import random

import pytest

from warehouse import (ClassBasedSlotting, DistanceOracle, FleetConfig, FleetSimulator,
                       NearestSlotting, RandomSlotting, ROUTERS, SimConfig, Simulator, WarehouseLayout,
                       astar, classify_abc)
from warehouse.env import SlottingEnv
from warehouse.inventory import Inventory, Pallet
from warehouse.routing import tour_length


@pytest.fixture(scope="module")
def L():
    return WarehouseLayout(rack_blocks=3, rack_length=6)


def test_layout_counts(L):
    assert len(L.slots) == 3 * 2 * 6 * L.levels
    for s in L.slots:
        assert L.is_walkable(L.access_point(s))
    assert L.is_walkable(L.dock) and L.is_walkable(L.inbound_dock)


def test_cross_aisle_keeps_capacity():
    a = WarehouseLayout(rack_blocks=4, rack_length=10, cross_aisles=0)
    b = WarehouseLayout(rack_blocks=4, rack_length=10, cross_aisles=1)
    assert len(a.slots) == len(b.slots)
    assert all(b.is_walkable((x, b.cross_rows[0])) for x in range(b.width))


def test_astar_matches_bfs(L):
    o = DistanceOracle(L)
    rng = random.Random(0)
    cells = [(x, y) for x in range(L.width) for y in range(L.height) if L.is_walkable((x, y))]
    for _ in range(50):
        a, b = rng.sample(cells, 2)
        p = astar(L, a, b)
        assert len(p) - 1 == o.dist(a, b)
        assert all(abs(p[i][0] - p[i + 1][0]) + abs(p[i][1] - p[i + 1][1]) == 1 for i in range(len(p) - 1))


def test_optimal_route_is_best(L):
    o = DistanceOracle(L)
    rng = random.Random(1)
    for _ in range(10):
        stops = list({L.access_point(s) for s in rng.sample(L.slots, 6)})
        lens = {k: tour_length(o, L.dock, f(L, o, L.dock, stops)) for k, f in ROUTERS.items()}
        assert lens["optimal"] <= lens["nn2opt"] and lens["optimal"] <= lens["sshape"]
        assert sorted(ROUTERS["nn2opt"](L, o, L.dock, stops)) == sorted(stops)


def test_abc_classes():
    c = classify_abc({"a": 50, "b": 30, "c": 10, "d": 5, "e": 5})
    assert c["a"] == "A" and c["b"] == "A" and c["c"] == "B" and c["e"] == "C"


def test_weight_limits_respected(L):
    sim = Simulator(L, SimConfig(n_skus=12, n_orders=300))
    r = sim.run(ClassBasedSlotting(L, sim.oracle, sim.demand))
    assert r.weight_violations == 0
    for (x, y), load in sim.inventory.bay_load.items():
        assert load <= L.bay_max_kg + 1e-6


def test_inspection_holds_mismatch(L):
    sim = Simulator(L, SimConfig(n_skus=12, n_orders=10))
    sim.cfg.weight_tolerance = 0.05
    ok = Pallet(1, "SKU000", 0, 1000, 1020)
    bad = Pallet(2, "SKU000", 0, 1200, 1000)
    assert sim.inspect(ok) and not sim.inspect(bad)


def test_abc_beats_random(L):
    def cost(make):
        sim = Simulator(L, SimConfig(n_skus=12, n_orders=600, seed=3))
        return sim.run(make(sim)).cost_per_line
    abc = cost(lambda s: ClassBasedSlotting(L, s.oracle, s.demand))
    rnd = cost(lambda s: RandomSlotting(3))
    assert abc < rnd


def test_fleet_no_collisions(L):
    sim = Simulator(L, SimConfig(n_skus=12, n_orders=200, record_trips=10**6))
    r = sim.run(NearestSlotting(L, sim.oracle))
    f = FleetSimulator(L, FleetConfig(n_amr=4, order_interval=10), sim.oracle).run(r.recorded)
    assert f.collisions == 0
    assert len(f.logs) == len(r.recorded)
    naive = FleetSimulator(L, FleetConfig(n_amr=4, order_interval=10, avoidance=False), sim.oracle).run(r.recorded)
    assert naive.collisions > 0


def test_env_roundtrip(L):
    env = SlottingEnv(L, SimConfig(n_skus=12, n_orders=30))
    obs, _ = env.reset(seed=0)
    steps = 0
    done = False
    while not done:
        a = next(i for i, ok in enumerate(obs["action_mask"]) if ok)
        obs, rew, done, _, _ = env.step(a)
        assert rew <= 0
        steps += 1
    assert steps > 0
