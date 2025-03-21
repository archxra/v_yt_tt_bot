import os
import re
import logging
import subprocess
import asyncio
import threading
import time
from collections import deque
from typing import Optional
from flask import Flask, request
from telegram import Update, Message
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import yt_dlp

# Global variables with thread-safe access
app_loop = None
app_loop_lock = threading.Lock()
application = None
processed_updates = deque(maxlen=1000)

print("Cookies file exists:", os.path.exists("cookies.txt"))

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Замените на ваш токен
TELEGRAM_TOKEN = '7748710830:AAFY98we_u6AQf8QiyfyAwhsfX8Hw8iK7kA'

# ------------------ Flask App ------------------

app = Flask(__name__)

@app.route('/')
def home():
    return "I'm alive!"

# ------------------ Cookie File Selection ------------------

def get_cookie_file(url: str) -> str:
    """
    Возвращает путь к файлу куки в зависимости от домена ссылки.
    Для YouTube – cookies_youtube.txt, для Pinterest – cookies_pinterest.txt.
    Если ссылка не соответствует, возвращает cookies.txt.
    """
    url_lower = url.lower()
    if "youtube.com" in url_lower or "youtu.be" in url_lower or "tiktok.com" in url_lower:
        return "cookies_youtube.txt"
    elif "pinterest.com" in url_lower or "pin.it" in url_lower:
        return "cookies_pinterest.txt"
    return "cookies.txt"

# ------------------ Webhook Handler ------------------

@app.route('/webhook', methods=['POST'])
def webhook_handler():
    try:
        json_data = request.get_json(force=True)
        logger.info(f"Webhook update received: {json_data}")
        update = Update.de_json(json_data, application.bot)
        
        # Проверка дубликатов
        if update.update_id in processed_updates:
            logger.info(f"Ignoring duplicate update: {update.update_id}")
            return "OK", 200
        processed_updates.append(update.update_id)
        
        future = asyncio.run_coroutine_threadsafe(
            application.process_update(update), app_loop
        )
        try:
            future.result(timeout=30)
        except TimeoutError:
            logger.error("🕒 Превышено время обработки обновления (30 сек)")
    except Exception as e:
        logger.error(f"Fatal webhook error: {str(e)}", exc_info=True)
    
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ------------------ Utility Functions ------------------

def extract_url(text: str) -> str:
    """
    Извлекает первую http(s) ссылку из текста и проверяет, что она принадлежит поддерживаемым платформам.
    """
    match = re.search(r'(https?://\S+)', text)
    if match:
        url = match.group(1)
        if any(domain in url.lower() for domain in ["youtube.com", "youtu.be", "tiktok.com", "pin.it", "pinterest.com"]):
            return url
    return None

def parse_title(full_title: str):
    """
    Ищет в заголовке видео распространённые разделители (тире, en-dash, em-dash, двоеточие).
    Если найден, разделяет заголовок на исполнителя и название трека.
    Иначе возвращает (None, full_title).
    """
    delimiters = ['-', '-', '–', '—', ':']
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
    # Опции для извлечения информации без загрузки
    ydl_opts_info = {
        'format': 'mp4',
        'noplaylist': True,
        'quiet': True,
        'cookiefile': get_cookie_file(url),
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
    }
    if any(x in url.lower() for x in ["pinterest.com", "pin.it"]):
        ydl_opts_info['format'] = 'bestvideo+bestaudio/best'
        ydl_opts_info['merge_output_format'] = 'mp4'
        ydl_opts_info['headers'] = {
            'Referer': 'https://www.pinterest.com/',
            'X-Pinterest-PWS-Handler': 'true'
        }
    else:
        ydl_opts_info['format'] = 'mp4'
    
    # Сначала извлекаем информацию без загрузки для проверки размера
    with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
        info_dict = ydl.extract_info(url, download=False)
        filesize = info_dict.get('filesize_approx') or info_dict.get('filesize')
        if filesize and filesize > 512 * 1024 * 1024:
            raise RuntimeError("Видео слишком большое (превышает 512 Мб)")
    
    # Фактическая загрузка
    with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
        info_dict = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info_dict)
        if not filename.endswith('.mp4'):
            base, _ = os.path.splitext(filename)
            new_filename = base + '.mp4'
            os.rename(filename, new_filename)
            filename = new_filename
    return filename


def download_audio(url: str) -> str:
    """
    Скачивает аудио с помощью yt-dlp с постпроцессором, используя куки, и для Pinterest добавляет нужные заголовки.
    """
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': '%(id)s.%(ext)s',
        'noplaylist': True,
        'quiet': True,
        'cookiefile': get_cookie_file(url),
        'addmetadata': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
    }
    if any(x in url.lower() for x in ["pinterest.com", "pin.it"]):
        ydl_opts['headers'] = {
            'Referer': 'https://www.pinterest.com/',
            'X-Pinterest-PWS-Handler': 'true'
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
        "-c:a", "libmp3lame", "-b:a", "192k",
        "-metadata", f"title={song_title}",
        "-metadata", f"artist={artist}",
        "-id3v2_version", "3",
        "-loglevel", "error",
        new_filename
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"FFmpeg error: {result.stderr}")
        raise RuntimeError(f"Audio conversion failed: {result.stderr}")
    else:
        os.remove(mp3_temp)
    return new_filename

# ------------------ Telegram Bot Handlers ------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hallo! Senden Sie mir einen Videolink für MP4 oder verwenden Sie den Befehl /mp3 <link>, um Audio (MP3) zu erhalten.\n"
        "Unterstützte Links: YouTube, TikTok und Pinterest."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        logger.warning("Empty message received")
        return
    text = update.message.text
    url = extract_url(text)
    if not url:
        if update.message.chat.type == "private":
            await update.message.reply_text("Bitte senden Sie einen gültigen Link (YouTube, TikTok, Pinterest).")
        return
    progress_msg = await update.message.reply_text("Ich lade das Video herunter, warte eine Weile...")
    try:
        filename = await asyncio.to_thread(download_video, url)
        with open(filename, 'rb') as video:
            await update.message.reply_video(video=video)
        os.remove(filename)
    except RuntimeError as e:
        logger.error(f"Fehler beim Herunterladen von Videos: {e}", exc_info=True)
        await update.message.reply_text(f"⚠️ {e}")
    except Exception as e:
        logger.error(f"Fehler beim Herunterladen von Videos: {e}", exc_info=True)
        await update.message.reply_text("Fehler beim Herunterladen des Videos. Überprüfen Sie den Link und die Verfügbarkeit des Videos.")
    await progress_msg.delete()

async def mp3_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.args:
        url = context.args[0]
    else:
        url = extract_url(update.message.text)
    if not url or not url.startswith("http"):
        await update.message.reply_text("Bitte senden Sie einen gültigen Link nach dem Befehl /mp3.")
        return
    progress_msg = await update.message.reply_text("Ich lade das Audio herunter, warte eine Weile...")
    try:
        filename = await asyncio.to_thread(download_audio, url)
        with open(filename, 'rb') as audio_file:
            await update.message.reply_audio(audio=audio_file)
        os.remove(filename)
    except Exception as e:
        logger.error(f"Fehler beim Herunterladen von Audio: {e}", exc_info=True)
        await update.message.reply_text("Fehler beim Herunterladen des Audios. Überprüfen Sie den Link und die Verfügbarkeit des Videos.")
    await progress_msg.delete()

async def ping_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.text.strip().lower() == "пинг":
        await update.message.reply_text("Понг!")

# ------------------ Main Function ------------------

def run_event_loop():
    global app_loop, application
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    with app_loop_lock:
        app_loop = loop

    application = Application.builder().token(TELEGRAM_TOKEN).pool_timeout(30).build()
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
    
    WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://v-yt-tt-bot.onrender.com/webhook")
    loop.run_until_complete(application.initialize())
    loop.run_until_complete(application.bot.set_webhook(WEBHOOK_URL))
    logger.info("Webhook установлен на: " + WEBHOOK_URL)
    loop.run_forever()

def main() -> None:
    event_loop_thread = threading.Thread(target=run_event_loop, daemon=True)
    event_loop_thread.start()
    time.sleep(2)  # Небольшая задержка, чтобы цикл запустился
    run_flask()

if __name__ == '__main__':
    main()
