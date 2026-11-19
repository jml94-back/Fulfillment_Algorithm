"""D4 배치를 Unity 씬 배치용 JSON + 확인 도면으로 내보낸다.

    cd warehouse-algo && python -m unity.export_d4      → unity/d4_layout.json, unity/d4_check.png

치수의 단일 원본은 plans/d_plan.py (+ plans/shuttle_plan.py 상수). 여기서는 숫자를 새로 정하지 않고 가져다 쓴다.
단, 도면에 없던 높이 값(도크 문 높이, 사무실 층고, 컨베이어 높이 등)은 ASSUMED 에 모아 두었다.

좌표계 (Unity, 왼손, Y-up, 1 unit = 1 m)
- 원점: 창고 바닥 남서쪽 안쪽 모서리.
- +X = 동쪽 (도면 x, 0~120), +Z = 북쪽 (도면 y, 0~80), +Y = 위.
- 위에서 내려다본(Top) 뷰가 도면과 같은 방향이다 (좌우 반전 없음).
- 남쪽 벽 바깥(트럭 대기)은 Z < 0.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from plans import d_plan as D
from plans.shuttle_plan import (BUILDING_H, DOCK_W, LANE, LEVEL_H, LEVELS, PALLET, PD_BUF, RACK_H, RAIL, SLOT,
                                STAGE_DEPTH, W, H)

HERE = Path(__file__).resolve().parent
R = lambda v: round(float(v), 3)  # noqa: E731

# 도면에 없는 높이·두께는 가정값. Unity 팀원이 바꿔도 평면 좌표에는 영향 없음.
ASSUMED = {
    "wall_thickness": 0.3,        # 벽은 0~120 / 0~80 바깥쪽으로 두께를 준다 (내부 치수 유지)
    "dock_door_height": 4.5,
    "office_floor_height": 4.0,   # 2층 → 8.0 m
    "conveyor_height": 0.6,       # 리프트 입·출고 버퍼 컨베이어 윗면
    "weigh_station_height": 0.1,
    "dock_leveler_height": 0.05,
    "charger_height": 1.5,
    "lift_height": RACK_H + 1.0,  # 랙 최상단 + 구동부
    "facility_wall_height": 2.5,  # 검수 띠 안 시설(칸막이) 높이
    "marking_height": 0.01,       # 바닥 표시(구역·통로·적치 칸)
    "pallet_load_height": 1.2,    # 팔레트 + 화물 높이 (층고 1.5 m 중)
}

COL = {"wall": "#3a3936", "floor": "#f3f2ee", "inbound": "#d7f0e6", "outbound": "#fbe1d6", "office": "#ecebe7",
       "spare": "#ffffff", "aisle": "#fff7e0", "band": "#f6f5f2", "facility": "#ffffff", "rack": "#2a78d6",
       "rail": "#6250d6", "lift": "#6250d6", "buf_in": "#9fd9c2", "buf_out": "#f6b79c", "dock_in": "#1baf7a",
       "dock_out": "#eb6834", "charger": "#eda100", "truck": "#d9d8d3"}


def build() -> dict:
    g = D.geometry()
    A = ASSUMED
    boxes: list[dict] = []

    def add(id_, type_, label, x0, x1, z0, z1, y0, y1, color, **extra):
        b = {"id": id_, "type": type_, "label": label,
             "center": [R((x0 + x1) / 2), R((y0 + y1) / 2), R((z0 + z1) / 2)],
             "size": [R(x1 - x0), R(y1 - y0), R(z1 - z0)], "color": color}
        b.update(extra)
        boxes.append(b)
        return b

    mk = A["marking_height"]
    t = A["wall_thickness"]

    # ---- 건물 -------------------------------------------------------------------------------------------
    add("floor", "floor", "바닥 120 x 80", 0, W, 0, H, -0.1, 0, COL["floor"])
    add("wall_N", "wall", "북쪽 벽", -t, W + t, H, H + t, 0, BUILDING_H, COL["wall"])
    add("wall_W", "wall", "서쪽 벽", -t, 0, 0, H, 0, BUILDING_H, COL["wall"])
    add("wall_E", "wall", "동쪽 벽", W, W + t, 0, H, 0, BUILDING_H, COL["wall"])
    dock_x = [("IN", i + 1, x) for i, x in enumerate(D.IN_X)] + [("OUT", i + 1, x) for i, x in enumerate(D.OUT_X)]
    doors = sorted((x - DOCK_W / 2, x + DOCK_W / 2) for _, _, x in dock_x)
    # 남쪽 벽: 문 사이 기둥 구간 + 문 위 상인방
    cur, k = -t, 1
    for a, b in doors + [(W + t, W + t)]:
        if a > cur:
            add(f"wall_S{k:02d}", "wall", "남쪽 벽", cur, a, -t, 0, 0, BUILDING_H, COL["wall"])
            k += 1
        cur = b
    for a, b in doors:
        add(f"wall_S_lintel_{R((a + b) / 2)}", "wall", "도크 문 위 벽", a, b, -t, 0, A["dock_door_height"], BUILDING_H,
            COL["wall"])

    # ---- 남쪽 띠: 입고 / 사무실 / 출고 -------------------------------------------------------------------
    add("zone_inbound", "zone", "입고·검수 구역", *D.IN_ZONE, 0, D.STAGE, 0, mk, COL["inbound"])
    add("zone_outbound", "zone", "출고 스테이징 구역", *D.OUT_ZONE, 0, D.STAGE, 0, mk, COL["outbound"])
    ox0, ox1 = g["office_x"][0]
    add("office", "office", "사무실·관제실·휴게 (2층)", ox0, ox1, 0, D.STAGE, 0, 2 * A["office_floor_height"],
        COL["office"], floors=2, floor_height=A["office_floor_height"])
    add("spare_IN", "spare_area", "입고 예비 구역 (초과 적치·검수 대기)", *D.IN_SPARE, 1.8, 10.2, 0, mk, COL["spare"])
    add("spare_OUT", "spare_area", "출고 예비 구역 (초과 적치·합포장 대기)", *D.OUT_SPARE, 1.8, 10.2, 0, mk, COL["spare"])

    docks = []
    for kind, i, x in dock_x:
        did = f"{kind}{i}"
        c = COL["dock_in"] if kind == "IN" else COL["dock_out"]
        add(f"dock_{did}_door", "dock_door", f"도크 {did} 문 ({DOCK_W} m)", x - DOCK_W / 2, x + DOCK_W / 2, -t, 0, 0,
            A["dock_door_height"], c, opening=True)
        add(f"dock_{did}_leveler", "dock_leveler", f"도크 {did} 레벨러", x - DOCK_W / 2, x + DOCK_W / 2, 0, 1.2, 0,
            A["dock_leveler_height"], c)
        add(f"dock_{did}_truck", "truck_bay", f"도크 {did} 트럭 정차 칸 (건물 밖)", x - 1.25, x + 1.25, -15.5, -1.5, 0, mk,
            COL["truck"], outdoor=True)
        st = "weigh_station" if kind == "IN" else "drop_station"
        st_id = f"SC-IN{i}" if kind == "IN" else f"DP-OUT{i}"
        add(st_id, st, f"도크 {did} {'계량대' if kind == 'IN' else '하차대'}", x - 1.2, x + 1.2, 10.6, 13.0, 0,
            A["weigh_station_height"], c)
        slots = []
        for li, dx in enumerate((-1.8, -.6, .6, 1.8)):
            for j in range(STAGE_DEPTH):
                cx, cz = x + dx, 1.8 + j * 1.2 + .55
                sid = f"dock_{did}_stage_{li + 1}{j + 1}"
                add(sid, "staging_slot", f"도크 {did} 적치 칸 {li + 1}-{j + 1}", cx - .55, cx + .55, cz - .55, cz + .55, 0,
                    mk, COL["buf_in"] if kind == "IN" else COL["buf_out"])
                slots.append(sid)
        docks.append({"id": did, "kind": kind.lower(), "index": i, "door_center": [R(x), 0.0, 0.0],
                      "station_id": st_id, "station_center": [R(x), 0.0, 11.8], "station_type": st, "amr_contact": [R(x), 0.0, 13.6],
                      "truck_bay_center": [R(x), 0.0, -8.5], "staging_slots": slots})

    # ---- AMR 통로 · 리프트 줄 --------------------------------------------------------------------------
    add("amr_aisle", "amr_aisle", "AMR 통로 4.8 m (양방향, 방향은 알고리즘이 시간 예약으로 결정)", 0, W, *D.AISLE, 0, mk,
        COL["aisle"])

    lifts, buffers, rails = [], [], []
    lane_top = D.LANE_Y0 + g["lanes_per_side"] * LANE
    for r, rl in enumerate(g["rails"], start=1):
        ra, rb = rl["rail"]
        cx = (ra + rb) / 2
        rails.append({"id": f"R{r}", "x_range": [R(ra), R(rb)], "z_range": [R(D.LIFT_ROW[1]), R(lane_top)],
                      "center_x": R(cx), "levels": LEVELS, "level_height": LEVEL_H,
                      "levels_id": [f"LV{lv + 1:02d}" for lv in range(LEVELS)],
                      "mother_shuttles": [f"MS-R{r}-LV{lv + 1:02d}" for lv in range(LEVELS)],
                      "satellites": [f"SAT-R{r}-LV{lv + 1:02d}" for lv in range(LEVELS)]})
        add(f"R{r}", "rail", f"모셔틀 레일 R{r} (층마다 모셔틀 1대)", ra, rb, D.LIFT_ROW[1], lane_top, 0, RACK_H,
            COL["rail"], levels=LEVELS)
        add(f"L{r}", "lift", f"수직 리프트 L{r} (입·출고 겸용)", cx - 1.2, cx + 1.2, *D.LIFT_ROW, 0,
            A["lift_height"], COL["lift"])
        lifts.append({"id": f"L{r}", "rail": f"R{r}", "center": [R(cx), 0.0, R(sum(D.LIFT_ROW) / 2)],
                      "serves_levels": list(range(LEVELS))})
        for kind, sign in (("in", -1), ("out", 1)):
            slots = []
            for k in range(PD_BUF):   # k=0 리프트 바로 옆, k=PD_BUF-1 AMR 접점 쪽 끝
                x0 = cx - 1.2 - (k + 1) * SLOT + .1 if sign < 0 else cx + 1.2 + k * SLOT + .1
                sid = f"L{r}-{kind.upper()}-{k + 1}"
                add(sid, "conveyor_slot", f"L{r} {'입고' if kind == 'in' else '출고'} 버퍼 {k + 1}", x0, x0 + SLOT - .2,
                    D.LIFT_ROW[0] + .3, D.LIFT_ROW[0] + 2.1, 0, A["conveyor_height"],
                    COL["buf_in"] if kind == "in" else COL["buf_out"])
                slots.append(sid)
            end = boxes[-1]["center"]
            buffers.append({"id": f"L{r}-{kind.upper()}", "lift": f"L{r}", "kind": kind, "slots_from_lift": slots,
                            "flow": "AMR→리프트 (서쪽 끝에서 넣음)" if kind == "in" else "리프트→AMR (동쪽 끝에서 꺼냄)",
                            "amr_contact": [end[0], 0.0, R(D.AISLE[1] - 0.6)]})

    # ---- 검수·지원 띠 -----------------------------------------------------------------------------------
    add("band", "zone", "검수·지원 띠", 0, W, *D.BAND, 0, mk, COL["band"])
    edges = [0.0] + [v for rl in g["rails"] for v in rl["rail"]] + [W]
    segs = [(edges[i], edges[i + 1]) for i in range(0, len(edges), 2)]
    y0, y1 = D.BAND[0] + .8, D.BAND[1] - .8
    fac = [("fac_maintenance", "셔틀·리프트 정비", segs[0][0] + 10.5, segs[0][1] - .8),
           ("QC-IN", "입고 검수·보류 (무게 불일치)", segs[1][0] + .8, segs[1][1] - .8),
           ("QC-OUT", "포장·라벨링·출고 검수", segs[2][0] + .8, segs[2][1] - .8),
           ("fac_spare_parts", "예비 셔틀·부품", segs[3][0] + .8, segs[3][1] - 10.5)]
    facilities = []
    for fid, label, a, b in fac:
        add(fid, "facility", label, a, b, y0, y1, 0, A["facility_wall_height"], COL["facility"], area_m2=R((b - a) * (y1 - y0)))
        facilities.append({"id": fid, "label": label, "amr_contact": [R((a + b) / 2), 0.0, R(y0 - 0.6)]})

    chargers = []
    for side in ("W", "E"):
        for k in range(3):
            cz = y0 + .2 + k * 2.9
            if side == "W":
                bx, px = (0, .6), (.6, 2.4)
            else:
                bx, px = (W - .6, W), (W - 2.4, W - .6)
            cid = f"CH{side}{k + 1}"
            add(cid, "charger", f"AMR 충전기 {cid} (벽면)", *bx, cz, cz + 2.0, 0, A["charger_height"], COL["charger"])
            add(f"{cid}_park", "charger_park", f"{cid} 충전 정차 칸", *px, cz - .1, cz + 2.1, 0, mk, COL["charger"])
            chargers.append({"id": cid, "wall": side, "park_center": [R(sum(px) / 2), 0.0, R(cz + 1.0)],
                             "heading_deg": 270.0 if side == "W" else 90.0})
    add("amr_wait_W", "amr_wait", "AMR 대기 (서쪽)", 2.4, segs[0][0] + 10.5 - .8, y0, y1, 0, mk, COL["aisle"])
    add("amr_wait_E", "amr_wait", "AMR 대기 (동쪽)", segs[3][1] - 10.5 + .8, W - 2.4, y0, y1, 0, mk, COL["aisle"])
    # 리프트 줄 안 AMR 쉬는 자리 (알고리즘 스레드 warehouse/d4 와 같은 좌표)
    wait_spots = []
    for i, wx in enumerate((33.0, 36.6, 84.6, 87.0), start=1):
        wz = 19.8
        add(f"WAIT{i}", "amr_wait_spot", f"AMR 쉬는 자리 WAIT{i}", wx - .6, wx + .6, wz - .6, wz + .6, 0, mk, COL["charger"])
        wait_spots.append({"id": f"WAIT{i}", "center": [wx, 0.0, wz]})

    # ---- 보관 레인 ----------------------------------------------------------------------------------------
    lanes = []
    n = g["lanes_per_side"]
    for r, rl in enumerate(g["rails"], start=1):
        for side, key in (("W", "west_block"), ("E", "east_block")):
            a, b = rl[key]
            add(f"rack_R{r}{side}", "rack_block", f"랙 블록 R{r}{side} ({n}줄 x 깊이 {D.DEPTH} x {LEVELS}층)", a, b,
                D.LANE_Y0, lane_top, 0, RACK_H, COL["rack"], rows=n, depth=D.DEPTH, levels=LEVELS)
            # 슬롯 0 = 레일 바로 옆 (입·출구), 깊이 방향으로 레일에서 멀어짐 (선입후출)
            step = -SLOT if side == "W" else SLOT
            s0 = b - SLOT / 2 if side == "W" else a + SLOT / 2
            for row in range(n):
                cz = D.LANE_Y0 + row * LANE + LANE / 2
                lanes.append({"id": f"R{r}-{side}-N{row + 1:02d}", "rail": f"R{r}", "side": side, "row": row + 1,
                              "center_z": R(cz), "slot0_x": R(s0), "slot_step_x": R(step)})

    # ---- AMR 내비 그래프 (통로 중앙선 + 접점 연결) -----------------------------------------------------------
    zc = R(sum(D.AISLE) / 2)
    nodes, edges_ = {}, []

    def node(nid, x, z, kind):
        nodes[nid] = {"id": nid, "pos": [R(x), 0.0, R(z)], "kind": kind}

    for d in docks:
        x = d["door_center"][0]
        node(f"n_dock_{d['id']}", x, 11.8, d["station_type"])
        node(f"n_aisle_{d['id']}", x, zc, "aisle")
        edges_.append([f"n_dock_{d['id']}", f"n_aisle_{d['id']}"])
    for bf in buffers:
        x = bf["amr_contact"][0]
        node(f"n_buf_{bf['id']}", x, bf["amr_contact"][2], f"buffer_{bf['kind']}")
        node(f"n_aisle_{bf['id']}", x, zc, "aisle")
        edges_.append([f"n_buf_{bf['id']}", f"n_aisle_{bf['id']}"])
    for side, xc in (("W", 4.5), ("E", W - 4.5)):
        node(f"n_aisle_CH_{side}", xc, zc, "aisle")
        prev = f"n_aisle_CH_{side}"
        for c in [c for c in chargers if c["wall"] == side]:
            jn = f"n_jn_{c['id']}"
            node(jn, xc, c["park_center"][2], "junction")
            node(f"n_{c['id']}", c["park_center"][0], c["park_center"][2], "charger")
            edges_ += [[prev, jn], [jn, f"n_{c['id']}"]]
            prev = jn
    fq = next(f for f in facilities if f["id"] == "QC-IN")
    node("n_inbound_qc", fq["amr_contact"][0], fq["amr_contact"][2], "inbound_qc")
    node("n_aisle_inbound_qc", fq["amr_contact"][0], zc, "aisle")
    edges_.append(["n_inbound_qc", "n_aisle_inbound_qc"])
    fo = next(f for f in facilities if f["id"] == "QC-OUT")
    node("n_outbound_qc", fo["amr_contact"][0], fo["amr_contact"][2], "outbound_qc")
    node("n_aisle_outbound_qc", fo["amr_contact"][0], zc, "aisle")
    edges_.append(["n_outbound_qc", "n_aisle_outbound_qc"])
    for w in wait_spots:
        node(f"n_{w['id']}", w["center"][0], w["center"][2], "wait")
        node(f"n_aisle_{w['id']}", w["center"][0], zc, "aisle")
        edges_.append([f"n_{w['id']}", f"n_aisle_{w['id']}"])
    aisle = sorted((v for v in nodes.values() if v["kind"] == "aisle"), key=lambda v: v["pos"][0])
    edges_ += [[p["id"], q["id"]] for p, q in zip(aisle, aisle[1:])]
    edges_ = [{"from": a, "to": b, "length": R(abs(nodes[a]["pos"][0] - nodes[b]["pos"][0]) + abs(nodes[a]["pos"][2] - nodes[b]["pos"][2]))}
              for a, b in edges_]

    capacity = len(lanes) * D.DEPTH * LEVELS
    assert capacity == g["pallet_positions"] == 27720, capacity

    return {
        "meta": {
            "name": "물류창고 배치 D4", "version": "1.0", "source": "plans/d_plan.py (geometry, 상수)",
            "units": "meter", "coordinate_system": {
                "handedness": "Unity left-handed, Y-up", "origin": "창고 바닥 남서쪽 안쪽 모서리 (0,0,0)",
                "+X": "동쪽 (도면 x, 0~120)", "+Y": "위 (높이)", "+Z": "북쪽 (도면 y, 0~80)",
                "note": "Top 뷰가 도면과 같은 방향. 남쪽 벽 바깥 트럭 칸은 Z<0. 각 box는 center(x,y,z)와 size(x,y,z), 회전 없음."},
            "assumed_heights": ASSUMED,
        },
        "params": {"width_x": W, "depth_z": H, "building_height": BUILDING_H, "rack_height": RACK_H, "levels": LEVELS,
                   "level_height": LEVEL_H, "pallet": [PALLET, PALLET], "slot_pitch": SLOT, "lane_width": LANE,
                   "lane_depth": D.DEPTH, "rail_width": RAIL, "rails": len(rails), "lanes_per_side": n,
                   "floor_lanes": len(lanes), "lanes_total": len(lanes) * LEVELS, "pallet_positions": capacity,
                   "shuttle_sets": g["shuttle_sets"], "lift_buffer_slots_each_side": PD_BUF,
                   "staging_slots_per_dock": 4 * STAGE_DEPTH, "amr_count": 4, "chargers": len(chargers),
                   "bands_z": {"dock_staging": [0, D.STAGE], "amr_aisle": list(D.AISLE), "lift_row": list(D.LIFT_ROW),
                               "inspection_band": list(D.BAND), "lanes": [D.LANE_Y0, R(lane_top)],
                               "north_margin": [R(lane_top), H]}},
        "docks": docks, "rails": rails, "lifts": lifts, "buffers": buffers, "facilities": facilities,
        "chargers": chargers,
        "wait_spots": wait_spots,
        "amrs": [{"id": f"AMR{i + 1:02d}", "home_charger": ch, "spawn": next(c["park_center"] for c in chargers if c["id"] == ch)}
                 for i, ch in enumerate(["CHW1", "CHW2", "CHE1", "CHE2"])],
        "storage": {"id_rule": "층별 레인 ID = R{레일}-{W|E}-LV{층 01~10}-N{줄 01~33, 남쪽부터}. 레인 안 위치 P = 1(레일 쪽 입·출구)~14.",
                    "slot_rule": "위치 P(1~depth) 중심 x = slot0_x + (P-1)*slot_step_x, z = center_z. 층 LVnn 의 팔레트 바닥 "
                                 "y = (nn-1)*level_height. 위치 1이 레일 쪽 입·출구(선입후출).",
                    "lanes": lanes},
        "nav": {"note": "AMR 바닥 경로 그래프. 통로는 양방향이며 진행 방향은 알고리즘이 시간 예약으로 정한다. "
                        "edge = {from, to, length(m)}, 양방향.", "aisle_center_z": zc,
                "nodes": list(nodes.values()), "edges": edges_},
        "boxes": boxes,
    }


def draw_check(data: dict, path: Path) -> None:
    from plans.shuttle_plan import INK, INK2, SURF  # noqa: F401  (한글 폰트 설정 포함)
    fig, ax = plt.subplots(figsize=(15, 12))
    fig.patch.set_facecolor(SURF)
    ax.set_facecolor(SURF)
    order = ["floor", "zone", "truck_bay", "amr_aisle", "amr_wait", "amr_wait_spot", "spare_area", "staging_slot", "office", "facility",
             "rack_block", "rail", "dock_leveler", "weigh_station", "drop_station", "conveyor_slot", "lift", "charger_park",
             "charger", "wall", "dock_door"]
    for typ in order:
        for b in (b for b in data["boxes"] if b["type"] == typ):
            (cx, _, cz), (sx, _, sz) = b["center"], b["size"]
            fc = b["color"] if typ not in ("rack_block", "rail") else ("#dce8f8" if typ == "rack_block" else "#e9e6fb")
            ls = "--" if typ in ("spare_area", "truck_bay", "charger_park") else "-"
            ax.add_patch(Rectangle((cx - sx / 2, cz - sz / 2), sx, sz, fc=fc, ec="#52514e", lw=.4, ls=ls,
                                   zorder=order.index(typ)))
    # 레인 경계
    for ln in data["storage"]["lanes"]:
        x0 = ln["slot0_x"] - ln["slot_step_x"] / 2
        x1 = x0 + ln["slot_step_x"] * data["params"]["lane_depth"]
        ax.plot([x0, x1], [ln["center_z"] - LANE / 2] * 2, color="#2a78d6", lw=.25, zorder=11)
    for lab in ("rack_block", "rail", "lift", "facility", "office", "spare_area"):
        for b in (b for b in data["boxes"] if b["type"] == lab):
            name = b["id"].replace("rack_", "").replace("rail_", "").replace("lift_", "").replace("fac_", "")
            rot = 90 if lab == "rail" else 0
            ax.text(b["center"][0], b["center"][2], name, ha="center", va="center", fontsize=7, rotation=rot, zorder=30,
                    bbox=dict(fc="white", ec="none", pad=.3, alpha=.85))
    for d in data["docks"]:
        ax.text(d["door_center"][0], -.9, d["id"], ha="center", va="center", fontsize=6, zorder=30)
    for c in data["chargers"]:
        ax.text(c["park_center"][0], c["park_center"][2], c["id"][3:], ha="center", va="center", fontsize=5.5, zorder=30)
    # 내비 그래프
    pos = {n["id"]: n["pos"] for n in data["nav"]["nodes"]}
    for e in data["nav"]["edges"]:
        a, b = e["from"], e["to"]
        ax.plot([pos[a][0], pos[b][0]], [pos[a][2], pos[b][2]], color="#d03b3b", lw=1.1, zorder=40)
    for n in data["nav"]["nodes"]:
        ax.plot(n["pos"][0], n["pos"][2], "o", ms=3.2 if n["kind"] != "aisle" else 2, color="#d03b3b", zorder=41)
    # 좌표축 표시
    ax.annotate("", xy=(10, -20), xytext=(-6, -20), arrowprops=dict(arrowstyle="-|>", color="#d03b3b", lw=1.5))
    ax.annotate("", xy=(-6, -4), xytext=(-6, -20), arrowprops=dict(arrowstyle="-|>", color="#1f6fd1", lw=1.5))
    ax.text(11, -20, "+X 동", va="center", fontsize=9, color="#d03b3b")
    ax.text(-6, -3, "+Z 북", ha="center", fontsize=9, color="#1f6fd1")
    ax.text(-6.5, -21.5, "원점 (0,0,0) = 바닥 남서쪽 안쪽 모서리", fontsize=8, color="#52514e", va="top")
    ax.plot(0, 0, "o", color="black", ms=5, zorder=50)
    for v in (0, 20, 40, 60, 80, 100, 120):
        ax.text(v, H + 1.6, f"x={v}", ha="center", fontsize=7, color="#52514e")
    for v in (0, 14.4, 19.2, 21.6, 31.4, 77.6, 80):
        ax.text(W + 1.2, v, f"z={v}", va="center", fontsize=7, color="#52514e")
    ax.set_xlim(-14, W + 9)
    ax.set_ylim(-24, H + 4)
    ax.set_aspect("equal")
    ax.axis("off")
    p = data["params"]
    ax.set_title(f"D4 Unity 배치 데이터 확인 도면 (d4_layout.json 에서 다시 그림) · 빨간 선 = AMR 내비 그래프\n"
                 f"box {len(data['boxes'])}개 · 레인 {p['floor_lanes']}줄 x {p['levels']}층 = {p['lanes_total']:,} · "
                 f"용량 {p['pallet_positions']:,} 팔레트 · 단위 m", fontsize=12, pad=12)
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=SURF)
    plt.close(fig)


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE
    data = build()
    (out / "d4_layout.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    draw_check(data, out / "d4_check.png")
    print(len(data["boxes"]), "boxes,", len(data["nav"]["nodes"]), "nav nodes,", data["params"]["pallet_positions"],
          "pallets")
