"""장비·위치 ID 규칙 (D4 배치 기준).

| 대상 | 형식 | 예 |
|---|---|---|
| AMR | AMR01~AMR04 | AMR02 |
| 리프트 | L1~L3 (레일마다 1기) | L1 |
| 레일 | R1~R3 (서→동) | R2 |
| 층 | LV01~LV10 (보관층), 0 = 바닥 P&D | LV03 |
| 모셔틀 / 세틀라이트 | MS-R{r}-LV{nn} / SAT-R{r}-LV{nn} | MS-R1-LV03 |
| 레인 | R{r}-{W|E}-LV{nn}-N{row:02} (row 01 = 남쪽) | R1-W-LV03-N12 |
| 레인 안 위치 | 1 = 레일 쪽 입구 ~ 14 = 벽 쪽 끝 | |
| 도크 | IN1~IN6 / OUT1~OUT6 (서→동 아님, 도면 라벨 그대로) | IN2 |
| 리프트 버퍼 | L{n}-IN / L{n}-OUT, 칸 L1-IN-1~5 (1 = 리프트 바로 옆, 5 = AMR 접점) | L2-OUT |
| 충전기 | CHW1~3 (서벽) / CHE1~3 (동벽) | CHE2 |
| 계량대 (입고 AMR 상차점) | SC-IN1~SC-IN6 | SC-IN4 |
| 출고 하차대 (출고 AMR 하차점) | DP-OUT1~DP-OUT6 | DP-OUT3 |
| 검수 구역 | QC-IN / QC-OUT | |
| 팔레트 | P + 8자리 숫자 | P00012345 |
"""
from __future__ import annotations

import re

N_AMR, N_RAILS, LEVELS, ROWS, DEPTH, N_DOCKS, BUF_SLOTS = 4, 3, 10, 33, 14, 6, 5

AMRS = [f"AMR{i:02d}" for i in range(1, N_AMR + 1)]
LIFTS = [f"L{r}" for r in range(1, N_RAILS + 1)]
RAILS = [f"R{r}" for r in range(1, N_RAILS + 1)]
SHUTTLES = [f"MS-R{r}-LV{lv:02d}" for r in range(1, N_RAILS + 1) for lv in range(1, LEVELS + 1)]
IN_DOCKS = [f"IN{i}" for i in range(1, N_DOCKS + 1)]
OUT_DOCKS = [f"OUT{i}" for i in range(1, N_DOCKS + 1)]
DOCKS = IN_DOCKS + OUT_DOCKS
BUFFERS = [f"L{r}-{d}" for r in range(1, N_RAILS + 1) for d in ("IN", "OUT")]
CHARGERS = [f"CHW{i}" for i in range(1, 4)] + [f"CHE{i}" for i in range(1, 4)]
SCALES = [f"SC-IN{i}" for i in range(1, N_DOCKS + 1)]
DROPS = [f"DP-OUT{i}" for i in range(1, N_DOCKS + 1)]

LANE_RE = re.compile(r"^R([1-3])-([WE])-LV(0[1-9]|10)-N(0[1-9]|[12][0-9]|3[0-3])$")
SHUTTLE_RE = re.compile(r"^MS-R([1-3])-LV(0[1-9]|10)$")
PALLET_RE = re.compile(r"^P\d{8}$")

# 스키마에 그대로 들어가는 정규식 (JSON Schema "pattern")
PATTERNS = {
    "amr": r"^AMR0[1-9]$",
    "lift": r"^L[1-3]$",
    "shuttle": SHUTTLE_RE.pattern,
    "lane": LANE_RE.pattern,
    "dock": r"^(IN|OUT)[1-6]$",
    "buffer": r"^L[1-3]-(IN|OUT)$",
    "charger": r"^CH[WE][1-3]$",
    "scale": r"^SC-IN[1-6]$",
    "drop": r"^DP-OUT[1-6]$",
    "point": r"^(IN[1-6]|DP-OUT[1-6]|L[1-3]-(IN|OUT)|CH[WE][1-3])$",
    "pallet": PALLET_RE.pattern,
}


def lane_id(rail: int, side: str, level: int, row: int) -> str:
    return f"R{rail}-{side}-LV{level:02d}-N{row:02d}"


def parse_lane(lane: str) -> tuple[int, str, int, int]:
    m = LANE_RE.match(lane)
    if not m:
        raise ValueError(f"잘못된 레인 ID: {lane}")
    return int(m[1]), m[2], int(m[3]), int(m[4])


def shuttle_id(rail: int, level: int) -> str:
    return f"MS-R{rail}-LV{level:02d}"


def shuttle_for_lane(lane: str) -> str:
    r, _, lv, _ = parse_lane(lane)
    return shuttle_id(r, lv)


def lift_for_lane(lane: str) -> str:
    return f"L{parse_lane(lane)[0]}"


def pallet_id(n: int) -> str:
    return f"P{n:08d}"
