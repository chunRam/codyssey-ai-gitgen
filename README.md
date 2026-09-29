# codyssey-ai-gitgen

Git 변경 사항(`git status`, `git diff`)을 읽고 AI(Google Gemini)로 **커밋 메시지**와 **PR 제목/본문 초안**을 만들어 주는 터미널 도구입니다.

- 생성 결과는 **초안**입니다. 터미널에 출력만 하고 `git commit`, `git push`, PR 생성은 하지 않습니다. 내용을 검토한 뒤 직접 복사해서 쓰세요.
- 표준 라이브러리만 사용하므로 `pip install`이 필요 없습니다.

```
git status / git diff 수집 → (safe-mode) 마스킹·전송량 제한 → Gemini API 호출
  → 형식 검증 (위반 시 1회 재요청 → 후처리) → 구분선으로 나눠 출력
```

---

## 1. 설치

**요구 사항:** Python 3.10 이상, Git, Gemini API Key

```bash
git clone https://github.com/chunRam/codyssey-ai-gitgen.git
cd codyssey-ai-gitgen
python3 --version   # 3.10 이상인지 확인
```

## 2. API Key 설정 (환경변수)

API Key는 **환경변수 `GEMINI_API_KEY`로만** 읽습니다. 코드나 레포에 키를 적지 마세요.

1. https://aistudio.google.com/apikey 에서 키를 발급받습니다.
2. 셸 설정 파일에 추가하고 다시 불러옵니다.

```bash
# zsh (macOS 기본)
echo 'export GEMINI_API_KEY="발급받은_키"' >> ~/.zshrc
source ~/.zshrc

# 설정 확인: 키 전체를 출력하지 말고 앞부분만 확인하세요
echo ${GEMINI_API_KEY:0:4}
```

> `.zshrc`에 추가할 때 따옴표 짝이 맞는지 확인하세요. 따옴표가 하나 빠지면 `unmatched "` 오류가 납니다.

## 3. 실행 방법

**커밋/PR을 만들 Git 프로젝트의 루트 디렉토리에서** 실행합니다. 하위 폴더에서 실행하면 루트로 이동하라는 안내가 나옵니다.

```bash
cd ~/my-project                                   # 대상 프로젝트 루트
python3 ~/codyssey-ai-gitgen/main.py commit       # 커밋 메시지 생성
python3 ~/codyssey-ai-gitgen/main.py pr           # PR 제목/본문 생성
```

### 명령

| 명령 | 사용하는 변경 범위 |
|---|---|
| `commit` | `git add`로 staged된 변경이 있으면 **staged만**, 없으면 working tree 변경 전체 |
| `pr` | 기준 브랜치에서 갈라진 뒤 현재 브랜치에 쌓인 변경(`git diff <base>...HEAD`). 비어 있으면 working tree 변경 |

### 옵션 (commit / pr 공통)

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--model` | `gemini-2.5-flash` | 사용할 Gemini 모델 |
| `--temperature` | `0.3` | 0.0~2.0. 낮을수록 매번 비슷한 결과, 높을수록 표현이 다양해짐 |
| `--max-tokens` | `2048` | 응답 최대 토큰 수 (추론 토큰 포함) |
| `--thinking-budget` | `0` | gemini-2.5 계열의 추론 토큰 예산. `0`=끔, `-1`=모델이 자동 결정 |
| `--context` | (없음) | 변경 이유 등 diff만으로 알 수 없는 맥락 |
| `--safe-mode` | 꺼짐 | 민감정보 마스킹 + diff 전송량 제한 ([6. 민감정보 대응](#6-민감정보-대응-safe-mode) 참고) |
| `--base` (pr 전용) | `main` | 비교 기준 브랜치 |

### 사용 예시

```bash
# 변경한 파일을 staged 한 뒤 커밋 메시지 생성
git add calc.py
python3 main.py commit

# 민감정보를 가리고 전송
python3 main.py commit --safe-mode

# 변경 이유를 알려주고 PR 초안 생성
python3 main.py pr --safe-mode --context "사용자 피드백: 종료 시 인사가 없어 어색하다는 의견"

# 기준 브랜치가 develop 인 경우
python3 main.py pr --base develop

# 파라미터 조정
python3 main.py commit --temperature 0.0 --max-tokens 4096
python3 main.py --help
python3 main.py commit --help
```

---

## 4. 출력 예시

아래는 실제로 실행한 결과입니다.

### 커밋 메시지 (`commit`)

AI의 첫 답이 규칙(본문 불릿 2개 이하)을 어겨서, 위반 내용을 알려주고 1회 재요청한 사례입니다.

```text
$ python3 main.py commit --safe-mode
[INFO] 설정: model=gemini-2.5-flash, temperature=0.3, max_tokens=2048, thinking_budget=0, safe_mode=True
[INFO] Git status 수집 완료: 3개 파일 변경 감지
          A formatter.py
          M main.py
          M prompts.py
[INFO] Git diff 수집 완료: 357줄 (staged)
[SAFE] 마스킹 대상 없음
[SAFE] 전송량 제한: 파일 0개, 157줄 제외 (최대 10개 파일 / 200줄)
[SAFE] AI 전송 예정 diff: 201줄
[INFO] AI API 요청 중... (model=gemini-2.5-flash)
[INFO] 토큰 사용량: 입력 2841 / 출력 87 / 추론 0 (finishReason=STOP)
[CHECK] 규칙 위반 1건:
         - 본문 불릿이 3개입니다. 핵심 변경 2개 이내로 줄여야 합니다.
[INFO] 위반 내용을 알려주고 1회 재요청합니다.
[INFO] AI API 요청 중... (model=gemini-2.5-flash)
[INFO] 토큰 사용량: 입력 2986 / 출력 68 / 추론 0 (finishReason=STOP)
[CHECK] 형식 검증 통과
[INFO] AI API 호출 횟수: 2회 (최대 2회)
[DONE] 커밋 메시지 생성 완료

--- Commit Message ---
feat: 커밋/PR 메시지 검증 및 후처리 기능 추가

- formatter.py 에 커밋/PR 메시지 형식 검증 및 후처리 로직을 추가
- main.py 에서 formatter 모듈을 사용하여 AI 응답을 검증하고 필요한 경우 수정하도록 변경
----------------------------------------
```

### PR 제목/본문 (`pr`)

```text
$ python3 main.py pr --safe-mode --context "사용자 피드백: 프로그램 종료 시 인사가 없어 어색하다는 의견"
[INFO] 설정: model=gemini-2.5-flash, temperature=0.3, max_tokens=2048, thinking_budget=0, safe_mode=True
[INFO] 현재 브랜치: feature/farewell (기준: main)
[INFO] Git status 수집 완료: 1개 파일 변경 감지
          M hello.py
[INFO] Git diff 수집 완료: 10줄 (main...HEAD)
[SAFE] 마스킹 대상 없음
[SAFE] AI 전송 예정 diff: 10줄
[INFO] AI API 요청 중... (model=gemini-2.5-flash)
[INFO] 토큰 사용량: 입력 595 / 출력 89 / 추론 0 (finishReason=STOP)
[CHECK] 형식 검증 통과
[INFO] AI API 호출 횟수: 1회 (최대 2회)
[DONE] PR 초안 생성 완료

--- PR Title ---
feat: 작별 인사 기능 추가
----------------------------------------

--- PR Body ---
## Why
- 프로그램 종료 시 인사가 없어 어색하다는 사용자 피드백이 있었다

## What
- hello.py 에 bye 함수를 추가하여 작별 인사를 출력한다

## How to Test
- bye 함수를 호출하여 '안녕히 가세요, {name}님' 메시지가 출력되는지 확인한다
----------------------------------------
```

### 변경 사항이 없을 때

```text
$ python3 main.py commit
[INFO] 변경 사항이 없습니다. 커밋 메시지를 생성하지 않고 종료합니다.
```
AI를 호출하지 않으므로 비용이 들지 않습니다.

### 오류 예시

오류는 `[ERROR] 원인`과 `[HINT] 해결 방법`으로 출력하고, 0이 아닌 종료 코드로 끝납니다.

```text
[ERROR] GEMINI_API_KEY 환경변수가 설정되지 않았습니다.
[HINT] 예) export GEMINI_API_KEY="YOUR_KEY"

[ERROR] 현재 디렉토리는 Git 저장소가 아닙니다.
[HINT] git init 으로 초기화된 프로젝트 루트에서 실행하세요.

[ERROR] API Key 가 유효하지 않습니다 [HTTP 400] (API key not valid. Please pass a valid API key.)
[HINT] GEMINI_API_KEY 값을 확인하거나 키를 재발급하세요.

[ERROR] AI 서버 오류입니다 [HTTP 503] (This model is currently experiencing high demand. ...)
[HINT] 서버 측 문제입니다. 잠시 후 다시 시도하세요.
```

| 종료 코드 | 의미 |
|---|---|
| `0` | 정상 종료 (초안 생성 완료, 또는 변경 사항 없음) |
| `1` | 실행 오류 (Git 저장소 아님, API Key 없음, API 호출 실패 등) |
| `2` | 사용법 오류 (없는 명령, 범위를 벗어난 옵션 값 등) |

---

## 5. 출력 형식 규칙과 검증

AI의 답을 그대로 쓰지 않고 아래 규칙으로 검사합니다.

| 대상 | 규칙 |
|---|---|
| 커밋 제목 | `<type>: <요약>` 한 줄. 50자 이내 권장(넘으면 경고), **최대 72자** |
| 커밋 본문 | 핵심 변경 불릿 1~2개, 변경 파일 이름 언급 |
| PR 제목 | `<type>: <요약>` 한 줄, **최대 80자** |
| PR 본문 | `## Why` / `## What` / `## How to Test` 섹션 필수, **섹션마다 불릿 1개 이상** |

`type`은 `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `style`, `perf` 중 하나입니다.

**규칙을 어기면**
1. 이전 답과 위반 내용을 함께 보내 **1회 재요청**합니다.
2. 그래도 어기면 프로그램이 직접 다듬습니다. 제목은 단어 경계에서 자르고, 불릿은 앞의 2개만 남기고, 빈 섹션에는 "직접 채워 주세요" 문구를 넣습니다.
3. 자동으로 고칠 수 없는 문제(예: 제목에 type 없음)는 `[WARN]`으로 알립니다.

---

## 6. 민감정보 대응 (safe-mode)

`git diff`는 **외부 AI 서버(Google)로 전송**됩니다. diff에 API Key나 개인정보가 섞여 있으면 그대로 외부로 나갈 수 있습니다.

`--safe-mode`를 켜면 전송 전에 다음을 적용합니다.

| 정책 | 내용 |
|---|---|
| (A) 마스킹 | API Key 형태의 토큰(Google `AIza…`/`AQ.…`, OpenAI `sk-…`, GitHub `ghp_…`, AWS `AKIA…`, Slack `xox…`)과 이메일 주소를 `[MASKED:종류]`로 바꿈 |
| (B) 전송량 제한 | diff를 **최대 10개 파일, 200줄**까지만 전송하고 나머지는 생략 |

```text
# 원래 diff                                   # AI 에 전송되는 diff
+API_KEY = "AIzaSyD...(생략)"          →      +API_KEY = "[MASKED:GOOGLE_API_KEY]"
+ADMIN_EMAIL = "admin@corp.com"        →      +ADMIN_EMAIL = "[MASKED:EMAIL]"
```

```text
[SAFE] 민감정보 2건 마스킹: GOOGLE_API_KEY 1, EMAIL 1
[SAFE] 전송량 제한: 파일 6개, 497줄 제외 (최대 10개 파일 / 200줄)
```

- safe-mode를 켜지 않아도 의심 패턴이 보이면 `[WARN] ... --safe-mode 사용을 권장합니다`라고 경고합니다.
- 마스킹은 정해진 패턴만 찾습니다. 모든 민감정보를 잡아내지는 못하므로, **비밀번호나 키가 담긴 파일은 애초에 커밋하지 마세요**(`.gitignore`, `.env` 활용).
- 전송량을 제한하면 AI가 변경의 일부만 보므로, 큰 변경에서는 요약이 덜 정확할 수 있습니다.

---

## 7. 비용 / 요청 횟수 제한

| 항목 | 내용 |
|---|---|
| 1회 실행당 호출 수 | **최대 2회** (첫 요청 1회 + 형식 위반 시 재요청 1회). 실행할 때마다 `[INFO] AI API 호출 횟수`로 표시 |
| 변경 없음 | AI를 호출하지 않고 종료 |
| 토큰 사용량 | 실행할 때마다 `입력 / 출력 / 추론` 토큰 수를 표시. 비용은 토큰 수에 비례 |
| 무료 티어 한도 | 모델별로 요청 수 제한이 있습니다. 개발 중 `gemini-2.5-flash` 무료 티어에서 **요청 20회 한도**를 넘어 `HTTP 429`가 발생했습니다. 최신 한도는 [공식 문서](https://ai.google.dev/gemini-api/docs/rate-limits)를 확인하세요 |

**권장 사용법**
- 큰 변경은 `--safe-mode`로 전송량을 줄이세요. 비용도 줄고 민감정보 노출도 막습니다.
- 변경을 작은 단위로 나눠 커밋하면 AI 입력도 작아지고 결과도 정확해집니다.
- `--thinking-budget`은 기본값 `0`(추론 끔)을 권장합니다. 추론을 켜면 추론 토큰도 `--max-tokens` 한도에 포함됩니다. 개발 중 입력이 약 3,000토큰일 때 추론이 1,962토큰을 써서 답변이 잘린 적이 있습니다.
- `HTTP 429`가 나면 안내된 시간만큼 기다린 뒤 다시 실행하세요.

---

## 8. 프로젝트 구조

```
codyssey-ai-gitgen/
├── main.py        CLI 진입점: 옵션 해석, 전체 흐름(수집 → 안전 처리 → AI → 검증 → 출력)
├── git_utils.py   git status / diff 수집, 저장소 루트 확인
├── safety.py      safe-mode: 마스킹, 전송량 제한
├── ai_client.py   Gemini REST API 호출(urllib), HTTP·네트워크 오류 처리
├── prompts.py     커밋/PR 프롬프트(역할·형식·금지·예시), 응답 JSON 스키마, 파싱
└── formatter.py   형식 검증, 후처리, 구분선 출력
```

## 9. 주의사항

- 생성된 커밋 메시지와 PR 본문은 **초안**입니다. 형식은 프로그램이 검사하지만 **내용이 맞는지는 사람이 확인**해야 합니다. 특히 safe-mode로 diff가 잘렸을 때는 일부 변경이 빠지거나 부정확하게 요약될 수 있습니다.
- `git diff` 원문은 외부 서버로 전송됩니다. 회사 코드 등 외부 전송이 허용되지 않는 코드에는 사용하지 마세요.
- 새 파일(untracked)은 `git add` 하기 전에는 diff에 내용이 없어 파일 이름만 전달됩니다.
