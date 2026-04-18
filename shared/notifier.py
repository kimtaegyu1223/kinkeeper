import httpx
import structlog

from shared.config import settings

log = structlog.get_logger()


async def send_message(chat_id: int, text: str) -> bool:
    """텔레그램 메시지 발송. 성공 시 True, 실패 시 False."""
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return True
    except Exception as e:
        log.error("텔레그램 발송 실패", chat_id=chat_id, error=str(e))
        return False
