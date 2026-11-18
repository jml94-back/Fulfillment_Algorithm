"""메시지 봉투. 모든 토픽의 payload 는 UTF-8 JSON 한 개:

{
  "v": 1,                      # 스키마 주 버전 (토픽의 v1 과 같음)
  "id": "6f1c2a9e0b7d4c11",    # 메시지 고유 id (16 hex). QoS 1 중복 수신 제거, 명령 응답의 corr 에 쓴다
  "ts": 1763461893.125,        # 보낸 쪽 벽시계 (Unix 초)
  "t": 1234.5,                 # 시뮬레이션 시각(초). sim/clock 기준. 실제 현장에서는 ts 와 같은 값
  "src": "scheduler",          # 보낸 클라이언트 id
  "type": "amr.cmd.mission",   # topics.TOPICS 의 key = 스키마 이름
  "corr": null,                # 응답이면 원래 명령의 id
  "data": { ... }              # type 별 본문 (schemas.py)
}
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

VERSION = 1


def new_id() -> str:
    return os.urandom(8).hex()


@dataclass
class Envelope:
    type: str
    data: dict[str, Any]
    src: str
    t: float | None = None
    corr: str | None = None
    id: str = field(default_factory=new_id)
    ts: float = field(default_factory=lambda: round(time.time(), 3))
    v: int = VERSION

    def to_json(self) -> str:
        d = {"v": self.v, "id": self.id, "ts": self.ts, "t": self.t, "src": self.src, "type": self.type,
             "corr": self.corr, "data": self.data}
        return json.dumps(d, ensure_ascii=False, separators=(",", ":"))

    def to_dict(self) -> dict:
        return json.loads(self.to_json())


def make(type_: str, data: dict, src: str, t: float | None = None, corr: str | None = None) -> Envelope:
    return Envelope(type=type_, data=data, src=src, t=None if t is None else round(t, 3), corr=corr)


def parse(payload: bytes | str) -> Envelope:
    d = json.loads(payload)
    if d.get("v") != VERSION:
        raise ValueError(f"지원하지 않는 버전: {d.get('v')}")
    return Envelope(type=d["type"], data=d["data"], src=d["src"], t=d.get("t"), corr=d.get("corr"), id=d["id"],
                    ts=d["ts"], v=d["v"])
