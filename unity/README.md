# D4 배치 → Unity 씬 데이터

확정 배치 D4(남쪽 도크, 입고 서쪽 · 사무실 중앙 2층 · 출고 동쪽, 3레일, 레인 깊이 14, 용량 27,720 팔레트)를
Unity에 그대로 배치할 수 있게 m 단위 좌표로 내보낸 것.

| 파일 | 내용 |
|---|---|
| `d4_layout.json` | 배치 데이터 (box 480개, 장비·접점·레인·AMR 경로 그래프) |
| `d4_check.png` | JSON만 읽어서 다시 그린 확인 도면 (원본 `out/d_plan.png`와 겹쳐 보면 됨) |
| `WarehouseLayoutLoader.cs` | JSON을 읽어 씬에 박스(Cube)로 깔아 주는 스크립트 |
| `export_d4.py` | 생성 스크립트. `cd warehouse-algo && python -m unity.export_d4` |

치수의 원본은 `plans/d_plan.py`. 배치가 바뀌면 위 명령을 다시 실행하면 된다. 용량이 27,720이 아니면 스크립트가 멈춘다.

## 좌표계 (Unity 기준)

- **1 unit = 1 m**, 왼손 좌표계, **Y-up**
- **원점 (0,0,0)** = 창고 바닥의 남서쪽 안쪽 모서리
- **+X = 동쪽** (0~120), **+Z = 북쪽** (0~80), **+Y = 위**
- Top 뷰(위에서 내려다봄)가 도면과 같은 방향. 남쪽 벽(도크) 바깥 트럭 칸은 Z < 0
- 모든 좌표 배열은 Unity `Vector3` 순서 `[x, y(높이), z(북)]`
- 도면 좌표 (x, y=북, z=위)와 바꿀 때는 `Unity Vector3(x, z, y)`. MQTT 메시지는 도면 좌표를 쓸 예정이라 이 변환만 하면 된다.

## JSON 구조

- `meta` 좌표계 설명, `assumed_heights`(도면에 없어 가정한 높이: 도크 문 4.5 m, 사무실 층고 4 m, 컨베이어 0.6 m 등)
- `params` 건물 120 x 80 x 25 m, 랙 15 m, 10층(층고 1.5 m), 슬롯 간격 1.3 m, 레인 폭 1.4 m, 남→북 띠 구간(`bands_z`)
- `boxes` 씬에 놓을 모든 물체. `center`·`size`(m)·`type`·`color`. 회전 없음
  - 주요 type: `wall`, `dock_door`(opening=true, 벽 구멍), `dock_leveler`, `truck_bay`(outdoor), `weigh_station`, `drop_station`, `staging_slot`, `office`, `spare_area`, `amr_aisle`, `lift`, `conveyor_slot`, `rail`, `rack_block`, `facility`, `charger`, `charger_park`, `amr_wait`, `zone`
- `docks` 도크 12개: 문 중심, 계량대·하차대, AMR 접점, 트럭 칸, 적치 칸 28개 ID
- `rails` / `lifts` / `buffers` 레일 3개, 리프트 3기, 리프트별 입·출고 버퍼 5칸과 AMR 접점
- `facilities` 검수 띠 시설 4개, `chargers` 벽면 충전기 6기(정차 칸 중심, 향하는 방향), `wait_spots` 리프트 줄의 AMR 쉬는 자리 WAIT1~4, `amrs` AMR 4대의 시작 위치
- `storage.lanes` 바닥 레인 198줄. 각 줄의 `center_z`, `slot0_x`, `slot_step_x`로 팔레트 위치 계산
  - 위치 P(1~14) 중심 x = `slot0_x + (P-1) * slot_step_x`, z = `center_z`
  - 층 LVnn 팔레트 바닥 높이 y = `(nn-1) * 1.5`
  - 위치 1이 레일 쪽 입·출구(선입후출)
- `nav` AMR 바닥 경로 그래프. 통로는 양방향, 진행 방향은 알고리즘이 시간 예약으로 정한다

## ID 규칙 (MQTT 통신 설계와 공통)

| 대상 | ID |
|---|---|
| AMR | `AMR01`~`AMR04` |
| 리프트 / 레일 / 층 | `L1`~`L3` / `R1`~`R3` / `LV01`~`LV10` |
| 모셔틀 / 세틀라이트 | `MS-R1-LV01` / `SAT-R1-LV01` |
| 레인 (층별) | `R1-W-LV01-N01` (레일-서/동-층-남쪽부터 줄번호 01~33). JSON에는 층을 뺀 `R1-W-N01` |
| 도크 | `IN1`~`IN6`, `OUT1`~`OUT6` (도면 라벨 그대로) |
| 계량대 / 출고 하차대 | `SC-IN1`~`SC-IN6` / `DP-OUT1`~`DP-OUT6` |
| 리프트 버퍼 | `L1-IN`, `L1-OUT`, 칸 `L1-IN-1`~`5` (1 = 리프트 옆, 5 = AMR 접점 끝) |
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
x, z는 이 JSON과 같은 좌표다. AMR 바닥 격자(1.2 m)와 접점은 `out/d4_nav_grid.json`.
