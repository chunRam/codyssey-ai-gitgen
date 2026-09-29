"""safe-mode: AI 에 보내기 전에 diff 를 안전하게 다듬는 모듈.

(A) 마스킹: API Key·토큰·비밀번호·이메일·전화번호 등 민감정보 패턴을 [MASKED:종류] 로 가린다.
(B) 전송량 제한: diff 를 최대 MAX_FILES 개 파일, MAX_LINES 줄까지만 남긴다.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field, replace

from git_utils import GitChanges

MAX_FILES = 10
MAX_LINES = 200

# (종류 이름, 정규표현식) — 구체적인 패턴을 먼저 검사해야 일반 패턴에 먼저 걸리지 않는다.
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("PRIVATE_KEY", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
    )),
    ("GOOGLE_API_KEY", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b|\bAQ\.[0-9A-Za-z_\-]{20,}")),
    ("OPENAI_API_KEY", re.compile(r"\bsk-[0-9A-Za-z_\-]{20,}")),
    ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}\b|\bgithub_pat_[0-9A-Za-z_]{20,}")),
    ("AWS_ACCESS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("SLACK_TOKEN", re.compile(r"\bxox[abprs]-[0-9A-Za-z\-]{10,}")),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("RRN", re.compile(r"\b\d{6}-[1-4]\d{6}\b")),  # 주민등록번호
    ("PHONE", re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b")),
]

# "비밀스러운 이름 = 값" 형태. 이름은 남기고 값만 가린다.
SECRET_NAME = r"[\w\-]*(?:api[_\-]?key|secret|token|password|passwd|pwd)"
ASSIGNMENT_PATTERNS: list[re.Pattern[str]] = [
    # 코드: password = "abc123", "apiKey": 'xyz...'  (따옴표로 감싼 값)
    re.compile(rf"(?i)\b({SECRET_NAME}[\w\-]*[\"']?)(\s*[:=]\s*)([\"'])([^\"'\s]{{4,}})\3"),
    # .env: API_KEY=abcd1234  (따옴표 없음, 8자 이상 — max_tokens = 2048 같은 평범한 코드는 제외)
    re.compile(rf"(?im)^([+\- ]?\s*(?:export\s+)?[A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD|PWD))(\s*=\s*)()([^\s\"']{{8,}})"),
]


@dataclass
class SafetyReport:
    masked: Counter[str] = field(default_factory=Counter)  # 종류별 마스킹 건수
    omitted_files: int = 0                                 # 제한으로 빠진 파일 수
    omitted_lines: int = 0                                 # 제한으로 빠진 줄 수

    @property
    def masked_total(self) -> int:
        return sum(self.masked.values())


def mask_secrets(text: str) -> tuple[str, Counter[str]]:
    """민감정보를 [MASKED:종류] 로 바꾼 텍스트와 종류별 건수를 돌려준다."""
    counts: Counter[str] = Counter()
    for name, pattern in SECRET_PATTERNS:
        text, n = pattern.subn(f"[MASKED:{name}]", text)
        if n:
            counts[name] += n

    def mask_value(match: re.Match[str]) -> str:
        key, sep, quote, value = match.groups()
        if value.startswith("[MASKED:"):  # 위에서 이미 가린 값
            return match.group(0)
        counts["SECRET_VALUE"] += 1
        return f"{key}{sep}{quote}[MASKED:SECRET_VALUE]{quote}"

    for pattern in ASSIGNMENT_PATTERNS:
        text = pattern.sub(mask_value, text)
    return text, counts


def split_diff_by_file(diff: str) -> list[str]:
    """diff 전체를 파일 단위 덩어리로 나눈다. 파일마다 'diff --git' 줄로 시작한다."""
    chunks = re.split(r"(?m)^(?=diff --git )", diff)
    return [chunk for chunk in chunks if chunk.strip()]


def limit_diff(diff: str, max_files: int, max_lines: int) -> tuple[str, int, int]:
    """diff 를 최대 파일 수·줄 수까지만 남긴다. (잘린 diff, 빠진 파일 수, 빠진 줄 수)"""
    chunks = split_diff_by_file(diff)
    kept_chunks = chunks[:max_files]
    omitted_files = len(chunks) - len(kept_chunks)

    lines = "".join(kept_chunks).splitlines()
    omitted_lines = sum(len(chunk.splitlines()) for chunk in chunks[max_files:])
    if len(lines) > max_lines:
        omitted_lines += len(lines) - max_lines
        lines = lines[:max_lines]

    text = "\n".join(lines)
    if omitted_files or omitted_lines:
        text += (
            f"\n... [safe-mode] 파일 {omitted_files}개, {omitted_lines}줄 생략 "
            f"(최대 {max_files}개 파일 / {max_lines}줄)\n"
        )
    elif text:
        text += "\n"
    return text, omitted_files, omitted_lines


def apply_safe_mode(
    changes: GitChanges,
    max_files: int = MAX_FILES,
    max_lines: int = MAX_LINES,
) -> tuple[GitChanges, SafetyReport]:
    """마스킹 → 전송량 제한 순서로 적용한 새 GitChanges 와 처리 보고서를 돌려준다."""
    report = SafetyReport()
    # 마스킹을 먼저 해야, 여러 줄에 걸친 비밀키가 줄 제한에 잘려 패턴을 벗어나는 일이 없다.
    masked_diff, report.masked = mask_secrets(changes.diff)
    limited_diff, report.omitted_files, report.omitted_lines = limit_diff(
        masked_diff, max_files, max_lines
    )
    safe_changes = replace(
        changes,
        files=changes.files[:max_files],
        diff=limited_diff,
    )
    return safe_changes, report


def count_secrets(text: str) -> int:
    """safe-mode 가 꺼져 있을 때 경고용으로, 민감정보 의심 패턴 개수만 센다."""
    _, counts = mask_secrets(text)
    return sum(counts.values())
