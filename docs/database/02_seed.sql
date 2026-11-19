-- =============================================================================
-- 기준 정보 초기 데이터 (배치 D4). 01_schema.sql 다음에 실행
-- 치수 원본: plans/d_plan.py, unity/d4_layout.json
-- =============================================================================
SET search_path = wh, public;

-- 단 10개: 바닥 높이 (단-1) x 1.5 m, 허용 하중 LEVEL_MAX_KG
INSERT INTO storage_level (level_no, floor_height_m, max_load_kg)
SELECT lv, (lv - 1) * 1.5,
       CASE WHEN lv <= 3 THEN 1500 WHEN lv <= 6 THEN 1200 ELSE 1000 END
  FROM generate_series(1, 10) lv;

-- 통로 3개 (리프트 줄 북쪽 21.6 ~ 랙 북쪽 끝 77.6)
INSERT INTO rail (rail_no, center_x, y_start, y_end) VALUES
    (1, 21.8, 21.6, 77.6), (2, 60.0, 21.6, 77.6), (3, 98.2, 21.6, 77.6);

-- 레인(채널) 1,980개 = 통로 3 x 면 2 x 줄 33 x 단 10. 줄 중심 y = 32.1 + (줄-1) x 1.4
INSERT INTO channel (rail_no, side, row_no, level_no, center_y)
SELECT r, s, n, lv, 32.1 + (n - 1) * 1.4
  FROM generate_series(1, 3) r, unnest(ARRAY['W', 'E']) s,
       generate_series(1, 33) n, generate_series(1, 10) lv;

-- 칸 27,720개 = 레인 x 14
INSERT INTO channel_slot (channel_id, slot_no, slot_code)
SELECT l.channel_id, k, l.channel_code || '-' || lpad(k::text, 2, '0')
  FROM channel l, generate_series(1, 14) k;

-- 도크 12개. 출고 번호는 배치도 기준 (OUT1 = 동쪽 끝 x 117.5)
INSERT INTO dock (dock_code, direction, door_x) VALUES
    ('IN1', 'inbound', 2.5),   ('IN2', 'inbound', 7.5),   ('IN3', 'inbound', 12.5),
    ('IN4', 'inbound', 27.5),  ('IN5', 'inbound', 32.5),  ('IN6', 'inbound', 37.5),
    ('OUT1', 'outbound', 117.5), ('OUT2', 'outbound', 112.5), ('OUT3', 'outbound', 107.5),
    ('OUT4', 'outbound', 92.5),  ('OUT5', 'outbound', 87.5),  ('OUT6', 'outbound', 82.5);

-- 바닥 위치 -------------------------------------------------------------------
-- 도크 문
INSERT INTO location (location_code, kind, dock_code, x, y)
SELECT dock_code, 'dock_door', dock_code, door_x, 0 FROM dock;

-- 계량대 SC-INn / 하차대 DP-OUTn (중심 y 11.8, AMR 접점 경로 노드 n_dock_*)
INSERT INTO location (location_code, kind, dock_code, x, y, nav_node, capacity)
SELECT CASE direction WHEN 'inbound' THEN 'SC-' ELSE 'DP-' END || dock_code,
       CASE direction WHEN 'inbound' THEN 'weigh_station' ELSE 'drop_stand' END::location_kind,
       dock_code, door_x, 11.8, 'n_dock_' || dock_code, 1
  FROM dock;

-- 도크별 적치 칸 28개 (4줄 x 7)
INSERT INTO location (location_code, kind, parent_id, dock_code, slot_index)
SELECT d.dock_code || '-S' || lpad((r * 10 + c)::text, 2, '0'), 'stage_slot',
       st.location_id, d.dock_code, (r - 1) * 7 + c
  FROM dock d
  JOIN location st ON st.dock_code = d.dock_code AND st.kind IN ('weigh_station', 'drop_stand'),
       generate_series(1, 4) r, generate_series(1, 7) c;

-- 예비 구역 (도크 3+3 사이 9 x 8.4 m)
INSERT INTO location (location_code, kind, x, y, capacity) VALUES
    ('RESERVE-IN', 'reserve_area', 20.0, 7.2, 40), ('RESERVE-OUT', 'reserve_area', 100.0, 7.2, 40);

-- 리프트 버퍼 R1-IN / R1-OUT (AMR 접점 좌표) + 칸 1(리프트 옆)~5(AMR 접점)
INSERT INTO location (location_code, kind, rail_no, x, y, nav_node, capacity)
SELECT 'R' || r.rail_no || '-' || d, 'buffer', r.rail_no,
       r.center_x + CASE d WHEN 'IN' THEN -7.05 ELSE 7.05 END, 18.6,
       'n_buf_L' || r.rail_no || '-' || d, 5
  FROM rail r, unnest(ARRAY['IN', 'OUT']) d;

INSERT INTO location (location_code, kind, parent_id, rail_no, slot_index, x, y, z)
SELECT b.location_code || '-' || k, 'buffer_slot', b.location_id, b.rail_no, k,
       r.center_x + CASE WHEN b.location_code LIKE '%-IN' THEN -1 ELSE 1 END * (1.2 + 1.3 * (k - 0.5)),
       20.4, 0.6
  FROM location b JOIN rail r USING (rail_no), generate_series(1, 5) k
 WHERE b.kind = 'buffer';

-- 검수·지원 띠
INSERT INTO location (location_code, kind, x, y, nav_node, capacity) VALUES
    ('QC-IN',  'qc_area',  40.9, 21.8, 'n_inbound_qc',  20),
    ('QC-OUT', 'qc_area',  79.1, 21.8, 'n_outbound_qc', 20),
    ('MAINT',  'facility', 15.3, 21.8, NULL, 0),
    ('SPARE',  'facility', 104.7, 21.8, NULL, 0);

-- 충전 자리 (벽면 충전기 앞) / 쉬는 자리
INSERT INTO location (location_code, kind, x, y, nav_node) VALUES
    ('CHW1', 'charger_spot', 1.5, 23.6, 'n_CHW1'),   ('CHW2', 'charger_spot', 1.5, 26.5, 'n_CHW2'),
    ('CHW3', 'charger_spot', 1.5, 29.4, 'n_CHW3'),   ('CHE1', 'charger_spot', 118.5, 23.6, 'n_CHE1'),
    ('CHE2', 'charger_spot', 118.5, 26.5, 'n_CHE2'), ('CHE3', 'charger_spot', 118.5, 29.4, 'n_CHE3'),
    ('WAIT1', 'wait_spot', 33.0, 19.8, 'n_WAIT1'),   ('WAIT2', 'wait_spot', 36.6, 19.8, 'n_WAIT2'),
    ('WAIT3', 'wait_spot', 84.6, 19.8, 'n_WAIT3'),   ('WAIT4', 'wait_spot', 87.0, 19.8, 'n_WAIT4');

-- 장비 -----------------------------------------------------------------------
-- 충전기 6기
INSERT INTO equipment (equipment_code, kind, unity_name, home_location_id)
SELECT location_code, 'charger', location_code, location_id
  FROM location WHERE kind = 'charger_spot';

-- AMR 4대 (AMR01·02 서벽, AMR03·04 동벽에서 시작)
INSERT INTO equipment (equipment_code, kind, unity_name, home_location_id, max_payload_kg)
SELECT a.code, 'amr', a.code, l.location_id, 1500
  FROM (VALUES ('AMR01', 'CHW1'), ('AMR02', 'CHW2'), ('AMR03', 'CHE1'), ('AMR04', 'CHE2')) a(code, home)
  JOIN location l ON l.location_code = a.home;

-- 리프트 3기 (통로마다 1기)
INSERT INTO equipment (equipment_code, kind, unity_name, rail_no, max_payload_kg)
SELECT 'L' || rail_no, 'lift', 'Lift', rail_no, 1500 FROM rail;

-- 모셔틀 30대 (통로 x 단, 새틀라이트 탑재). Unity 이름 Shuttle_L01~L10 은 통로 안에서만 유일
INSERT INTO equipment (equipment_code, kind, unity_name, rail_no, level_no, max_payload_kg)
SELECT 'R' || r.rail_no || '-Shuttle_' || lv.level_code, 'mother_shuttle',
       'Shuttle_' || lv.level_code, r.rail_no, lv.level_no, lv.max_load_kg
  FROM rail r, storage_level lv;
