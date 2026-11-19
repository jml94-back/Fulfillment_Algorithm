-- =============================================================================
-- 물류창고(배치 D4, 셔틀 앤 새틀라이트) WMS 데이터베이스 스키마
-- PostgreSQL 16 이상. 실행: psql -d <db> -f 01_schema.sql
--
-- 근거
--   - 보관 구조·적재 규칙 : warehouse/d4/layout.py, warehouse/d4/storage.py
--   - 메시지 필드·상태 값 : comm/schemas/*.schema.json (wms.order, wms.job, wms.inventory,
--                           scale.reading, amr.*, rack.*, dock.*, charger.state)
--   - Unity 쪽 이름 규칙  : Warehouse_System_Spec.md 6.3장 (채널 R2-E07-L03, 칸 R2-E07-L03-14)
--   - 랙 연동            : comm/schemas/rack.*.schema.json (스케줄러는 통로만, 채널·칸은 Unity가 결정)
--
-- 단위: 길이 m, 무게 kg, 배터리 %, 시각 timestamptz(실제 시각) + sim_t(시뮬레이션 초, MQTT t)
-- 좌표: 도면 좌표 (x 동, y 북, z 위), 원점 = 창고 바닥 남서쪽 안쪽 모서리
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS btree_gist;   -- 도크·노드 시간 겹침 금지(EXCLUDE)에 필요

DROP SCHEMA IF EXISTS wh CASCADE;
CREATE SCHEMA wh;
SET search_path = wh, public;

-- -----------------------------------------------------------------------------
-- 0. 열거형 (MQTT 스키마의 enum 값과 같게)
-- -----------------------------------------------------------------------------
CREATE TYPE flow_dir        AS ENUM ('inbound', 'outbound');
CREATE TYPE location_kind   AS ENUM (
    'dock_door', 'weigh_station', 'drop_stand', 'stage_slot', 'reserve_area',
    'buffer', 'buffer_slot', 'qc_area', 'charger_spot', 'wait_spot', 'facility');
CREATE TYPE equipment_kind  AS ENUM ('amr', 'lift', 'mother_shuttle', 'charger');
CREATE TYPE order_status    AS ENUM ('planned', 'in_progress', 'done', 'canceled');
CREATE TYPE visit_status    AS ENUM ('scheduled', 'arrived', 'docked', 'unloading', 'loading', 'departed', 'canceled');
CREATE TYPE pallet_status   AS ENUM (                -- Unity PalletUnit.state 를 세분화
    'expected', 'received', 'held', 'queued', 'in_transit', 'stored', 'picking', 'staged', 'shipped', 'rejected');
CREATE TYPE job_kind        AS ENUM ('inbound', 'outbound', 'relocate');   -- relocate = 채널 정리 (DB 확장, MQTT엔 아직 없음)
CREATE TYPE job_status      AS ENUM (               -- wms.job.status
    'planned', 'waiting', 'amr_assigned', 'in_rack', 'done', 'held', 'failed', 'canceled');
CREATE TYPE mission_status  AS ENUM ('created', 'sent', 'accepted', 'rejected', 'running', 'done', 'failed', 'canceled');
CREATE TYPE step_action     AS ENUM ('move', 'pick', 'drop', 'charge', 'wait');
CREATE TYPE command_action  AS ENUM ('store', 'retrieve');                    -- rack.cmd.action
CREATE TYPE command_status  AS ENUM ('sent', 'accepted', 'rejected', 'started', 'done', 'failed');  -- sent + rack.evt.event
CREATE TYPE inspection_kind AS ENUM ('inbound_weight', 'inbound_visual', 'outbound_pack');
CREATE TYPE inspection_result AS ENUM ('pass', 'hold', 'released', 'rejected');
CREATE TYPE inventory_event AS ENUM (               -- wms.inventory.event
    'received', 'held', 'released', 'stored', 'retrieved', 'shipped', 'adjusted');

-- =============================================================================
-- 1. 기준 정보 (배치·장비·품목) — 배치가 바뀔 때만 변함
-- =============================================================================

-- 단(층). LEVEL_MAX_KG = (1500,1500,1500,1200,1200,1200,1000,1000,1000,1000)
CREATE TABLE storage_level (
    level_no        smallint PRIMARY KEY CHECK (level_no BETWEEN 1 AND 10),
    level_code      text GENERATED ALWAYS AS ('L' || lpad(level_no::text, 2, '0')) STORED UNIQUE,
    floor_height_m  numeric(5,2) NOT NULL,           -- 팔레트 바닥 높이 (단-1) x 1.5
    max_load_kg     numeric(7,1) NOT NULL CHECK (max_load_kg > 0)
);

-- 모셔틀 통로(레일). 리프트 1기가 남쪽 끝에 붙음
CREATE TABLE rail (
    rail_no         smallint PRIMARY KEY CHECK (rail_no BETWEEN 1 AND 9),
    rail_code       text GENERATED ALWAYS AS ('R' || rail_no::text) STORED UNIQUE,
    center_x        numeric(6,2) NOT NULL,
    y_start         numeric(6,2) NOT NULL,           -- 리프트 쪽 끝
    y_end           numeric(6,2) NOT NULL,
    rows_per_side   smallint NOT NULL DEFAULT 33,
    channel_depth      smallint NOT NULL DEFAULT 14
);

-- 레인(채널) = 통로·면·줄·단. SKU 1종 전용, 입구 하나(선입후출)
CREATE TABLE channel (
    channel_id         serial PRIMARY KEY,
    rail_no         smallint NOT NULL REFERENCES rail,
    side            char(1) NOT NULL CHECK (side IN ('W', 'E')),   -- 서쪽 면 / 동쪽 면
    row_no          smallint NOT NULL CHECK (row_no BETWEEN 1 AND 99),   -- 01 = 리프트에 가장 가까운 줄
    level_no        smallint NOT NULL REFERENCES storage_level,
    channel_code       text GENERATED ALWAYS AS (
                        'R' || rail_no::text || '-' || side || lpad(row_no::text, 2, '0')
                        || '-L' || lpad(level_no::text, 2, '0')) STORED,          -- 예: R2-E07-L03
    center_y        numeric(6,2),                    -- 레인 중심 y (도면)
    is_blocked      boolean NOT NULL DEFAULT false,  -- 정비·고장으로 사용 중지
    UNIQUE (rail_no, side, row_no, level_no),
    UNIQUE (channel_code)
);

-- 칸(셀). 01 = 통로 쪽 입구, 14 = 가장 안쪽. 안쪽부터 채움
CREATE TABLE channel_slot (
    slot_id         serial PRIMARY KEY,
    channel_id         int NOT NULL REFERENCES channel ON DELETE CASCADE,
    slot_no         smallint NOT NULL CHECK (slot_no BETWEEN 1 AND 30),
    slot_code       text NOT NULL UNIQUE,            -- 예: R2-E07-L03-14 (시드에서 channel_code||'-'||칸)
    UNIQUE (channel_id, slot_no)
);

-- 도크. 입고 IN1~6 / 출고 OUT1~6
CREATE TABLE dock (
    dock_code       text PRIMARY KEY CHECK (dock_code ~ '^(IN|OUT)[1-9]$'),
    direction       flow_dir NOT NULL,
    door_x          numeric(6,2) NOT NULL,
    stage_capacity  smallint NOT NULL DEFAULT 28,     -- 적치 그리드 4 x 7
    CHECK ((direction = 'inbound') = (dock_code LIKE 'IN%'))
);

-- 바닥 위치 (랙 칸을 뺀 모든 자리): 계량대, 하차대, 버퍼 칸, 검수 구역, 충전 자리 ...
CREATE TABLE location (
    location_id     serial PRIMARY KEY,
    location_code   text NOT NULL UNIQUE,            -- SC-IN1, DP-OUT1, R1-IN, R1-IN-5, QC-IN, CHW1, WAIT1 ...
    kind            location_kind NOT NULL,
    parent_id       int REFERENCES location,         -- 버퍼 칸 → 버퍼, 적치 칸 → 계량대/하차대
    dock_code       text REFERENCES dock,
    rail_no         smallint REFERENCES rail,
    slot_index      smallint,                        -- 버퍼 칸 1(리프트 옆)~5(AMR 접점), 적치 칸 번호
    x               numeric(6,2),
    y               numeric(6,2),
    z               numeric(6,2) DEFAULT 0,
    nav_node        text,                            -- AMR 경로 그래프 노드 (n_dock_IN1, n_buf_L1-IN ...)
    capacity        smallint NOT NULL DEFAULT 1 CHECK (capacity >= 0)
);

-- 품목
CREATE TABLE sku (
    sku_id              serial PRIMARY KEY,
    sku_code            text NOT NULL UNIQUE,        -- SKU101
    name                text NOT NULL,
    std_weight_kg       numeric(7,1) CHECK (std_weight_kg > 0),   -- 팔레트 1개 표준 무게
    weight_tolerance_pct numeric(4,1) NOT NULL DEFAULT 5.0,       -- 송장 대비 허용 오차 (±5%)
    turnover_rank       numeric(4,3) CHECK (turnover_rank BETWEEN 0 AND 1),  -- 0 = 가장 많이 나감 (sku_rank)
    abc_class           char(1) CHECK (abc_class IN ('A','B','C')),
    created_at          timestamptz NOT NULL DEFAULT now()
);

-- 장비: AMR 4, 리프트 3, 모셔틀 30(통로 3 x 단 10, 새틀라이트 탑재), 충전기 6
CREATE TABLE equipment (
    equipment_id    serial PRIMARY KEY,
    equipment_code  text NOT NULL UNIQUE,            -- AMR01, L1, R1-Shuttle_L03, CHW1
    kind            equipment_kind NOT NULL,
    unity_name      text,                            -- 씬 오브젝트 이름 (Shuttle_L03 등)
    rail_no         smallint REFERENCES rail,        -- 리프트·모셔틀
    level_no        smallint REFERENCES storage_level, -- 모셔틀 담당 단
    home_location_id int REFERENCES location,        -- AMR 기본 충전기 자리, 충전기 자리
    max_payload_kg  numeric(7,1),
    is_active       boolean NOT NULL DEFAULT true,
    CHECK (kind <> 'mother_shuttle' OR (rail_no IS NOT NULL AND level_no IS NOT NULL)),
    CHECK (kind <> 'lift' OR rail_no IS NOT NULL)
);
CREATE UNIQUE INDEX uq_shuttle_per_level ON equipment (rail_no, level_no) WHERE kind = 'mother_shuttle';
CREATE UNIQUE INDEX uq_lift_per_rail     ON equipment (rail_no)           WHERE kind = 'lift';

-- 트럭
CREATE TABLE truck (
    truck_id        serial PRIMARY KEY,
    truck_code      text NOT NULL UNIQUE,            -- TRK-IN-01
    plate_no        text,
    carrier         text
);

-- =============================================================================
-- 2. 주문·트럭 도착·팔레트
-- =============================================================================

-- 트럭 도착(도크 배정). 같은 도크에 시간이 겹치는 방문은 금지
CREATE TABLE truck_visit (
    visit_id        serial PRIMARY KEY,
    truck_id        int NOT NULL REFERENCES truck,
    dock_code       text NOT NULL REFERENCES dock,
    status          visit_status NOT NULL DEFAULT 'scheduled',
    planned_from    timestamptz NOT NULL,
    planned_to      timestamptz NOT NULL,
    arrived_at      timestamptz,
    docked_at       timestamptz,
    departed_at     timestamptz,
    CHECK (planned_to > planned_from),
    EXCLUDE USING gist (dock_code WITH =, tstzrange(planned_from, planned_to) WITH &&)
        WHERE (status <> 'canceled')
);

-- 입고 예정(ASN)·출고 주문 (wms.order)
CREATE TABLE wms_order (
    order_id        serial PRIMARY KEY,
    order_code      text NOT NULL UNIQUE,            -- ASN-0001, SO-0001
    direction       flow_dir NOT NULL,
    visit_id        int REFERENCES truck_visit,
    status          order_status NOT NULL DEFAULT 'planned',
    due_at          timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    completed_at    timestamptz
);

CREATE TABLE order_line (
    order_line_id   serial PRIMARY KEY,
    order_id        int NOT NULL REFERENCES wms_order ON DELETE CASCADE,
    line_no         smallint NOT NULL,
    sku_id          int NOT NULL REFERENCES sku,
    qty_pallets     int NOT NULL CHECK (qty_pallets > 0),
    qty_done        int NOT NULL DEFAULT 0 CHECK (qty_done >= 0),
    UNIQUE (order_id, line_no)
);

-- 팔레트. 현재 위치는 랙 칸(current_slot_id) 또는 바닥 위치(current_location_id) 중 하나
CREATE TABLE pallet (
    pallet_id           bigserial PRIMARY KEY,
    pallet_code         text NOT NULL UNIQUE CHECK (pallet_code ~ '^(P[0-9]{8}|R[1-3]-[0-9]{6})$'),
                                                     -- P00000001 (입고 송장) / R1-000123 (랙 시작 재고)
    sku_id              int NOT NULL REFERENCES sku,
    status              pallet_status NOT NULL DEFAULT 'expected',
    declared_weight_kg  numeric(7,1) CHECK (declared_weight_kg >= 0),   -- 송장 무게
    measured_weight_kg  numeric(7,1) CHECK (measured_weight_kg >= 0),   -- 계량대 실측 (마지막 측정)
    inbound_line_id     int REFERENCES order_line,
    outbound_line_id    int REFERENCES order_line,
    current_slot_id     int UNIQUE REFERENCES channel_slot,                -- 한 칸에 팔레트 하나
    current_location_id int REFERENCES location,
    carried_by_id       int REFERENCES equipment,                       -- AMR·리프트·모셔틀 위에 있을 때
    received_at         timestamptz,
    stored_at           timestamptz,
    shipped_at          timestamptz,
    CHECK (num_nonnulls(current_slot_id, current_location_id, carried_by_id) <= 1),
    CHECK (status <> 'stored' OR current_slot_id IS NOT NULL)
);
CREATE INDEX ix_pallet_sku_status ON pallet (sku_id, status);
CREATE INDEX ix_pallet_location   ON pallet (current_location_id) WHERE current_location_id IS NOT NULL;

-- 레인 SKU 배정 이력. 레인당 열린 배정(released_at IS NULL)은 하나
CREATE TABLE channel_sku_assignment (
    assignment_id   serial PRIMARY KEY,
    channel_id         int NOT NULL REFERENCES channel,
    sku_id          int NOT NULL REFERENCES sku,
    assigned_at     timestamptz NOT NULL DEFAULT now(),
    released_at     timestamptz,                     -- 레인이 비고 예약도 0 이 되면 해제
    reason          text,                            -- 'new_channel' / 'consolidate' ...
    CHECK (released_at IS NULL OR released_at >= assigned_at)
);
CREATE UNIQUE INDEX uq_channel_open_assignment ON channel_sku_assignment (channel_id) WHERE released_at IS NULL;
CREATE INDEX ix_assignment_sku_open ON channel_sku_assignment (sku_id) WHERE released_at IS NULL;

-- 계량 (scale.reading). 오차 %는 계산 열
CREATE TABLE weighing (
    weighing_id     bigserial PRIMARY KEY,
    pallet_id       bigint NOT NULL REFERENCES pallet,
    station_id      int NOT NULL REFERENCES location,  -- SC-IN1~6
    weight_kg       numeric(7,1) NOT NULL CHECK (weight_kg >= 0),
    declared_kg     numeric(7,1),
    deviation_pct   numeric(6,2) GENERATED ALWAYS AS (
                        CASE WHEN declared_kg > 0 THEN round((weight_kg - declared_kg) / declared_kg * 100, 2) END) STORED,
    is_stable       boolean NOT NULL DEFAULT true,
    measured_at     timestamptz NOT NULL DEFAULT now(),
    sim_t           numeric(12,3)
);
CREATE INDEX ix_weighing_pallet ON weighing (pallet_id, measured_at DESC);

-- 검수 결과 (입고 무게 불일치 보류 → 관제 해제, 출고 포장 검수)
CREATE TABLE inspection (
    inspection_id   bigserial PRIMARY KEY,
    pallet_id       bigint NOT NULL REFERENCES pallet,
    kind            inspection_kind NOT NULL,
    result          inspection_result NOT NULL,
    weighing_id     bigint REFERENCES weighing,
    area_id         int REFERENCES location,         -- QC-IN / QC-OUT
    reason          text,                            -- "무게 불일치: 실측 728 kg / 송장 650 kg"
    inspector       text,                            -- 사람 또는 'auto'
    inspected_at    timestamptz NOT NULL DEFAULT now(),
    resolved_at     timestamptz,                     -- ctrl.cmd release_hold 시각
    resolved_by     text
);
CREATE INDEX ix_inspection_open ON inspection (pallet_id) WHERE result = 'hold' AND resolved_at IS NULL;

-- =============================================================================
-- 3. 작업·명령 (wms.job, amr.cmd.mission, rack.cmd)
-- =============================================================================

-- 팔레트 1개 단위 작업
CREATE TABLE job (
    job_id          bigserial PRIMARY KEY,
    job_code        text NOT NULL UNIQUE,            -- J00018
    kind            job_kind NOT NULL,
    status          job_status NOT NULL DEFAULT 'planned',
    order_line_id   int REFERENCES order_line,
    pallet_id       bigint REFERENCES pallet,
    sku_id          int NOT NULL REFERENCES sku,
    dock_code       text REFERENCES dock,
    rail_no         smallint REFERENCES rail,        -- 스케줄러가 고른 통로 (wms.job.rail)
    from_slot_id    int REFERENCES channel_slot,     -- 출고: 랙이 꺼낸 칸 (rack.evt done 후 기록)
    to_slot_id      int REFERENCES channel_slot,     -- 입고: 랙이 넣은 칸 (rack.evt done 후 기록)
    amr_id          int REFERENCES equipment,
    priority        smallint NOT NULL DEFAULT 5,
    reason          text,                            -- held / failed 사유
    created_at      timestamptz NOT NULL DEFAULT now(),
    eta             timestamptz,
    started_at      timestamptz,
    finished_at     timestamptz,
    sim_created_t   numeric(12,3),
    CHECK (kind <> 'inbound'  OR from_slot_id IS NULL),
    CHECK (kind <> 'outbound' OR to_slot_id IS NULL)
);
CREATE INDEX ix_job_active ON job (status, priority, created_at)
    WHERE status NOT IN ('done', 'failed', 'canceled');
CREATE INDEX ix_job_pallet ON job (pallet_id);

-- AMR 미션 (작업 1개 = 보통 미션 1개: move → pick → move → drop)
CREATE TABLE amr_mission (
    mission_id      bigserial PRIMARY KEY,
    mission_code    text NOT NULL UNIQUE,            -- M00011
    job_id          bigint REFERENCES job,           -- 충전·대기 이동은 NULL
    amr_id          int NOT NULL REFERENCES equipment,
    status          mission_status NOT NULL DEFAULT 'created',
    drive_mode      text NOT NULL DEFAULT 'scripted' CHECK (drive_mode IN ('scripted', 'rl')),
    priority        smallint NOT NULL DEFAULT 5,
    fail_reason     text,                            -- busy, collision, timeout ...
    created_at      timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    finished_at     timestamptz
);
CREATE INDEX ix_mission_amr ON amr_mission (amr_id, created_at DESC);

CREATE TABLE mission_step (
    mission_id      bigint NOT NULL REFERENCES amr_mission ON DELETE CASCADE,
    seq             smallint NOT NULL,
    action          step_action NOT NULL,
    stop_id         int NOT NULL REFERENCES location, -- SC-IN2, R1-IN ...
    pallet_id       bigint REFERENCES pallet,
    path            jsonb,                           -- [{node,x,y,t_start,t_end}, ...] 메시지 그대로
    deadline_t      numeric(12,3),
    duration_s      numeric(8,2),
    started_at      timestamptz,
    done_at         timestamptz,
    PRIMARY KEY (mission_id, seq)
);

-- 랙 명령 (rack/{rail}/cmd · evt). 스케줄러는 통로와 SKU만 정하고,
-- 채널·칸·리프트·모셔틀은 Unity ShuttleRackSystem 이 정해 evt 로 알려 준다
CREATE TABLE rack_command (
    command_id      bigserial PRIMARY KEY,
    cmd_code        text NOT NULL UNIQUE,            -- C00002 (봉투 id)
    rail_no         smallint NOT NULL REFERENCES rail,
    job_id          bigint REFERENCES job,
    action          command_action NOT NULL,         -- store / retrieve
    sku_id          int NOT NULL REFERENCES sku,
    pallet_id       bigint REFERENCES pallet,        -- store: 버퍼의 팔레트, retrieve: 랙이 고른 팔레트 (done 후)
    weight_kg       numeric(7,1),
    channel_id      int REFERENCES channel,          -- accepted 때 랙이 알려 준 채널
    slot_id         int REFERENCES channel_slot,     -- done 때 랙이 알려 준 칸
    status          command_status NOT NULL DEFAULT 'sent',
    reason          text,                            -- rejected / failed 사유 (만재, 재고 없음 ...)
    sent_at         timestamptz NOT NULL DEFAULT now(),
    accepted_at     timestamptz,
    started_at      timestamptz,
    finished_at     timestamptz
);
CREATE INDEX ix_rack_command_open ON rack_command (rail_no, sent_at)
    WHERE status IN ('sent', 'accepted', 'started');

-- AMR 통로 노드 시간 예약 (traffic/reservations). 같은 노드를 같은 시간에 두 대가 못 씀
CREATE TABLE traffic_reservation (
    reservation_id  bigserial PRIMARY KEY,
    amr_id          int NOT NULL REFERENCES equipment,
    mission_id      bigint REFERENCES amr_mission ON DELETE CASCADE,
    nav_node        text NOT NULL,                   -- n_aisle_IN2 ...
    t_from          numeric(12,3) NOT NULL,          -- 시뮬레이션 초
    t_to            numeric(12,3) NOT NULL CHECK (t_to > t_from),
    EXCLUDE USING gist (nav_node WITH =, numrange(t_from, t_to) WITH &&)
);

-- 충전 기록
CREATE TABLE charging_session (
    session_id      bigserial PRIMARY KEY,
    amr_id          int NOT NULL REFERENCES equipment,
    charger_id      int NOT NULL REFERENCES equipment,
    started_at      timestamptz NOT NULL,
    ended_at        timestamptz,
    battery_start   numeric(5,2) CHECK (battery_start BETWEEN 0 AND 100),
    battery_end     numeric(5,2) CHECK (battery_end BETWEEN 0 AND 100),
    energy_kwh      numeric(7,3)
);
CREATE UNIQUE INDEX uq_charger_in_use ON charging_session (charger_id) WHERE ended_at IS NULL;
CREATE UNIQUE INDEX uq_amr_charging   ON charging_session (amr_id)     WHERE ended_at IS NULL;

-- =============================================================================
-- 4. 이력·로그 (추가만 함)
-- =============================================================================

-- 장비 상태 변화 (amr.state, lift.state, shuttle.state, charger.state). 10 Hz pose 는 저장하지 않음
CREATE TABLE equipment_status_log (
    log_id          bigserial PRIMARY KEY,
    equipment_id    int NOT NULL REFERENCES equipment,
    ts              timestamptz NOT NULL DEFAULT now(),
    sim_t           numeric(12,3),
    mode            text NOT NULL,                   -- idle / moving / charging / in_channel / error ...
    battery_pct     numeric(5,2) CHECK (battery_pct BETWEEN 0 AND 100),
    x               numeric(6,2),
    y               numeric(6,2),
    level_no        smallint,                        -- 리프트 현재 단
    pallet_id       bigint REFERENCES pallet,
    mission_id      bigint REFERENCES amr_mission,
    satellite       jsonb,                           -- 모셔틀: {"where":"in_channel","channel":...,"pos":...}
    error           text
);
CREATE INDEX ix_status_equipment_ts ON equipment_status_log (equipment_id, ts DESC);

-- 재고 원장 (wms.inventory evt). 재고 변화의 근거
CREATE TABLE inventory_txn (
    txn_id          bigserial PRIMARY KEY,
    event_code      text UNIQUE,                     -- E000012
    event           inventory_event NOT NULL,
    pallet_id       bigint NOT NULL REFERENCES pallet,
    sku_id          int NOT NULL REFERENCES sku,
    slot_id         int REFERENCES channel_slot,
    location_id     int REFERENCES location,
    job_id          bigint REFERENCES job,
    qty_delta       smallint NOT NULL DEFAULT 0 CHECK (qty_delta BETWEEN -1 AND 1),  -- 창고 재고 +1 / -1
    weight_kg       numeric(7,1),
    ts              timestamptz NOT NULL DEFAULT now(),
    sim_t           numeric(12,3)
);
CREATE INDEX ix_txn_pallet ON inventory_txn (pallet_id, ts);
CREATE INDEX ix_txn_sku_ts ON inventory_txn (sku_id, ts);

-- 모든 MQTT 메시지 원문 (봉투 {v,id,ts,t,src,type,corr,data}). 감사·재생용
CREATE TABLE event_log (
    event_id        bigserial PRIMARY KEY,
    msg_id          text,                            -- 봉투 id
    corr_id         text,                            -- 봉투 corr (원래 명령 id)
    topic           text NOT NULL,                   -- wh/v1/amr/AMR01/evt
    msg_type        text NOT NULL,                   -- 스키마 이름 amr.evt
    source          text NOT NULL,                   -- 봉투 src (unity, scheduler ...)
    ts              timestamptz NOT NULL,
    sim_t           numeric(12,3),
    equipment_id    int REFERENCES equipment,
    job_id          bigint REFERENCES job,
    pallet_id       bigint REFERENCES pallet,
    payload         jsonb NOT NULL
);
CREATE INDEX ix_event_ts      ON event_log (ts);
CREATE INDEX ix_event_type_ts ON event_log (msg_type, ts);
CREATE INDEX ix_event_job     ON event_log (job_id) WHERE job_id IS NOT NULL;
CREATE INDEX ix_event_payload ON event_log USING gin (payload jsonb_path_ops);

-- =============================================================================
-- 5. 규칙 (트리거) — 레인 SKU 1종, 단 허용 하중
-- =============================================================================
CREATE FUNCTION check_pallet_slot() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_channel      int;
    v_level_max numeric;
    v_sku       int;
    v_weight    numeric;
BEGIN
    IF NEW.current_slot_id IS NULL OR NEW.current_slot_id IS NOT DISTINCT FROM OLD.current_slot_id THEN
        RETURN NEW;
    END IF;
    SELECT s.channel_id, lv.max_load_kg INTO v_channel, v_level_max
      FROM channel_slot s JOIN channel l USING (channel_id) JOIN storage_level lv USING (level_no)
     WHERE s.slot_id = NEW.current_slot_id;

    SELECT sku_id INTO v_sku FROM channel_sku_assignment
     WHERE channel_id = v_channel AND released_at IS NULL;
    IF v_sku IS NULL OR v_sku <> NEW.sku_id THEN
        RAISE EXCEPTION 'channel % is not assigned to sku % (assigned: %)', v_channel, NEW.sku_id, v_sku;
    END IF;

    v_weight := coalesce(NEW.measured_weight_kg, NEW.declared_weight_kg);
    IF v_weight > v_level_max THEN
        RAISE EXCEPTION 'pallet % weighs % kg, level limit is % kg', NEW.pallet_code, v_weight, v_level_max;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_pallet_slot
    BEFORE INSERT OR UPDATE OF current_slot_id ON pallet
    FOR EACH ROW EXECUTE FUNCTION check_pallet_slot();

-- =============================================================================
-- 6. 조회용 뷰
-- =============================================================================

-- 레인별 현재 SKU와 재고 (wms.inventory.snapshot 의 [레인, SKU, 개수])
CREATE VIEW v_channel_stock AS
SELECT l.channel_id, l.channel_code, l.rail_no, l.side, l.row_no, l.level_no,
       sk.sku_code,
       count(p.pallet_id)                    AS pallets,
       r.channel_depth - count(p.pallet_id)     AS free_slots
  FROM channel l
  JOIN rail r USING (rail_no)
  LEFT JOIN channel_sku_assignment a ON a.channel_id = l.channel_id AND a.released_at IS NULL
  LEFT JOIN sku sk ON sk.sku_id = a.sku_id
  LEFT JOIN channel_slot s ON s.channel_id = l.channel_id
  LEFT JOIN pallet p ON p.current_slot_id = s.slot_id
 GROUP BY l.channel_id, r.channel_depth, sk.sku_code;

-- SKU별 재고
CREATE VIEW v_sku_stock AS
SELECT sk.sku_code, sk.name, ps.stored, ps.inbound_wip, ps.held,
       (SELECT count(*) FROM channel_sku_assignment a
         WHERE a.sku_id = sk.sku_id AND a.released_at IS NULL) AS channels
  FROM sku sk
  CROSS JOIN LATERAL (
        SELECT count(*) FILTER (WHERE p.status = 'stored')                           AS stored,
               count(*) FILTER (WHERE p.status IN ('received','queued','in_transit')) AS inbound_wip,
               count(*) FILTER (WHERE p.status = 'held')                             AS held
          FROM pallet p WHERE p.sku_id = sk.sku_id) ps;

-- 창고 사용률
CREATE VIEW v_capacity AS
SELECT (SELECT count(*) FROM channel_slot)                                   AS slots,
       (SELECT count(*) FROM pallet WHERE current_slot_id IS NOT NULL)    AS occupied,
       (SELECT count(*) FROM channel_sku_assignment WHERE released_at IS NULL) AS channels_in_use,
       (SELECT count(*) FROM channel)                                        AS channels;
