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

TELEGRAM_TOKEN = '7748710830:AAFY98we_u6AQf8QiyfyAwhsfX8Hw8iK7kA'  # Замените на ваш токен

# ------------------ Flask App ------------------

app = Flask(__name__)

@app.route('/')
def home():
    return "I'm alive!"

# Глобальный event loop для обработки обновлений
app_loop = None

@app.route('/webhook', methods=['POST'])
def webhook_handler():
    try:
        json_data = request.get_json(force=True)
        logger.debug(f"Raw update: {json_data}")

        with app_loop_lock:
            loop = app_loop
        
        if not loop or not application:
            logger.critical("Event loop or application not initialized")
            return "Service Unavailable", 503

        update = Update.de_json(json_data, application.bot)
        
        # Проверка дубликатов
        if update.update_id in processed_updates:
            logger.info(f"Ignoring duplicate update: {update.update_id}")
            return "OK", 200
        processed_updates.append(update.update_id)
        
        # Безопасное определение типа сообщения
        msg_type = 'no_message'
        if update.effective_message:
            if hasattr(update.effective_message, 'content_type'):
                msg_type = update.effective_message.content_type
            else:
                msg_type = 'special_message'
        
        logger.info(f"Processing update: {update.update_id} [type: {msg_type}]")
        
        future = asyncio.run_coroutine_threadsafe(
            application.process_update(update),
            loop
        )
        
        try:
            future.result(timeout=30)  # Увеличенный таймаут
        except TimeoutError:
            logger.error("🕒 Превышено время обработки обновления (30 сек)")
            return "Timeout", 500

    except Exception as e:
        logger.error(f"Fatal webhook error: {str(e)}", exc_info=True)
        return "Internal Server Error", 500
    
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

def parse_title(full_title: str) -> tuple:
    # Улучшенный парсинг с приоритетом разделителей
    patterns = [
        r'(.*?)\s*[-–—:]\s*(.*)',  # Основной паттерн
        r'(.*?)\s*[\"“](.*?)[\"”]',  # Название в кавычках
        r'(.*?)\s*\((.*?)\)'  # Название в скобках
    ]
    
    for pattern in patterns:
        match = re.match(pattern, full_title)
        if match:
            return match.group(1).strip(), match.group(2).strip()
    
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
        'outtmpl': 'temp/%(id)s.%(ext)s',
        'noplaylist': True,
        'cookiefile': 'cookies.txt',
        'external_downloader': 'aria2c',
        'external_downloader_args': ['-x16', '-s16', '-k5M'],
        'socket_timeout': 30,
        'noprogress': True,
        'writethumbnail': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }
    
    thumbnail = None  # Инициализация переменной
    base = None  # Инициализация переменной
    thumbnail = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            filename = os.path.abspath(ydl.prepare_filename(info_dict))
            
            time.sleep(2)  # 2 секунды задержки
            if not os.path.exists(filename):
                raise FileNotFoundError(f"Downloaded file {filename} not found")
            
            base, _ = os.path.splitext(filename)  # Теперь base всегда определен
            
            # Обработка обложки
            thumbnail_path = base + ".webp"
            if os.path.exists(thumbnail_path):
                os.rename(thumbnail_path, base + ".jpg")
                thumbnail = base + ".jpg"
            
            # Парсинг метаданных
            full_title = info_dict.get('title', 'Unknown Title')
            artist, song_title = parse_title(full_title)
            if not artist:
                artist = info_dict.get('uploader', 'Unknown Artist')
            
            # Конвертация
            mp3_filename = base + ".mp3"
            cmd = [
                'ffmpeg', '-y',
                '-i', filename,
                '-metadata', f'title={song_title}',
                '-metadata', f'artist={artist}',
                '-c:a', 'copy',
                '-id3v2_version', '3',
                '-loglevel', 'error',
                mp3_filename
            ]
            
            if thumbnail:
                cmd += [
                    '-i', thumbnail,
                    '-c:v', 'copy',
                    '-map', '0:a',
                    '-map', '1:v',
                    '-metadata:s:v', 'title="Album cover"',
                    '-metadata:s:v', 'comment="Cover (front)"'
                ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"FFmpeg error: {result.stderr}")
                raise RuntimeError(f"Audio conversion failed: {result.stderr}")
            
            return mp3_filename
            
    finally:
        temp_files = []
        if filename:
            temp_files.append(filename)
        if base:  # Проверка на существование base
            temp_files.extend([
                base + ".webp",
                base + ".webm",
                base + ".jpg",
                base + ".mp3"
            ])
        if thumbnail:
            temp_files.append(thumbnail)
        for f in temp_files:
            if f and os.path.exists(f):
                try:
                    os.remove(f)
                except Exception as e:
                    logger.warning(f"Failed to delete {f}: {str(e)}")

# ------------------ Telegram Bot Handlers ------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hallo! Senden Sie mir einen Videolink für MP4 oder verwenden Sie den Befehl /mp3 <link>, um Audio (MP3) zu erhalten.\n"
        "Unterstützte Links: YouTube, TikTok und Pinterest."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Проверка наличия текстового сообщения
    if not update.message or not update.message.text:
        logger.warning("Empty message received: %s", update)
        return

    text = update.message.text
    url = extract_url(text)
    
    if not url:
        if update.message.chat.type == "private":
            await update.message.reply_text("Bitte senden Sie einen gültigen Link (YouTube, TikTok, Pinterest).")
        return
        
    try:
        progress_msg = await update.message.reply_text("Ich lade das Video herunter, warte eine Weile...")
        filename = download_video(url)
        
        with open(filename, 'rb') as video:
            await update.message.reply_video(video=video)
            
        os.remove(filename)
        
    except yt_dlp.DownloadError as e:
        logger.error(f"Download error: {str(e)}")
        await update.message.reply_text("⚠️ Video konnte nicht heruntergeladen werden. Mögliche Ursachen:\n"
                                      "- Altersbeschränkung\n"
                                      "- Geoblocking\n"
                                      "- Ungültiger Link")
    except Exception as e:
        logger.exception("Critical error in video download")
        await update.message.reply_text("❌ Schwerer Fehler bei der Verarbeitung")
        
    finally:
        if progress_msg:
            await progress_msg.delete()

async def mp3_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    progress_msg = None
    try:
        # Проверка наличия сообщения
        if not update.message or not update.message.text:
            logger.error("Получена команда /mp3 без сообщения")
            return

        # Извлечение URL
        url = None
        if context.args:
            # Объединение аргументов для URL с пробелами
            potential_url = " ".join(context.args)
            if extract_url(potential_url):
                url = potential_url
        else:
            url = extract_url(update.message.text)

        # Вторичная проверка через прямое извлечение
        if not url:
            url = extract_url(update.message.text)

        # Валидация URL
        if not url or not url.startswith(("http://", "https://")):
            logger.warning(f"Ungültige URL: {url}")
            await update.message.reply_text("❌ Ungültiger Link. Beispiel für das richtige Format:\n/mp3 https://youtu.be/...")
            return

        # Начало загрузки
        progress_msg = await update.message.reply_text("⏳ Ich beginne mit der Audioverarbeitung...")
        
        # Загрузка и конвертация
        filename = download_audio(url)
        
        # Получение метаданных из имени файла
        base = os.path.splitext(filename)[0]
        song_title = os.path.basename(base)
        artist = "Unknown Artist"
        
        # Отправка аудио
        with open(filename, 'rb') as audio_file:
            thumb_path = base + ".jpg"
            thumb = open(thumb_path, 'rb') if os.path.exists(thumb_path) else None
            
            await update.message.reply_audio(
                audio=audio_file,
                title=song_title,
                performer=artist,
                thumb=thumb,
                read_timeout=30,
                write_timeout=30
            )
            
        if thumb:
            thumb.close()
        
        # Финализация
        os.remove(filename)
        await progress_msg.delete()

    except yt_dlp.DownloadError as e:
        error_msg = f"Ошибка загрузки: {str(e)}"
        logger.error(error_msg)
        await handle_error(update, progress_msg, "🚫 Fehler beim Herunterladen des Videos. Überprüfen Sie:\n- Verfügbarkeit des Videos\n- Altersbeschränkungen\n- Korrektheit des Links")
    
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg error: {e.stderr.decode()}")
        await handle_error(update, progress_msg, "⚠️ Audio-Konvertierung ist fehlgeschlagen. Versuchen Sie es später")
    
    except Exception as e:
        logger.error(f"Critical MP3 error: {str(e)}", exc_info=True)
        await handle_error(update, progress_msg, "‼️ Interner Serverfehler")

    except telegram.error.TimedOut:
        await update.message.reply_text("⌛ Das Timeout ist abgelaufen, versuchen Sie es später")

async def handle_error(update: Update, progress_msg: Optional[Message], text: str):
    """Унифицированная обработка ошибок"""
    try:
        if progress_msg:
            await progress_msg.delete()
        if update and update.message:
            await update.message.reply_text(text)
    except Exception as e:
        logger.error(f"Error handling failed: {str(e)}", exc_info=True)

async def ping_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    if update.message.text.strip().lower() == "пинг":
        await update.message.reply_text("Der Bot funktioniert erfolgreich!!")

# ------------------ Main Function ------------------

def run_event_loop():
    global app_loop, application
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    with app_loop_lock:
        app_loop = loop

    application = Application.builder().token(TELEGRAM_TOKEN).pool_timeout(30).build()
    
    # Добавление обработчиков команд
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
    
    loop.run_forever()

def main() -> None:
    event_loop_thread = threading.Thread(target=run_event_loop, daemon=True)
    event_loop_thread.start()
    time.sleep(2)
    run_flask()

if __name__ == '__main__':
    main()
