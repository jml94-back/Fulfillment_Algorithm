"""토픽 레지스트리: 토픽 패턴, 발행자·구독자, QoS, retain, 주기, 메시지 유효 시간.

모든 토픽은 ROOT(`wh/v1`) 아래에 있다. `{amr}` 같은 자리 표시자는 ids.py 의 ID 형식을 따른다.
메시지 본문은 항상 envelope.py 의 봉투(JSON)이고, 봉투의 `type` 이 아래 key 와 같다.

이 파일이 원본이다. README의 토픽 표와 spec/topics.json 은 `python -m whmqtt.export` 로 여기서 만든다.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

ROOT = "wh/v1"

# 역할(클라이언트) 이름: envelope 의 src 앞부분과 브로커 ACL 사용자명으로 쓴다.
ROLES = {
    "unity": "Unity 시뮬레이션 (Mac mini M4). 장비 물리·센서의 원본. 실제 현장에서는 각 장비 컨트롤러가 이 자리를 맡는다.",
    "scheduler": "적재·출고 알고리즘 (Python). 작업 생성, 레인 배정, AMR·리프트·셔틀 지시, 시간 예약 경로.",
    "rl": "AMR 주행 정책 추론 (RTX 4060 노트북). 미션 경로를 받아 속도 명령을 낸다.",
    "control": "중앙 관제 (NPU, Qwen + LangGraph). 상태 요약, 운영 명령, 주문 투입.",
    "sensor": "온도 센서·카메라 (자리만 예약, 비상 대응은 현재 범위 밖).",
    "monitor": "기록·대시보드 (읽기 전용).",
}


@dataclass(frozen=True)
class Topic:
    key: str                 # 메시지 type (= 스키마 이름)
    pattern: str             # ROOT 뒤 경로, {자리표시자}
    pub: tuple[str, ...]
    sub: tuple[str, ...]
    qos: int
    retain: bool
    rate: str                # 발행 주기
    desc: str
    expiry_s: int | None = None      # MQTT v5 Message Expiry Interval (브로커가 늦은 메시지를 버림)
    reserved: bool = False           # 자리만 예약 (현재 범위 밖)
    params: tuple[str, ...] = field(default=())

    @property
    def full(self) -> str:
        return f"{ROOT}/{self.pattern}"

    def wildcard(self) -> str:
        """구독용: 자리 표시자를 + 로 바꾼 토픽."""
        return re.sub(r"\{[^}]+\}", "+", self.full)


def _t(key, pattern, pub, sub, qos, retain, rate, desc, expiry_s=None, reserved=False) -> Topic:
    params = tuple(re.findall(r"\{([^}]+)\}", pattern))
    return Topic(key, pattern, tuple(pub.split()), tuple(sub.split()), qos, retain, rate, desc, expiry_s, reserved,
                 params)


_LIST = [
    # ── 시스템 ──────────────────────────────────────────────────────────────
    _t("sys.online", "sys/{client}/online", "*", "control monitor scheduler", 1, True, "접속·끊김 시 (LWT)",
       "클라이언트 접속 상태. 끊기면 브로커가 Last Will 로 online=false 를 대신 발행한다."),
    _t("sim.clock", "sim/clock", "unity", "scheduler rl control monitor", 0, True, "1 Hz + 변경 시",
       "시뮬레이션 시각·배속. 모든 봉투의 t 는 이 시각 기준. 실제 현장에서는 발행하지 않고 t = 벽시계."),
    _t("sim.control", "sim/control", "scheduler control", "unity", 1, False, "명령 시",
       "시뮬레이션 시작·정지·재개·초기화·배속 변경."),

    # ── AMR ────────────────────────────────────────────────────────────────
    _t("amr.pose", "amr/{amr}/pose", "unity", "rl scheduler monitor", 0, False, "10 Hz",
       "위치·속도·배터리·적재 무게. 자주 오고 하나쯤 잃어도 다음 값으로 덮이므로 QoS 0.", expiry_s=1),
    _t("amr.scan2d", "amr/{amr}/scan2d", "unity", "rl", 0, False, "10 Hz",
       "2D LiDAR 72빔(5° 간격, 0번 = 정면, 반시계), 최대 10 m, 단위 m. 3D LiDAR는 MQTT로 보내지 않는다.", expiry_s=1),
    _t("amr.state", "amr/{amr}/state", "unity", "scheduler control monitor", 1, True, "변경 시 + 5초마다",
       "운행 모드, 배터리, 실은 팔레트, 진행 중 미션·단계. retain 이라 새로 붙은 클라이언트도 바로 현재 상태를 안다."),
    _t("amr.evt", "amr/{amr}/evt", "unity", "scheduler control monitor", 1, False, "사건 시",
       "미션 수락·거부, 단계 시작·완료, 미션 완료·실패, 배터리 부족. 명령에 대한 응답은 corr 에 명령 id."),
    _t("amr.cmd.mission", "amr/{amr}/cmd/mission", "scheduler", "unity rl", 1, False, "작업 배정 시",
       "이동·적재·하역·충전 단계 목록. 각 이동 단계에 경유 노드와 시간 예약 창(t_start~t_end).", expiry_s=60),
    _t("amr.cmd.vel", "amr/{amr}/cmd/vel", "rl", "unity", 0, False, "10 Hz",
       "RL 정책의 속도 명령 v(m/s) w(rad/s). 0.5초 안에 새 명령이 없으면 Unity가 정지시킨다.", expiry_s=1),
    _t("amr.cmd.control", "amr/{amr}/cmd/control", "scheduler control", "unity rl", 1, False, "명령 시",
       "일시정지·재개·미션 취소·주행 모드(scripted/rl) 변경·충전 보내기.", expiry_s=30),

    # ── 리프트 · 버퍼 · 셔틀 ────────────────────────────────────────────────
    _t("lift.state", "lift/{lift}/state", "unity", "scheduler monitor", 1, True, "변경 시",
       "리프트 높이(층), 모드, 실은 팔레트, 진행 중 명령."),
    _t("lift.cmd", "lift/{lift}/cmd", "scheduler", "unity", 1, False, "작업 시",
       "lift_in: 입고 버퍼 1번 칸 → n층 셔틀 이송점. lift_out: n층 셔틀 이송점 → 출고 버퍼 1번 칸.", expiry_s=60),
    _t("lift.evt", "lift/{lift}/evt", "unity", "scheduler monitor", 1, False, "사건 시",
       "명령 수락·거부·시작·완료·실패."),
    _t("buffer.state", "buffer/{buffer}/state", "unity", "scheduler monitor", 1, True, "변경 시",
       "리프트 입·출고 컨베이어 5칸의 팔레트 (0번 원소 = 1번 칸 = 리프트 바로 옆)."),
    _t("shuttle.state", "shuttle/{shuttle}/state", "unity", "scheduler monitor", 1, True, "변경 시 (이동 중 2 Hz)",
       "모셔틀 레일 위치와 세틀라이트 상태(모셔틀에 탑재 / 레인 안). 셔틀 세트 1개 = 토픽 1개."),
    _t("shuttle.cmd", "shuttle/{shuttle}/cmd", "scheduler", "unity", 1, False, "작업 시",
       "store: 이송점의 팔레트를 레인 pos 에 넣기. retrieve: 레인 pos 의 팔레트를 이송점으로. relocate: 레인 간 이동.",
       expiry_s=60),
    _t("shuttle.evt", "shuttle/{shuttle}/evt", "unity", "scheduler monitor", 1, False, "사건 시",
       "명령 수락·거부·시작·완료·실패."),

    # ── 도크 · 계량 · 충전 ─────────────────────────────────────────────────
    _t("dock.state", "dock/{dock}/state", "unity", "scheduler control monitor", 1, True, "변경 시",
       "트럭 유무, 하차·상차 진행 수."),
    _t("dock.evt", "dock/{dock}/evt", "unity", "scheduler monitor", 1, False, "사건 시",
       "트럭 도착·출발, 팔레트 하차(계량대에 놓임)·상차(트럭에 실림)."),
    _t("scale.reading", "scale/{scale}/reading", "unity", "scheduler monitor", 1, False, "팔레트마다",
       "검수 계량값. 송장 무게와 ±5% 넘게 다르면 스케줄러가 보류(QC-IN)로 보낸다."),
    _t("charger.state", "charger/{charger}/state", "unity", "scheduler monitor", 1, True, "변경 시",
       "충전기 점유 AMR, 충전 중 여부."),

    # ── 적재·출고 알고리즘 (WMS/스케줄러) ─────────────────────────────────
    _t("wms.order", "wms/order", "control", "scheduler monitor", 1, False, "주문 시",
       "입고 예정(ASN, 팔레트·송장 무게) 또는 출고 주문(SKU·수량). 관제나 테스트 스크립트가 넣는다."),
    _t("wms.job", "wms/job", "scheduler", "control monitor unity", 1, False, "작업 상태 변경 시",
       "팔레트 1개 단위 작업의 계획·진행 상태(레인·리프트·셔틀·AMR 배정, 예상 완료)."),
    _t("wms.inventory", "wms/inventory/evt", "scheduler", "control monitor unity", 1, False, "재고 변경 시",
       "입고 접수·보류·해제·적재·출고 인출·선적·조정. event_id 로 중복 수신을 걸러낸다."),
    _t("wms.snapshot", "wms/inventory/snapshot", "scheduler", "control monitor unity", 1, True, "60초마다 + 시작 시",
       "레인별 SKU·수량 전체 목록. 새로 붙은 Unity·관제가 재고를 한 번에 맞춘다."),
    _t("traffic.reservations", "traffic/reservations", "scheduler", "rl unity monitor", 1, True, "변경 시",
       "AMR별 경유 노드 시간 예약표(t_start~t_end). 통로 방향을 고정하지 않고 시간으로 나눠 쓴다."),
    _t("fleet.kpi", "fleet/kpi", "scheduler", "control monitor", 0, True, "10초마다",
       "처리량, AMR 가동률, 대기 작업 수."),

    # ── 중앙 관제 (LangGraph) ──────────────────────────────────────────────
    _t("ctrl.cmd", "ctrl/cmd", "control", "scheduler", 1, False, "명령 시",
       "전체 일시정지·재개, 정책 변경, 보고 요청, 보류 팔레트 해제.", expiry_s=60),
    _t("ctrl.evt", "ctrl/evt", "scheduler", "control monitor", 1, False, "응답·경고 시",
       "관제 명령 응답, 상태 보고, 경고(배터리, 작업 지연)."),

    # ── 예약 (현재 범위 밖) ────────────────────────────────────────────────
    _t("sensor.temp", "sensor/temp/{sensor}", "sensor", "control monitor", 0, True, "10초마다",
       "온도 센서 값.", reserved=True),
    _t("sensor.cam", "sensor/cam/{camera}/detect", "sensor", "control monitor", 1, False, "감지 시",
       "카메라 감지 결과(연기·누액·사람 등).", reserved=True),
    _t("alert", "alert/{kind}", "control", "scheduler unity rl monitor", 1, True, "발생·해제 시",
       "비상 구역 발령·해제 (화재, 랙 붕괴·기울어짐, 누액, 부상). 해제는 빈 payload 로 retain 삭제.",
       reserved=True),
]

TOPICS: dict[str, Topic] = {t.key: t for t in _LIST}


class _Builder:
    """T.amr_pose("AMR01") → "wh/v1/amr/AMR01/pose" 처럼 토픽 문자열을 만든다."""

    def __getattr__(self, name: str):
        key = name.replace("_", ".")
        if key not in TOPICS:
            raise AttributeError(name)
        t = TOPICS[key]

        def build(*args: str) -> str:
            if len(args) != len(t.params):
                raise TypeError(f"{key} 는 인자 {t.params} 가 필요합니다")
            out = t.full
            for p, a in zip(t.params, args):
                out = out.replace("{" + p + "}", a)
            return out

        return build


T = _Builder()


def topic(key: str, *args: str) -> str:
    return getattr(T, key.replace(".", "_"))(*args)


_REGEX = [(t, re.compile("^" + re.sub(r"\\\{([^}]+)\\\}", r"(?P<\1>[^/]+)", re.escape(t.full)) + "$")) for t in _LIST]


def match(topic_str: str) -> tuple[Topic, dict[str, str]] | None:
    """실제 토픽 문자열 → (Topic, 자리표시자 값). 모르는 토픽이면 None."""
    for t, rx in _REGEX:
        m = rx.match(topic_str)
        if m:
            return t, m.groupdict()
    return None


# 역할별 권장 구독 (와일드카드)
SUBSCRIPTIONS: dict[str, list[str]] = {}
for _role in ROLES:
    SUBSCRIPTIONS[_role] = sorted({t.wildcard() for t in _LIST if _role in t.sub and not t.reserved})


def as_dicts() -> list[dict]:
    out = []
    for t in _LIST:
        d = asdict(t)
        d["topic"] = t.full
        out.append(d)
    return out
