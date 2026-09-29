"""safe-mode: AI 에 보내기 전에 diff 를 안전하게 다듬는 모듈.

(A) 마스킹: API Key 형태의 토큰과 이메일 패턴을 [MASKED:종류] 로 가린다.
(B) 전송량 제한: diff 를 최대 MAX_FILES 개 파일, MAX_LINES 줄까지만 남긴다.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field, replace

from git_utils import GitChanges

MAX_FILES = 10
MAX_LINES = 200

# (종류 이름, 정규표현식)
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("GOOGLE_API_KEY", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b|\bAQ\.[0-9A-Za-z_\-]{20,}")),
    ("OPENAI_API_KEY", re.compile(r"\bsk-[0-9A-Za-z_\-]{20,}")),
    ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}\b|\bgithub_pat_[0-9A-Za-z_]{20,}")),
    ("AWS_ACCESS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("SLACK_TOKEN", re.compile(r"\bxox[abprs]-[0-9A-Za-z\-]{10,}")),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
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


def apply_safe_mode(changes: GitChanges) -> tuple[GitChanges, SafetyReport]:
    """마스킹 → 전송량 제한 순서로 적용한 새 GitChanges 와 처리 보고서를 돌려준다."""
    report = SafetyReport()
    masked_diff, report.masked = mask_secrets(changes.diff)
    limited_diff, report.omitted_files, report.omitted_lines = limit_diff(
        masked_diff, MAX_FILES, MAX_LINES
    )
    safe_changes = replace(
        changes,
        files=changes.files[:MAX_FILES],
        diff=limited_diff,
    )
    return safe_changes, report


def count_secrets(text: str) -> int:
    """safe-mode 가 꺼져 있을 때 경고용으로, 민감정보 의심 패턴 개수만 센다."""
    _, counts = mask_secrets(text)
    return sum(counts.values())
