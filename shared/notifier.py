"""텔레그램 sendMessage 래퍼 — 재시도·토큰 마스킹.

봇·웹의 모든 발송이 거치는 단일 전송 지점. 일시 오류(타임아웃·5xx·429)는 재시도하고,
반환·로깅되는 에러 문자열에서 봇 토큰을 마스킹한다 (audit #8, #10).
"""

import asyncio
import re

import httpx
import structlog

from shared.config import settings

log = structlog.get_logger()

# 일시 오류(타임아웃/네트워크 순단/5xx/429)에 대한 재시도 설정.
# 모듈 상수로 두어 테스트에서 조정·monkeypatch할 수 있게 한다 (audit #8).
_TIMEOUT = 10.0
_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 1.0  # 초. 지수 백오프: 1s, 2s
_RETRY_AFTER_CAP = 60.0  # 429 Retry-After가 과도할 때 dispatch 루프가 막히지 않도록 상한

# https://api.telegram.org/bot<TOKEN>/... 형태의 URL에서 토큰 부분을 마스킹한다.
_TOKEN_URL_RE = re.compile(r"/bot\d+:[A-Za-z0-9_-]+")


def _mask_token(text: str) -> str:
    """예외/URL 문자열에 노출된 봇 토큰을 마스킹한다 (audit #10).

    httpx.HTTPStatusError 등의 문자열에는 요청 URL 전체(토큰 포함)가 담기므로,
    error 컬럼·로그에 남기기 전에 반드시 마스킹한다.
    """
    if settings.telegram_bot_token:
        text = text.replace(settings.telegram_bot_token, "***")
    return _TOKEN_URL_RE.sub("/bot***", text)


def _parse_retry_after(resp: httpx.Response) -> float:
    """429 응답에서 대기 시간(초)을 추출한다.

    텔레그램은 body의 parameters.retry_after로 알려준다. 표준 Retry-After 헤더도 확인.
    과도한 값은 dispatch 루프 지연을 막기 위해 상한을 둔다.
    """
    try:
        retry_after = resp.json().get("parameters", {}).get("retry_after")
        if retry_after is not None:
            return min(float(retry_after), _RETRY_AFTER_CAP)
    except (ValueError, AttributeError):
        pass
    header = resp.headers.get("retry-after")
    if header:
        try:
            return min(float(header), _RETRY_AFTER_CAP)
        except ValueError:
            pass
    return _BACKOFF_BASE


def _extract_description(resp: httpx.Response) -> str:
    """텔레그램 에러 응답의 description을 추출한다 (토큰 미포함이라 마스킹 불필요)."""
    try:
        desc = resp.json().get("description")
        if desc:
            return str(desc)[:200]
    except (ValueError, AttributeError):
        pass
    return f"HTTP {resp.status_code}"


async def send_message(chat_id: int, text: str) -> tuple[bool, str | None]:
    """텔레그램 메시지 발송.

    (성공여부, 에러 상세) 반환. 일시 오류(타임아웃·네트워크 순단·5xx)는 지수
    백오프로 최대 _MAX_ATTEMPTS회 재시도하고, 429는 Retry-After를 존중해 대기 후
    재시도한다. 그 외 4xx(400 chat not found·403 봇 차단 등)는 영구 오류로 보고
    재시도하지 않는다. 반환하는 에러 문자열의 봇 토큰은 마스킹한다 (audit #8, #10).
    """
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}

    last_error: str | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                return True, None
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == 429:
                retry_after = _parse_retry_after(e.response)
                last_error = f"429 Too Many Requests (retry_after={retry_after:g}s)"
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(retry_after)
                    continue
            elif 500 <= status < 600:
                last_error = f"{status} {_extract_description(e.response)}"
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_BACKOFF_BASE * 2 ** (attempt - 1))
                    continue
            else:
                # 4xx 영구 오류 — 재시도해도 동일하게 실패하므로 즉시 중단한다.
                last_error = _mask_token(f"{status} {_extract_description(e.response)}")
                break
            # 429/5xx 재시도 횟수 소진
            break
        except httpx.HTTPError as e:
            # 타임아웃·연결 실패 등 일시 오류로 간주하고 재시도한다.
            last_error = _mask_token(f"{type(e).__name__}: {e}")
            if attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(_BACKOFF_BASE * 2 ** (attempt - 1))
                continue
            break

    log.error("텔레그램 발송 실패", chat_id=chat_id, error=last_error)
    return False, last_error
