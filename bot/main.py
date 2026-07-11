import asyncio
import contextlib
import logging

import structlog
from structlog.typing import Processor
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from bot.handlers.basic import help_command, start
from bot.handlers.health import health_done_command, health_status_command
from bot.handlers.query import birthday_command, upcoming_command
from bot.scheduler import create_scheduler, rebuild_upcoming_async
from shared.config import settings

log = structlog.get_logger()


_KOREAN_COMMANDS = {"/다음일정", "/내생일", "/내건강검진", "/검진완료"}


async def _handle_korean_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """한글 명령어 라우팅."""
    if update.message is None or not update.message.text:
        return

    # 선행 공백을 허용하되 첫 토큰이 명령과 정확히 일치할 때만 처리한다.
    # 접두만 보는 startswith는 '/다음일정표' 같은 일반 텍스트를 명령으로 오탐한다 (audit #55).
    parts = update.message.text.split()
    command = parts[0] if parts else ""
    args = parts[1:]

    if command not in _KOREAN_COMMANDS:
        return

    # 메시지 원문 대신 매칭된 명령명·메타데이터만 로깅한다 (사적 대화 내용 로깅 제거, audit #76).
    log.info(
        "명령 수신",
        chat_id=update.message.chat_id,
        chat_type=update.message.chat.type,
        command=command,
    )

    context.args = args
    if command == "/다음일정":
        await upcoming_command(update, context)
    elif command == "/내생일":
        await birthday_command(update, context)
    elif command == "/내건강검진":
        await health_status_command(update, context)
    elif command == "/검진완료":
        await health_done_command(update, context)


async def _startup_rebuild(max_attempts: int = 5) -> None:
    """시작 시 예정 알림 rebuild. DB가 아직 안 떠 있으면 지수 백오프로 몇 회 재시도한다.

    끝내 실패해도 예외를 전파하지 않고 로깅만 한다 — polling은 rebuild 없이도 동작하고
    03시 cron rebuild가 보충하므로, DB 미기동으로 봇 프로세스가 크래시 루프에 빠지는 것을
    막는다 (audit #36).
    """
    delay = 1.0
    for attempt in range(1, max_attempts + 1):
        try:
            await rebuild_upcoming_async()
            return
        except Exception:
            if attempt >= max_attempts:
                log.exception("시작 시 rebuild 실패 — polling은 계속 진행", attempts=attempt)
                return
            log.warning("시작 시 rebuild 실패 — 재시도", attempt=attempt, retry_in=delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)


def _configure_logging() -> None:
    """structlog와 stdlib logging을 하나의 JSON 파이프라인으로 합친다.

    봇 핸들러는 structlog로 직접 로깅하지만 PTB·APScheduler·httpx는 stdlib logging을
    쓴다. ProcessorFormatter로 두 경로를 같은 프로세서 체인에 태워, 내부 에러/경고가
    stderr raw로 새지 않고 기존과 동일한 JSON 형식으로 나가게 한다(journald 정합).

    httpx는 요청 URL(봇 토큰 포함)을 INFO로 남기고 polling의 getUpdates가 이를 매
    호출 찍으므로, 토큰 유출·로그 폭주를 막기 위해 WARNING으로 낮춘다(notifier의
    토큰 마스킹과 같은 취지, audit #10).
    """
    # structlog·stdlib 양쪽 레코드에 동일하게 적용할 프로세서(기존 timestamp/level 유지).
    # format_exc_info는 예외 스택이 살아 있는 로깅 시점에 실행돼야 하므로 이 체인에 둔다.
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            # 최종 렌더링은 stdlib 핸들러의 ProcessorFormatter에 위임한다.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # structlog을 거치지 않은 외부(stdlib) 레코드에만 적용하는 사전 체인.
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # 재호출·중복 import로 핸들러가 누적되지 않게 초기화 후 단일 핸들러만 둔다.
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    # 토큰이 담긴 httpx 요청 URL INFO 로그가 매 getUpdates마다 쌓이는 것을 막는다.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """핸들러/PTB 내부 예외 처리기.

    예외 타입명과 메시지 요약만 구조화 로깅한다. update 본문·멤버/사용자 이름·chat
    내용은 로깅하지 않는다(journald PII 방지 — 이번 하드닝의 핵심 원칙). effective_message가
    있으면 사용자에게 일반 안내 1줄을 회신하고, 회신 실패는 삼킨다.
    """
    err = context.error
    fields: dict[str, object] = {
        "error_type": type(err).__name__ if err is not None else "Unknown",
        "error": str(err)[:200] if err is not None else "",
    }
    # chat_id는 내용이 아닌 대리 식별자라 로깅 허용(_handle_korean_command과 동일 관례).
    chat = getattr(update, "effective_chat", None)
    if chat is not None:
        fields["chat_id"] = getattr(chat, "id", None)
    log.error("핸들러 처리 중 오류", **fields)

    message = getattr(update, "effective_message", None)
    if message is not None:
        # 안내 회신마저 실패하면 조용히 삼킨다(2차 예외로 재귀 호출되지 않도록).
        with contextlib.suppress(Exception):
            await message.reply_text("요청 처리 중 문제가 발생했어요. 잠시 후 다시 시도해주세요.")


def main() -> None:
    _configure_logging()

    # 필수 설정(토큰/그룹ID/tz) 검증 — 누락·오류면 즉시 중단 (audit #19, #75).
    settings.validate_runtime()

    scheduler = create_scheduler()

    app = Application.builder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    # 한글 명령어 처리 (MessageHandler 사용)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            _handle_korean_command,
        )
    )
    # 핸들러 예외를 삼켜 사용자 무응답이 되거나 PTB 내부 에러가 JSON 파이프라인 밖으로
    # 새지 않도록 에러 핸들러를 등록한다.
    app.add_error_handler(_on_error)

    async def on_startup(app: Application) -> None:  # type: ignore[type-arg]
        scheduler.start()
        # 시작 시 1회 rebuild — DB에 예정 알림 채우기 (DB 미기동 시 재시도, audit #36)
        await _startup_rebuild()
        # 한글 슬래시 명령은 텔레그램 bot_command 엔티티가 아니라 순수 텍스트로 처리되므로,
        # 그룹에서 봇 privacy mode가 켜져 있으면(BotFather 기본값) 봇에 전달되지 않는다.
        # 그룹 명령을 쓰려면 BotFather에서 /setprivacy Disable 필요 (README 참고, audit #23).
        log.warning(
            "그룹에서 한글 명령이 동작하려면 BotFather에서 privacy mode를 꺼야 합니다 "
            "(/setprivacy Disable). DM에서는 privacy와 무관하게 동작합니다."
        )
        log.info("KinKeeper 봇 시작", mode="polling")

    async def on_shutdown(app: Application) -> None:  # type: ignore[type-arg]
        scheduler.shutdown(wait=False)
        log.info("KinKeeper 봇 종료")

    app.post_init = on_startup
    app.post_shutdown = on_shutdown

    log.info("polling 시작")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
