"""AI 기반 Git 커밋 메시지 / PR 초안 생성기 CLI 진입점.

사용 예:
    python main.py commit
    python main.py pr --base main --temperature 0.5
"""

from __future__ import annotations

import argparse
import sys

import git_utils
from git_utils import GitChanges, GitError

# 기본 API 파라미터 (CLI 옵션으로 덮어쓸 수 있다)
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 2048
DEFAULT_BASE_BRANCH = "main"

# 종료 코드: 0 = 정상, 그 외 = 오류
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def temperature_type(value: str) -> float:
    """--temperature 값 검증: 0.0 ~ 2.0 범위의 실수만 허용한다."""
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"숫자가 아닙니다: {value}")
    if not 0.0 <= number <= 2.0:
        raise argparse.ArgumentTypeError("0.0 ~ 2.0 사이 값이어야 합니다.")
    return number


def positive_int(value: str) -> int:
    """--max-tokens 값 검증: 1 이상의 정수만 허용한다."""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"정수가 아닙니다: {value}")
    if number < 1:
        raise argparse.ArgumentTypeError("1 이상이어야 합니다.")
    return number


def build_parser() -> argparse.ArgumentParser:
    # commit / pr 이 공통으로 쓰는 옵션을 부모 파서에 모아 중복을 없앤다.
    common = argparse.ArgumentParser(add_help=False)
    api = common.add_argument_group("AI API 옵션")
    api.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"사용할 Gemini 모델 (기본값: {DEFAULT_MODEL})",
    )
    api.add_argument(
        "--temperature",
        type=temperature_type,
        default=DEFAULT_TEMPERATURE,
        help=f"창의성 정도 0.0~2.0, 낮을수록 일관적 (기본값: {DEFAULT_TEMPERATURE})",
    )
    api.add_argument(
        "--max-tokens",
        type=positive_int,
        default=DEFAULT_MAX_TOKENS,
        help=f"응답 최대 토큰 수 (기본값: {DEFAULT_MAX_TOKENS})",
    )
    safety = common.add_argument_group("보안 옵션")
    safety.add_argument(
        "--safe-mode",
        action="store_true",
        help="민감정보 마스킹 + diff 전송량 제한 후 AI에 전송",
    )

    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="Git 변경 사항으로 커밋 메시지와 PR 초안을 AI로 생성합니다.",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    subparsers.required = True

    commit_parser = subparsers.add_parser(
        "commit",
        parents=[common],
        help="커밋 메시지 생성",
        description="현재 변경 사항(staged 우선)으로 커밋 메시지를 생성합니다.",
    )
    commit_parser.set_defaults(func=run_commit)

    pr_parser = subparsers.add_parser(
        "pr",
        parents=[common],
        help="PR 제목/본문 생성",
        description="기준 브랜치 대비 변경 사항으로 PR 제목과 본문을 생성합니다.",
    )
    pr_parser.add_argument(
        "--base",
        default=DEFAULT_BASE_BRANCH,
        help=f"비교 기준 브랜치 (기본값: {DEFAULT_BASE_BRANCH})",
    )
    pr_parser.set_defaults(func=run_pr)

    return parser


def print_error(message: str, hint: str = "") -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    if hint:
        print(f"[HINT] {hint}", file=sys.stderr)


def print_settings(args: argparse.Namespace) -> None:
    print(
        f"[INFO] 설정: model={args.model}, temperature={args.temperature}, "
        f"max_tokens={args.max_tokens}, safe_mode={args.safe_mode}"
    )


def report_changes(changes: GitChanges) -> None:
    """수집한 git 변경 사항을 요약해서 보여준다."""
    print(f"[INFO] Git status 수집 완료: {len(changes.files)}개 파일 변경 감지")
    for change in changes.files:
        print(f"         {change.status:>2} {change.path}")
    print(
        f"[INFO] Git diff 수집 완료: {changes.diff_line_count}줄 "
        f"({changes.diff_source})"
    )


def run_commit(args: argparse.Namespace) -> int:
    print_settings(args)
    try:
        git_utils.ensure_repo_root()
        changes = git_utils.collect_commit_changes()
    except GitError as error:
        print_error(error.message, error.hint)
        return EXIT_ERROR

    if changes.is_empty:
        print("[INFO] 변경 사항이 없습니다. 커밋 메시지를 생성하지 않고 종료합니다.")
        return EXIT_OK
    report_changes(changes)
    if not changes.diff.strip():
        print("[HINT] 새 파일만 있어 diff 가 비어 있습니다. git add 후 실행하면 파일 내용까지 반영됩니다.")
    # TODO(3~6단계): safe-mode → AI 호출 → 검증 → 출력
    return EXIT_OK


def run_pr(args: argparse.Namespace) -> int:
    print_settings(args)
    try:
        git_utils.ensure_repo_root()
        changes = git_utils.collect_pr_changes(args.base)
    except GitError as error:
        print_error(error.message, error.hint)
        return EXIT_ERROR

    print(f"[INFO] 현재 브랜치: {changes.branch or '(detached HEAD)'} (기준: {args.base})")
    if changes.is_empty:
        print("[INFO] 변경 사항이 없습니다. PR 초안을 생성하지 않고 종료합니다.")
        return EXIT_OK
    report_changes(changes)
    # TODO(3~6단계): safe-mode → AI 호출 → 검증 → 출력
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n[INFO] 사용자가 중단했습니다.", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
