"""AI 기반 Git 커밋 메시지 / PR 초안 생성기 CLI 진입점.

사용 예:
    python main.py commit
    python main.py pr --base main --temperature 0.5
"""

from __future__ import annotations

import argparse
import sys

import ai_client
import formatter
import git_utils
import prompts
import safety
from ai_client import AIError, AIResponse, BaseClient
from formatter import ValidationResult
from git_utils import GitChanges, GitError

# 기본 API 파라미터 (CLI 옵션으로 덮어쓸 수 있다)
DEFAULT_PROVIDER = ai_client.AUTO_PROVIDER
DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 2048
DEFAULT_THINKING_BUDGET = 0  # 0 = 추론 끔
DEFAULT_BASE_BRANCH = "main"
MAX_LISTED_FILES = 10  # 화면에 나열할 변경 파일 수
MAX_API_CALLS = 2      # 1회 실행당 최대 AI 호출 수 (첫 요청 1 + 검증 실패 시 재요청 1)

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


def thinking_budget_type(value: str) -> int:
    """--thinking-budget 값 검증: -1(자동) 또는 0 이상의 정수만 허용한다."""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"정수가 아닙니다: {value}")
    if number < -1:
        raise argparse.ArgumentTypeError("-1(자동) 또는 0 이상이어야 합니다.")
    return number


def build_parser() -> argparse.ArgumentParser:
    # commit / pr 이 공통으로 쓰는 옵션을 부모 파서에 모아 중복을 없앤다.
    common = argparse.ArgumentParser(add_help=False)
    api = common.add_argument_group("AI API 옵션")
    api.add_argument(
        "--provider",
        choices=[ai_client.AUTO_PROVIDER, *ai_client.PROVIDERS],
        default=DEFAULT_PROVIDER,
        help=(
            "AI 공급자. auto 는 API Key 가 설정된 공급자를 gemini → openrouter 순으로 사용 "
            f"(기본값: {DEFAULT_PROVIDER})"
        ),
    )
    api.add_argument(
        "--model",
        default=None,
        help=(
            "사용할 모델 (기본값: gemini 는 "
            f"{ai_client.GeminiClient.default_model}, openrouter 는 "
            f"{ai_client.OpenRouterClient.default_model})"
        ),
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
        help=f"응답 최대 토큰 수, 추론 토큰 포함 (기본값: {DEFAULT_MAX_TOKENS})",
    )
    api.add_argument(
        "--thinking-budget",
        type=thinking_budget_type,
        default=DEFAULT_THINKING_BUDGET,
        help=(
            "추론 토큰 예산 (gemini-2.5 계열, OpenRouter 추론 모델). 0=끔, -1=자동 "
            f"(기본값: {DEFAULT_THINKING_BUDGET})"
        ),
    )
    common.add_argument(
        "--context",
        default="",
        help='변경 이유 등 AI 에 추가로 알려줄 맥락 (예: --context "로그인 오류 신고 대응")',
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
        f"[INFO] 설정: provider={args.provider}, model={args.model or '(공급자 기본값)'}, "
        f"temperature={args.temperature}, "
        f"max_tokens={args.max_tokens}, thinking_budget={args.thinking_budget}, "
        f"safe_mode={args.safe_mode}"
    )


def report_changes(changes: GitChanges) -> None:
    """수집한 git 변경 사항을 요약해서 보여준다."""
    print(f"[INFO] Git status 수집 완료: {len(changes.files)}개 파일 변경 감지")
    for change in changes.files[:MAX_LISTED_FILES]:
        print(f"         {change.status:>2} {change.path}")
    if len(changes.files) > MAX_LISTED_FILES:
        print(f"         ... 외 {len(changes.files) - MAX_LISTED_FILES}개")
    print(
        f"[INFO] Git diff 수집 완료: {changes.diff_line_count}줄 "
        f"({changes.diff_source})"
    )


def apply_safety(args: argparse.Namespace, changes: GitChanges) -> GitChanges:
    """--safe-mode 면 마스킹·전송량 제한을 적용하고, 꺼져 있으면 민감정보 의심 시 경고만 한다."""
    if not args.safe_mode:
        suspected = safety.count_secrets(changes.diff)
        if suspected:
            print(
                f"[WARN] diff 에 민감정보로 의심되는 패턴 {suspected}건이 있습니다. "
                "--safe-mode 사용을 권장합니다."
            )
        return changes

    safe_changes, report = safety.apply_safe_mode(changes)
    if report.masked_total:
        detail = ", ".join(f"{name} {count}" for name, count in report.masked.items())
        print(f"[SAFE] 민감정보 {report.masked_total}건 마스킹: {detail}")
    else:
        print("[SAFE] 마스킹 대상 없음")
    if report.omitted_files or report.omitted_lines:
        print(
            f"[SAFE] 전송량 제한: 파일 {report.omitted_files}개, "
            f"{report.omitted_lines}줄 제외 (최대 {safety.MAX_FILES}개 파일 / "
            f"{safety.MAX_LINES}줄)"
        )
    print(f"[SAFE] AI 전송 예정 diff: {safe_changes.diff_line_count}줄")
    return safe_changes


def create_client(args: argparse.Namespace) -> BaseClient | None:
    try:
        return ai_client.create_client(
            args.provider, args.model, args.temperature, args.max_tokens, args.thinking_budget
        )
    except AIError as error:  # API Key 미설정
        print_error(error.message, error.hint)
        return None


def call_ai(
    client: BaseClient, system: str, prompt: str, schema: dict
) -> AIResponse | None:
    """AI 를 1회 호출한다. 실패하거나 답변이 잘리면 오류를 출력하고 None."""
    print(f"[INFO] AI API 요청 중... (provider={client.provider}, model={client.model})")
    try:
        response = client.generate(system, prompt, schema)
    except AIError as error:
        print_error(error.message, error.hint)
        return None

    print(
        f"[INFO] 토큰 사용량: 입력 {response.prompt_tokens} / 출력 {response.output_tokens}"
        f" / 추론 {response.thinking_tokens} (finishReason={response.finish_reason})"
    )
    if response.truncated:
        # JSON 답변이 중간에 잘리면 쓸 수 없으므로 오류로 처리한다.
        print_error(
            f"답변이 길이 제한({client.max_tokens} 토큰)에 걸려 중간에 잘렸습니다.",
            "--max-tokens 를 늘리거나 --thinking-budget 0 으로 추론을 끄세요.",
        )
        return None
    return response


def generate_draft(args, system, user_prompt, schema, parse, validate):
    """AI 로 초안을 만들고 검증한다. 규칙을 어기면 위반 내용을 알려주고 1회 재요청한다.

    돌려주는 값: (초안, 마지막 검증 결과). 유효한 초안을 얻지 못하면 None.
    """
    client = create_client(args)
    if client is None:
        return None

    prompt = user_prompt
    draft, result = None, None
    try:
        for attempt in range(1, MAX_API_CALLS + 1):
            response = call_ai(client, system, prompt, schema)
            if response is None:
                return None
            try:
                draft = parse(response.text)
                result = validate(draft)
            except AIError as error:  # JSON 이 아닌 답변
                result = ValidationResult(errors=[error.message])

            if result.ok:
                print("[CHECK] 형식 검증 통과")
                break
            print(f"[CHECK] 규칙 위반 {len(result.errors)}건:")
            for error in result.errors:
                print(f"         - {error}")
            if attempt < MAX_API_CALLS:
                print("[INFO] 위반 내용을 알려주고 1회 재요청합니다.")
                prompt = prompts.build_retry_prompt(user_prompt, response.text, result.errors)
    finally:
        print(f"[INFO] AI API 호출 횟수: {client.call_count}회 (최대 {MAX_API_CALLS}회)")

    if draft is None:
        print_error("AI 로부터 올바른 형식의 답변을 받지 못했습니다.", "다시 실행해 보세요.")
        return None
    return draft, result


def finalize(draft, result: ValidationResult, fix, validate):
    """재요청 후에도 남은 위반은 후처리로 다듬고, 그래도 남는 문제와 권장 사항은 경고로 알린다."""
    if not result.ok:
        draft, notes = fix(draft)
        for note in notes:
            print(f"[FIX] {note}")
        result = validate(draft)
    for error in result.errors:
        print(f"[WARN] {error} 직접 수정이 필요합니다.")
    for warning in result.warnings:
        print(f"[WARN] {warning}")
    return draft


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
    changes = apply_safety(args, changes)

    user_prompt = prompts.build_user_prompt(changes, args.context)
    generated = generate_draft(
        args, prompts.COMMIT_SYSTEM, user_prompt, prompts.COMMIT_SCHEMA,
        prompts.parse_commit, formatter.validate_commit,
    )
    if generated is None:
        return EXIT_ERROR
    draft = finalize(*generated, formatter.fix_commit, formatter.validate_commit)

    print("[DONE] 커밋 메시지 생성 완료\n")
    print(formatter.render_commit(draft))
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
    changes = apply_safety(args, changes)

    user_prompt = prompts.build_user_prompt(changes, args.context)
    generated = generate_draft(
        args, prompts.PR_SYSTEM, user_prompt, prompts.PR_SCHEMA,
        prompts.parse_pr, formatter.validate_pr,
    )
    if generated is None:
        return EXIT_ERROR
    draft = finalize(*generated, formatter.fix_pr, formatter.validate_pr)

    print("[DONE] PR 초안 생성 완료\n")
    print(formatter.render_pr(draft))
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
