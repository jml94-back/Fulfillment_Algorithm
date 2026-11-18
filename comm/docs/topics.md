# 토픽 표 (자동 생성: `python -m whmqtt.export`)

| 토픽 (`wh/v1/` 뒤) | type | 발행 → 구독 | QoS | retain | 주기 | 만료 | 내용 |
|---|---|---|---|---|---|---|---|
| `sys/{client}/online` | `sys.online` | * → control monitor scheduler | 1 | O | 접속·끊김 시 (LWT) |  | 클라이언트 접속 상태. 끊기면 브로커가 Last Will 로 online=false 를 대신 발행한다. |
| `sim/clock` | `sim.clock` | unity → scheduler rl control monitor | 0 | O | 1 Hz + 변경 시 |  | 시뮬레이션 시각·배속. 모든 봉투의 t 는 이 시각 기준. 실제 현장에서는 발행하지 않고 t = 벽시계. |
| `sim/control` | `sim.control` | scheduler control → unity | 1 |  | 명령 시 |  | 시뮬레이션 시작·정지·재개·초기화·배속 변경. |
| `amr/{amr}/pose` | `amr.pose` | unity → rl scheduler monitor | 0 |  | 10 Hz | 1s | 위치·속도·배터리·적재 무게. 자주 오고 하나쯤 잃어도 다음 값으로 덮이므로 QoS 0. |
| `amr/{amr}/scan2d` | `amr.scan2d` | unity → rl | 0 |  | 10 Hz | 1s | 2D LiDAR 72빔(5° 간격, 0번 = 정면, 반시계), 최대 10 m, 단위 m. 3D LiDAR는 MQTT로 보내지 않는다. |
| `amr/{amr}/state` | `amr.state` | unity → scheduler control monitor | 1 | O | 변경 시 + 5초마다 |  | 운행 모드, 배터리, 실은 팔레트, 진행 중 미션·단계. retain 이라 새로 붙은 클라이언트도 바로 현재 상태를 안다. |
| `amr/{amr}/evt` | `amr.evt` | unity → scheduler control monitor | 1 |  | 사건 시 |  | 미션 수락·거부, 단계 시작·완료, 미션 완료·실패, 배터리 부족. 명령에 대한 응답은 corr 에 명령 id. |
| `amr/{amr}/cmd/mission` | `amr.cmd.mission` | scheduler → unity rl | 1 |  | 작업 배정 시 | 60s | 이동·적재·하역·충전 단계 목록. 각 이동 단계에 경유 노드와 시간 예약 창(t_start~t_end). |
| `amr/{amr}/cmd/vel` | `amr.cmd.vel` | rl → unity | 0 |  | 10 Hz | 1s | RL 정책의 속도 명령 v(m/s) w(rad/s). 0.5초 안에 새 명령이 없으면 Unity가 정지시킨다. |
| `amr/{amr}/cmd/control` | `amr.cmd.control` | scheduler control → unity rl | 1 |  | 명령 시 | 30s | 일시정지·재개·미션 취소·주행 모드(scripted/rl) 변경·충전 보내기. |
| `lift/{lift}/state` | `lift.state` | unity → scheduler monitor | 1 | O | 변경 시 |  | 리프트 높이(층), 모드, 실은 팔레트, 진행 중 명령. |
| `lift/{lift}/cmd` | `lift.cmd` | scheduler → unity | 1 |  | 작업 시 | 60s | lift_in: 입고 버퍼 1번 칸 → n층 셔틀 이송점. lift_out: n층 셔틀 이송점 → 출고 버퍼 1번 칸. |
| `lift/{lift}/evt` | `lift.evt` | unity → scheduler monitor | 1 |  | 사건 시 |  | 명령 수락·거부·시작·완료·실패. |
| `buffer/{buffer}/state` | `buffer.state` | unity → scheduler monitor | 1 | O | 변경 시 |  | 리프트 입·출고 컨베이어 5칸의 팔레트 (0번 원소 = 1번 칸 = 리프트 바로 옆). |
| `shuttle/{shuttle}/state` | `shuttle.state` | unity → scheduler monitor | 1 | O | 변경 시 (이동 중 2 Hz) |  | 모셔틀 레일 위치와 세틀라이트 상태(모셔틀에 탑재 / 레인 안). 셔틀 세트 1개 = 토픽 1개. |
| `shuttle/{shuttle}/cmd` | `shuttle.cmd` | scheduler → unity | 1 |  | 작업 시 | 60s | store: 이송점의 팔레트를 레인 pos 에 넣기. retrieve: 레인 pos 의 팔레트를 이송점으로. relocate: 레인 간 이동. |
| `shuttle/{shuttle}/evt` | `shuttle.evt` | unity → scheduler monitor | 1 |  | 사건 시 |  | 명령 수락·거부·시작·완료·실패. |
| `dock/{dock}/state` | `dock.state` | unity → scheduler control monitor | 1 | O | 변경 시 |  | 트럭 유무, 하차·상차 진행 수. |
| `dock/{dock}/evt` | `dock.evt` | unity → scheduler monitor | 1 |  | 사건 시 |  | 트럭 도착·출발, 팔레트 하차(계량대에 놓임)·상차(트럭에 실림). |
| `scale/{scale}/reading` | `scale.reading` | unity → scheduler monitor | 1 |  | 팔레트마다 |  | 검수 계량값. 송장 무게와 ±5% 넘게 다르면 스케줄러가 보류(QC-IN)로 보낸다. |
| `charger/{charger}/state` | `charger.state` | unity → scheduler monitor | 1 | O | 변경 시 |  | 충전기 점유 AMR, 충전 중 여부. |
| `wms/order` | `wms.order` | control → scheduler monitor | 1 |  | 주문 시 |  | 입고 예정(ASN, 팔레트·송장 무게) 또는 출고 주문(SKU·수량). 관제나 테스트 스크립트가 넣는다. |
| `wms/job` | `wms.job` | scheduler → control monitor unity | 1 |  | 작업 상태 변경 시 |  | 팔레트 1개 단위 작업의 계획·진행 상태(레인·리프트·셔틀·AMR 배정, 예상 완료). |
| `wms/inventory/evt` | `wms.inventory` | scheduler → control monitor unity | 1 |  | 재고 변경 시 |  | 입고 접수·보류·해제·적재·출고 인출·선적·조정. event_id 로 중복 수신을 걸러낸다. |
| `wms/inventory/snapshot` | `wms.snapshot` | scheduler → control monitor unity | 1 | O | 60초마다 + 시작 시 |  | 레인별 SKU·수량 전체 목록. 새로 붙은 Unity·관제가 재고를 한 번에 맞춘다. |
| `traffic/reservations` | `traffic.reservations` | scheduler → rl unity monitor | 1 | O | 변경 시 |  | AMR별 경유 노드 시간 예약표(t_start~t_end). 통로 방향을 고정하지 않고 시간으로 나눠 쓴다. |
| `fleet/kpi` | `fleet.kpi` | scheduler → control monitor | 0 | O | 10초마다 |  | 처리량, AMR 가동률, 대기 작업 수. |
| `ctrl/cmd` | `ctrl.cmd` | control → scheduler | 1 |  | 명령 시 | 60s | 전체 일시정지·재개, 정책 변경, 보고 요청, 보류 팔레트 해제. |
| `ctrl/evt` | `ctrl.evt` | scheduler → control monitor | 1 |  | 응답·경고 시 |  | 관제 명령 응답, 상태 보고, 경고(배터리, 작업 지연). |
| `sensor/temp/{sensor}` | `sensor.temp` | sensor → control monitor | 0 | O | 10초마다 |  | (예약) 온도 센서 값. |
| `sensor/cam/{camera}/detect` | `sensor.cam` | sensor → control monitor | 1 |  | 감지 시 |  | (예약) 카메라 감지 결과(연기·누액·사람 등). |
| `alert/{kind}` | `alert` | control → scheduler unity rl monitor | 1 | O | 발생·해제 시 |  | (예약) 비상 구역 발령·해제 (화재, 랙 붕괴·기울어짐, 누액, 부상). 해제는 빈 payload 로 retain 삭제. |

## 역할별 구독

| 역할 | 하는 일 | 구독 |
|---|---|---|
| `unity` | Unity 시뮬레이션 (Mac mini M4). 장비 물리·센서의 원본. 실제 현장에서는 각 장비 컨트롤러가 이 자리를 맡는다. | `wh/v1/amr/+/cmd/control`<br>`wh/v1/amr/+/cmd/mission`<br>`wh/v1/amr/+/cmd/vel`<br>`wh/v1/lift/+/cmd`<br>`wh/v1/shuttle/+/cmd`<br>`wh/v1/sim/control`<br>`wh/v1/traffic/reservations`<br>`wh/v1/wms/inventory/evt`<br>`wh/v1/wms/inventory/snapshot`<br>`wh/v1/wms/job` |
| `scheduler` | 적재·출고 알고리즘 (Python). 작업 생성, 레인 배정, AMR·리프트·셔틀 지시, 시간 예약 경로. | `wh/v1/amr/+/evt`<br>`wh/v1/amr/+/pose`<br>`wh/v1/amr/+/state`<br>`wh/v1/buffer/+/state`<br>`wh/v1/charger/+/state`<br>`wh/v1/ctrl/cmd`<br>`wh/v1/dock/+/evt`<br>`wh/v1/dock/+/state`<br>`wh/v1/lift/+/evt`<br>`wh/v1/lift/+/state`<br>`wh/v1/scale/+/reading`<br>`wh/v1/shuttle/+/evt`<br>`wh/v1/shuttle/+/state`<br>`wh/v1/sim/clock`<br>`wh/v1/sys/+/online`<br>`wh/v1/wms/order` |
| `rl` | AMR 주행 정책 추론 (RTX 4060 노트북). 미션 경로를 받아 속도 명령을 낸다. | `wh/v1/amr/+/cmd/control`<br>`wh/v1/amr/+/cmd/mission`<br>`wh/v1/amr/+/pose`<br>`wh/v1/amr/+/scan2d`<br>`wh/v1/sim/clock`<br>`wh/v1/traffic/reservations` |
| `control` | 중앙 관제 (NPU, Qwen + LangGraph). 상태 요약, 운영 명령, 주문 투입. | `wh/v1/amr/+/evt`<br>`wh/v1/amr/+/state`<br>`wh/v1/ctrl/evt`<br>`wh/v1/dock/+/state`<br>`wh/v1/fleet/kpi`<br>`wh/v1/sim/clock`<br>`wh/v1/sys/+/online`<br>`wh/v1/wms/inventory/evt`<br>`wh/v1/wms/inventory/snapshot`<br>`wh/v1/wms/job` |
| `sensor` | 온도 센서·카메라 (자리만 예약, 비상 대응은 현재 범위 밖). | - |
| `monitor` | 기록·대시보드 (읽기 전용). | `wh/v1/amr/+/evt`<br>`wh/v1/amr/+/pose`<br>`wh/v1/amr/+/state`<br>`wh/v1/buffer/+/state`<br>`wh/v1/charger/+/state`<br>`wh/v1/ctrl/evt`<br>`wh/v1/dock/+/evt`<br>`wh/v1/dock/+/state`<br>`wh/v1/fleet/kpi`<br>`wh/v1/lift/+/evt`<br>`wh/v1/lift/+/state`<br>`wh/v1/scale/+/reading`<br>`wh/v1/shuttle/+/evt`<br>`wh/v1/shuttle/+/state`<br>`wh/v1/sim/clock`<br>`wh/v1/sys/+/online`<br>`wh/v1/traffic/reservations`<br>`wh/v1/wms/inventory/evt`<br>`wh/v1/wms/inventory/snapshot`<br>`wh/v1/wms/job`<br>`wh/v1/wms/order` |
