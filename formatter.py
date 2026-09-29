"""AI 가 만든 커밋/PR 초안을 검증·후처리하고, 구획을 나눠 출력하는 모듈.

검증 규칙 (과제 4-5):
- 커밋 제목: 50자 이내 권장, 최대 72자, "<type>: <요약>" 형식
- 커밋 본문: 핵심 변경 사항 불릿 1~2개
- PR 제목: 최대 80자, "<type>: <요약>" 형식
- PR 본문: Why / What / How to Test 섹션마다 불릿 1개 이상
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from prompts import (
    COMMIT_TITLE_MAX,
    COMMIT_TITLE_RECOMMENDED,
    COMMIT_TYPES,
    PR_TITLE_MAX,
    CommitDraft,
    PRDraft,
)

COMMIT_BODY_MAX_ITEMS = 2
EMPTY_SECTION_PLACEHOLDER = "(AI 가 작성하지 못했습니다. 직접 채워 주세요)"
FRAME_WIDTH = 40

# "feat: 요약", "fix(auth): 요약" 형태. (auth) 같은 범위 표기는 선택.
TITLE_PATTERN = re.compile(rf"^(?:{'|'.join(COMMIT_TYPES)})(?:\([^)]+\))?: \S")


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)    # 규칙 위반 → 재요청/후처리 대상
    warnings: list[str] = field(default_factory=list)  # 권장 사항 미충족 → 안내만

    @property
    def ok(self) -> bool:
        return not self.errors


# ──────────────────────────────────────────────────────────────
# 검증
# ──────────────────────────────────────────────────────────────


def _check_title(title: str, max_length: int, result: ValidationResult) -> None:
    if not title:
        result.errors.append("제목이 비어 있습니다.")
        return
    if len(title) > max_length:
        result.errors.append(f"제목이 {len(title)}자로 최대 {max_length}자를 넘습니다.")
    if not TITLE_PATTERN.match(title):
        result.errors.append(
            f'제목이 "<type>: <요약>" 형식이 아닙니다. (type: {", ".join(COMMIT_TYPES)})'
        )


def validate_commit(draft: CommitDraft) -> ValidationResult:
    result = ValidationResult()
    _check_title(draft.title, COMMIT_TITLE_MAX, result)
    if COMMIT_TITLE_RECOMMENDED < len(draft.title) <= COMMIT_TITLE_MAX:
        result.warnings.append(
            f"제목이 {len(draft.title)}자입니다. {COMMIT_TITLE_RECOMMENDED}자 이내를 권장합니다."
        )
    if len(draft.body) > COMMIT_BODY_MAX_ITEMS:
        result.errors.append(
            f"본문 불릿이 {len(draft.body)}개입니다. 핵심 변경 {COMMIT_BODY_MAX_ITEMS}개 이내로 줄여야 합니다."
        )
    return result


def validate_pr(draft: PRDraft) -> ValidationResult:
    result = ValidationResult()
    _check_title(draft.title, PR_TITLE_MAX, result)
    for header, items in _pr_sections(draft):
        if not items:
            result.errors.append(f"'## {header}' 섹션에 불릿이 없습니다. 최소 1개 필요합니다.")
    return result


def _pr_sections(draft: PRDraft) -> list[tuple[str, list[str]]]:
    return [("Why", draft.why), ("What", draft.what), ("How to Test", draft.how_to_test)]


# ──────────────────────────────────────────────────────────────
# 후처리: 재요청 후에도 규칙을 어기면 프로그램이 직접 다듬는다.
# ──────────────────────────────────────────────────────────────


def truncate_title(title: str, max_length: int) -> str:
    """제목을 최대 길이에 맞춰 자른다. 가능하면 단어 경계(공백)에서 자른다."""
    if len(title) <= max_length:
        return title
    cut = title[:max_length]
    space = cut.rfind(" ")
    if space > max_length // 2:  # 너무 앞에서 잘리지 않을 때만 공백 기준으로 자른다
        cut = cut[:space]
    return cut.rstrip(" ,.;:")


def fix_commit(draft: CommitDraft) -> tuple[CommitDraft, list[str]]:
    notes = []
    title = draft.title
    if len(title) > COMMIT_TITLE_MAX:
        title = truncate_title(title, COMMIT_TITLE_MAX)
        notes.append(f"제목을 {COMMIT_TITLE_MAX}자 이내로 잘랐습니다.")
    body = draft.body
    if len(body) > COMMIT_BODY_MAX_ITEMS:
        body = body[:COMMIT_BODY_MAX_ITEMS]
        notes.append(f"본문 불릿을 앞의 {COMMIT_BODY_MAX_ITEMS}개만 남겼습니다.")
    return replace(draft, title=title, body=body), notes


def fix_pr(draft: PRDraft) -> tuple[PRDraft, list[str]]:
    notes = []
    title = draft.title
    if len(title) > PR_TITLE_MAX:
        title = truncate_title(title, PR_TITLE_MAX)
        notes.append(f"PR 제목을 {PR_TITLE_MAX}자 이내로 잘랐습니다.")
    sections = {}
    for (header, items), key in zip(_pr_sections(draft), ("why", "what", "how_to_test")):
        if not items:
            items = [EMPTY_SECTION_PLACEHOLDER]
            notes.append(f"비어 있는 '## {header}' 섹션에 안내 문구를 넣었습니다.")
        sections[key] = items
    return replace(draft, title=title, **sections), notes


# ──────────────────────────────────────────────────────────────
# 출력: 구분선으로 구획을 나눈다.
# ──────────────────────────────────────────────────────────────


def _frame(label: str, content: str) -> str:
    header = f"--- {label} ---"
    return f"{header}\n{content}\n{'-' * max(len(header), FRAME_WIDTH)}"


def render_commit(draft: CommitDraft) -> str:
    return _frame("Commit Message", draft.to_text())


def render_pr(draft: PRDraft) -> str:
    return "\n\n".join([
        _frame("PR Title", draft.title),
        _frame("PR Body", draft.body_text()),
    ])
