import sys, json; sys.path.insert(0, "/home/user/warehouse-algo")
import warehouse.shuttle as S
from experiments.shuttle_compare import capacity
from warehouse.shuttle import Policy, Layout, LiftSpec
base = S.make_layouts()["D2 남쪽 도크 + 사무실 남쪽 중앙"]
cases = [("3레일·깊이10·40줄", 3, 10, 40), ("3레일·깊이12·34줄", 3, 12, 34),
         ("2레일·깊이15·40줄", 2, 15, 40), ("2레일·깊이16·38줄", 2, 16, 38)]
out = {}
for name, nr, d, n in cases:
    module = 2 * d * 1.3 + 1.8
    width = nr * module
    x0 = 60 - width / 2
    rx = [x0 + module * i + module / 2 for i in range(nr)]
    S.DEPTH, S.RAIL_X = d, rx
    lanes = [21.6 + (i + .5) * 1.4 for i in range(n)]
    L = Layout(name, lanes, [LiftSpec(r, base.lifts[0].y, base.lifts[0].aisle_y, x, frozenset({"in","out"})) for r, x in enumerate(rx)],
               base.aisle_rows, in_pts=base.in_pts, out_pts=base.out_pts, dock_row=base.dock_row)
    cap = nr * 2 * n * d * 10
    side = 120 - width
    free = side * 57.2 + width * (57.2 - n * 1.4)
    r = dict(rails=nr, depth=d, rows=n, positions=cap, lanes=nr*2*n*10, width=round(width,1), free_m2=round(free),
             thr4=capacity(L, Policy(), 4), thr40=capacity(L, Policy(), 40))
    out[name] = r; print(name, r, flush=True)
json.dump(out, open("/home/user/warehouse-algo/out/depth_compare.json", "w"), ensure_ascii=False, indent=1)
