"""D4 시뮬레이션(warehouse/d4/sim.py)의 메시지를 실제 MQTT 브로커로 내보내는 버스.

    from adapters.mqtt_bus import MqttBus
    sim = D4Sim(...)
    sim.bus = MqttBus(host="localhost")          # 이후 시뮬 메시지가 그대로 발행된다

코어의 EventBus 를 감싸기만 한다(comm → 코어). 예전 위치: warehouse/d4/bus.py
"""
from __future__ import annotations

import json

import paho.mqtt.client as mqtt

from warehouse.d4.bus import EventBus


class MqttBus(EventBus):
    """실제 브로커로 내보내기. 예: MqttBus(host="localhost") → 시뮬 메시지가 그대로 발행된다."""

    def __init__(self, host: str = "localhost", port: int = 1883, keep: bool = False) -> None:
        super().__init__(keep=keep)
        self.client = mqtt.Client()
        self.client.connect(host, port)

    def publish(self, t: float, topic: str, payload: dict) -> None:
        super().publish(t, topic, payload)
        self.client.publish(topic, json.dumps({"t": t, **payload}, ensure_ascii=False))
