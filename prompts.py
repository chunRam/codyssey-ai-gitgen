"""커밋 메시지 / PR 초안 생성을 위한 프롬프트 설계 모듈.

프롬프트 = 지시문(system: 역할·형식·규칙·예시) + 입력(user: 변경 맥락·파일 목록·diff).
AI 에게는 정해진 JSON 구조(스키마)로만 답하게 하고, 받은 JSON 을 Draft 객체로 바꾼다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ai_client import AIError
from git_utils import GitChanges

COMMIT_TYPES = ["feat", "fix", "docs", "refactor", "test", "chore", "style", "perf"]
COMMIT_TITLE_RECOMMENDED = 50
COMMIT_TITLE_MAX = 72
PR_TITLE_MAX = 80

# ──────────────────────────────────────────────────────────────
# 커밋 메시지
# ──────────────────────────────────────────────────────────────

COMMIT_SYSTEM = f"""\
너는 Git 커밋 메시지를 작성하는 시니어 개발자다.
주어진 코드 변경(diff)만 근거로, 팀원이 읽기 쉬운 커밋 메시지를 작성한다.

[형식 규칙]
- title: "<type>: <요약>" 한 줄. type 은 {", ".join(COMMIT_TYPES)} 중 하나.
  - feat=기능 추가, fix=버그 수정, docs=문서, refactor=동작 변화 없는 구조 개선,
    test=테스트, chore=설정·빌드·기타, style=포맷팅, perf=성능 개선
- title 길이: {COMMIT_TITLE_RECOMMENDED}자 이내 권장, 절대 {COMMIT_TITLE_MAX}자를 넘기지 않는다. 마침표로 끝내지 않는다.
- body: 핵심 변경 사항 1~2개. 각 항목은 한 문장이고, 변경된 파일(또는 모듈) 이름을 1~3개 언급한다.
- 요약은 한국어로 쓰고, 코드 식별자(함수·파일 이름)는 원문 그대로 쓴다.

[금지]
- diff 에 없는 내용을 추측해서 쓰지 않는다.
- [MASKED:...] 로 가려진 값을 복원하거나 추측하지 않는다.
- "코드 수정", "업데이트" 처럼 무엇을 바꿨는지 알 수 없는 표현을 쓰지 않는다.

[예시]
{{"title": "feat: 로그인 실패 시 재시도 안내 메시지 추가",
 "body": ["auth.py 의 login() 이 실패 횟수를 세고 3회 이상이면 안내 문구를 반환하도록 변경",
          "messages.py 에 재시도 안내 문구 상수 추가"]}}
"""

COMMIT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING", "description": "커밋 제목 한 줄"},
        "body": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "핵심 변경 사항 1~2개 (불릿 기호 없이 문장만)",
        },
    },
    "required": ["title", "body"],
}

# ──────────────────────────────────────────────────────────────
# PR
# ──────────────────────────────────────────────────────────────

PR_SYSTEM = f"""\
너는 Pull Request 설명을 작성하는 시니어 개발자다.
브랜치의 코드 변경(diff)만 근거로, 리뷰어가 빠르게 이해할 수 있는 PR 제목과 본문을 작성한다.

[형식 규칙]
- title: "<type>: <요약>" 한 줄, {PR_TITLE_MAX}자 이내. type 은 {", ".join(COMMIT_TYPES)} 중 하나.
- why: 이 변경이 필요한 배경·문제 1~3개. 사용자가 준 [변경 이유]가 있으면 그것을 우선 반영한다.
       이유를 diff 로 알 수 없으면 diff 로 확인되는 목적만 쓰고, 지어내지 않는다.
- what: 핵심 변경 사항 1~5개. 파일·함수 이름을 구체적으로 언급한다.
- how_to_test: 리뷰어가 따라 할 수 있는 확인 방법 1~4개. 실행 명령이 있으면 명령을 포함한다.
- 각 항목은 한 문장, 한국어. 코드 식별자는 원문 그대로 쓴다.

[금지]
- diff 에 없는 내용을 추측해서 쓰지 않는다.
- [MASKED:...] 로 가려진 값을 복원하거나 추측하지 않는다.

[예시]
{{"title": "feat: 커밋 메시지 자동 생성 기능 추가",
 "why": ["커밋 메시지 작성에 시간이 들고 형식이 제각각이라 일관된 초안이 필요했다"],
 "what": ["git_utils.py 에 git status/diff 수집 로직 추가",
          "main.py 에 commit 명령 추가"],
 "how_to_test": ["export GEMINI_API_KEY=\\"YOUR_KEY\\" 로 키 설정",
                 "파일 수정 후 python main.py commit 실행해 제목과 본문이 출력되는지 확인"]}}
"""

PR_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING", "description": "PR 제목 한 줄"},
        "why": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "변경 배경"},
        "what": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "핵심 변경 사항"},
        "how_to_test": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "테스트 방법"},
    },
    "required": ["title", "why", "what", "how_to_test"],
}

# ──────────────────────────────────────────────────────────────
# 입력(user) 프롬프트 조립
# ──────────────────────────────────────────────────────────────


def build_user_prompt(changes: GitChanges, context: str = "") -> str:
    """AI 에 보낼 입력: 변경 이유(선택) + 브랜치 + 파일 목록 + diff."""
    file_lines = "\n".join(f"- {c.status} {c.path}" for c in changes.files) or "- (없음)"
    sections = []
    if context.strip():
        sections.append(f"[변경 이유 (작성자 제공)]\n{context.strip()}")
    sections.append(f"[브랜치]\n{changes.branch or '(detached HEAD)'}")
    sections.append(f"[변경 범위]\n{changes.diff_source}")
    sections.append(f"[변경 파일] (상태: M=수정, A=추가, D=삭제, R=이름변경, ??=새 파일)\n{file_lines}")
    sections.append(f"[diff]\n{changes.diff.strip() or '(diff 없음: 새 파일만 있음)'}")
    return "\n\n".join(sections)


# ──────────────────────────────────────────────────────────────
# 응답(JSON) → Draft
# ──────────────────────────────────────────────────────────────


@dataclass
class CommitDraft:
    title: str
    body: list[str]

    def to_text(self) -> str:
        lines = [self.title]
        if self.body:
            lines.append("")
            lines.extend(f"- {item}" for item in self.body)
        return "\n".join(lines)


@dataclass
class PRDraft:
    title: str
    why: list[str]
    what: list[str]
    how_to_test: list[str]

    def body_text(self) -> str:
        sections = [("Why", self.why), ("What", self.what), ("How to Test", self.how_to_test)]
        blocks = []
        for header, items in sections:
            bullets = "\n".join(f"- {item}" for item in items)
            blocks.append(f"## {header}\n{bullets}")
        return "\n\n".join(blocks)


def _load_json(text: str) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise AIError("AI 응답이 JSON 형식이 아닙니다.", "다시 실행하거나 --temperature 를 낮춰 보세요.")
    if not isinstance(data, dict):
        raise AIError("AI 응답 JSON 의 구조가 예상과 다릅니다.")
    return data


def _clean_line(value: object) -> str:
    """한 줄 문자열로 정리: 줄바꿈 제거, 앞뒤 공백 제거."""
    return " ".join(str(value).split())


def _clean_items(value: object) -> list[str]:
    """불릿 목록 정리: 문자열 목록으로 만들고, AI 가 붙인 '- ', '* ' 기호와 빈 항목을 뺀다."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    items = []
    for item in value:
        text = _clean_line(item).lstrip("-*• ").strip()
        if text:
            items.append(text)
    return items


def parse_commit(text: str) -> CommitDraft:
    data = _load_json(text)
    return CommitDraft(title=_clean_line(data.get("title", "")), body=_clean_items(data.get("body")))


def parse_pr(text: str) -> PRDraft:
    data = _load_json(text)
    return PRDraft(
        title=_clean_line(data.get("title", "")),
        why=_clean_items(data.get("why")),
        what=_clean_items(data.get("what")),
        how_to_test=_clean_items(data.get("how_to_test")),
    )
