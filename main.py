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
    application.process_update(update)
    return "OK", 200

def run_flask():
    # Запускаем Flask-приложение на порту, указанном в переменной окружения PORT (или 8080 по умолчанию)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

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
    # Если ссылка с Pinterest, скачиваем видео+аудио и объединяем их
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
        new_filename = mp3_temp  # fallback, если ffmpeg не сработал
    else:
        os.remove(mp3_temp)
    return new_filename

# ------------------ Telegram Bot Handlers ------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Отправь мне ссылку на видео для MP4, или используй команду /mp3 <ссылка> для получения аудио (MP3).\n"
        "Поддерживаются ссылки с YouTube, TikTok и Pinterest."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    url = extract_url(text)
    if not url:
        if update.message.chat.type == "private":
            await update.message.reply_text("Пожалуйста, отправьте корректную ссылку (поддерживаются YouTube, TikTok, Pinterest).")
        return
    progress_msg = await update.message.reply_text("Скачиваю видео, подождите немного...")
    try:
        filename = download_video(url)
        with open(filename, 'rb') as video:
            await update.message.reply_video(video=video)
        os.remove(filename)
    except Exception as e:
        logger.error(f"Ошибка при скачивании видео: {e}")
        await update.message.reply_text("Ошибка при скачивании видео. Проверьте ссылку и доступность видео.")
    await progress_msg.delete()

async def mp3_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.args:
        url = context.args[0]
    else:
        url = extract_url(update.message.text)
    if not url or not url.startswith("http"):
        await update.message.reply_text("Пожалуйста, отправьте корректную ссылку после команды /mp3.")
        return
    progress_msg = await update.message.reply_text("Скачиваю аудио, подождите немного...")
    try:
        filename = download_audio(url)
        with open(filename, 'rb') as audio_file:
            await update.message.reply_audio(audio=audio_file)
        os.remove(filename)
    except Exception as e:
        logger.error(f"Ошибка при скачивании аудио: {e}")
        await update.message.reply_text("Ошибка при скачивании аудио. Проверьте ссылку и доступность видео.")
    await progress_msg.delete()

async def ping_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Обрабатывает сообщения, содержащие ровно слово "пинг" (без других слов)
    if update.message.text.strip().lower() == "пинг":
        await update.message.reply_text("Понг!")

# ------------------ Main Function ------------------

def main() -> None:
    global application
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("mp3", mp3_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # Обработчик "пинг" только в групповых чатах:
    application.add_handler(
        MessageHandler(
            filters.TEXT 
            & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP)
            & filters.Regex(r'^(?i:пинг)$'),
            ping_handler
        )
    )
    
    # Устанавливаем вебхук – корректно await'им вызов с помощью asyncio.run()
    WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://v-yt-tt-bot.onrender.com/webhook")
    asyncio.run(application.bot.set_webhook(WEBHOOK_URL))
    logger.info("Webhook установлен на: " + WEBHOOK_URL)
    
    # Запускаем Flask-приложение (вебхук-обработчик)
    run_flask()

if __name__ == '__main__':
    main()
