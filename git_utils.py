"""Git 변경 사항 수집 모듈.

git status / git diff 명령을 실행하고, 그 결과를 프로그램이 쓰기 좋은 형태로 정리한다.
Git 연동 범위는 과제 제약에 따라 status / diff (+ 저장소 확인용 rev-parse, branch)로 제한한다.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field


class GitError(Exception):
    """git 실행 실패. 사용자에게 보여줄 원인(message)과 해결 힌트(hint)를 담는다."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


@dataclass
class FileChange:
    """변경된 파일 하나: 상태 코드(예: 'M' 수정, 'A' 추가, 'D' 삭제, '??' 새 파일)와 경로."""

    status: str
    path: str
    staged: bool = False             # git add 로 커밋 대기 중인지


@dataclass
class GitChanges:
    """AI 에 넘길 변경 사항 묶음."""

    branch: str                      # 현재 브랜치 이름 (detached HEAD 면 빈 문자열)
    files: list[FileChange] = field(default_factory=list)  # git status 결과
    diff: str = ""                   # git diff 결과 텍스트
    diff_source: str = ""            # diff 를 어디서 가져왔는지 (staged / working tree / main...HEAD)

    @property
    def diff_line_count(self) -> int:
        return len(self.diff.splitlines())

    @property
    def is_empty(self) -> bool:
        return not self.files and not self.diff.strip()


def run_git(*args: str) -> str:
    """git 명령을 실행하고 표준 출력을 문자열로 돌려준다. 실패하면 GitError."""
    # core.quotepath=false: 한글 파일명이 "\355\225\234" 처럼 깨져 보이지 않게 한다.
    command = ["git", "-c", "core.quotepath=false", *args]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        raise GitError("git 이 설치되어 있지 않습니다.", "git 설치 후 다시 실행하세요.")
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise GitError(f"git {args[0]} 실행 실패: {detail}")
    return result.stdout


def ensure_repo_root() -> str:
    """현재 위치가 Git 프로젝트 루트인지 확인하고, 루트 경로를 돌려준다."""
    try:
        root = run_git("rev-parse", "--show-toplevel").strip()
    except GitError:
        raise GitError(
            "현재 디렉토리는 Git 저장소가 아닙니다.",
            "git init 으로 초기화된 프로젝트 루트에서 실행하세요.",
        )
    if os.path.realpath(os.getcwd()) != os.path.realpath(root):
        raise GitError(
            "Git 프로젝트 루트 디렉토리에서 실행해야 합니다.",
            f"cd {root} 후 다시 실행하세요.",
        )
    return root


def get_current_branch() -> str:
    return run_git("branch", "--show-current").strip()


def get_status() -> list[FileChange]:
    """git status --porcelain 결과를 FileChange 목록으로 바꾼다."""
    # --porcelain: 사람이 아닌 프로그램이 읽기 좋은 고정 형식. 한 줄 = "XY 경로"
    #   X = staged 상태, Y = working tree 상태, '??' = 추적하지 않는 새 파일
    output = run_git("status", "--porcelain", "--untracked-files=all")
    changes: list[FileChange] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        code = line[:2]
        path = line[3:]
        # 이름 변경은 "R  old -> new" 형식이므로 새 이름만 남긴다.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        changes.append(FileChange(status=code.strip(), path=path, staged=code[0] not in " ?"))
    return changes


def get_diff(*args: str) -> str:
    # --no-color: 사용자 git 설정과 상관없이 색상 코드(ESC 문자) 없는 순수 텍스트를 받는다.
    return run_git("diff", "--no-color", *args)


def get_diff_files(*args: str) -> list[FileChange]:
    """git diff --name-status 로 diff 범위 안의 파일 목록을 얻는다. 한 줄 = "상태<TAB>경로"."""
    output = run_git("diff", "--name-status", *args)
    changes: list[FileChange] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        # 이름 변경(R100 old new)·복사(C) 는 마지막 칸이 새 경로다.
        changes.append(FileChange(status=parts[0][0], path=parts[-1], staged=True))
    return changes


def collect_commit_changes() -> GitChanges:
    """commit 용 변경 사항: staged 가 있으면 staged 만, 없으면 working tree 변경을 쓴다."""
    files = get_status()
    staged_files = [change for change in files if change.staged]
    if staged_files:
        # 실제로 커밋될 내용(staged)만 AI 에 넘긴다.
        files, diff, source = staged_files, get_diff("--cached"), "staged"
    else:
        diff, source = get_diff(), "working tree"
    return GitChanges(
        branch=get_current_branch(),
        files=files,
        diff=diff,
        diff_source=source,
    )


def collect_pr_changes(base: str) -> GitChanges:
    """pr 용 변경 사항: 기준 브랜치(base)에서 갈라진 뒤 현재 브랜치에 쌓인 변경을 쓴다.

    브랜치에 커밋된 변경이 없으면, 아직 커밋하지 않은 working tree 변경으로 대신한다.
    """
    try:
        run_git("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    except GitError:
        raise GitError(
            f"기준 브랜치 '{base}' 를 찾을 수 없습니다.",
            "--base 옵션으로 존재하는 브랜치를 지정하세요. (예: --base main)",
        )
    # base...HEAD (점 3개): base 와 현재 브랜치가 갈라진 지점부터 HEAD 까지의 변경만 비교한다.
    branch_range = f"{base}...HEAD"
    branch_diff = get_diff(branch_range)
    if branch_diff.strip():
        files, diff, source = get_diff_files(branch_range), branch_diff, branch_range
    else:
        files, diff, source = get_status(), get_diff("HEAD"), "working tree"
    return GitChanges(
        branch=get_current_branch(),
        files=files,
        diff=diff,
        diff_source=source,
    )
