import os
import logging
import threading
from flask import Flask
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

TELEGRAM_TOKEN = '7748710830:AAFY98we_u6AQf8QiyfyAwhsfX8Hw8iK7kA'  # Replace with your actual token

# ------------------ Flask Server for Pinging ------------------

app = Flask(__name__)

@app.route('/')
def home():
    return "I'm alive!"

def run_flask():
    # Run the Flask server on port 8080
    app.run(host='0.0.0.0', port=8080)

# ------------------ Telegram Bot Handlers ------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Отправь мне ссылку на видео для MP4, "
        "или используй команду /mp3 <ссылка> для получения аудио (MP3).\n"
        "Имя MP3-файла будет соответствовать заголовку видео."
    )

def download_video(url: str) -> str:
    ydl_opts = {
        'format': 'mp4',
        'outtmpl': '%(id)s.%(ext)s',
        'noplaylist': True,
        'quiet': True,
        'cookiefile': 'cookies.txt',  # Path to your cookies file
    }
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
    """
    Downloads the audio as an MP3 using yt_dlp and renames the output file to the video's title.
    """
    # Use a temporary output template based on video id
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': '%(id)s.%(ext)s',
        'noplaylist': True,
        'quiet': True,
        'cookiefile': 'cookies.txt',  # Path to your cookies file
        'addmetadata': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=True)
        # Get the video's title from the info dictionary; fallback to video ID if missing
        title = info_dict.get("title", info_dict.get("id"))
        # Sanitize the title for a safe filename (allow letters, digits, spaces, dashes, and underscores)
        sanitized_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()
        # Determine the temporary filename produced by yt_dlp
        temp_filename = ydl.prepare_filename(info_dict)
        base, _ = os.path.splitext(temp_filename)
        temp_audio_filename = base + ".mp3"
        # New filename will be the sanitized title with .mp3 extension
        new_filename = sanitized_title + ".mp3"
        # If the temporary file exists, rename it to the new filename
        if os.path.exists(temp_audio_filename):
            os.rename(temp_audio_filename, new_filename)
        else:
            logger.error("Temporary audio file not found.")
            new_filename = temp_audio_filename  # Fallback
    return new_filename

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Process plain text messages as video download requests (MP4)
    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text("Пожалуйста, отправьте корректную ссылку.")
        return
    await update.message.reply_text("Скачиваю видео, подождите немного...")
    try:
        filename = download_video(url)
        with open(filename, 'rb') as video:
            await update.message.reply_video(video=video)
        os.remove(filename)
    except Exception as e:
        logger.error(f"Ошибка при скачивании видео: {e}")
        await update.message.reply_text(
            "Произошла ошибка при скачивании видео. Проверьте правильность ссылки и доступность видео."
        )

async def mp3_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Process the /mp3 command for audio download (MP3)
    args = context.args
    if not args:
        await update.message.reply_text("Пожалуйста, укажите ссылку после команды /mp3.")
        return
    url = args[0]
    if not url.startswith("http"):
        await update.message.reply_text("Пожалуйста, отправьте корректную ссылку.")
        return
    await update.message.reply_text("Скачиваю аудио, подождите немного...")
    try:
        filename = download_audio(url)
        with open(filename, 'rb') as audio_file:
            await update.message.reply_audio(audio=audio_file)
        os.remove(filename)
    except Exception as e:
        logger.error(f"Ошибка при скачивании аудио: {e}")
        await update.message.reply_text(
            "Произошла ошибка при скачивании аудио. Проверьте правильность ссылки и доступность видео."
        )

def main() -> None:
    # Start Flask server in a separate thread for uptime monitoring
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Configure and run the Telegram bot
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("mp3", mp3_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запущен...")
    application.run_polling()

if __name__ == '__main__':
    main()
