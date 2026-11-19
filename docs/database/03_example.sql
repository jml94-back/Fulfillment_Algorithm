-- =============================================================================
-- 예제 시나리오 + 규칙 검증. 01_schema.sql, 02_seed.sql 다음에 실행
-- comm/examples 의 메시지(ASN-0001, P00000001~3, J00009, J00018, M00011, C00017)를 DB 행으로 옮긴 것
-- 마지막에 ROLLBACK 하므로 기준 정보만 남는다. 데이터를 남기려면 COMMIT 으로 바꾼다.
-- =============================================================================
\set ON_ERROR_STOP on
SET search_path = wh, public;
BEGIN;

INSERT INTO sku (sku_code, name, std_weight_kg, turnover_rank, abc_class) VALUES
    ('SKU101', '생수 2L 묶음', 650, 0.05, 'A'),
    ('SKU202', '복사용지 A4',  900, 0.40, 'B');

-- 1) 트럭 도착 예약: IN2, 09:00~10:00
INSERT INTO truck (truck_code) VALUES ('TRK-IN-01'), ('TRK-IN-02');
INSERT INTO truck_visit (truck_id, dock_code, planned_from, planned_to)
SELECT truck_id, 'IN2', '2026-11-19 09:00+09', '2026-11-19 10:00+09' FROM truck WHERE truck_code = 'TRK-IN-01';

-- 같은 도크·겹치는 시간은 거부되어야 한다
DO $$ BEGIN
    INSERT INTO wh.truck_visit (truck_id, dock_code, planned_from, planned_to)
    SELECT truck_id, 'IN2', '2026-11-19 09:30+09', '2026-11-19 10:30+09' FROM wh.truck WHERE truck_code = 'TRK-IN-02';
    RAISE EXCEPTION 'TEST FAIL: overlapping dock visit accepted';
EXCEPTION WHEN exclusion_violation THEN RAISE NOTICE 'OK  도크 시간 겹침 거부';
END $$;

-- 2) 입고 예정(ASN) + 팔레트 3개 (송장 무게 650 kg)
INSERT INTO wms_order (order_code, direction, visit_id)
SELECT 'ASN-0001', 'inbound', visit_id FROM truck_visit;
INSERT INTO order_line (order_id, line_no, sku_id, qty_pallets)
SELECT o.order_id, 1, s.sku_id, 3 FROM wms_order o, sku s WHERE s.sku_code = 'SKU101';
INSERT INTO pallet (pallet_code, sku_id, declared_weight_kg, inbound_line_id)
SELECT 'P0000000' || n, ol.sku_id, 650, ol.order_line_id
  FROM order_line ol, generate_series(1, 3) n;

-- 3) 계량: P1 정상(648.3), P3 불일치(728 → +12%) → 검수 보류
INSERT INTO weighing (pallet_id, station_id, weight_kg, declared_kg, sim_t)
SELECT p.pallet_id, l.location_id, w.kg, 650, w.t
  FROM (VALUES ('P00000001', 648.3, 60.0), ('P00000003', 728.0, 74.9)) w(code, kg, t)
  JOIN pallet p ON p.pallet_code = w.code
  JOIN location l ON l.location_code = 'SC-IN2';

UPDATE pallet p SET measured_weight_kg = w.weight_kg, received_at = now(),
       status = CASE WHEN abs(w.deviation_pct) > 5 THEN 'held' ELSE 'received' END::pallet_status,
       current_location_id = (SELECT location_id FROM location WHERE location_code =
                              CASE WHEN abs(w.deviation_pct) > 5 THEN 'QC-IN' ELSE 'SC-IN2' END)
  FROM weighing w WHERE w.pallet_id = p.pallet_id;

INSERT INTO inspection (pallet_id, kind, result, weighing_id, area_id, reason, inspector)
SELECT w.pallet_id, 'inbound_weight', 'hold', w.weighing_id,
       (SELECT location_id FROM location WHERE location_code = 'QC-IN'),
       format('무게 불일치: 실측 %s kg / 송장 %s kg', w.weight_kg, w.declared_kg), 'auto'
  FROM weighing w WHERE abs(w.deviation_pct) > 5;

-- 4) P1 적재 작업: 스케줄러는 통로 R1 만 정함 (채널·칸은 랙이 결정)
INSERT INTO job (job_code, kind, status, order_line_id, pallet_id, sku_id, dock_code, rail_no, amr_id)
SELECT 'J00009', 'inbound', 'amr_assigned', p.inbound_line_id, p.pallet_id, p.sku_id, 'IN2', 1,
       (SELECT equipment_id FROM equipment WHERE equipment_code = 'AMR01')
  FROM pallet p WHERE p.pallet_code = 'P00000001';

-- AMR 미션 M00011: SC-IN2 에서 집어 R1-IN 에 내려놓기
INSERT INTO amr_mission (mission_code, job_id, amr_id, status)
SELECT 'M00011', job_id, amr_id, 'done' FROM job WHERE job_code = 'J00009';
INSERT INTO mission_step (mission_id, seq, action, stop_id, pallet_id, duration_s)
SELECT m.mission_id, s.seq, s.act::step_action, l.location_id,
       CASE WHEN s.act IN ('pick', 'drop') THEN j.pallet_id END, s.dur
  FROM amr_mission m JOIN job j USING (job_id),
       (VALUES (0, 'move', 'SC-IN2', NULL), (1, 'pick', 'SC-IN2', 8.0),
               (2, 'move', 'R1-IN', NULL),  (3, 'drop', 'R1-IN', 8.0)) s(seq, act, stop, dur)
  JOIN location l ON l.location_code = s.stop
 WHERE m.mission_code = 'M00011';

-- 경로 예약: 같은 노드 같은 시간에 두 AMR 불가
INSERT INTO traffic_reservation (amr_id, mission_id, nav_node, t_from, t_to)
SELECT amr_id, mission_id, 'n_aisle_IN2', 24.24, 28.24 FROM amr_mission WHERE mission_code = 'M00011';
DO $$ BEGIN
    INSERT INTO wh.traffic_reservation (amr_id, nav_node, t_from, t_to)
    SELECT equipment_id, 'n_aisle_IN2', 27.0, 30.0 FROM wh.equipment WHERE equipment_code = 'AMR02';
    RAISE EXCEPTION 'TEST FAIL: overlapping node reservation accepted';
EXCEPTION WHEN exclusion_violation THEN RAISE NOTICE 'OK  경로 노드 시간 겹침 거부';
END $$;

-- 랙 명령: 통로 R1 에 store → 랙이 채널 R1-W01-L01 수락(accepted) → 칸 14 완료(done)
INSERT INTO channel_sku_assignment (channel_id, sku_id, reason)
SELECT c.channel_id, s.sku_id, 'new_channel' FROM channel c, sku s
 WHERE c.channel_code = 'R1-W01-L01' AND s.sku_code = 'SKU101';
INSERT INTO rack_command (cmd_code, rail_no, job_id, action, sku_id, pallet_id, weight_kg, channel_id, slot_id, status, accepted_at, finished_at)
SELECT 'C00017', 1, j.job_id, 'store', j.sku_id, j.pallet_id, 648.3,
       (SELECT channel_id FROM channel WHERE channel_code = 'R1-W01-L01'),
       (SELECT slot_id FROM channel_slot WHERE slot_code = 'R1-W01-L01-14'), 'done', now(), now()
  FROM job j WHERE j.job_code = 'J00009';
UPDATE job SET status = 'in_rack', to_slot_id = (SELECT slot_id FROM channel_slot WHERE slot_code = 'R1-W01-L01-14')
 WHERE job_code = 'J00009';

-- 5) 레인에 넣기 → 트리거가 SKU·하중 검사
UPDATE pallet SET current_location_id = NULL,
       current_slot_id = (SELECT slot_id FROM channel_slot WHERE slot_code = 'R1-W01-L01-14'),
       status = 'stored', stored_at = now()
 WHERE pallet_code = 'P00000001';
UPDATE job SET status = 'done', finished_at = now() WHERE job_code = 'J00009';
INSERT INTO inventory_txn (event_code, event, pallet_id, sku_id, slot_id, job_id, qty_delta, weight_kg)
SELECT 'E000012', 'stored', p.pallet_id, p.sku_id, p.current_slot_id, j.job_id, 1, p.measured_weight_kg
  FROM pallet p JOIN job j USING (pallet_id) WHERE p.pallet_code = 'P00000001';

-- 다른 SKU 를 같은 레인에 넣으면 거부
INSERT INTO pallet (pallet_code, sku_id, declared_weight_kg)
SELECT 'P00000099', sku_id, 900 FROM sku WHERE sku_code = 'SKU202';
DO $$ BEGIN
    UPDATE wh.pallet SET current_slot_id = (SELECT slot_id FROM wh.channel_slot WHERE slot_code = 'R1-W01-L01-13')
     WHERE pallet_code = 'P00000099';
    RAISE EXCEPTION 'TEST FAIL: mixed sku channel accepted';
EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE 'TEST FAIL%' THEN RAISE; END IF;
    RAISE NOTICE 'OK  레인 SKU 혼합 거부: %', SQLERRM;
END $$;

-- 단 허용 하중 초과(7단 1,000 kg 에 1,100 kg)는 거부
INSERT INTO channel_sku_assignment (channel_id, sku_id)
SELECT l.channel_id, s.sku_id FROM channel l, sku s WHERE l.channel_code = 'R2-E07-L07' AND s.sku_code = 'SKU202';
UPDATE pallet SET measured_weight_kg = 1100 WHERE pallet_code = 'P00000099';
DO $$ BEGIN
    UPDATE wh.pallet SET current_slot_id = (SELECT slot_id FROM wh.channel_slot WHERE slot_code = 'R2-E07-L07-14')
     WHERE pallet_code = 'P00000099';
    RAISE EXCEPTION 'TEST FAIL: overweight pallet accepted';
EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE 'TEST FAIL%' THEN RAISE; END IF;
    RAISE NOTICE 'OK  단 허용 하중 초과 거부: %', SQLERRM;
END $$;

-- 한 레인에 열린 SKU 배정은 하나
DO $$ BEGIN
    INSERT INTO wh.channel_sku_assignment (channel_id, sku_id)
    SELECT l.channel_id, s.sku_id FROM wh.channel l, wh.sku s WHERE l.channel_code = 'R1-W01-L01' AND s.sku_code = 'SKU202';
    RAISE EXCEPTION 'TEST FAIL: second open assignment accepted';
EXCEPTION WHEN unique_violation THEN RAISE NOTICE 'OK  레인 이중 배정 거부';
END $$;

-- 6) 관제가 P3 보류 해제 (ctrl.cmd release_hold)
UPDATE inspection SET result = 'released', resolved_at = now(), resolved_by = 'control'
 WHERE pallet_id = (SELECT pallet_id FROM pallet WHERE pallet_code = 'P00000003');

-- 7) 충전: 한 충전기에 두 대 동시 충전 불가
INSERT INTO charging_session (amr_id, charger_id, started_at, battery_start)
SELECT a.equipment_id, c.equipment_id, now(), 22.5 FROM equipment a, equipment c
 WHERE a.equipment_code = 'AMR01' AND c.equipment_code = 'CHW1';
DO $$ BEGIN
    INSERT INTO wh.charging_session (amr_id, charger_id, started_at)
    SELECT a.equipment_id, c.equipment_id, now() FROM wh.equipment a, wh.equipment c
     WHERE a.equipment_code = 'AMR02' AND c.equipment_code = 'CHW1';
    RAISE EXCEPTION 'TEST FAIL: double charger use accepted';
EXCEPTION WHEN unique_violation THEN RAISE NOTICE 'OK  충전기 동시 사용 거부';
END $$;

-- 8) MQTT 원문 로그
INSERT INTO event_log (msg_id, corr_id, topic, msg_type, source, ts, sim_t, equipment_id, job_id, pallet_id, payload)
SELECT 'E-amr-1', 'M00011', 'wh/v1/amr/AMR01/evt', 'amr.evt', 'unity', now(), 118.1,
       j.amr_id, j.job_id, j.pallet_id,
       '{"event":"mission_done","mission_id":"M00011","seq":null,"pallet":null,"reason":null}'
  FROM job j WHERE j.job_code = 'J00009';

-- 결과 확인
SELECT * FROM v_capacity;
SELECT channel_code, sku_code, pallets, free_slots FROM v_channel_stock WHERE pallets > 0 OR sku_code IS NOT NULL ORDER BY channel_code;
SELECT * FROM v_sku_stock ORDER BY sku_code;
SELECT p.pallet_code, p.status, w.weight_kg, w.deviation_pct, i.result
  FROM pallet p LEFT JOIN weighing w USING (pallet_id) LEFT JOIN inspection i USING (pallet_id)
 ORDER BY p.pallet_code;

ROLLBACK;
