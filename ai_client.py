"""Gemini AI API 를 REST 방식으로 호출하는 모듈.

SDK 없이 표준 라이브러리(urllib)로 HTTP 요청을 직접 구성하고, 응답(JSON)을 해석하고,
실패 원인(키 없음·인증·요청 한도·서버·네트워크·타임아웃)을 사용자에게 알려준다.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

API_KEY_ENV = "GEMINI_API_KEY"
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_TIMEOUT = 60  # 초


class AIError(Exception):
    """AI API 호출 실패. 사용자에게 보여줄 원인(message)과 해결 힌트(hint)를 담는다."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


@dataclass
class AIResponse:
    text: str              # 생성된 텍스트
    finish_reason: str     # 생성이 끝난 이유 (STOP = 정상 완료, MAX_TOKENS = 길이 제한에 걸림 ...)
    prompt_tokens: int     # 보낸 입력의 토큰 수
    output_tokens: int     # 받은 답변의 토큰 수
    thinking_tokens: int   # 모델이 답하기 전 내부 추론에 쓴 토큰 수 (2.5 계열)


def get_api_key() -> str:
    """환경변수에서 API Key 를 읽는다. 코드에 키를 적지 않는다."""
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise AIError(
            f"{API_KEY_ENV} 환경변수가 설정되지 않았습니다.",
            f'예) export {API_KEY_ENV}="YOUR_KEY"',
        )
    return key


def supports_thinking_budget(model: str) -> bool:
    """thinkingBudget 설정은 gemini-2.5 계열 모델에서 쓰는 방식이다."""
    return model.startswith("gemini-2.5")


class GeminiClient:
    """Gemini generateContent 호출기. 호출 횟수(call_count)를 센다."""

    def __init__(
        self,
        model: str,
        temperature: float,
        max_tokens: int,
        thinking_budget: int | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        # 추론(thinking) 토큰 예산. 0 = 추론 끔, -1 = 모델이 자동 결정, None = 설정 안 보냄.
        # 추론 토큰도 max_tokens 한도에 포함되므로, 끄면 한도를 답변에 모두 쓸 수 있다.
        self.thinking_budget = thinking_budget if supports_thinking_budget(model) else None
        self.timeout = timeout
        self.api_key = get_api_key()
        self.call_count = 0

    def build_body(self, system: str, prompt: str, schema: dict | None) -> dict:
        """요청 본문(JSON) 구성: 지시문 + 사용자 입력 + 생성 파라미터."""
        generation_config: dict = {
            "temperature": self.temperature,
            "maxOutputTokens": self.max_tokens,
        }
        if schema is not None:
            # 답을 정해진 구조의 JSON 으로만 달라고 요청한다 → 프로그램이 파싱하기 쉽다.
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseSchema"] = schema
        if self.thinking_budget is not None:
            generation_config["thinkingConfig"] = {"thinkingBudget": self.thinking_budget}
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }

    def generate(self, system: str, prompt: str, schema: dict | None = None) -> AIResponse:
        """AI 에 요청을 보내고 응답을 AIResponse 로 돌려준다. 실패하면 AIError.

        schema: 답변 JSON 구조(OpenAPI 스키마). 주면 JSON 으로, 없으면 자유 텍스트로 답한다.
        """
        body = json.dumps(self.build_body(system, prompt, schema)).encode("utf-8")
        request = urllib.request.Request(
            API_URL.format(model=self.model),
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                # 키는 URL 이 아닌 헤더로 보낸다 (URL 은 로그·오류 메시지에 남기 쉽다).
                "x-goog-api-key": self.api_key,
            },
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
        return self._parse_response(data)

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
            detail = payload.get("error", {}).get("message", "")
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            pass
        code = error.code
        suffix = f" ({detail})" if detail else ""

        if code == 400 and "API key" in detail:
            return AIError(f"API Key 가 유효하지 않습니다 [HTTP {code}]{suffix}",
                           f"{API_KEY_ENV} 값을 확인하거나 키를 재발급하세요.")
        if code in (401, 403):
            return AIError(f"인증에 실패했습니다 [HTTP {code}]{suffix}",
                           f"{API_KEY_ENV} 값과 키 권한을 확인하세요.")
        if code == 404:
            return AIError(f"모델 '{self.model}' 을 찾을 수 없습니다 [HTTP {code}]{suffix}",
                           "--model 옵션의 모델 이름을 확인하세요. (예: --model gemini-2.5-flash)")
        if code == 429:
            return AIError(f"요청 한도를 초과했습니다 [HTTP {code}]{suffix}",
                           "잠시 후 다시 시도하세요. 무료 티어는 분당/일일 호출 수 제한이 있습니다.")
        if code >= 500:
            return AIError(f"AI 서버 오류입니다 [HTTP {code}]{suffix}",
                           "서버 측 문제입니다. 잠시 후 다시 시도하세요.")
        return AIError(f"AI API 요청이 거부되었습니다 [HTTP {code}]{suffix}",
                       "--temperature / --max-tokens 등 옵션 값을 확인하세요.")

    def _parse_response(self, data: dict) -> AIResponse:
        """응답 JSON 에서 생성된 텍스트와 사용량을 꺼낸다."""
        block_reason = data.get("promptFeedback", {}).get("blockReason")
        if block_reason:
            raise AIError(f"입력이 AI 안전 정책에 의해 차단되었습니다: {block_reason}")

        candidates = data.get("candidates") or []
        if not candidates:
            raise AIError("AI 응답에 생성 결과가 없습니다.")
        candidate = candidates[0]
        finish_reason = candidate.get("finishReason", "")
        parts = candidate.get("content", {}).get("parts", [])
        # thought=True 인 조각은 모델의 내부 추론 요약이므로 결과에서 뺀다.
        text = "".join(part.get("text", "") for part in parts if not part.get("thought"))

        if not text.strip():
            if finish_reason == "MAX_TOKENS":
                raise AIError(
                    f"답변이 길이 제한({self.max_tokens} 토큰)에 걸려 비어 있습니다.",
                    "--max-tokens 를 늘리거나 --thinking-budget 0 으로 추론을 끄세요. (추론 토큰도 한도에 포함됨)",
                )
            raise AIError(f"AI 가 빈 응답을 돌려주었습니다 (finishReason={finish_reason or '없음'}).")

        usage = data.get("usageMetadata", {})
        return AIResponse(
            text=text,
            finish_reason=finish_reason,
            prompt_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0),
            thinking_tokens=usage.get("thoughtsTokenCount", 0),
        )
