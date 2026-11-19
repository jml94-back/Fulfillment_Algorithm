# Unity 연동 명세 (배치 D4)

Unity 시뮬레이션 담당 팀원의 질문 6개에 대한 답입니다. 모든 답은 저장소의 실제 코드·데이터를 근거로 했고,
근거 파일을 각 항목에 적었습니다. **코드에 없는 내용은 "코드에 없음"이라고 쓰고, 제안은 `(제안)`으로 따로 표시**했습니다.

함께 볼 문서

| 문서 | 내용 |
|---|---|
| [`unity/README.md`](../unity/README.md) | 배치 JSON(`unity/d4_layout.json`) 구조, 좌표계, 씬 생성 스크립트 |
| [`comm/README.md`](../comm/README.md) | MQTT 통신 설계 전체, 시퀀스 다이어그램 |
| [`comm/docs/topics.md`](../comm/docs/topics.md) | 토픽 32개 표 (발행·구독·QoS·주기) |
| [`comm/sim/virtual_plant.py`](../comm/sim/virtual_plant.py) | **Unity 대역(가상 장비)**. Unity가 해야 할 동작이 그대로 구현돼 있음 |
| [`comm/unity/WarehouseMqtt/`](../comm/unity/WarehouseMqtt/) | Unity C# 스크립트 (MQTT 클라이언트, AMR·리프트·셔틀 에이전트) |

---

## 한눈에 보기

| 질문 | 짧은 답 |
|---|---|
| 1. L1~L3는 리프트인가 연결 지점인가 | **리프트**입니다. 레일 R1~R3마다 1기씩 있는 수직 리프트(입·출고 겸용). AMR과의 연결 지점은 리프트가 아니라 리프트 옆 **버퍼 `L1-IN`/`L1-OUT`** 입니다 |
| 2. 도크·버퍼·레일·셔틀 역할 | 도크 = 트럭 하차/상차, 버퍼 = AMR↔리프트 사이 5칸 컨베이어, 레일 = 층마다 모셔틀이 다니는 남북 통로, 셔틀 = 레일 위 모셔틀 + 레인 안으로 들어가는 세틀라이트 |
| 3. 상태는 어느 객체가 제공하나 | **장비 상태는 Unity가 원본**(AMR·리프트·셔틀·버퍼·도크·계량·충전기). **작업·재고·예약은 스케줄러가 원본**. Unity는 이걸 읽기만 함 |
| 4. 단위·용량·처리시간 | m, s, rad, kg, %. 용량 27,720 팔레트. AMR 1.2 m/s, 싣기·내리기 8 s, 리프트 1.5 m/s, 모셔틀 2 m/s, 세틀라이트 1 m/s 등 (4장 표) |
| 5. 예약과 제어 책임 | 경로·시간 예약, 레인·위치 선택, AMR 배정은 **스케줄러**. Unity는 명령을 **순서대로 실행**하고 전제 조건(팔레트 있음/자리 빔)이 될 때까지 기다리며, 예약 시각 전에는 출발하지 않음 |
| 6. Unity ID·좌표·구역 | 1 unit = 1 m, 원점 남서쪽 바닥 모서리, +X 동, +Z 북. MQTT 좌표 (x, y, z) → Unity `Vector3(x, z, y)`. ID는 JSON·MQTT·씬 오브젝트 이름 모두 같게 (6장 표) |

---

## 1. L1~L3: 리프트인가, 연결 지점인가

**리프트입니다.** 레일마다 1기, 입고·출고 겸용 수직 리프트입니다.

근거: `unity/d4_layout.json` → `lifts`, `boxes`(type `lift`, label "수직 리프트 L1 (입·출고 겸용)"), `comm/whmqtt/ids.py` ("리프트 | L1~L3 (레일마다 1기)").

| 리프트 | 담당 레일 | 중심 (Unity x, z) | 크기 (x × 높이 × z) |
|---|---|---|---|
| `L1` | `R1` | (21.8, 20.4) | 2.4 × 16.0 × 2.4 m |
| `L2` | `R2` | (60.0, 20.4) | 2.4 × 16.0 × 2.4 m |
| `L3` | `R3` | (98.2, 20.4) | 2.4 × 16.0 × 2.4 m |

리프트는 레일의 **남쪽 끝**(리프트 줄, z 19.2~21.6)에 있습니다. 레일은 z 21.6~77.6.

**연결 지점(AMR이 팔레트를 주고받는 곳)은 리프트가 아니라 리프트 양옆의 버퍼**입니다.

```
        서쪽                    리프트 L1                   동쪽
  [AMR 접점] L1-IN-5 … L1-IN-1  [ L1 ]  L1-OUT-1 … L1-OUT-5 [AMR 접점]
   x 14.75   ←── 입고 버퍼 ───→        ←── 출고 버퍼 ───→   x 28.85
```

- 입고: AMR이 `L1-IN`의 5번 칸(서쪽 끝)에 내려놓음 → 컨베이어가 1번 칸(리프트 옆)으로 → 리프트가 집어 올림
- 출고: 리프트가 `L1-OUT` 1번 칸에 내려놓음 → 컨베이어가 5번 칸(동쪽 끝)으로 → AMR이 집음
- 리프트와 셔틀은 각 층의 **이송점**(handoff)에서 팔레트를 주고받습니다 (2장 참고)

리프트의 두 명령 (`lift/{L1}/cmd`, `comm/whmqtt/topics.py`):

| action | 동작 |
|---|---|
| `lift_in` | 입고 버퍼 1번 칸 → `level`층 **입고 이송점** |
| `lift_out` | `level`층 **출고 이송점** → 출고 버퍼 1번 칸 |

---

## 2. 도크·버퍼·레일·셔틀의 역할

팔레트 흐름 (`comm/README.md` 7장 시퀀스):

```
입고: 트럭 → 도크 INn → 계량대 SC-INn → AMR → 버퍼 Ln-IN → 리프트 Ln → 층 입고 이송점 → 셔틀(MS) → 레인
출고: 레인 → 셔틀(MS) → 층 출고 이송점 → 리프트 Ln → 버퍼 Ln-OUT → AMR → 하차대 DP-OUTn → 도크 OUTn → 트럭
```

| 대상 | ID | 역할 | 근거 |
|---|---|---|---|
| **도크** | `IN1~6`, `OUT1~6` | 남쪽 벽의 트럭 문. 입고 도크는 트럭에서 팔레트를 내려 계량대에 놓고(지게차, 팔레트당 30 s), 출고 도크는 하차대의 팔레트를 트럭에 싣습니다. 트럭 도착·출발도 도크가 알립니다 | `d4_layout.json` `docks`, `virtual_plant.py` `_dock_step` |
| 계량대 | `SC-IN1~6` | 입고 도크 앞. 팔레트 무게를 재서 `scale/SC-INn/reading` 발행. 송장 무게와 ±5% 넘게 다르면 스케줄러가 검수 구역 `QC-IN`으로 보냅니다. **입고 AMR이 팔레트를 집는 곳** | `topics.py` scale.reading |
| 하차대 | `DP-OUT1~6` | 출고 도크 앞. **출고 AMR이 팔레트를 내려놓는 곳** | `d4_layout.json` `docks` |
| 적치 칸 | `dock_IN1_stage_11` 등 도크당 28칸 | 트럭 1대분(4줄 × 7)을 바닥에 내려놓는 자리. MQTT에는 칸 단위 ID·상태가 **없고**, 계량대 `SC-INn`의 대기 줄(최대 28개)로만 다룹니다 | `virtual_plant.py` (`len(stations[st]) >= 28`이면 하차 중지), `warehouse/d4/sim.py` `STAGE_CAP = 28` |
| **버퍼** | `L1-IN`, `L1-OUT` … `L3-OUT` (6개), 칸 `L1-IN-1~5` | 리프트 옆 컨베이어, 리프트당 입고 5칸 + 출고 5칸. **AMR과 리프트 사이의 완충 지점**. 칸 1 = 리프트 옆, 칸 5 = AMR 접점. 한 칸 이동 2 s, 높이 0.6 m(가정) | `d4_layout.json` `buffers`, `virtual_plant.py` `CONVEYOR_S` |
| **레일** | `R1~R3` | 리프트 북쪽으로 이어지는 남북 방향 통로(폭 1.8 m, 길이 56 m, 10층). 층마다 모셔틀 1대가 이 레일 위를 남북으로 다닙니다. 레일 **양옆(서 W·동 E)** 으로 레인이 33줄씩 붙어 있습니다 | `d4_layout.json` `rails` |
| **셔틀** | 모셔틀 `MS-R1-LV01` … (레일 3 × 층 10 = **30세트**), 세틀라이트 `SAT-R1-LV01` … | 모셔틀은 레일 위를 남북으로 움직여 레인 입구에 서고, 세틀라이트가 모셔틀에서 내려 **레인 안으로 들어가** 팔레트를 넣거나 꺼냅니다. 층을 옮기지 않습니다(층마다 1세트 고정) | `d4_layout.json` `rails[].mother_shuttles/satellites`, `topics.py` shuttle.cmd |
| 층 이송점 | ID 없음 (아래 참고) | 각 층 레일 남쪽 끝, 리프트와 모셔틀이 팔레트를 주고받는 자리. 입고용·출고용이 따로 있음 | `DeviceMqttAgents.cs` `IRackModel.Handoff(rail, level, inbound)`, `schemas.py` ("리프트 이송점 = 20.4") |
| 레인 | `R1-W-LV01-N01` … (1,980개) | 깊이 14 팔레트, **SKU 1종 전용**, 입구가 레일 쪽 하나뿐이라 **선입후출**. 안쪽(위치 14)부터 채움 | `warehouse/d4/storage.py` 머리말 |
| 검수 구역 | `QC-IN`, `QC-OUT` | `QC-IN` = 무게 불일치 보류 팔레트를 두는 곳(관제가 해제하면 다시 입고 흐름으로). `QC-OUT` = 포장·라벨링·출고 검수 | `d4_layout.json` `facilities` |
| 충전기 | `CHW1~3`(서벽), `CHE1~3`(동벽) | AMR 충전. AMR01·02는 CHW1·2, AMR03·04는 CHE1·2에서 시작 | `d4_layout.json` `chargers`, `amrs` |

셔틀 명령 (`shuttle/{MS-…}/cmd`):

| action | 동작 |
|---|---|
| `store` | 그 층 **입고 이송점**의 팔레트를 `lane`의 `pos`에 넣기 |
| `retrieve` | `lane`의 `pos` 팔레트를 그 층 **출고 이송점**으로 |
| `relocate` | 레인 간 이동 (스키마만 있고 가상 장비·C#에는 **구현 없음**) |

> 세틀라이트는 따로 토픽이 없습니다. 세틀라이트 위치는 `shuttle/{MS-…}/state`의 `satellite`(`where`: docked/in_lane, `lane`, `pos`)에 함께 들어갑니다.

---

## 3. 상태를 어느 객체가 제공하나

원칙 (`comm/README.md` 1장): **Unity = 장비 상태의 원본, 스케줄러 = 작업·재고의 원본.** 같은 정보를 두 곳이 발행하지 않습니다.

### Unity가 발행 (토픽은 `wh/v1/` 뒤)

| 상태 | 토픽 | Unity 쪽 발행 객체 (C# 예제) | retain | 주기 |
|---|---|---|---|---|
| 시뮬레이션 시각·배속 | `sim/clock` | `WhMqttClient` (씬에 1개) | O | 1 Hz + 변경 시 |
| AMR 위치·속도·배터리·적재 무게 | `amr/{AMR01}/pose` | AMR 프리팹의 `AmrMqttAgent` | X | 10 Hz |
| AMR 2D LiDAR 72빔 | `amr/{id}/scan2d` | 〃 | X | 10 Hz |
| AMR 모드·실은 팔레트·진행 중 미션 | `amr/{id}/state` | 〃 | O | 변경 시 + 5 s |
| AMR 사건(미션 수락·완료 등) | `amr/{id}/evt` | 〃 | X | 사건 시 |
| 리프트 높이·모드·실은 팔레트·대기열 | `lift/{L1}/state`, `evt` | 리프트의 `LiftMqttAgent` | state O | 변경 시 |
| 모셔틀 위치(y)·세틀라이트 위치 | `shuttle/{MS-R1-LV01}/state`, `evt` | 모셔틀의 `ShuttleMqttAgent` (30개) | state O | 변경 시, 이동 중 2 Hz |
| 버퍼 5칸의 팔레트 | `buffer/{L1-IN}/state` | `FacilityMqtt.Buffer(...)` 호출 | O | 변경 시 |
| 도크 트럭 유무·진행 수 | `dock/{IN1}/state`, `evt` | `FacilityMqtt.Dock(...)`, `DockEvent(...)` | state O | 변경 시 |
| 계량값 | `scale/{SC-IN1}/reading` | `FacilityMqtt.Scale(...)` | X | 팔레트마다 |
| 충전기 점유 | `charger/{CHW1}/state` | `FacilityMqtt.Charger(...)` | O | 변경 시 |

- 버퍼·도크·계량대·충전기는 **전용 컴포넌트가 코드에 없습니다.** 정적 함수 `FacilityMqtt.*`만 있어서 씬에서 값이 바뀔 때 호출하면 됩니다.
  (제안) 오브젝트마다 `BufferConveyor`(6개), `DockController`(12개), `WeighStation`(6개), `Charger`(6개) 컴포넌트를 만들어 그 안에서 호출.
- **팔레트가 어디 있는지**(버퍼 칸, 층 이송점, 레인 위치)는 씬의 재고 모델 하나가 `IRackModel` 인터페이스로 들고 있어야 합니다.
  리프트·셔틀 에이전트가 이 인터페이스로 팔레트를 옮깁니다 (`DeviceMqttAgents.cs`).
- AMR이 집고 놓는 정지점(계량대·하차대·버퍼 끝·검수 구역)은 각각 `IPalletPort`를 구현해 `AmrMqttAgent.RegisterPort()`로 등록합니다 (`AmrMqttAgent.cs`).

### 스케줄러가 발행 (Unity는 읽기만)

| 내용 | 토픽 | Unity에서 쓰는 곳 |
|---|---|---|
| 팔레트 1개 단위 작업 상태 | `wms/job` | 화면 표시 |
| 재고 변경 사건 | `wms/inventory/evt` | 화면 표시 |
| 레인별 재고 전체 (retain, 60 s마다) | `wms/inventory/snapshot` | **시작 재고 배치.** `[레인, SKU, 개수]` 목록이며 개수만큼 위치 14부터 안쪽→바깥으로 채움 (`virtual_plant.py` `_on_snapshot`) |
| AMR 경유 노드 시간 예약표 (retain) | `traffic/reservations` | 경로 시각화 (선택) |

- 시작 재고 팔레트에는 **팔레트 ID가 없습니다**(스냅샷에 SKU·개수만 있음). 가상 장비는 `~SKU` 자리표시로 두고, 나중에 `retrieve` 명령이 오면 명령의 `pallet` ID를 그 팔레트에 붙입니다.
- 트럭은 실제로는 그냥 오지만, 시뮬레이션에서는 **Unity가 `wms/order`를 구독해 그 도크에 트럭을 세웁니다** (`virtual_plant.py` `_on_order`). 토픽 표의 Unity 구독 목록에는 빠져 있지만 Unity 계정 권한은 `wh/v1/#` 전체 읽기라 구독할 수 있습니다.
- 명령 결과는 반드시 `evt`로 `accepted → started → done / failed / rejected` 순서로 보내고, `corr`에 원래 명령의 봉투 `id`를 넣습니다.

---

## 4. 단위·용량·처리시간

### 단위 (`comm/whmqtt/schemas.py` 머리말)

| 항목 | 단위 |
|---|---|
| 거리·좌표 | m (Unity 1 unit = 1 m) |
| 시간 | s. 메시지의 시각(`t`, `t_start`, `t_end`, `deadline`, `eta`)은 모두 **시뮬레이션 시각**(`sim/clock` 기준) |
| 각도 | rad, 동쪽 0, 반시계 + (`theta`). Unity yaw(deg) = 90° − theta (`WhCoords.cs`) |
| 속도 | m/s, rad/s |
| 무게 | kg |
| 배터리 | 0~100 % |
| 층 | MQTT `level` 1~10 = `LV01~LV10`, 0 = 바닥 버퍼 높이 |
| 레인 안 위치 `pos` | 1 = 레일 쪽 입·출구 ~ 14 = 벽 쪽 끝 |

### 크기·용량 (`d4_layout.json` `params`)

| 항목 | 값 |
|---|---|
| 건물 | 120 × 80 m, 높이 25 m. 랙 높이 15 m |
| 층 | 10층, 층고 1.5 m. `LVnn` 팔레트 바닥 높이 = (nn−1) × 1.5 m |
| 팔레트 | 1.1 × 1.1 m, 적재 높이 1.2 m(가정), 레인 안 간격 1.3 m |
| 레인 | 폭 1.4 m, 깊이 14 팔레트. 레일 3 × 양쪽 2 × 33줄 × 10층 = **1,980 레인** |
| 보관 용량 | **27,720 팔레트** (1,980 × 14) |
| 층별 허용 하중 | LV01~03 1,500 kg, LV04~06 1,200 kg, LV07~10 1,000 kg (`warehouse/d4/layout.py` `LEVEL_MAX_KG`) |
| 장비 수 | AMR 4, 리프트 3, 셔틀 30세트, 충전기 6, 도크 입 6 + 출 6 |
| 버퍼 | 리프트마다 입고 5칸 + 출고 5칸 |
| 도크 적치 | 도크마다 28칸 (트럭 1대분) |
| 층 이송점 | 층마다 입고·출고 각각 — **코드끼리 다름**: 알고리즘 시뮬 2칸(`sim.py` `LEVEL_SLOT = 2`), 가상 장비·C# 1칸 |

### 처리시간 (장비 성능 가정, `warehouse/shuttle.py` = `comm/sim/virtual_plant.py` = C# 기본값)

| 장비 | 값 | 계산 |
|---|---|---|
| AMR 주행 (scripted) | 1.2 m/s | 경유점 사이 직선 |
| AMR 주행 (RL) | v −0.3~1.5 m/s, w −1.2~1.2 rad/s, 10 Hz | 0.5 s 동안 새 명령이 없으면 정지 |
| AMR 싣기·내리기 | 각 8 s | |
| 트럭 하차 | 팔레트당 30 s (도크당) | |
| 트럭 상차 | **코드끼리 다름**: 가상 장비 20 s, 알고리즘 시뮬 30 s | |
| 계량 | 알고리즘 시뮬 20 s, 가상 장비는 하차와 동시에 계량값 발행 | |
| 버퍼 컨베이어 | 한 칸 2 s | |
| 리프트 | 1.5 m/s, 이동마다 가감속 2 s, 이송 5 s | 이동 = 2 + |Δ층| × 1.5 / 1.5 = **2 + |Δ층| s**. 예: 바닥에서 집어 LV10 이송점에 놓기 = 5 + 12 + 5 = 22 s |
| 모셔틀 | 2.0 m/s, 이동마다 3 s | 이동 = 3 + 거리 / 2 |
| 세틀라이트 | 1.0 m/s, 들어가기·나오기 각 4 s (들기·놓기 포함) | 왕복 ≈ 2 × pos × 1.3 + 8 s |
| 모셔틀 ↔ 이송점 | 4 s | |
| AMR 배터리 | **코드끼리 다름**: 알고리즘 시뮬은 이동 5시간에 100% 소모·충전 1.5시간에 100%, 25% 미만이면 작업 안 받고 90%까지 충전. 가상 장비·C#은 데모용으로 빠르게(이동 0.02 %/s, 충전 0.5 %/s) | |

예: 가장 먼 레인(33번 줄, 위치 14)에서 꺼내기 ≈ 모셔틀 31 s + 세틀라이트 44 s + 모셔틀 31 s + 이송 4 s ≈ **2분**.

### 처리량 (알고리즘 시뮬레이션 결과)

| 조건 | 결과 | 근거 |
|---|---|---|
| AMR 4대 포화 처리량 | 약 **151 팔레트/h** (병목 = AMR) | `out/shuttle_compare.json` |
| 리프트 3기 한계 (AMR 무제한) | 약 628 팔레트/h | 〃 |
| 입고·출고 각 40/h, 8시간 | 입고→레인 평균 16분, 출고 트럭 도착→상차 완료 평균 22분, AMR 충돌 0 | `README.md`, `out/d4_compare.json` |

(제안) 코드끼리 다른 값(상차 시간, 이송점 칸 수, 배터리)은 Unity에서 **인스펙터 값으로 열어 두고**, 기본값은 알고리즘 시뮬 쪽(상차 30 s, 이송점 2칸, 배터리 5시간/1.5시간)을 쓰면 처리량 결과와 비교하기 쉽습니다. 확정되면 가상 장비와 C# 기본값을 맞추겠습니다.

---

## 5. 예약과 제어 책임

| 판단 | 누가 | 방법 |
|---|---|---|
| 작업 생성, 레인 배정(적재), 레인 선택(출고) | **스케줄러** | `warehouse/d4/comm_planner.py` `D4Planner` (같은 SKU 레인 우선, 층 허용 하중, 회전율, 리프트 부하) |
| 레인 안 위치 `pos` | **스케줄러** | `store` 명령을 보내는 순간 정함. 세틀라이트가 다른 팔레트를 넘어가지 않도록 |
| 어떤 AMR이 갈지 | **스케줄러** | `Planner.choose_amr` |
| AMR 경로와 노드별 시간 예약 | **스케줄러** | 미션의 `path[].t_start ~ t_end`, 전체 예약표 `traffic/reservations`. 통로 방향은 고정하지 않고 시간으로 나눠 씀 |
| 미션 실행 (scripted) | **Unity** | 경유점을 따라 이동. **`t_start` 전이면 그 노드 앞에서 대기** |
| 미션 실행 (rl) | **RL 정책 → Unity** | RL이 `cmd/vel`(10 Hz) 발행, Unity가 그 속도로 움직임. 경유점 0.3 m 이내면 다음 경유점. **pick/drop은 Unity가 수행** |
| 주행 모드 전환 | **스케줄러·관제** | `amr/{id}/cmd/control` `set_drive_mode` (RL 접속이 끊기면 scripted로) |
| 안전 정지 | **Unity** | `cmd/vel`이 0.5 s 안에 안 오면 정지 (RL 프로세스가 죽어도 안전) |
| pick/drop 전제 조건 | **Unity** | `pick`은 팔레트가 그 자리에 있을 때, `drop`은 자리가 비었을 때 시작. 될 때까지 기다림 (가상 장비는 600 s 넘으면 실패. C# AMR 예제에는 제한 시간 없음) |
| 리프트·셔틀 명령 실행 | **Unity** | 장비별 **FIFO 대기열**. 순서를 바꾸지 않음. 이송점·버퍼 칸이 차 있으면 빌 때까지 대기 (C# 600 s 넘으면 `failed`) |
| AMR 동시 미션 | **Unity** | 한 번에 1개. 바쁘면 `mission_rejected` (reason "busy") |
| 무게 불일치 보류·해제 | 스케줄러 판단, **관제**가 해제 | `ctrl/cmd release_hold` |
| 시뮬 시작·정지·배속 | 스케줄러·관제 → **Unity** | `sim/control` |
| 시뮬 시각 | **Unity** | `sim/clock` 발행. 다른 프로그램은 이 시각 기준 |

정리하면 **Unity는 "무엇을, 어디로, 언제"를 정하지 않고, 받은 명령을 물리적으로 실행하고 결과를 보고**합니다.

코드에 없는 것

- AMR끼리 **물리 충돌**이 났을 때 Unity가 할 일은 정해져 있지 않습니다. 가상 장비는 충돌을 흉내 내지 않고, 스케줄러 브리지의 예약은 노드 충돌만 보고 교차 이동(간선 충돌)은 아직 검사하지 않습니다 (`comm/README.md` 13장).
  (제안) Unity에서 충돌·근접(예: 0.5 m 이내)을 감지하면 두 AMR을 정지하고 `amr/{id}/evt`에 `mission_failed` + reason `"collision"`으로 보고.
- AMR이 미션을 마친 뒤 쉬는 자리 `WAIT1~4`는 배치 JSON과 경로 그래프(`n_WAIT1` 등)에는 있지만 **MQTT 미션의 정지점 ID로는 아직 허용되지 않습니다**(`ids.py` 정지점 패턴에 없음). 지금은 미션이 끝난 자리에 그대로 있습니다.
  (제안) 정지점 패턴에 `WAIT1~4`를 추가해 스케줄러가 `move` 단계로 보냄.

---

## 6. Unity ID·좌표·구역 대응 관계

### 좌표계 (`unity/README.md`)

- **1 unit = 1 m**, Y-up, 원점 (0,0,0) = 창고 바닥 **남서쪽 안쪽 모서리**
- **+X = 동쪽**(0~120), **+Z = 북쪽**(0~80), **+Y = 위**. 남쪽 벽 바깥 트럭 칸은 Z < 0
- `d4_layout.json`의 좌표 배열은 Unity `Vector3` 순서 `[x, 높이, z]`
- **MQTT 메시지는 도면 좌표** `(x 동, y 북, z 위)` → Unity `new Vector3(x, z, y)` (`WhCoords.ToUnity`)
- 방향: MQTT `theta`(동쪽 0, 반시계 rad) → Unity yaw = 90° − theta(deg) (`WhCoords.ThetaToYaw`)

### 구역 (남→북 띠, `params.bands_z`)

| z 범위 (m) | 구역 | 들어 있는 것 |
|---|---|---|
| < 0 | 바깥 | 트럭 칸 `truck_bay` |
| 0 ~ 14.4 | 도크·적치 | 서쪽 x 0~40 **입고 구역**(도크 IN1~6, 계량대 SC-IN), 가운데 x 40~80 **사무실 2층**, 동쪽 x 80~120 **출고 구역**(도크 OUT1~6, 하차대 DP-OUT). 도크 3+3 사이에 예비 구역 9 × 8.4 m |
| 14.4 ~ 19.2 | AMR 통로 | 폭 4.8 m, 양방향, 경로 그래프 통로 노드의 z = 16.8 |
| 19.2 ~ 21.6 | 리프트 줄 | 리프트 L1~3, 버퍼, 쉬는 자리 WAIT1~4 |
| 21.6 ~ 31.4 | 검수·지원 띠 | 서벽 충전기 CHW1~3, 정비, QC-IN, QC-OUT, 예비 부품, 동벽 충전기 CHE1~3 |
| 31.4 ~ 77.6 | 보관 | 레일 R1~3, 랙 블록 6개, 레인 33줄 |
| 77.6 ~ 80 | 북쪽 여유 | |

### ID ↔ 좌표 ↔ 경로 노드

| 대상 | MQTT·씬 ID | JSON 위치 | 좌표 (Unity x, z) | AMR 경로 노드 (`nav`) |
|---|---|---|---|---|
| AMR | `AMR01~04` | `amrs[].spawn` | AMR01 (1.5, 23.6) … | 시작 = 충전기 노드 |
| 입고 도크 | `IN1~IN6` | `docks[].door_center` | x 2.5, 7.5, 12.5, 27.5, 32.5, 37.5 / z 0 | — |
| 계량대 | `SC-IN1~6` | `docks[].station_center` | 도크와 같은 x, z 11.8 | `n_dock_IN1~6` |
| 출고 도크 | `OUT1~OUT6` | `docks[].door_center` | **OUT1 = x 117.5 (동쪽 끝)** … OUT6 = x 82.5. 번호가 동→서 순서 | — |
| 하차대 | `DP-OUT1~6` | `docks[].station_center` | 도크와 같은 x, z 11.8 | `n_dock_OUT1~6` |
| 트럭 칸 | (도크 ID) | `docks[].truck_bay_center` | 도크와 같은 x, z −8.5 | — |
| 리프트 | `L1~L3` | `lifts[].center` | (21.8 / 60.0 / 98.2, 20.4) | — |
| 입고 버퍼 | `L1-IN` (칸 `L1-IN-1~5`) | `buffers[]`, `boxes`(type `conveyor_slot`) | AMR 접점 L1 (14.75, 18.6), L2 (52.95, 18.6), L3 (91.15, 18.6) | `n_buf_L1-IN` |
| 출고 버퍼 | `L1-OUT` (칸 `L1-OUT-1~5`) | 〃 | AMR 접점 L1 (28.85, 18.6), L2 (67.05, 18.6), L3 (105.25, 18.6) | `n_buf_L1-OUT` |
| 레일 | `R1~R3` | `rails[]` | 중심 x 21.8 / 60.0 / 98.2, z 21.6~77.6 | — |
| 모셔틀 | `MS-R1-LV01` | `rails[].mother_shuttles` | 레일 중심 x, 높이 (층−1) × 1.5. 위치는 `shuttle.state.y`(도면 y = Unity z) | — |
| 세틀라이트 | `SAT-R1-LV01` | `rails[].satellites` | 레인 안 위치는 `shuttle.state.satellite` | — |
| 레인 | `R1-W-LV03-N12` | `storage.lanes[]` (층을 뺀 `R1-W-N12`) | 위치 P 중심 x = `slot0_x + (P−1) × slot_step_x`, z = `center_z`, 높이 = (층−1) × 1.5 | — |
| 검수 구역 | `QC-IN`, `QC-OUT` | `facilities[].amr_contact` | (40.9, 21.8), (79.1, 21.8) | `n_inbound_qc`, `n_outbound_qc` |
| 충전기 | `CHW1~3`, `CHE1~3` | `chargers[].park_center` | 서 x 1.5 / 동 x 118.5, z 23.6·26.5·29.4 | `n_CHW1` … |
| 쉬는 자리 | `WAIT1~4` | `wait_spots[]` | x 33.0, 36.6, 84.6, 87.0 / z 19.8 | `n_WAIT1~4` |
| 팔레트 | `P` + 8자리 | — | — | — |

- MQTT 미션의 정지점 ID → 경로 노드 변환 규칙은 `comm/whmqtt/geometry.py` `_stop_nodes()`에 있습니다 (위 표의 마지막 열).
- 정지점에서 AMR이 바라보는 방향: 계량대·하차대는 남쪽(theta −π/2), 버퍼는 북쪽(+π/2), 서벽 충전기는 서쪽(π), 동벽 충전기는 동쪽(0) (`geometry.py` `stop_theta`).
- 레인 위치 1이 레일 쪽 입구입니다. 서쪽 레인은 `slot_step_x`가 −1.3이라 위치 번호가 커질수록 서쪽(벽 쪽)으로 갑니다.
- **씬 오브젝트 이름 = 위 ID** 로 맞춰 주세요. `WarehouseLayoutLoader.cs`가 박스를 만들 때 이미 이 이름(`L1`, `R1`, `L1-IN-1`, `SC-IN1` …)을 씁니다. 실제 에셋으로 바꿀 때 같은 이름·위치·크기를 유지하면 됩니다.

### 층 번호 주의 (코드끼리 다름)

| 곳 | LV01을 나타내는 값 |
|---|---|
| MQTT `lift.cmd.level`, `lift.state.level` | **1** (0 = 바닥 버퍼 높이) |
| `d4_layout.json` `lifts[].serves_levels` | **0** (`[0..9]`, 알고리즘 내부의 0부터 세는 층 번호가 그대로 나옴) |
| ID 문자열 | `LV01` |

C# `LiftMqttAgent`는 MQTT 기준으로 리프트 운반대 높이를 `max(0, level − 1) × 1.5`로 둡니다. 즉 level 0(버퍼)과 level 1(LV01)이 같은 높이(바닥)입니다.
(제안) Unity는 **MQTT 값(1~10)과 ID 문자열만** 쓰고 `serves_levels`는 무시하세요. 다음 내보내기에서 `serves_levels`를 `[1..10]`로 고치겠습니다.

### 층 이송점 위치 (코드에 없음)

층 이송점은 **배치 JSON에 박스도 ID도 없습니다.** 코드에 있는 것은

- 셔틀 상태의 설명 "모셔틀 레일 방향 위치 y, 리프트 이송점 = 20.4" (`schemas.py`) → 리프트 중심과 같은 z
- C# `IRackModel.Handoff(rail, level, inbound)` → 레일·층·입고/출고로 구분

(제안) Unity에서는 각 레일·층마다 리프트 북쪽 면(z ≈ 21.6, 레일 중심 x, 높이 (층−1) × 1.5)에 입고·출고 이송점 2개를 두고, 오브젝트 이름은 `R1-LV03-XIN`, `R1-LV03-XOUT`처럼 정하면 됩니다. MQTT 메시지에는 이 이름이 나가지 않으므로 씬 안에서만 쓰는 이름입니다.

---

## 7. 코드에 없거나 서로 다른 것 (정리)

| 항목 | 지금 상태 | 제안 |
|---|---|---|
| 층 이송점 | JSON에 박스·ID 없음. 칸 수 알고리즘 2 vs C#·가상 장비 1 | 씬 이름 `R1-LV03-XIN/XOUT`, 칸 수는 2로 통일 |
| `serves_levels` | 0~9 (MQTT는 1~10) | JSON을 1~10으로 수정 |
| 쉬는 자리 `WAIT1~4` | 배치·경로에는 있으나 미션 정지점 ID로 허용 안 됨 | 정지점 패턴에 추가 |
| 출고 상차 시간 | 20 s vs 30 s | 30 s로 통일 |
| 배터리 모델 | 데모용 빠른 값 vs 5시간/1.5시간 | 인스펙터 값, 기본은 5시간/1.5시간 |
| 버퍼·도크·계량·충전기 컴포넌트 | 정적 함수 `FacilityMqtt.*`만 있음 | 오브젝트별 컴포넌트 |
| AMR 물리 충돌 처리 | 정해지지 않음 | 정지 + `mission_failed` reason `collision` |
| 셔틀 `relocate` | 스키마만 있음 | Unity는 `rejected`로 응답해도 됨 (지금 스케줄러는 보내지 않음) |
| 시작 재고 팔레트 ID | 스냅샷에 없음 | 가상 장비처럼 `retrieve` 때 ID를 붙임 |
| Unity 에디터 실행 | C# 스크립트는 MQTTnet 4.3.7 + Newtonsoft로 컴파일·상호 운용까지만 확인, **Unity에서 직접 실행은 아직 안 해 봄** | 팀원 쪽에서 첫 실행 결과 공유 부탁 |

질문이나 바꾸고 싶은 값이 있으면 이 문서에 이어서 적어 주세요. 위 제안 중 합의된 것은 코드(가상 장비, C#, 배치 내보내기)에 반영하겠습니다.
