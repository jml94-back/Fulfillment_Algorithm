"""장비·위치 ID 규칙 (D4 배치, Unity 구현 이름 기준 — 2026-11-19 팀원 명세에 맞춤).

| 대상 | 형식 | 예 |
|---|---|---|
| AMR | AMR01~AMR04 | AMR02 |
| 통로(레일) = Unity ShuttleRackSystem 1개 | R1~R3 (서→동) | R2 |
| 리프트 | L1~L3 (통로마다 1기, 남쪽 끝) | L1 |
| 단 | 1~10 (ID 안에서는 L01~L10) | |
| 모셔틀 | R{통로}-Shuttle_L{단} (Unity 오브젝트 Shuttle_L01~L10) | R1-Shuttle_L03 |
| 채널(단별 레인) | R{통로}-{W|E}{줄 01~33}-L{단 01~10}, 줄 01 = 리프트에 가장 가까운(남쪽) 줄 | R2-E07-L03 |
| 칸 (위치 문자열) | {채널}-{칸 01~14}, 01 = 통로 쪽 맨 앞, 14 = 가장 안쪽 | R2-E07-L03-14 |
| 도크 | IN1~IN6 / OUT1~OUT6 (도면 라벨 그대로) | IN2 |
| 리프트 버퍼 (= Unity 위치 문자열) | R{n}-IN / R{n}-OUT, 칸 IN1~IN5 / OUT1~OUT5 (1 = 리프트 쪽) | R2-OUT |
| 충전기 | CHW1~3 (서벽) / CHE1~3 (동벽) | CHE2 |
| 계량대 (입고 AMR 상차점) | SC-IN1~SC-IN6 (Unity 오브젝트 Scale_IN1~6) | SC-IN4 |
| 출고 하차대 (출고 AMR 하차점) | DP-OUT1~DP-OUT6 (Unity 오브젝트 DropStand_OUT1~6) | DP-OUT3 |
| 검수 구역 | QC-IN / QC-OUT | |
| AMR 쉬는 자리 | WAIT1~WAIT4 | |
| 팔레트 | Unity 랙이 만든 팔레트 R{통로}-{6자리}, 트럭(ASN)으로 들어온 팔레트 P{8자리} | R1-000123 |
"""
from __future__ import annotations

import re

N_AMR, N_RAILS, LEVELS, ROWS, DEPTH, N_DOCKS, BUF_SLOTS = 4, 3, 10, 33, 14, 6, 5

AMRS = [f"AMR{i:02d}" for i in range(1, N_AMR + 1)]
LIFTS = [f"L{r}" for r in range(1, N_RAILS + 1)]
RAILS = [f"R{r}" for r in range(1, N_RAILS + 1)]
SHUTTLES = [f"R{r}-Shuttle_L{lv:02d}" for r in range(1, N_RAILS + 1) for lv in range(1, LEVELS + 1)]
IN_DOCKS = [f"IN{i}" for i in range(1, N_DOCKS + 1)]
OUT_DOCKS = [f"OUT{i}" for i in range(1, N_DOCKS + 1)]
DOCKS = IN_DOCKS + OUT_DOCKS
BUFFERS = [f"R{r}-{d}" for r in range(1, N_RAILS + 1) for d in ("IN", "OUT")]
CHARGERS = [f"CHW{i}" for i in range(1, 4)] + [f"CHE{i}" for i in range(1, 4)]
SCALES = [f"SC-IN{i}" for i in range(1, N_DOCKS + 1)]
DROPS = [f"DP-OUT{i}" for i in range(1, N_DOCKS + 1)]
WAITS = [f"WAIT{i}" for i in range(1, 5)]

_ROW = r"(0[1-9]|[12][0-9]|3[0-3])"
_LV = r"(0[1-9]|10)"
CHANNEL_RE = re.compile(rf"^R([1-3])-([WE]){_ROW}-L{_LV}$")
SLOT_RE = re.compile(rf"^R([1-3])-([WE]){_ROW}-L{_LV}-(0[1-9]|1[0-4])$")
SHUTTLE_RE = re.compile(rf"^R([1-3])-Shuttle_L{_LV}$")
PALLET_RE = re.compile(r"^(P\d{8}|R[1-3]-\d{6})$")
LANE_RE = CHANNEL_RE          # 예전 이름

# 스키마에 그대로 들어가는 정규식 (JSON Schema "pattern")
PATTERNS = {
    "amr": r"^AMR0[1-9]$",
    "lift": r"^L[1-3]$",
    "rail": r"^R[1-3]$",
    "shuttle": SHUTTLE_RE.pattern,
    "channel": CHANNEL_RE.pattern,
    "slot": SLOT_RE.pattern,
    "dock": r"^(IN|OUT)[1-6]$",
    "buffer": r"^R[1-3]-(IN|OUT)$",
    "charger": r"^CH[WE][1-3]$",
    "scale": r"^SC-IN[1-6]$",
    "drop": r"^DP-OUT[1-6]$",
    "stop": r"^(SC-IN[1-6]|DP-OUT[1-6]|R[1-3]-(IN|OUT)|CH[WE][1-3]|QC-(IN|OUT)|WAIT[1-4])$",
    "pallet": PALLET_RE.pattern,
}
PATTERNS["lane"] = PATTERNS["channel"]


def channel_id(rail: int, side: str, row: int, level: int) -> str:
    """Unity ShuttleRackSystem 채널 이름. 예: channel_id(2, "E", 7, 3) = "R2-E07-L03"."""
    return f"R{rail}-{side}{row:02d}-L{level:02d}"


def parse_channel(ch: str) -> tuple[int, str, int, int]:
    """채널 → (통로, 면, 줄, 단)."""
    m = CHANNEL_RE.match(ch)
    if not m:
        raise ValueError(f"잘못된 채널 ID: {ch}")
    return int(m[1]), m[2], int(m[3]), int(m[4])


def slot_id(ch: str, slot: int) -> str:
    return f"{ch}-{slot:02d}"


def shuttle_id(rail: int, level: int) -> str:
    return f"R{rail}-Shuttle_L{level:02d}"


def shuttle_for_channel(ch: str) -> str:
    r, _, _, lv = parse_channel(ch)
    return shuttle_id(r, lv)


def rail_of(ch_or_buffer: str) -> str:
    return ch_or_buffer.split("-")[0]


def lift_for_rail(rail: str) -> str:
    return "L" + rail[1:]


def pallet_id(n: int) -> str:
    """트럭(ASN)으로 들어오는 팔레트 ID."""
    return f"P{n:08d}"


def rack_pallet_id(rail: int, n: int) -> str:
    """Unity ShuttleRackSystem 이 만드는 팔레트 ID 형식 R{통로}-{6자리}."""
    return f"R{rail}-{n:06d}"
