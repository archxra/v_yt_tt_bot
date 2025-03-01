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

# ------------------ Utility Functions ------------------

def parse_title(full_title: str):
    """
    Looks for common delimiters (hyphen variants and colon) in the title.
    If found, splits the title into artist and song title.
    If not, returns (None, full_title).
    """
    delimiters = ['-', '–', '—', ':']
    index = None
    chosen_delim = None
    for delim in delimiters:
        idx = full_title.find(delim)
        if idx != -1:
            # choose the earliest delimiter occurrence
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
        "или используй команду /mp3 <ссылка> для получения аудио (MP3)."
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
    It extracts the video's title and attempts to split it using common delimiters.
    If a delimiter is found, the left part becomes the artist and the right part the song title.
    Extra ffmpeg arguments are passed via 'args' to embed the metadata.
    The output file is then renamed to the video's full title (sanitized).
    """
    # Base options for yt_dlp extraction and conversion
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': '%(id)s.%(ext)s',
        'noplaylist': True,
        'quiet': True,
        'cookiefile': 'cookies.txt',
        'addmetadata': True,
        # Use 'args' instead of 'postprocessor_args' if required by your version
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
            'args': [],  # Placeholder; will set below
        }],
    }
    
    # First, extract info without downloading
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=False)
    
    full_title = info_dict.get("title", info_dict.get("id"))
    artist, song_title = parse_title(full_title)
    if artist is None:
        # No delimiter found; leave artist empty and song_title as full title
        artist = ""
        song_title = full_title

    # Update the ffmpeg arguments with the metadata
    ydl_opts["postprocessors"][0]["args"] = [
        '-metadata', f'title={song_title}',
        '-metadata', f'artist={artist}',
    ]
    
    # Download the audio with the updated options
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=True)
        temp_filename = ydl.prepare_filename(info_dict)
        base, _ = os.path.splitext(temp_filename)
        temp_audio_filename = base + ".mp3"
        # Sanitize the full title for a safe filename
        sanitized_title = "".join(c for c in full_title if c.isalnum() or c in " -_").strip()
        new_filename = sanitized_title + ".mp3"
        if os.path.exists(temp_audio_filename):
            os.rename(temp_audio_filename, new_filename)
        else:
            logger.error("Temporary audio file not found.")
            new_filename = temp_audio_filename  # fallback
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
