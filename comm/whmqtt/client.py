"""paho-mqtt 2.x 래퍼.

    from whmqtt.client import WhClient
    c = WhClient("scheduler", role="scheduler", host="192.168.0.10")
    c.on("amr.evt", lambda env, p: print(p["amr"], env.data["event"]))   # p = 토픽 자리표시자 값
    c.start()
    c.send("amr.cmd.mission", {"mission_id": "M1", ...}, "AMR01")

하는 일
- MQTT v5 접속, Last Will(sys/{id}/online = false, retain), 접속 시 online = true.
- 토픽 레지스트리에서 QoS·retain·만료 시간을 자동으로 가져와 발행 → 쓰는 쪽은 key 와 본문만 넘긴다.
- QoS 1 중복 수신(같은 봉투 id)을 걸러낸다.
- sim/clock 을 듣고 봉투 t 에 시뮬레이션 시각을 채운다.
- validate=True 면 보내고 받는 모든 메시지를 JSON Schema 로 검사한다(개발·테스트용).
- 끊기면 paho 가 자동 재접속(1~30초), 재접속 시 구독을 다시 건다.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

from . import schemas
from .envelope import Envelope, make, parse
from .topics import TOPICS, match, topic

log = logging.getLogger("whmqtt")
Handler = Callable[[Envelope, dict[str, str]], Any]


class WhClient:
    def __init__(self, client_id: str, role: str, host: str = "localhost", port: int = 1883,
                 username: str | None = None, password: str | None = None, *, validate: bool = False,
                 persistent: bool = False, session_expiry_s: int = 3600, keepalive: int = 15) -> None:
        self.id, self.role, self.host, self.port = client_id, role, host, port
        self.validate, self.keepalive = validate, keepalive
        self._clock: tuple[float, float, float, bool] | None = None
        self.sim_scale: float = 1.0
        self._handlers: dict[str, list[Handler]] = {}
        self._raw: list[tuple[str, Callable[[str, bytes], Any]]] = []
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()
        self.connected = threading.Event()
        self.errors: list[str] = []
        self.persistent, self.session_expiry_s = persistent, session_expiry_s

        self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, protocol=mqtt.MQTTv5)
        if username:
            self.mqtt.username_pw_set(username, password)
        will = make("sys.online", {"online": False, "role": role, "version": "1.0.0"}, client_id)
        self.mqtt.will_set(topic("sys.online", client_id), will.to_json(), qos=1, retain=True)
        self.mqtt.reconnect_delay_set(1, 30)
        self.mqtt.on_connect = self._on_connect
        self.mqtt.on_disconnect = self._on_disconnect
        self.mqtt.on_message = self._on_message
        self.on("sim.clock", self._on_clock)

    # ── 연결 ────────────────────────────────────────────────────────────────
    def start(self, wait: float = 10.0) -> "WhClient":
        props = Properties(PacketTypes.CONNECT)
        if self.persistent:   # 끊겨 있던 동안의 QoS 1 메시지를 브로커가 보관 (스케줄러·관제용)
            props.SessionExpiryInterval = self.session_expiry_s
        self.mqtt.connect(self.host, self.port, keepalive=self.keepalive, clean_start=not self.persistent,
                          properties=props)
        self.mqtt.loop_start()
        if wait and not self.connected.wait(wait):
            raise TimeoutError(f"브로커 {self.host}:{self.port} 접속 실패")
        return self

    def stop(self) -> None:
        if self.connected.is_set():
            self._publish_raw(topic("sys.online", self.id),
                              make("sys.online", {"online": False, "role": self.role, "version": "1.0.0"},
                                   self.id).to_json(), 1, True).wait_for_publish(2)
        self.mqtt.disconnect()
        self.mqtt.loop_stop()

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            log.error("접속 거부: %s", reason_code)
            return
        subs = sorted({TOPICS[k].wildcard() for k in self._handlers} | {p for p, _ in self._raw})
        for s in subs:
            client.subscribe(s, qos=1)
        self.send("sys.online", {"online": True, "role": self.role, "version": "1.0.0"}, self.id)
        self.connected.set()
        log.info("%s 접속 (%s:%s), 구독 %d개", self.id, self.host, self.port, len(subs))

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self.connected.clear()
        if reason_code != 0:
            log.warning("%s 연결 끊김: %s (자동 재접속)", self.id, reason_code)

    # ── 구독 ────────────────────────────────────────────────────────────────
    def on(self, key: str, handler: Handler) -> None:
        """key 토픽(모든 자리표시자)에 handler(env, params) 등록."""
        if key not in TOPICS:
            raise KeyError(key)
        first = key not in self._handlers
        self._handlers.setdefault(key, []).append(handler)
        if first and self.connected.is_set():
            self.mqtt.subscribe(TOPICS[key].wildcard(), qos=1)

    def on_raw(self, pattern: str, handler: Callable[[str, bytes], Any]) -> None:
        """와일드카드 토픽을 날것(payload bytes)으로 받기 (기록용)."""
        self._raw.append((pattern, handler))
        if self.connected.is_set():
            self.mqtt.subscribe(pattern, qos=1)

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage) -> None:
        try:
            for pattern, h in self._raw:
                if mqtt.topic_matches_sub(pattern, msg.topic):
                    h(msg.topic, msg.payload)
            if not msg.payload:          # retain 삭제용 빈 메시지
                return
            m = match(msg.topic)
            if m is None:
                return
            t, params = m
            if t.key not in self._handlers:
                return
            env = parse(msg.payload)
            if self.validate:
                schemas.validate(env.to_dict())
                if env.type != t.key:
                    raise ValueError(f"토픽 {msg.topic} 에 type {env.type}")
            if msg.qos > 0:
                with self._lock:
                    if env.id in self._seen:
                        return
                    self._seen[env.id] = None
                    if len(self._seen) > 5000:
                        self._seen.popitem(last=False)
            for h in self._handlers[t.key]:
                h(env, params)
        except Exception as e:   # 핸들러 예외로 네트워크 스레드가 죽지 않게
            self.errors.append(f"{msg.topic}: {e!r}")
            log.exception("메시지 처리 실패: %s", msg.topic)

    def _on_clock(self, env: Envelope, _p) -> None:
        self._clock = (env.data["t"], time.time(), env.data["scale"], env.data["paused"])
        self.sim_scale = env.data["scale"]

    # ── 발행 ────────────────────────────────────────────────────────────────
    @property
    def sim_t(self) -> float | None:
        """마지막 sim/clock 에 벽시계 경과 x 배속을 더한 현재 시뮬레이션 시각."""
        if self._clock is None:
            return None
        t, wall, scale, paused = self._clock
        return round(t if paused else t + (time.time() - wall) * scale, 3)

    def now(self) -> float | None:
        return self.sim_t

    def send(self, key: str, data: dict, *args: str, corr: str | None = None, t: float | None = None,
             retain: bool | None = None) -> Envelope:
        """레지스트리 설정(QoS·retain·만료)대로 key 메시지를 발행하고 봉투를 돌려준다."""
        spec = TOPICS[key]
        env = make(key, data, self.id, t=self.sim_t if t is None else t, corr=corr)
        d = env.to_dict()
        if self.validate:
            schemas.validate(d)
        self._publish_raw(topic(key, *args), env.to_json(), spec.qos, spec.retain if retain is None else retain,
                          spec.expiry_s)
        return env

    def clear_retained(self, key: str, *args: str) -> None:
        self._publish_raw(topic(key, *args), b"", 1, True)

    def _publish_raw(self, topic_str: str, payload, qos: int, retain: bool, expiry_s: int | None = None):
        props = None
        if expiry_s:
            props = Properties(PacketTypes.PUBLISH)
            props.MessageExpiryInterval = expiry_s
        props_ct = props or Properties(PacketTypes.PUBLISH)
        props_ct.ContentType = "application/json"
        return self.mqtt.publish(topic_str, payload, qos=qos, retain=retain, properties=props_ct)


def wait_until(pred: Callable[[], bool], timeout: float, step: float = 0.05) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()
