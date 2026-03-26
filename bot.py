from __future__ import annotations

import logging
from datetime import date

from telegram import Update
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


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    await update.message.reply_text(  # type: ignore[union-attr]
        "Hey! Send me photos of your food throughout the day.\n"
        "Add a caption to describe what you're eating.\n\n"
        "When you're ready, send /summary to get a Telegraph article "
        "with all your food photos for today.\n\n"
        "Commands:\n"
        "/summary - Generate today's food diary article\n"
        "/count - How many entries today\n"
        "/cancel - Clear today's entries",
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
    await update.message.reply_text(
        f"Saved! ({len(entries)} entr{'y' if len(entries) == 1 else 'ies'} today)"
    )


async def count_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show count of today's entries."""
    if not update.message:
        return

    storage: Storage = context.bot_data["storage"]
    user_id = update.effective_user.id  # type: ignore[union-attr]
    entries = storage.get_today_entries(user_id)

    if not entries:
        await update.message.reply_text("No food entries today. Send me some photos!")
    else:
        await update.message.reply_text(
            f"You have {len(entries)} entr{'y' if len(entries) == 1 else 'ies'} today."
        )


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
            "No food entries today. Send me some photos first!"
        )
        return

    progress = await update.message.reply_text(
        f"Creating article from {len(entries)} entries... Uploading photos..."
    )

    try:
        # Download and upload each photo to Telegraph
        image_entries: list[tuple[str, str | None]] = []

        for entry in entries:
            # Download photo from Telegram
            file = await context.bot.get_file(entry.photo_file_id)
            photo_bytes = await file.download_as_bytearray()

            # Upload to Telegraph
            image_url = await telegraph.upload_image(
                bytes(photo_bytes),
                filename=f"food_{entry.id}.jpg",
            )
            image_entries.append((image_url, entry.caption))

        # Build article content
        content = build_article_content(image_entries)

        # Create Telegraph page
        today = date.today()
        title = f"Food Diary - {today.strftime('%B %d, %Y')}"
        url = await telegraph.create_page(title, content)

        await progress.edit_text(f"Your food diary for today:\n{url}")

    except Exception:
        logger.exception("Failed to create summary")
        await progress.edit_text(
            "Sorry, something went wrong creating the article. Try again later."
        )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear today's entries."""
    if not update.message:
        return

    storage: Storage = context.bot_data["storage"]
    user_id = update.effective_user.id  # type: ignore[union-attr]
    count = storage.clear_today_entries(user_id)

    if count == 0:
        await update.message.reply_text("Nothing to clear — no entries today.")
    else:
        await update.message.reply_text(
            f"Cleared {count} entr{'y' if count == 1 else 'ies'} for today."
        )


async def post_init(application: Application) -> None:  # type: ignore[type-arg]
    """Initialize shared resources after app starts."""
    config: Config = application.bot_data["config"]

    storage = Storage(config.db_path)
    storage.init()
    application.bot_data["storage"] = storage

    telegraph = TelegraphClient(
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
