import asyncio

import structlog
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from bot.handlers.basic import help_command, start
from bot.handlers.health import health_done_command, health_status_command
from bot.handlers.query import birthday_command, upcoming_command
from bot.handlers.weight import weight_command
from bot.scheduler import create_scheduler, rebuild_upcoming_async
from shared.config import settings

log = structlog.get_logger()


_KOREAN_COMMANDS = {"/몸무게", "/다음일정", "/내생일", "/내건강검진", "/검진완료"}


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
    if command == "/몸무게":
        if not settings.weight_feature_enabled:
            await update.message.reply_text("몸무게 기능은 현재 꺼져 있습니다.")
            return
        await weight_command(update, context)
    elif command == "/다음일정":
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


def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )

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
