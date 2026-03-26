from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

EKB_TZ = ZoneInfo("Asia/Yekaterinburg")

from telegram import LinkPreviewOptions, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import Config
from storage import Storage
from telegraph_api import TelegraphClient, build_article_content

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG,
)
logger = logging.getLogger(__name__)


MONTHS_RU = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def format_date_ru(d: datetime) -> str:
    return f"{d.day} {MONTHS_RU[d.month]} {d.year}"


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(  # type: ignore[union-attr]
        "Привет! Отправляй мне фото еды в течение дня.\n"
        "Можешь добавить подпись к фото.\n\n"
        "Когда будешь готов — отправь /summary, и я создам "
        "статью на Telegraph со всеми фото за сегодня.\n\n"
        "Команды:\n"
        "/summary — Создать статью за сегодня\n"
        "/count — Сколько записей сегодня\n"
        "/cancel — Очистить записи за сегодня",
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save incoming photo with optional caption."""
    if not update.message or not update.message.photo:
        return

    storage: Storage = context.bot_data["storage"]
    user_id = update.effective_user.id  # type: ignore[union-attr]

    # Get the largest photo (best quality)
    photo = update.message.photo[-1]
    caption = update.message.caption

    storage.save_entry(user_id, photo.file_id, caption)

    entries = storage.get_today_entries(user_id)
    await update.message.reply_text(f"Сохранено! ({len(entries)} за сегодня)")


async def count_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show count of today's entries."""
    if not update.message:
        return

    storage: Storage = context.bot_data["storage"]
    user_id = update.effective_user.id  # type: ignore[union-attr]
    entries = storage.get_today_entries(user_id)

    if not entries:
        await update.message.reply_text("Сегодня записей нет. Отправь мне фото!")
    else:
        await update.message.reply_text(f"Сегодня записей: {len(entries)}")


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate Telegraph article from today's entries."""
    if not update.message:
        return

    storage: Storage = context.bot_data["storage"]
    telegraph: TelegraphClient = context.bot_data["telegraph"]
    user_id = update.effective_user.id  # type: ignore[union-attr]

    entries = storage.get_today_entries(user_id)

    if not entries:
        await update.message.reply_text(
            "Сегодня записей нет. Сначала отправь фото!"
        )
        return

    progress = await update.message.reply_text(
        f"Создаю статью из {len(entries)} записей... Загружаю фото..."
    )

    try:
        image_entries: list[tuple[str, str | None, str]] = []

        for entry in entries:
            file = await context.bot.get_file(entry.photo_file_id)
            photo_bytes = await file.download_as_bytearray()

            image_url = await telegraph.upload_image(
                bytes(photo_bytes),
                filename=f"food_{entry.id}.jpg",
            )
            time_str = entry.created_at.strftime("%H:%M")
            image_entries.append((image_url, entry.caption, time_str))

        placeholder_url = await telegraph.get_placeholder_url()
        content = build_article_content(image_entries, placeholder_url)

        today = datetime.now(EKB_TZ)
        title = f"Дневник питания — {format_date_ru(today)}"
        url = await telegraph.create_page(title, content)

        await progress.delete()
        await update.message.reply_text(
            f"Твой дневник питания за сегодня:\n{url}",
            link_preview_options=LinkPreviewOptions(
                url=url,
                prefer_large_media=True,
                show_above_text=True,
            ),
        )

    except Exception:
        logger.exception("Failed to create summary")
        await progress.edit_text(
            "Что-то пошло не так при создании статьи. Попробуй позже."
        )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear today's entries."""
    if not update.message:
        return

    storage: Storage = context.bot_data["storage"]
    user_id = update.effective_user.id  # type: ignore[union-attr]
    count = storage.clear_today_entries(user_id)

    if count == 0:
        await update.message.reply_text("Нечего удалять — записей за сегодня нет.")
    else:
        await update.message.reply_text(f"Удалено записей: {count}")


async def post_init(application: Application) -> None:  # type: ignore[type-arg]
    """Initialize shared resources after app starts."""
    config: Config = application.bot_data["config"]

    storage = Storage(config.db_path)
    storage.init()
    application.bot_data["storage"] = storage

    telegraph = TelegraphClient(
        imgbb_api_key=config.imgbb_api_key,
        author_name=config.telegraph_author_name,
        author_url=config.telegraph_author_url,
    )
    # Account created lazily on first /summary call
    application.bot_data["telegraph"] = telegraph

    logger.info("Bot initialized. Storage: %s", config.db_path)


async def post_shutdown(application: Application) -> None:  # type: ignore[type-arg]
    """Clean up resources on shutdown."""
    storage: Storage | None = application.bot_data.get("storage")
    if storage:
        storage.close()

    telegraph: TelegraphClient | None = application.bot_data.get("telegraph")
    if telegraph:
        await telegraph.close()

    logger.info("Bot shut down cleanly.")


def main() -> None:
    config = Config.from_env()

    app = (
        Application.builder()
        .token(config.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.bot_data["config"] = config

    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("summary", summary_command))
    app.add_handler(CommandHandler("count", count_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("Starting bot...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
