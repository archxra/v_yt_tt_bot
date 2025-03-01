import os
import logging
import threading
import subprocess
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

# ------------------ Utility Functions ------------------

def parse_title(full_title: str):
    """
    Looks for common delimiters (hyphen, en-dash, em-dash, colon) in the title.
    If found, splits the title into artist and song title.
    Otherwise, returns (None, full_title).
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

# ------------------ Telegram Bot Handlers ------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Отправь мне ссылку на видео для MP4, "
        "или используй команду /mp3 <ссылка> для получения аудио (MP3).\n"
        "Если заголовок видео имеет вид 'Artist - Song Title' (or uses a colon), "
        "то аудио-файл будет переименован, а метаданные установят название и исполнителя."
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
    Downloads the audio as an MP3 using yt_dlp.
    Then, it uses ffmpeg to re-mux the file and embed metadata.
    It attempts to parse the video's title to split it into artist and song title.
    The final file is renamed to the full title (sanitized).
    """
    # Base options for yt_dlp extraction and conversion (without custom ffmpeg args)
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
    
    # Download audio
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=True)
        temp_filename = ydl.prepare_filename(info_dict)
        base, _ = os.path.splitext(temp_filename)
        mp3_temp = base + ".mp3"
    
    # Get full title and parse it for metadata
    full_title = info_dict.get("title", info_dict.get("id"))
    artist, song_title = parse_title(full_title)
    if artist is None:
        artist = ""
        song_title = full_title

    # Sanitize the full title for a safe filename
    sanitized_title = "".join(c for c in full_title if c.isalnum() or c in " -_").strip()
    new_filename = sanitized_title + ".mp3"

    # Use ffmpeg to embed metadata into the MP3 file
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
        # Fallback: if ffmpeg fails, use the original file
        new_filename = mp3_temp
    else:
        os.remove(mp3_temp)
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
