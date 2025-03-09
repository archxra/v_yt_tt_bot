import os
import re
import logging
import subprocess
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import yt_dlp

print("Cookies file exists:", os.path.exists("cookies.txt"))

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = '7748710830:AAFY98we_u6AQf8QiyfyAwhsfX8Hw8iK7kA'  # Замените на ваш токен

# ------------------ Flask App ------------------

app = Flask(__name__)

@app.route('/')
def home():
    return "I'm alive!"

@app.route('/webhook', methods=['POST'])
def webhook_handler():
    json_data = request.get_json(force=True)
    update = Update.de_json(json_data, application.bot)
    # Корректно await’им асинхронную функцию
    asyncio.run(application.process_update(update))
    return "OK", 200

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

# ------------------ Utility Functions ------------------

def extract_url(text: str) -> str:
    match = re.search(r'(https?://\S+)', text)
    if match:
        url = match.group(1)
        if any(domain in url.lower() for domain in ["youtube.com", "youtu.be", "tiktok.com", "pin.it", "pinterest.com"]):
            return url
    return None

def parse_title(full_title: str):
    delimiters = ['-', '–', '—', ':']
    index = None
    chosen_delim = None
    for delim in delimiters:
        idx = full_title.find(delim)
        if idx != -1:
            if index is None or idx < index:
                index = idx
                chosen_delim = delim
    if index is not None:
        artist = full_title[:index].strip()
        song_title = full_title[index + len(chosen_delim):].strip()
        return artist, song_title
    else:
        return None, full_title

# ------------------ Download Functions ------------------

def download_video(url: str) -> str:
    ydl_opts = {
        'outtmpl': '%(id)s.%(ext)s',
        'noplaylist': True,
        'quiet': True,
        'cookiefile': 'cookies.txt',
    }
    if 'pin.it' in url.lower():
        ydl_opts['format'] = 'bestvideo+bestaudio/best'
        ydl_opts['merge_output_format'] = 'mp4'
    else:
        ydl_opts['format'] = 'mp4'
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info_dict)
        if not filename.endswith('.mp4'):
            base, _ = os.path.splitext(filename)
            new_filename = base + '.mp4'
            os.rename(filename, new_filename)
            filename = new_filename
    return filename

def download_audio(url: str) -> str:
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': '%(id)s.%(ext)s',
        'noplaylist': True,
        'quiet': True,
        'cookiefile': 'cookies.txt',
        'addmetadata': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=True)
        temp_filename = ydl.prepare_filename(info_dict)
        base, _ = os.path.splitext(temp_filename)
        mp3_temp = base + ".mp3"
    
    full_title = info_dict.get("title", info_dict.get("id"))
    artist, song_title = parse_title(full_title)
    if artist is None:
        artist = ""
        song_title = full_title

    sanitized_title = "".join(c for c in full_title if c.isalnum() or c in " -_").strip()
    new_filename = sanitized_title + ".mp3"

    command = [
        "ffmpeg", "-y", "-i", mp3_temp,
        "-metadata", f"title={song_title}",
        "-metadata", f"artist={artist}",
        "-c", "copy",
        new_filename
    ]
    result = subprocess.run(command, capture_output=True)
    if result.returncode != 0:
        logger.error(f"ffmpeg error: {result.stderr.decode()}")
        new_filename = mp3_temp  # fallback если ffmpeg не сработал
    else:
        os.remove(mp3_temp)
    return new_filename

# ------------------ Telegram Bot Handlers ------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hallo! Senden Sie mir einen Videolink für MP4 oder verwenden Sie den Befehl /mp3 <link>, um Audio (MP3) zu erhalten.\n"
        "Links von YouTube, TikTok und Pinterest werden unterstützt."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    url = extract_url(text)
    if not url:
        if update.message.chat.type == "private":
            await update.message.reply_text("Bitte senden Sie einen gültigen Link (YouTube, TikTok, Pinterest werden unterstützt).")
        return
    progress_msg = await update.message.reply_text("Ich lade das Video herunter, warte eine Weile...")
    try:
        filename = download_video(url)
        with open(filename, 'rb') as video:
            await update.message.reply_video(video=video)
        os.remove(filename)
    except Exception as e:
        logger.error(f"Fehler beim Herunterladen von Videos: {e}")
        await update.message.reply_text("Fehler beim Herunterladen des Videos. Überprüfen Sie den Link und die Verfügbarkeit des Videos.")
    await progress_msg.delete()

async def mp3_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.args:
        url = context.args[0]
    else:
        url = extract_url(update.message.text)
    if not url or not url.startswith("http"):
        await update.message.reply_text("Bitte senden Sie den korrekten Link nach dem Befehl /mp3.")
        return
    progress_msg = await update.message.reply_text("Ich lade Audio herunter, warte eine Weile...")
    try:
        filename = download_audio(url)
        with open(filename, 'rb') as audio_file:
            await update.message.reply_audio(audio=audio_file)
        os.remove(filename)
    except Exception as e:
        logger.error(f"Fehler beim Herunterladen von Audio: {e}")
        await update.message.reply_text("Fehler beim Herunterladen von Audio. Überprüfen Sie den Link und die Verfügbarkeit des Videos.")
    await progress_msg.delete()

async def ping_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.text.strip().lower() == "пинг":
        await update.message.reply_text("Понг!")

# ------------------ Main Function ------------------

def main() -> None:
    global application
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("mp3", mp3_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(
        MessageHandler(
            filters.TEXT 
            & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP)
            & filters.Regex(r'^(?i:пинг)$'),
            ping_handler
        )
    )
    
    # Устанавливаем вебхук корректно с использованием asyncio.run()
    WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://v-yt-tt-bot.onrender.com/webhook")
    asyncio.run(application.bot.set_webhook(WEBHOOK_URL))
    logger.info("Webhook установлен на: " + WEBHOOK_URL)
    
    # Запускаем Flask-приложение (вебхук-обработчик)
    run_flask()

if __name__ == '__main__':
    main()
