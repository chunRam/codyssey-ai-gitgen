"""AI 기반 Git 커밋 메시지 / PR 초안 생성기 CLI 진입점.

사용 예:
    python main.py commit
    python main.py pr --base main --temperature 0.5

[이 파일의 역할]
프로그램의 "접수 창구"다. 사용자가 터미널에 친 명령(commit 또는 pr)과
옵션(--temperature 등)을 알아듣고, 잘못된 입력은 거절하고,
알맞은 작업(run_commit / run_pr)으로 넘겨준다.

[읽는 법]
- '#' 뒤의 글, 그리고 이렇게 큰따옴표 세 개로 감싼 글은 "주석"이다.
  사람을 위한 설명일 뿐이고 컴퓨터는 무시한다.
- 줄 앞의 들여쓰기(칸 띄움)는 "이 줄이 위 줄에 속해 있다"는 뜻이다.
"""

# 파이썬 버전이 달라도 'list[str] | None' 같은 타입 표기를 쓸 수 있게 해주는 설정.
# 몰라도 되는 줄이다. "최신 문법 허용 스위치" 정도로 이해하면 된다.
from __future__ import annotations

# import = "남이 만들어 둔 도구 상자를 가져온다".
# argparse: 터미널 명령어(commit, --temperature 0.7 등)를 해석해 주는 도구 (파이썬 기본 제공)
import argparse
# sys: 프로그램 종료, 오류 출력 등 "시스템"과 관련된 도구 (파이썬 기본 제공)
import sys

# ──────────────────────────────────────────────────────────────
# [덩어리 1] 설정값
# '이름 = 값' 은 "값에 이름표를 붙여 저장한다"는 뜻이다.
# 대문자 이름은 "프로그램 내내 바뀌지 않는 설정값"이라는 관례다.
# ──────────────────────────────────────────────────────────────

# 사용자가 옵션을 안 적었을 때 쓸 기본값들 (CLI 옵션으로 덮어쓸 수 있다)
DEFAULT_MODEL = "gemini-2.5-flash"  # 사용할 AI 모델 이름 (빠르고 저렴한 모델)
DEFAULT_TEMPERATURE = 0.3           # 창의성 정도. 낮을수록 매번 비슷한 답 → 커밋 메시지에 적합
DEFAULT_MAX_TOKENS = 2048           # AI 답변의 최대 길이 (토큰 = 단어 조각 단위)
DEFAULT_BASE_BRANCH = "main"        # pr 명령에서 "어느 브랜치와 비교할지" 기본값

# 종료 코드: 프로그램이 끝날 때 운영체제에 남기는 "성공/실패 신호 숫자".
# 터미널에서 'echo $?' 를 치면 직전 프로그램의 종료 코드를 볼 수 있다.
EXIT_OK = 0      # 0 = 정상 종료
EXIT_ERROR = 1   # 1 = 실행 중 오류 (git 저장소 아님, API 실패 등 — 2단계부터 사용)
EXIT_USAGE = 2   # 2 = 사용법 오류 (argparse가 잘못된 옵션을 거절할 때 자동으로 쓰는 값)


# ──────────────────────────────────────────────────────────────
# [덩어리 2] 입력 검사원
# 'def 이름(재료):' 는 "이름이 붙은 작업(함수)을 정의한다"는 뜻이다.
# 정의만 해 둔 것이고, 실제 실행은 누군가 이 함수를 부를 때 일어난다.
# ──────────────────────────────────────────────────────────────

def temperature_type(value: str) -> float:
    """--temperature 값 검증: 0.0 ~ 2.0 범위의 실수만 허용한다.

    (value: str) → 재료로 '글자'를 받는다. 터미널 입력은 전부 글자로 들어온다.
    -> float     → 결과로 '소수(실수)'를 돌려준다.
    """
    # try: "일단 해 본다" / except: "해 보다가 이 문제가 생기면 이렇게 대처한다"
    try:
        # float(...): 글자 "0.7" 을 숫자 0.7 로 바꾼다.
        number = float(value)
    except ValueError:
        # "abc" 처럼 숫자로 못 바꾸는 글자면 ValueError 가 나고, 여기로 온다.
        # raise = "문제 발생!" 이라고 알린다. argparse 가 이걸 받아 사용자에게 오류를 보여준다.
        # f"...{value}..." 는 글자 안에 변수 값을 끼워 넣는 문법이다.
        raise argparse.ArgumentTypeError(f"숫자가 아닙니다: {value}")
    # if not A: → "A가 아니면"
    # 0.0 <= number <= 2.0 → "number 가 0.0 이상 2.0 이하"
    # (Gemini API 가 허용하는 temperature 범위가 0~2 이다)
    if not 0.0 <= number <= 2.0:
        raise argparse.ArgumentTypeError("0.0 ~ 2.0 사이 값이어야 합니다.")
    # return = "작업 끝. 이 값을 결과로 돌려준다." 검사를 통과한 숫자를 돌려준다.
    return number


def positive_int(value: str) -> int:
    """--max-tokens 값 검증: 1 이상의 정수만 허용한다.

    구조는 바로 위 temperature_type 과 똑같다. 검사 기준만 다르다.
    """
    try:
        # int(...): 글자 "2048" 을 정수 2048 로 바꾼다. "abc" 나 "1.5" 는 실패한다.
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"정수가 아닙니다: {value}")
    # 답변 길이가 0 이하일 수는 없으므로 거절한다.
    if number < 1:
        raise argparse.ArgumentTypeError("1 이상이어야 합니다.")
    return number


# ──────────────────────────────────────────────────────────────
# [덩어리 3] 메뉴판과 주문서 양식 만들기
# "어떤 명령(commit/pr)이 있고, 각 명령에 어떤 옵션을 쓸 수 있는지" 정의한다.
# ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    # ── (1) 공통 옵션 양식 ──
    # commit 과 pr 은 둘 다 --model, --temperature 등 같은 옵션을 쓴다.
    # 두 번 적지 않으려고 "공통 옵션 양식(common)"을 먼저 하나 만들어 두고,
    # 아래에서 commit/pr 에 복사해 붙인다(parents=[common]).
    #
    # add_help=False 인 이유: 양식마다 자동으로 '-h(도움말)' 옵션이 생기는데,
    # 공통 양식과 commit 양식 둘 다 -h 가 있으면 "중복!" 오류가 난다. 그래서 여기선 끈다.
    common = argparse.ArgumentParser(add_help=False)

    # add_argument_group: --help 화면에서 옵션을 "AI API 옵션" 제목 아래 묶어 보여주기 위한 것.
    # 기능에는 영향 없고, 도움말을 보기 좋게 정리하는 용도다.
    api = common.add_argument_group("AI API 옵션")

    # add_argument(...) = "주문서에 옵션 칸 하나를 추가한다".
    # 아래 옵션들은 모두 같은 모양이다:
    #   "--옵션이름"   : 사용자가 터미널에 치는 이름
    #   type=...      : 들어온 글자를 검사/변환할 함수 (덩어리 2의 검사원)
    #   default=...   : 사용자가 안 적으면 쓸 기본값 (덩어리 1의 설정값)
    #   help=...      : --help 를 쳤을 때 보여줄 설명
    api.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"사용할 Gemini 모델 (기본값: {DEFAULT_MODEL})",
    )
    api.add_argument(
        "--temperature",
        type=temperature_type,  # 들어온 값을 temperature_type 검사원에게 맡긴다
        default=DEFAULT_TEMPERATURE,
        help=f"창의성 정도 0.0~2.0, 낮을수록 일관적 (기본값: {DEFAULT_TEMPERATURE})",
    )
    api.add_argument(
        "--max-tokens",
        type=positive_int,      # 들어온 값을 positive_int 검사원에게 맡긴다
        default=DEFAULT_MAX_TOKENS,
        help=f"응답 최대 토큰 수 (기본값: {DEFAULT_MAX_TOKENS})",
    )
    # 참고: 이름에 있는 '-' 는 코드 안에서 '_' 로 바뀐다.
    #       --max-tokens → args.max_tokens (파이썬 이름에는 '-' 를 쓸 수 없기 때문)

    safety = common.add_argument_group("보안 옵션")
    safety.add_argument(
        "--safe-mode",
        # store_true: 값을 받지 않는 "스위치" 옵션.
        # --safe-mode 를 적으면 True(켜짐), 안 적으면 False(꺼짐).
        action="store_true",
        help="민감정보 마스킹 + diff 전송량 제한 후 AI에 전송",
    )

    # ── (2) 메인 양식 ──
    # 프로그램 전체의 주문서. 'python main.py --help' 를 치면 이 내용이 보인다.
    parser = argparse.ArgumentParser(
        prog="python main.py",  # 도움말에 표시될 프로그램 이름
        description="Git 변경 사항으로 커밋 메시지와 PR 초안을 AI로 생성합니다.",
    )

    # ── (3) 메뉴(서브커맨드) 등록 ──
    # 서브커맨드 = 'git commit', 'git push' 처럼 프로그램 이름 뒤에 오는 "하위 명령".
    # dest="command": 사용자가 고른 메뉴 이름을 args.command 에 저장한다.
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    # 메뉴 선택은 필수. 'python main.py' 만 치면 "명령을 골라주세요" 오류를 낸다.
    subparsers.required = True

    # 메뉴 1: commit
    commit_parser = subparsers.add_parser(
        "commit",
        parents=[common],  # 공통 옵션 양식을 그대로 붙인다
        help="커밋 메시지 생성",
        description="현재 변경 사항(staged 우선)으로 커밋 메시지를 생성합니다.",
    )
    # set_defaults(func=run_commit):
    # "commit 메뉴가 선택되면, 나중에 run_commit 작업을 실행하라" 고 메모해 둔다.
    # (여기서는 실행하지 않는다. 함수 이름만 적어 두는 것이다.)
    commit_parser.set_defaults(func=run_commit)

    # 메뉴 2: pr
    pr_parser = subparsers.add_parser(
        "pr",
        parents=[common],  # 공통 옵션 양식을 그대로 붙인다
        help="PR 제목/본문 생성",
        description="기준 브랜치 대비 변경 사항으로 PR 제목과 본문을 생성합니다.",
    )
    # pr 에만 있는 옵션: 어느 브랜치와 비교해서 PR 을 쓸지
    pr_parser.add_argument(
        "--base",
        default=DEFAULT_BASE_BRANCH,
        help=f"비교 기준 브랜치 (기본값: {DEFAULT_BASE_BRANCH})",
    )
    # "pr 메뉴가 선택되면, 나중에 run_pr 작업을 실행하라"
    pr_parser.set_defaults(func=run_pr)

    # 완성된 주문서 양식을 돌려준다.
    return parser


# ──────────────────────────────────────────────────────────────
# [덩어리 4] 실제 작업 (주방 자리)
# 지금은 받은 주문을 복창만 한다. 2~6단계에서 여기를 채워 나간다.
# args = 해석이 끝난 주문 내용. args.model, args.temperature 처럼 꺼내 쓴다.
# ──────────────────────────────────────────────────────────────

def print_options(args: argparse.Namespace) -> None:
    """받은 옵션을 화면에 출력한다 (주문 복창). -> None 은 "돌려주는 값 없음" 이라는 뜻."""
    # print(...): 화면에 글자를 출력한다.
    print(f"[INFO] command     = {args.command}")
    print(f"[INFO] model       = {args.model}")
    print(f"[INFO] temperature = {args.temperature}")
    print(f"[INFO] max_tokens  = {args.max_tokens}")
    print(f"[INFO] safe_mode   = {args.safe_mode}")


def run_commit(args: argparse.Namespace) -> int:
    """commit 메뉴가 선택됐을 때 실행되는 작업."""
    print_options(args)
    # TODO = "나중에 할 일" 메모.
    # TODO(2~6단계): git 수집 → safe-mode → AI 호출 → 검증 → 출력
    return EXIT_OK  # 성공 신호(0)를 돌려준다


def run_pr(args: argparse.Namespace) -> int:
    """pr 메뉴가 선택됐을 때 실행되는 작업."""
    print_options(args)
    print(f"[INFO] base        = {args.base}")  # pr 전용 옵션도 출력
    # TODO(2~6단계): git 수집 → safe-mode → AI 호출 → 검증 → 출력
    return EXIT_OK


# ──────────────────────────────────────────────────────────────
# [덩어리 5] 프로그램 시작 버튼
# ──────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    """프로그램 전체 흐름: 양식 준비 → 주문 해석 → 작업 실행 → 결과 신호 반환.

    argv: 명령어 목록. 보통은 비워 두고(None), 그러면 터미널에 친 내용을 자동으로 읽는다.
          테스트할 때는 main(["commit", "--temperature", "0.5"]) 처럼 직접 넣을 수도 있다.
    """
    # 1. 주문서 양식을 만든다 (덩어리 3)
    parser = build_parser()
    # 2. 사용자가 친 명령을 해석한다.
    #    잘못된 입력(없는 메뉴, 범위 밖 숫자 등)이면 argparse 가 여기서 오류를 출력하고
    #    종료 코드 2 로 프로그램을 끝낸다. 아래 줄까지 오지 않는다.
    args = parser.parse_args(argv)
    try:
        # 3. 선택된 메뉴의 작업을 실행한다.
        #    args.func 에는 set_defaults 로 메모해 둔 함수(run_commit 또는 run_pr)가 들어 있다.
        #    그 함수를 args 를 재료로 실행하고, 돌려받은 종료 코드를 그대로 돌려준다.
        return args.func(args)
    except KeyboardInterrupt:
        # 실행 도중 사용자가 Ctrl+C 를 누르면 여기로 온다.
        # 지저분한 오류 화면 대신 짧은 안내를 출력하고 오류 신호(1)로 끝낸다.
        # file=sys.stderr: 일반 출력이 아니라 "오류 출력 통로"로 내보낸다.
        print("\n[INFO] 사용자가 중단했습니다.", file=sys.stderr)
        return EXIT_ERROR


# "이 파일을 직접 실행했을 때만(python main.py ...) 아래를 실행하라"는 파이썬 관용구.
# 다른 파일에서 import 해서 쓸 때는 실행되지 않는다.
if __name__ == "__main__":
    # main() 을 실행하고, 돌려받은 숫자(0/1)를 종료 코드로 남기며 프로그램을 끝낸다.
    sys.exit(main())
