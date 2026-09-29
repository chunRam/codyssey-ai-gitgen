"""AI API 를 REST 방식으로 호출하는 모듈. (Gemini, OpenRouter 지원)

SDK 없이 표준 라이브러리(urllib)로 HTTP 요청을 직접 구성하고, 응답(JSON)을 해석하고,
실패 원인(키 없음·인증·요청 한도·서버·네트워크·타임아웃)을 사용자에게 알려준다.

공통 흐름(HTTP 전송·네트워크 오류 처리)은 BaseClient 에 두고,
공급자마다 다른 부분(요청 형식·응답 해석·HTTP 오류 안내)만 하위 클래스에서 구현한다.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_TIMEOUT = 60  # 초
AUTO_PROVIDER = "auto"


class AIError(Exception):
    """AI API 호출 실패. 사용자에게 보여줄 원인(message)과 해결 힌트(hint)를 담는다."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


@dataclass
class AIResponse:
    text: str              # 생성된 텍스트
    finish_reason: str     # 생성이 끝난 이유 (공급자 원문 값: STOP, stop, MAX_TOKENS, length ...)
    truncated: bool        # 길이 제한(max_tokens)에 걸려 답변이 잘렸는지
    prompt_tokens: int     # 보낸 입력의 토큰 수
    output_tokens: int     # 받은 답변의 토큰 수
    thinking_tokens: int   # 모델이 답하기 전 내부 추론에 쓴 토큰 수


class BaseClient:
    """공급자 공통 호출기. 호출 횟수(call_count)를 센다."""

    provider = ""       # 공급자 이름 (--provider 값)
    key_env = ""        # API Key 를 읽을 환경변수 이름
    default_model = ""  # --model 을 안 줬을 때 쓸 모델

    def __init__(
        self,
        model: str | None,
        temperature: float,
        max_tokens: int,
        thinking_budget: int | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.model = model or self.default_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        # 추론(thinking) 토큰 예산. 0 = 추론 끔, -1 = 모델이 자동 결정, None = 설정 안 보냄.
        # 추론 토큰도 max_tokens 한도에 포함되므로, 끄면 한도를 답변에 모두 쓸 수 있다.
        self.thinking_budget = thinking_budget
        self.timeout = timeout
        self.api_key = self.get_api_key()
        self.call_count = 0

    @classmethod
    def get_api_key(cls) -> str:
        """환경변수에서 API Key 를 읽는다. 코드에 키를 적지 않는다."""
        key = os.environ.get(cls.key_env, "").strip()
        if not key:
            raise AIError(
                f"{cls.key_env} 환경변수가 설정되지 않았습니다.",
                f'예) export {cls.key_env}="YOUR_KEY"',
            )
        return key

    # ── 공급자별로 구현하는 부분 ──
    def build_request(self, system: str, prompt: str, schema: dict | None) -> tuple[str, dict, dict]:
        """(URL, 헤더, 본문) 을 돌려준다."""
        raise NotImplementedError

    def parse_response(self, data: dict) -> AIResponse:
        raise NotImplementedError

    def http_error(self, code: int, detail: str) -> AIError | None:
        """공급자 고유의 HTTP 오류 안내. 없으면 None → 공통 안내를 쓴다."""
        return None

    # ── 공통 흐름 ──
    def generate(self, system: str, prompt: str, schema: dict | None = None) -> AIResponse:
        """AI 에 요청을 보내고 응답을 AIResponse 로 돌려준다. 실패하면 AIError.

        schema: 답변 JSON 구조. 주면 JSON 으로, 없으면 자유 텍스트로 답한다.
        """
        url, headers, body = self.build_request(system, prompt, schema)
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", **headers},
        )
        self.call_count += 1
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise self._http_error(error)
        except (TimeoutError, socket.timeout):
            raise self._timeout_error()
        except urllib.error.URLError as error:
            # 연결 단계에서 시간이 초과되면 urllib 이 타임아웃을 URLError 로 감싸서 던진다.
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise self._timeout_error()
            raise AIError(
                f"네트워크 오류로 AI 서버에 연결하지 못했습니다: {error.reason}",
                "인터넷 연결을 확인하세요.",
            )
        except json.JSONDecodeError:
            raise AIError("AI 서버 응답을 해석할 수 없습니다 (JSON 아님).")
        return self.parse_response(data)

    def empty_answer_error(self, truncated: bool, finish_reason: str) -> AIError:
        if truncated:
            return AIError(
                f"답변이 길이 제한({self.max_tokens} 토큰)에 걸려 비어 있습니다.",
                "--max-tokens 를 늘리거나 --thinking-budget 0 으로 추론을 끄세요. (추론 토큰도 한도에 포함됨)",
            )
        return AIError(f"AI 가 빈 응답을 돌려주었습니다 (finish_reason={finish_reason or '없음'}).")

    def _timeout_error(self) -> AIError:
        return AIError(
            f"응답 대기 시간({self.timeout}초)을 초과했습니다.",
            "잠시 후 다시 시도하거나 --safe-mode 로 전송량을 줄이세요.",
        )

    def _http_error(self, error: urllib.error.HTTPError) -> AIError:
        """HTTP 오류 코드별로 원인과 해결 힌트를 정리한다."""
        detail = ""
        try:
            payload = json.loads(error.read().decode("utf-8"))
            detail = str(payload.get("error", {}).get("message", ""))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError, AttributeError):
            pass
        code = error.code
        specific = self.http_error(code, detail)
        if specific is not None:
            return specific

        suffix = f" ({detail})" if detail else ""
        if code in (401, 403):
            return AIError(f"인증에 실패했습니다 [HTTP {code}]{suffix}",
                           f"{self.key_env} 값과 키 권한을 확인하세요.")
        if code == 404:
            return AIError(f"모델 '{self.model}' 을 찾을 수 없습니다 [HTTP {code}]{suffix}",
                           f"--model 옵션의 모델 이름을 확인하세요. (예: --model {self.default_model})")
        if code == 429:
            return AIError(f"요청 한도를 초과했습니다 [HTTP {code}]{suffix}",
                           "잠시 후 다시 시도하세요. 무료 사용량은 분당/일일 호출 수 제한이 있습니다.")
        if code >= 500:
            return AIError(f"AI 서버 오류입니다 [HTTP {code}]{suffix}",
                           "서버 측 문제입니다. 잠시 후 다시 시도하세요.")
        return AIError(f"AI API 요청이 거부되었습니다 [HTTP {code}]{suffix}",
                       "--model / --temperature / --max-tokens 등 옵션 값을 확인하세요.")


# ──────────────────────────────────────────────────────────────
# Google Gemini
# ──────────────────────────────────────────────────────────────


class GeminiClient(BaseClient):
    provider = "gemini"
    key_env = "GEMINI_API_KEY"
    default_model = "gemini-2.5-flash"
    api_url = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def build_request(self, system, prompt, schema):
        generation_config: dict = {
            "temperature": self.temperature,
            "maxOutputTokens": self.max_tokens,
        }
        if schema is not None:
            # 답을 정해진 구조의 JSON 으로만 달라고 요청한다 → 프로그램이 파싱하기 쉽다.
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseSchema"] = schema
        # thinkingBudget 설정은 gemini-2.5 계열 모델에서 쓰는 방식이다.
        if self.thinking_budget is not None and self.model.startswith("gemini-2.5"):
            generation_config["thinkingConfig"] = {"thinkingBudget": self.thinking_budget}
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }
        # 키는 URL 이 아닌 헤더로 보낸다 (URL 은 로그·오류 메시지에 남기 쉽다).
        headers = {"x-goog-api-key": self.api_key}
        return self.api_url.format(model=self.model), headers, body

    def http_error(self, code, detail):
        if code == 400 and "API key" in detail:
            return AIError(f"API Key 가 유효하지 않습니다 [HTTP {code}] ({detail})",
                           f"{self.key_env} 값을 확인하거나 키를 재발급하세요.")
        return None

    def parse_response(self, data):
        block_reason = data.get("promptFeedback", {}).get("blockReason")
        if block_reason:
            raise AIError(f"입력이 AI 안전 정책에 의해 차단되었습니다: {block_reason}")

        candidates = data.get("candidates") or []
        if not candidates:
            raise AIError("AI 응답에 생성 결과가 없습니다.")
        candidate = candidates[0]
        finish_reason = candidate.get("finishReason", "")
        truncated = finish_reason == "MAX_TOKENS"
        parts = candidate.get("content", {}).get("parts", [])
        # thought=True 인 조각은 모델의 내부 추론 요약이므로 결과에서 뺀다.
        text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        if not text.strip():
            raise self.empty_answer_error(truncated, finish_reason)

        usage = data.get("usageMetadata", {})
        return AIResponse(
            text=text,
            finish_reason=finish_reason,
            truncated=truncated,
            prompt_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0),
            thinking_tokens=usage.get("thoughtsTokenCount", 0),
        )


# ──────────────────────────────────────────────────────────────
# OpenRouter (여러 회사 모델을 OpenAI 호환 형식 하나로 호출)
# ──────────────────────────────────────────────────────────────


def to_json_schema(schema: dict) -> dict:
    """Gemini 형식 스키마(type: "OBJECT")를 표준 JSON Schema(type: "object")로 바꾼다."""
    converted: dict = {}
    for key, value in schema.items():
        if key == "type" and isinstance(value, str):
            converted[key] = value.lower()
        elif key == "properties":
            converted[key] = {name: to_json_schema(prop) for name, prop in value.items()}
        elif key == "items":
            converted[key] = to_json_schema(value)
        else:
            converted[key] = value
    if converted.get("type") == "object":
        # strict 모드에서는 정의하지 않은 칸을 허용하지 않는다고 명시해야 한다.
        converted["additionalProperties"] = False
    return converted


class OpenRouterClient(BaseClient):
    provider = "openrouter"
    key_env = "OPENROUTER_API_KEY"
    default_model = "google/gemini-2.5-flash"
    api_url = "https://openrouter.ai/api/v1/chat/completions"

    def build_request(self, system, prompt, schema):
        body: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if schema is not None:
            # 답을 정해진 구조의 JSON 으로만 달라고 요청한다.
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "draft", "strict": True, "schema": to_json_schema(schema)},
            }
        if self.thinking_budget is not None:
            # OpenRouter 공통 추론 설정. 추론을 지원하지 않는 모델에서는 무시된다.
            if self.thinking_budget == 0:
                body["reasoning"] = {"enabled": False}
            elif self.thinking_budget == -1:
                body["reasoning"] = {"enabled": True}
            else:
                body["reasoning"] = {"max_tokens": self.thinking_budget}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Title": "codyssey-ai-gitgen",  # OpenRouter 사용 기록에 표시될 앱 이름 (선택)
        }
        return self.api_url, headers, body

    def http_error(self, code, detail):
        suffix = f" ({detail})" if detail else ""
        if code == 401:
            return AIError(f"API Key 가 유효하지 않습니다 [HTTP {code}]{suffix}",
                           f"{self.key_env} 값을 확인하거나 키를 재발급하세요.")
        if code == 402:
            return AIError(f"OpenRouter 크레딧이 부족합니다 [HTTP {code}]{suffix}",
                           "https://openrouter.ai/settings/credits 에서 크레딧을 충전하거나 "
                           "무료 모델(이름이 ':free' 로 끝나는 모델)을 --model 로 지정하세요.")
        if code == 400 and "model" in detail.lower():
            return AIError(f"모델 '{self.model}' 을 사용할 수 없습니다 [HTTP {code}]{suffix}",
                           f"--model 에 'google/gemini-2.5-flash' 처럼 '회사/모델' 형식의 ID 를 쓰세요. "
                           "목록: https://openrouter.ai/models")
        return None

    def parse_response(self, data):
        # OpenRouter 는 상위 공급자의 오류를 HTTP 200 + error 객체로 돌려주기도 한다.
        if isinstance(data.get("error"), dict):
            error = data["error"]
            raise AIError(f"AI 공급자 오류입니다 (code={error.get('code')}): {error.get('message', '')}",
                          "잠시 후 다시 시도하거나 --model 로 다른 모델을 지정하세요.")

        choices = data.get("choices") or []
        if not choices:
            raise AIError("AI 응답에 생성 결과가 없습니다.")
        choice = choices[0]
        finish_reason = choice.get("finish_reason") or ""
        truncated = finish_reason == "length"
        text = (choice.get("message") or {}).get("content") or ""
        if not text.strip():
            raise self.empty_answer_error(truncated, finish_reason)

        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        return AIResponse(
            text=text,
            finish_reason=finish_reason,
            truncated=truncated,
            prompt_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            thinking_tokens=details.get("reasoning_tokens") or 0,
        )


# ──────────────────────────────────────────────────────────────
# 공급자 선택
# ──────────────────────────────────────────────────────────────

PROVIDERS: dict[str, type[BaseClient]] = {
    GeminiClient.provider: GeminiClient,
    OpenRouterClient.provider: OpenRouterClient,
}


def resolve_provider(name: str) -> type[BaseClient]:
    """--provider 값으로 호출기 클래스를 고른다. auto 면 키가 설정된 공급자를 순서대로 찾는다."""
    if name != AUTO_PROVIDER:
        return PROVIDERS[name]
    for client_class in PROVIDERS.values():
        if os.environ.get(client_class.key_env, "").strip():
            return client_class
    envs = " 또는 ".join(c.key_env for c in PROVIDERS.values())
    raise AIError(
        f"API Key 환경변수가 설정되지 않았습니다. ({envs})",
        '예) export GEMINI_API_KEY="YOUR_KEY"  또는  export OPENROUTER_API_KEY="YOUR_KEY"',
    )


def create_client(
    provider: str,
    model: str | None,
    temperature: float,
    max_tokens: int,
    thinking_budget: int | None = None,
) -> BaseClient:
    client_class = resolve_provider(provider)
    return client_class(model, temperature, max_tokens, thinking_budget)
