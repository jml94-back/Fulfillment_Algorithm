# D4 배치 → Unity 씬 데이터

확정 배치 D4(남쪽 도크, 입고 서쪽 · 사무실 중앙 2층 · 출고 동쪽, 3레일, 레인 깊이 14, 용량 27,720 팔레트)를
Unity에 그대로 배치할 수 있게 m 단위 좌표로 내보낸 것.

| 파일 | 내용 |
|---|---|
| `d4_layout.json` | 배치 데이터 (box 484개, 장비·접점·레인·AMR 경로 그래프) |
| `d4_check.png` | JSON만 읽어서 다시 그린 확인 도면 (원본 `out/d_plan.png`와 겹쳐 보면 됨) |
| `WarehouseLayoutLoader.cs` | JSON을 읽어 씬에 박스(Cube)로 깔아 주는 스크립트 |
| `export_d4.py` | 생성 스크립트. `cd warehouse-algo && python -m unity.export_d4` |

치수의 원본은 `plans/d_plan.py`. 배치가 바뀌면 위 명령을 다시 실행하면 된다. 용량이 27,720이 아니면 스크립트가 멈춘다.

## 좌표계 (Unity 구현 기준, 2026-11-19 팀원 명세에 맞춤)

- **1 unit = 1 m**, 왼손 좌표계, **Y-up**. 박스는 회전 없음
- JSON 좌표 = **배치도 좌표** (DockZoneBuilder 와 같은 기준)
  - `x` = 서쪽 벽 **외부면**에서의 거리 (fromWest, 0~120.01), `+X` 동쪽
  - `z` = 남쪽 벽 **안쪽면**에서의 깊이 (depth, 0~80.01), `+Z` 북쪽. 남쪽 벽 바깥 트럭 칸은 `z < 0`
  - `y` = 창고 바닥 **윗면**에서의 높이
- **Unity 월드 = 배치도 좌표 + (-60.005, 1.20, -39.705)** (`meta.coordinate_system.world_origin`).
  `WarehouseLayoutLoader` 의 `snapToWorldOrigin`(기본 켜짐)이 이 오브젝트를 그 위치에 놓아 기존 씬(DockZoneBuilder, ShuttleRackSystem)과 겹친다.
- 모든 좌표 배열은 Unity `Vector3` 순서 `[x, y(높이), z(북)]`. MQTT 메시지는 도면 좌표 (x, y=북, z=위)를 쓰므로
  배치도 로컬은 `Vector3(x, z, y)`, 월드는 여기에 원점을 더한다 (`comm/unity/WarehouseMqtt/WhCoords.cs` 의 `ToWorld`).
- 동·북쪽 벽의 정확한 위치는 명세에 없어 바닥 끝(120.01 / 80.01) 바깥에 0.3 m 두께로 놓았다 (추정).

## Unity 와 맞춘 치수

| 항목 | 값 |
|---|---|
| 바닥 | 120.01 x 80.01 m |
| 통로(레일) 중심 x | R1 21.8, R2 60.0, R3 98.2 · 통로 폭 2.0 m |
| 칸 | 깊이 방향 1.25 m 간격 14칸 (블록 17.5 m), 칸 P 중심 = 통로 중심 ± (1.0 + (P-0.5) x 1.25) |
| 줄 | 33줄, 1.4 m 간격. 줄 1 중심 z = 32.1 (리프트 중심 20.4 + 11.0 + 0.7) |
| 단 | 10단, 단 n 레일 윗면 y = 0.40 + 1.45 x (n-1) |
| 모셔틀·리프트 주고받는 위치 | z = 22.4 (리프트 중심 + 2.0) |
| 리프트 버퍼 | 5칸, 간격 1.3 m, 컨베이어 윗면 0.50 m |
| 팔레트 | 1.1 x 1.1 x 0.15 m, 짐 포함 높이 1.1 m 이하 |

## JSON 구조

- `meta` 좌표계 설명(`world_origin`, `to_world`), `assumed_heights`(명세·도면에 없어 가정한 높이: 도크 문 4.5 m, 사무실 층고 4 m 등)
- `params` 위 치수, 남→북 띠 구간(`bands_z`)
- `boxes` 씬에 놓을 모든 물체. `center`·`size`(m)·`type`·`color`
  - 주요 type: `wall`, `dock_door`(opening=true, 벽 구멍), `dock_leveler`, `truck_bay`(outdoor), `weigh_station`, `drop_station`, `staging_slot`, `office`, `spare_area`, `amr_aisle`, `lift`, `conveyor_slot`, `rail`, `rack_block`, `facility`, `charger`, `charger_park`, `amr_wait`, `zone`
  - 버퍼 칸 box 이름은 Unity 모델 이름 그대로 `Buffer_L1_IN1` …
- `docks` 도크 12개: 문 중심, 계량대·하차대, AMR 접점, 트럭 칸, 적치 칸 28개, `unity_object`(Dock_IN1 …)
- `rails` 통로 3개: 단별 레일 높이 `level_rail_top`, 모셔틀 ID `mother_shuttles`, 주고받는 위치 `shuttle_xfer_z`, `unity_object`
- `lifts` 리프트 3기 (`serves_levels` 1~10, 버퍼 높이), `buffers` 통로별 입·출고 버퍼 (`R1-IN`, `R1-OUT`, 칸 순서는 리프트 쪽부터)
- `facilities` 검수 띠 시설 4개, `chargers` 벽면 충전기 6기, `wait_spots` AMR 쉬는 자리 WAIT1~4, `amrs` AMR 4대 시작 위치
- `storage.lanes` 바닥 레인 198줄 (`R1-W01` …, `unity_object` = `Rack_R1/Rack_Generated/Lanes/Lane_W01`)
  - 칸 P(1~14) 중심 x = `slot0_x + (P-1) * slot_step_x`, z = `center_z`, 단 n 의 팔레트 바닥 y = 0.40 + 1.45 x (n-1)
  - 칸 01 이 통로 쪽 입·출구 (후입선출)
- `nav` AMR 바닥 경로 그래프. 통로는 양방향, 진행 방향은 알고리즘이 시간 예약으로 정한다

## ID 규칙 (Unity ShuttleRackSystem · MQTT 공통)

| 대상 | ID |
|---|---|
| AMR | `AMR01`~`AMR04` |
| 통로 / 리프트 / 단 | `R1`~`R3` / `L1`~`L3` (통로마다 1기) / `L01`~`L10` |
| 모셔틀 (새틀라이트 포함) | `R1-Shuttle_L01` ~ `R1-Shuttle_L10` (Unity 오브젝트 `Shuttle_L01`) |
| 채널 (단별 레인) | `R2-E07-L03` = 통로-서/동 줄(01 = 리프트 쪽 남쪽)-단. JSON `storage.lanes` 는 단을 뺀 `R2-E07` |
| 칸 | `R2-E07-L03-14` (01 = 통로 쪽 앞칸 ~ 14 = 안쪽) |
| 랙에서 만든 팔레트 | `R1-000123`. 입고 트럭 팔레트는 `P` + 8자리 |
| 도크 | `IN1`~`IN6`, `OUT1`~`OUT6` (도면 라벨 그대로) |
| 계량대 / 출고 하차대 | `SC-IN1`~`SC-IN6` / `DP-OUT1`~`DP-OUT6` |
| 리프트 버퍼 | `R1-IN`, `R1-OUT`, 칸 `IN1`~`IN5` / `OUT1`~`OUT5` (1 = 리프트 쪽, 5 = AMR 쪽) |
| 충전기 | `CHW1`~`CHW3`(서쪽 벽), `CHE1`~`CHE3`(동쪽 벽) |
| 검수 구역 | `QC-IN`, `QC-OUT` |
| AMR 쉬는 자리 | `WAIT1`~`WAIT4` |

MQTT 토픽과 메시지 형식은 [`../comm/README.md`](../comm/README.md) 참고.

## Unity에서 쓰기

1. `d4_layout.json`을 `Assets/` 아래에 넣는다 (TextAsset으로 인식됨).
2. `WarehouseLayoutLoader.cs`를 넣고 빈 GameObject에 붙인 뒤, `layoutJson`에 JSON을 지정한다.
3. 인스펙터 ⋮ 메뉴 → **Build Layout** (또는 플레이). 종류별로 묶인 박스가 생기고, 씬 뷰에 AMR 경로 그래프가 기즈모로 보인다.
4. 진짜 에셋으로 바꿀 때는 같은 이름의 박스 위치·크기에 맞춰 놓으면 된다.

스크립트는 Unity 2021 이상의 `JsonUtility` 기준으로 짰지만 Unity에서 직접 실행해 보지는 않았다. URP면 URP Lit, 아니면 Standard 셰이더를 쓴다.

## 알고리즘 결과 재생

알고리즘 쪽 `out/d4_events.json`은 MQTT 메시지 모양(`{"t", "topic", "payload"}`) 목록이다.
AMR 경로는 `wh/amr/AMR01/path`의 `points [[t, x, z], ...]`(꺾이는 점만)이므로 그 사이를 선형 보간하면 된다.
x, z는 이 JSON과 같은 배치도 좌표다 (월드로는 원점을 더한다). AMR 바닥 격자(1.2 m)와 접점은 `out/d4_nav_grid.json`.
