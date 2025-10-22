import logging
import requests
from urllib.parse import urlparse, urlunparse
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
import threading
import time

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# In-memory storage for user domains and links
user_domains = {}
user_links = {}

# Default polling interval (seconds)
REFRESH_INTERVAL = 30

def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "Welcome! Send me a short link and I will replace its domain with your custom domain.\n"
        "Use /setdomain <your_domain> to set your custom domain."
    )

def set_domain(update: Update, context: CallbackContext):
    if len(context.args) != 1:
        update.message.reply_text("Usage: /setdomain <your_custom_domain>")
        return
    domain = context.args[0].rstrip('/')  # remove trailing slash
    user_domains[update.message.chat_id] = domain
    update.message.reply_text(f"Custom domain set to: {domain}")

def replace_domain(original_url: str, new_domain: str) -> str:
    parsed = urlparse(original_url)
    # Replace netloc with new domain
    parsed = parsed._replace(netloc=urlparse(new_domain).netloc, scheme=urlparse(new_domain).scheme)
    return urlunparse(parsed)

def process_link(chat_id: int, link: str, context: CallbackContext):
    try:
        # Follow redirect
        response = requests.get(link, allow_redirects=True, timeout=10)
        final_url = response.url

        # Get user domain
        domain = user_domains.get(chat_id, "https://mtc1.ctyas.com")
        modified_url = replace_domain(final_url, domain)

        context.bot.send_message(chat_id=chat_id, text=f"Modified link: {modified_url}")
    except Exception as e:
        logger.error(f"Error processing link: {e}")
        context.bot.send_message(chat_id=chat_id, text=f"Failed to process link: {e}")

def handle_message(update: Update, context: CallbackContext):
    link = update.message.text.strip()
    chat_id = update.message.chat_id
    user_links[chat_id] = link
    update.message.reply_text(f"Processing your link every {REFRESH_INTERVAL} seconds...")

    # Start a background thread to send modified links every 30 seconds
    def repeat_send():
        while chat_id in user_links:
            process_link(chat_id, link, context)
            time.sleep(REFRESH_INTERVAL)

    thread = threading.Thread(target=repeat_send, daemon=True)
    thread.start()

def stop_links(update: Update, context: CallbackContext):
    chat_id = update.message.chat_id
    if chat_id in user_links:
        del user_links[chat_id]
        update.message.reply_text("Stopped sending links.")
    else:
        update.message.reply_text("No active link sending found.")

def main():
    TOKEN = "8479816021:AAGuvc_auuT4iYFn2vle0xVk-t2bswey8k8"
    updater = Updater(TOKEN)

    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("setdomain", set_domain))
    dp.add_handler(CommandHandler("stop", stop_links))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
