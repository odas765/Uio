import telebot
import os
import requests
import subprocess
import shlex
import json

BOT_TOKEN = "7644200147:AAFqgxEEozss3p3L593Ujd3rWlIDuydF5SY"
bot = telebot.TeleBot(BOT_TOKEN)

# ---------- GOFILE UPLOAD FUNCTION ----------
def uploadFile(file_path: str, token=None, folderId=None) -> dict:
    response = requests.get("https://api.gofile.io/servers/").json()
    servers = response["data"]["servers"]
    server = servers[0]["name"]
    cmd = "curl "
    cmd += f'-F "file=@{file_path}" '
    if token:
        cmd += f'-F "token={token}" '
    if folderId:
        cmd += f'-F "folderId={folderId}" '
    cmd += f"'https://{server}.gofile.io/uploadFile'"
    upload_cmd = shlex.split(cmd)
    try:
        out = subprocess.check_output(upload_cmd, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        raise Exception(e)
    try:
        os.remove(file_path)
    except:
        pass
    out = out.decode("UTF-8").strip()
    if out:
        out = out.split("\n")[-1]
        try:
            response = json.loads(out)
        except:
            raise Exception("API Error (Not Valid JSON Data Received)")
        if not response:
            raise Exception("API Error (No JSON Data Received)")
    else:
        raise Exception("API Error (No Data Received)")

    if response["status"] == "ok":
        return response["data"]
    elif "error-" in response["status"]:
        error = response["status"].split("-")[1]
        raise Exception(error)

# ---------- TELEGRAM BOT ----------
@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "Send me any file, and I will upload it to GoFile!")

@bot.message_handler(content_types=['document', 'video', 'audio', 'photo'])
def handle_file(message):
    try:
        # Download file from Telegram
        file_info = bot.get_file(message.document.file_id if message.content_type == 'document' else message.photo[-1].file_id)
        file_name = message.document.file_name if message.content_type == 'document' else "file.jpg"
        downloaded_file = bot.download_file(file_info.file_path)

        # Save locally
        with open(file_name, 'wb') as f:
            f.write(downloaded_file)

        # Upload using your function
        upload_data = uploadFile(file_name)

        bot.send_message(
            message.chat.id,
            f"✅ Uploaded successfully!\nDownload Page: {upload_data['downloadPage']}\nDirect Link: {upload_data['directLink']}"
        )
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error: {e}")

# ---------- RUN BOT ----------
print("Bot is running...")
bot.infinity_polling()
