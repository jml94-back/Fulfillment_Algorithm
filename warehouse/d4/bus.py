"""장비 간 통신: MQTT 토픽 규칙과 메시지 기록기.

시뮬레이션은 실제 브로커 없이 돌고, 모든 메시지를 시각 순으로 기록한다(out/d4_events.json).
같은 토픽·페이로드를 실제 MQTT 브로커로 내보내려면 MqttBus 를 쓴다(paho-mqtt 필요, 선택).

토픽 (wh = warehouse)
- wh/amr/{id}/task      AMR 작업 배정: 종류, 팔레트, 집는 곳, 놓는 곳
- wh/amr/{id}/path      계획된 경로: [[t, x, z], ...] (방향이 바뀌는 점만)
- wh/amr/{id}/event     pick / drop / charge_start / charge_end
- wh/lift/{rail}/cycle  리프트 한 사이클: 올리는 팔레트·층, 내리는 팔레트·층, 시작·끝 시각
- wh/shuttle/{rail}-{level}/task   셔틀 작업: store / retrieve / consolidate
- wh/lane/{idx}         레인 재고 변화: sku, count
- wh/dock/{in|out}/{idx}/truck     트럭 도착·출발
- wh/pallet/{id}/inspect           계량 결과 (신고 무게, 측정 무게, 통과 여부)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class EventBus:
    keep: bool = True
    messages: list[dict] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def publish(self, t: float, topic: str, payload: dict) -> None:
        kind = topic.split("/")[1]
        self.counts[kind] = self.counts.get(kind, 0) + 1
        if self.keep:
            self.messages.append({"t": round(t, 1), "topic": topic, "payload": payload})

    def dump(self, path, until: float | None = None) -> int:
        msgs = [m for m in self.messages if until is None or m["t"] <= until]
        with open(path, "w") as f:
            json.dump({"format": "mqtt-log/1", "time_unit": "s", "messages": msgs}, f, ensure_ascii=False)
        return len(msgs)


class MqttBus(EventBus):
    """실제 브로커로 내보내기 (선택). 예: MqttBus(host="localhost") → 시뮬 메시지가 그대로 발행된다."""

    def __init__(self, host: str = "localhost", port: int = 1883, keep: bool = False) -> None:
        super().__init__(keep=keep)
        import paho.mqtt.client as mqtt   # 선택 의존성
        self.client = mqtt.Client()
        self.client.connect(host, port)

    def publish(self, t: float, topic: str, payload: dict) -> None:
        super().publish(t, topic, payload)
        self.client.publish(topic, json.dumps({"t": t, **payload}, ensure_ascii=False))
