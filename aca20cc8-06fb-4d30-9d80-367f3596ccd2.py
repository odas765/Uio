import asyncio
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# === CONFIG ===
BOT_TOKEN = "8479816021:AAGtLR2LFUnqCohfqLxsgU1x66dDYQu8EhE"

# Store running tasks per chat
running_tasks = {}


def expand_url(short_url: str) -> str:
    """Expand a short URL to its final destination."""
    try:
        r = requests.head(short_url, allow_redirects=True, timeout=10)
        return r.url
    except Exception:
        return short_url


def rewrite_url(expanded_url: str) -> str:
    """
    Replace the expanded URL prefix (domain + '?adlinkfly=') 
    with 'https://shortxlinks.com/'.
    """
    try:
        if "?adlinkfly=" in expanded_url:
            _, after = expanded_url.split("?adlinkfly=", 1)
            return "https://shortxlinks.com/" + after
        else:
            return expanded_url
    except Exception:
        return expanded_url


async def updater_loop(chat_id: int, short_url: str, context: ContextTypes.DEFAULT_TYPE, sent_message_id: int):
    """Loop that keeps updating message every 10s until stopped."""
    while True:
        expanded = expand_url(short_url)
        new_link = rewrite_url(expanded)

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=sent_message_id,
                text=f"`{new_link}`",  # mono format
                parse_mode="Markdown"
            )
        except Exception:
            pass  # ignore edit errors

        await asyncio.sleep(10)


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming short link messages."""
    chat_id = update.effective_chat.id
    short_url = update.message.text.strip()

    # Cancel any existing task for this chat
    if chat_id in running_tasks:
        running_tasks[chat_id].cancel()

    # Send initial message
    sent_message = await context.bot.send_message(chat_id, "Processing your link...")

    # Start updater loop as a task
    task = asyncio.create_task(updater_loop(chat_id, short_url, context, sent_message.message_id))
    running_tasks[chat_id] = task


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop the updater loop for this chat."""
    chat_id = update.effective_chat.id
    if chat_id in running_tasks:
        running_tasks[chat_id].cancel()
        del running_tasks[chat_id]
        await update.message.reply_text("⏹️ Stopped updating the link.")
    else:
        await update.message.reply_text("No active updating process to stop.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send me a short link (e.g., https://shortxlinks.com/XXXX)\n"
        "I'll expand, rewrite, and refresh it every 10s forever.\n\n"
        "Commands:\n"
        "• /stop → stop updating the link"
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))

    app.run_polling()


if __name__ == "__main__":
    main()
