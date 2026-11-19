# Unity 연동 명세 (배치 D4) v2

2026-11-19 Unity 팀원의 구현 명세(`Warehouse_System_Spec.md`)를 **기준**으로 우리 쪽(알고리즘·배치 데이터·MQTT·C# 스크립트)을 맞춘 결과입니다.
v1(질문 6개에 대한 답)에서 바뀐 점과, Unity 쪽에서 아직 비어 있거나 다른 것을 정리했습니다.
Unity 쪽 항목은 **목록으로만 정리**했고 우리가 고치지 않았습니다. 추정한 값은 `(추정)`으로 표시했습니다.

함께 볼 문서

| 문서 | 내용 |
|---|---|
| [`unity/README.md`](../unity/README.md) | 배치 JSON 구조, 좌표계(월드 원점), Unity와 맞춘 치수, ID 규칙 |
| [`comm/README.md`](../comm/README.md) | MQTT 통신 설계 전체, 시퀀스 다이어그램, Unity 스크립트 붙이는 법 |
| [`comm/docs/topics.md`](../comm/docs/topics.md) | 토픽 32개 표 (발행·구독·QoS·주기) |
| [`comm/sim/rack_model.py`](../comm/sim/rack_model.py) | Unity `ShuttleRackSystem` 규칙을 흉내 낸 가상 랙 (MQTT 데모·테스트용) |
| [`comm/unity/WarehouseMqtt/`](../comm/unity/WarehouseMqtt/) | Unity C# 스크립트 (MQTT 클라이언트, AMR 에이전트, 랙 브리지) |

---

## 1. 무엇을 바꿨나 (우리 쪽)

### 책임 나누기: 랙 안은 Unity, 통로 고르기는 우리

v1에서는 스케줄러가 레인·칸을 정하고 리프트·셔틀에 명령을 하나씩 보냈습니다. Unity 구현은 `ShuttleRackSystem`(통로마다 1개)이
채널 배정, 예약(virtualCount), 칸 선택, 리프트·모셔틀 순서를 **전부** 합니다(명세 5.1). 그래서 이렇게 바꿨습니다.

| 판단 | v1 | v2 (지금) |
|---|---|---|
| 어느 통로(R1~R3)로 넣고 어디서 꺼낼지 | 스케줄러 | **스케줄러** (명세 7장 4번 "통로 간 배분 없음" = WMS 몫) |
| 채널(단별 레인)·칸 선택, 예약 | 스케줄러 | **Unity `ShuttleRackSystem`** |
| 리프트·모셔틀 명령, 작업 순서 | 스케줄러가 `lift/cmd`, `shuttle/cmd` | **Unity** (스케줄러는 명령 안 보냄) |
| AMR 배정·경로·시간 예약 | 스케줄러 | 스케줄러 (그대로) |
| 랙 재고(채널별 SKU·수량)의 원본 | 스케줄러 | **Unity** (`rack/{rail}/inventory` 로 알려 줌) |

### MQTT 토픽

| 없앤 것 | 새로 만든 것 |
|---|---|
| `lift/{L1}/cmd`, `lift/{L1}/evt`, `shuttle/{id}/cmd`, `shuttle/{id}/evt` | `rack/{R1}/cmd` (스케줄러 → Unity): `store`(pallet, sku, weight) = `RequestStore`, `retrieve`(sku) = `RequestRetrieve` |
| | `rack/{R1}/evt` (Unity → ): `accepted`(예약한 channel) / `rejected`(만재·재고 없음) / `started` / `done`(channel, slot 1~14, pallet) / `failed` |
| | `rack/{R1}/state` (retain): `StoredCount`, `Capacity`, `PendingTasks`, `InboundFree`, `TotalStored`, `TotalRetrieved`, `AvgCycleSeconds` |
| | `rack/{R1}/inventory` (retain, 시작 + 60 s): 채널마다 `[채널, SKU, 실제 수량, 가상 수량]`. 시작 재고(`PrefillRandom`)도 이걸로 스케줄러에 알림 |

`lift/{L1}/state`(Status, Height), `shuttle/{R1-Shuttle_L01}/state`(Busy, Status, level, 새틀라이트), `buffer/{R1-IN}/state`는 **모니터링 전용**으로 남겼습니다.
토픽 수는 32개 그대로, 스키마 33개.

입고 흐름은 이렇게 됩니다.

```
AMR 이 R1-IN 에 drop (= AcceptInbound, IN5 가 비었을 때: rack/R1/state inbound_free)
→ 스케줄러 rack/R1/cmd store → Unity RequestStore → rack/R1/evt accepted(channel) → started → done(slot)
출고: 스케줄러 rack/R2/cmd retrieve(sku) → accepted(channel) → started → done(slot, pallet)
→ 팔레트가 OUT5 에 오면 AMR 이 R2-OUT 에서 pick (= TakeOutbound)
```

### 좌표·ID·치수

| 항목 | v1 | v2 (Unity 기준) |
|---|---|---|
| 원점 | 바닥 남서쪽 안쪽 모서리 | **서쪽 벽 외부면 · 바닥 윗면 · 남쪽 벽 안쪽면 = 월드 (-60.005, 1.20, -39.705)**. 월드 = 배치도 + 원점 |
| 바닥 | 120 x 80 | 120.01 x 80.01 |
| 통로 | 폭 1.8 m | 폭 2.0 m, 중심 x 21.8 / 60.0 / 98.2 |
| 칸 간격 | 1.3 m | 1.25 m (깊이 17.5 m), 칸 중심 = 통로 중심 ± (1.0 + (칸-0.5) x 1.25) |
| 줄 | 줄 1 중심 32.1 | 같음 (리프트 중심 20.4 + 11.0 + 0.7) |
| 단 높이 | (단-1) x 1.5 | 레일 윗면 **0.40 + 1.45 x (단-1)** |
| 리프트 ↔ 모셔틀 주고받는 위치 | 리프트 중심 (z 20.4) | 리프트 중심 + 2.0 (**z 22.4**) |
| 버퍼 | 높이 0.6 m (가정) | 롤러 윗면 0.50 m, 간격 1.3 m |
| 사무동 | 40~80 x 0~14.4 | 건물 44~76 x 0~12 (`Office_Building`). 40~80 은 바닥 구역 표시로 남김 |
| 채널 ID | `R1-W-LV03-N12` | **`R1-W12-L03`**, 칸 `R1-W12-L03-14` |
| 모셔틀 ID | `MS-R1-LV03`, 새틀라이트 `SAT-R1-LV03` | **`R1-Shuttle_L03`** (새틀라이트는 모셔틀 상태에 포함) |
| 버퍼 ID | `L1-IN`, 칸 `L1-IN-1` | **`R1-IN` / `R1-OUT`**, 칸 IN1~IN5 (1 = 리프트 쪽), 모델 `Buffer_L1_IN1` |
| 팔레트 ID | `P` + 8자리 | 입고 트럭 팔레트 `P` + 8자리, 랙에서 만든 팔레트 `R1-000123` (둘 다 허용) |
| `serves_levels` | 0~9 | 1~10 |

배치 JSON(`unity/d4_layout.json` v2.0)에는 Unity 오브젝트 경로(`unity_object`: `Rack_R1/Rack_Generated`, `Lane_W01`, `Dock_IN1`, `Scale_IN1`, `DropStand_OUT1` …)를 붙였고,
`WarehouseLayoutLoader.cs`는 `snapToWorldOrigin`(기본 켬)으로 기존 씬과 겹치게 놓습니다.

### 장비 시간 (알고리즘 시뮬레이션 `warehouse/d4/storage.py`, 가상 랙 `comm/sim/rack_model.py`)

| 장비 | v1 | v2 (명세 4.3) |
|---|---|---|
| 모셔틀 | 2.0 m/s + 이동마다 3 s | 2.5 m/s, 가속 1.0 m/s² (사다리꼴 속도) |
| 새틀라이트 | 1.0 m/s + 4 s | 1.2 m/s, 가속 0.8, 판 승강 0.05 m @ 0.08 m/s |
| 리프트 | 1.5 m/s + 2 s | 1.5 m/s, 가속 0.8 |
| 이송 | 5 s / 4 s | 0.6 m/s 등속, 거리 1.95 m → 3.25 s **(추정: 명세의 리프트 1회 점유 18.5 s 에서 거꾸로 계산)** |
| 버퍼 컨베이어 | 한 칸 2 s | 0.5 m/s, 한 칸 1.3 m = 2.6 s (끝칸 → 첫칸 10.4 s) |

명세 4.4 표와 대조: 리프트 평균 6.0 s / 최대 10.5 s, 모셔틀 최대 24.3 s, 새틀라이트 최대 16.4 s 가 계산과 맞습니다 (`tests/test_d4.py::test_unity_timing`).

### 알고리즘 규칙

| 규칙 | v1 | v2 |
|---|---|---|
| 채널 고르기 (기본) | 회전율·접근 시간 순위 | **Unity 비용식**: 같은 SKU 채널 → 빈 채널 중 (리프트 시간 + 모셔틀 시간 + 그 단 대기 작업 x 20 s + 빈칸 x 0.1) 최소. 회전율 방식은 `slotting="abc"`로 비교용 |
| 리프트 | 이중 사이클 | 한 번에 한 작업 (`liftOwner`). 이중 사이클은 비교용 옵션 |
| 같은 단 작업 순서 | 가까운 것 먼저 | 등록 순서(FIFO) |
| 레인 합치기 | 셔틀이 놀 때 | 끔 (Unity 에 없음) |
| 층별 허용 하중 | 지킴 | **지킴** (안전 규칙이라 남김. Unity 에는 없음 → 3장) |

---

## 2. Unity 쪽에서 해 주면 되는 연결

1. `comm/unity/WarehouseMqtt/` 스크립트 7개를 넣고 `WhMqttClient`를 씬에 하나 둡니다 (MQTTnet 4.3.7, Newtonsoft).
2. 통로마다 `RackMqttBridge`(railId = R1~R3)를 붙이고, `ShuttleRackSystem`을 감싸 `IRackSystem`을 구현하는 짧은 연결 스크립트를 끼웁니다.
   `RequestStore`/`RequestRetrieve`/`TaskStarted`/`TaskCompleted`/공개 속성을 그대로 넘기면 되고, `RackTask`에서 채널 이름·칸 번호·팔레트 ID만 꺼내 주면 됩니다.
   `RackDemoDriver`는 끕니다.
3. AMR이 생기면 `R1-IN`(=`AcceptInbound`)·`R1-OUT`(=`TakeOutbound`, `outboundAutoClearSeconds = 0`)·계량대·하차대를 `IPalletPort`로 등록합니다.
4. MQTT 좌표는 도면 좌표이므로 씬에 놓을 때 `WhCoords.ToWorld(x, y, z)`를 씁니다.

C# 스크립트는 Unity API 대역으로 **실제 MQTTnet 4.3.7에 대해 컴파일**, C# 메시지 16종이 Python 스키마를 통과하는 것, Python 스케줄러와의 왕복까지 확인했습니다.
Unity 에디터에서 직접 실행해 보지는 못했습니다.

---

## 3. Unity 쪽 미구현·불일치 (우리가 고치지 않음, 목록만)

### 명세 7장에 Unity 팀원이 적은 것

| # | 항목 | 우리 쪽에 미치는 영향 |
|---|---|---|
| 1 | AMR 없음 (버퍼 팔레트가 바로 생기고, 출고 끝칸은 8 s 뒤 삭제) | AMR 연동 전까지 MQTT 미션은 가상 장비로만 확인 가능 |
| 2 | 하역·검수·상차 없음, `CloseDock()` 호출 주체 없음 | `dock/*/evt`(pallet_unloaded, pallet_loaded, truck_departed)를 보낼 곳이 없음 |
| 3 | 계량대·하차대·적치 그리드 상태 없음 | `scale/SC-INn/reading`을 보낼 객체가 없음 → 무게 불일치 보류(QC-IN)를 Unity 에서 못 봄 |
| 4 | 통로 간 배분 없음 | **우리 스케줄러가 맡음** (`Planner.choose_rail` / `pick_rail`) |
| 5 | 버퍼 `inBuf`/`outBuf` 비공개 (`InboundFree`만 공개) | `buffer/R1-IN/state` 를 못 보냄. `IRackSystem.InboundBuffer()`가 null 이면 브리지가 건너뜀. 공개 조회(읽기 전용 배열) 추가 부탁 |
| 6 | OUT 도크 번호가 배치도와 반대 (Unity 는 왼쪽부터 OUT1) | 배치 JSON·MQTT 는 **배치도 기준 OUT1 = 동쪽 끝(x 117.5)**. Unity 에서 `Apply D4` 수정 필요 |
| 7 | 남쪽 양문 출입문 offset −0.5 → 0 | 영향 없음 |
| 8 | 사무동 양옆 색 없는 구간 (DockZoneBuilder 사무 구간 40~80) | 배치 JSON 은 구역 40~80 + 건물 44~76 으로 둘 다 넣음 |
| 9 | 사무동·도크 설비·랙에 충돌체 없음 | AMR 2D LiDAR(`Physics.Raycast`)가 랙·설비를 못 봄 → RL 관측에 영향 |
| 10 | 바닥 높이 2 cm 차이 (1.20 vs 1.22) | 우리는 1.20 기준 |
| 11 | 검수 띠(21.6~31.4) 비어 있음 | 충전기 CHW/CHE, QC-IN/OUT, 정비 구역이 Unity 에 없음 → AMR 충전·보류 흐름 확인 불가 |

### 맞추면서 우리가 더 찾은 것

| 항목 | 내용 | 우리 쪽 처리 |
|---|---|---|
| 층별 허용 하중 | Unity 채널 배정에 무게 조건이 없음 (명세 5.1 비용식에 없음) | 우리 알고리즘은 LV01~03 1,500 / LV04~06 1,200 / LV07~10 1,000 kg 을 지킴. `rack.cmd store` 에 `weight_kg` 를 실어 보내니 Unity 에서 쓸 수 있음 |
| MQTT 연결 | Unity 에 MQTT 코드가 아직 없음 | C# 스크립트·`IRackSystem` 연결 방식 제공 (2장) |
| `RackTask` 내용 | 명세에 필드가 안 적혀 있음 (채널·칸·팔레트를 꺼낼 수 있는지) | `IRackSystem.TaskChannel/TaskSlot/TaskPallet` 으로 받게 해 둠. 필드 이름 알려 주면 연결 예제 작성 가능 |
| 출고 채널 고르는 규칙 | `RequestRetrieve(sku)` 가 같은 SKU 채널 여러 개 중 어디를 고르는지 명세에 없음 | 가상 랙은 "리프트·모셔틀 시간 최소"로 가정 (추정) |
| 이송 거리 | 버퍼 ↔ 리프트, 리프트 ↔ 모셔틀 이송 거리가 명세에 없음 | 1.95 m (3.25 s) 로 추정 |
| 동·북쪽 벽 위치 | 서·남쪽 벽만 좌표가 있음 | 바닥 끝(120.01 / 80.01) 바깥 0.3 m 로 추정 |
| 시작 재고 | `PrefillRandom` 결과를 밖에서 알 방법 | `rack/{rail}/inventory` 를 시작할 때 한 번 보내 주면 됨 (브리지가 자동으로 함) |
| 트럭 출발·도크 배정·야드 대기 | 명세 5.3 "책임 주체 없음" | 지금은 스케줄러가 `wms/order` 로 도크를 정하고, 가상 장비가 트럭을 세움. Unity 가 `wms/order` 를 구독해 그 도크에 트럭을 세우면 됨 |

### v1 제안 중 남은 것

| 항목 | 상태 |
|---|---|
| 층 이송점 씬 이름 `R1-LV03-XIN/XOUT` | **취소**. Unity 는 모셔틀이 리프트 앞(z 22.4)에서 직접 주고받음 |
| `serves_levels` 1~10 | **반영함** |
| `WAIT1~4` 를 미션 정지점에 추가 | **반영함** (`ids.py` 정지점 패턴) |
| 상차 30 s 로 통일 | **반영함** (가상 장비 `LOAD_S = 30`) |
| 배터리 5시간 / 1.5시간 | 알고리즘 시뮬은 그대로. 가상 장비는 데모용 빠른 값 유지 |
| 충돌 시 `mission_failed` reason `collision` | AMR 이 생기면 합의 필요 |

질문이나 바꾸고 싶은 값이 있으면 이 문서에 이어서 적어 주세요.
