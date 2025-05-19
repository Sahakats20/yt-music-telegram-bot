import os
import re
import random
import time
import logging
import sys
import subprocess
from datetime import datetime
import requests
import telebot
from bs4 import BeautifulSoup
import yt_dlp
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
from threading import Thread
from queue import Queue

# Проверяем, не запущен ли скрипт из терминала
if sys.stdout.isatty():
    # Запускаем новую копию без терминала
    subprocess.Popen(["python3", __file__], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sys.exit(0)

# Конфигурация по умолчанию
DEFAULT_CONFIG = {
    'telegram_token': '',  # Формат: "123456789:ABCdefGHIjklMnOpQRSTuvwxyz"
    'telegram_channel': '',
    'temp_folder': 'temp_audio',
    'check_interval': 60,  # 1 минута для теста
    'music_source': 'youtube',
    'youtube_url': 'https://music.youtube.com/playlist?list=PLFTLA_vr_gYaJLKBRIiiBqgJ25TLjUcbF',
    'max_retries': 3,
    'request_timeout': 30,
    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'use_cookies': True,
    'cookies_file': 'cookies.txt'
}

# Глобальные переменные
CONFIG = DEFAULT_CONFIG.copy()
bot_running = False
log_queue = Queue()

def setup_logger():
    logger = logging.getLogger('MusicBot')
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    file_handler = logging.FileHandler('music_bot.log')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    class QueueHandler(logging.Handler):
        def __init__(self, queue):
            super().__init__()
            self.queue = queue
        
        def emit(self, record):
            self.queue.put(self.format(record))
    
    queue_handler = QueueHandler(log_queue)
    queue_handler.setFormatter(formatter)
    logger.addHandler(queue_handler)
    
    return logger

logger = setup_logger()

def validate_telegram_token(token):
    """Проверяет правильность формата Telegram токена"""
    if not token or ':' not in token:
        logger.error(f"Неверный формат токена: {token}")
        return False
    return True

def sanitize_filename(filename):
    """Очищает имя файла от недопустимых символов"""
    return re.sub(r'[\\/*?:"<>|]', "_", filename)

def cleanup_temp_files():
    """Удаляет временные файлы"""
    for file in os.listdir(CONFIG['temp_folder']):
        file_path = os.path.join(CONFIG['temp_folder'], file)
        try:
            if os.path.isfile(file_path):
                os.unlink(file_path)
        except Exception as e:
            logger.error(f"Ошибка удаления файла {file_path}: {e}")

class YouTubeMusicParser:
    @staticmethod
    def get_tracks_from_url(url):
        """Получает треки из YouTube Music плейлиста"""
        if not url:
            logger.error("URL YouTube Music не указан")
            return None
            
        ydl_opts = {
            'extract_flat': True,
            'quiet': True,
            'logger': logger,
            'extractor_args': {
                'youtube': {
                    'skip': ['authcheck'],
                    'music': True
                }
            }
        }
        
        if CONFIG['use_cookies'] and os.path.exists(CONFIG['cookies_file']):
            ydl_opts['cookiefile'] = CONFIG['cookies_file']
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    logger.error("Не удалось получить информацию о треках")
                    return None
                
                entries = info.get('entries', [])
                if not entries:
                    logger.warning("Плейлист YouTube Music пуст")
                    return None
                
                tracks = []
                for entry in entries:
                    # Улучшенное извлечение артиста и названия
                    artist = entry.get('artist') or entry.get('uploader') or 'Unknown Artist'
                    title = entry.get('title', 'Unknown Track')
                    
                    # Дополнительная обработка названия
                    if ' - ' in title:
                        parts = title.split(' - ')
                        if len(parts) > 1:
                            artist = parts[0].strip()
                            title = ' - '.join(parts[1:]).strip()
                    
                    tracks.append({
                        'artist': artist,
                        'title': title,
                        'duration': entry.get('duration', 0),
                        'url': entry.get('url', '')
                    })
                
                logger.info(f"Найдено {len(tracks)} треков из YouTube Music")
                return tracks
                
        except Exception as e:
            logger.error(f"Ошибка получения треков с YouTube Music: {e}")
            return None

class TrackDownloader:
    @staticmethod
    def get_ydl_opts():
        """Возвращает параметры для yt-dlp"""
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [
                {
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                },
                {
                    'key': 'FFmpegMetadata',
                    'add_metadata': True,
                }
            ],
            'writethumbnail': True,
            'ignoreerrors': True,
            'extractaudio': True,
            'logger': logger,
            'quiet': True,
            'extractor_args': {
                'youtube': {
                    'skip': ['authcheck'],
                    'music': True
                }
            }
        }
        
        if CONFIG['use_cookies'] and os.path.exists(CONFIG['cookies_file']):
            ydl_opts['cookiefile'] = CONFIG['cookies_file']
        
        return ydl_opts

    @staticmethod
    def download(track):
        """Скачивает трек"""
        for attempt in range(CONFIG['max_retries']):
            try:
                artist = track.get('artist', 'Unknown Artist')
                title = track.get('title', 'Unknown Track')
                query = f"{artist} - {title}"
                safe_filename = sanitize_filename(query)
                
                ydl_opts = TrackDownloader.get_ydl_opts()
                ydl_opts['outtmpl'] = f"{CONFIG['temp_folder']}/{safe_filename}.%(ext)s"

                if track.get('url'):
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([track['url']])
                else:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(f"ytsearch1:{query}", download=True)
                        if not info or 'entries' not in info or not info['entries']:
                            logger.warning(f"Трек не найден: {query}")
                            return None

                # Находим скачанные файлы
                files = {}
                for f in os.listdir(CONFIG['temp_folder']):
                    if f.startswith(safe_filename):
                        if f.endswith('.mp3'):
                            files['audio'] = os.path.join(CONFIG['temp_folder'], f)
                        elif f.endswith(('.jpg', '.webp')):
                            files['thumb'] = os.path.join(CONFIG['temp_folder'], f)

                return {
                    'audio_path': files.get('audio'),
                    'thumb_path': files.get('thumb'),
                    'artist': artist,
                    'title': title,
                    'url': track.get('url', ''),
                    'duration': track.get('duration', 0)
                }

            except Exception as e:
                logger.warning(f"Попытка {attempt + 1} не удалась: {e}")
                if attempt < CONFIG['max_retries'] - 1:
                    time.sleep(5)
                continue
        
        return None

class TelegramSender:
    @staticmethod
    def send_track(track_data):
        """Отправляет трек в Telegram"""
        try:
            if not track_data:
                logger.error("Нет данных для отправки")
                return False

            if not validate_telegram_token(CONFIG['telegram_token']):
                logger.error("Неверный формат Telegram токена")
                return False

            duration = track_data.get('duration', 0)
            if isinstance(duration, int):
                mins, secs = divmod(duration, 60)
                duration_str = f"{mins}:{secs:02d}"
            else:
                duration_str = str(duration)

            message = f"""🎧 <b>Случайный трек с YouTube Music</b>

🎵 <b>{track_data['artist']} - {track_data['title']}</b>
⏳ <i>Длительность:</i> {duration_str}
🕒 <i>Время отправки:</i> {datetime.now().strftime('%H:%M')}
🔗 <a href="{track_data.get('url', '')}">Ссылка на YouTube</a>

#музыка #youtubemusic #случайныйтрек""".strip()

            if track_data.get('audio_path'):
                with open(track_data['audio_path'], 'rb') as audio_file:
                    thumb = None
                    if track_data.get('thumb_path') and os.path.exists(track_data['thumb_path']):
                        thumb = open(track_data['thumb_path'], 'rb')
                    
                    try:
                        bot = telebot.TeleBot(CONFIG['telegram_token'])
                        bot.send_audio(
                            chat_id=CONFIG['telegram_channel'],
                            audio=audio_file,
                            caption=message,
                            parse_mode='HTML',
                            thumb=thumb,
                            timeout=60
                        )
                    except Exception as e:
                        logger.error(f"Ошибка отправки в Telegram: {e}")
                        return False
                    finally:
                        if thumb:
                            thumb.close()
                
                # Удаляем временные файлы
                try:
                    os.remove(track_data['audio_path'])
                    if track_data.get('thumb_path') and os.path.exists(track_data['thumb_path']):
                        os.remove(track_data['thumb_path'])
                except Exception as e:
                    logger.error(f"Ошибка удаления временных файлов: {e}")
                
                logger.info(f"Успешно отправлен: {track_data['artist']} - {track_data['title']}")
                return True
            
            # Если не удалось отправить аудио, отправляем текстовое сообщение
            try:
                bot = telebot.TeleBot(CONFIG['telegram_token'])
                bot.send_message(
                    chat_id=CONFIG['telegram_channel'],
                    text=message,
                    parse_mode='HTML'
                )
                return True
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения: {e}")
                return False
        
        except Exception as e:
            logger.error(f"Ошибка отправки трека: {e}")
            return False

class MusicBotGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("YT Music Telegram Bot")
        self.root.geometry("900x650")
        
        self.setup_ui()
        self.update_logs()
        self.load_config()

    def setup_ui(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # Вкладка управления
        self.control_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.control_frame, text="Управление")
        self.setup_control_tab()

        # Вкладка настроек
        self.settings_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.settings_frame, text="Настройки")
        self.setup_settings_tab()

        # Вкладка логов
        self.log_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.log_frame, text="Логи")
        self.setup_log_tab()

    def setup_control_tab(self):
        self.start_button = ttk.Button(
            self.control_frame,
            text="Запустить бота",
            command=self.start_bot
        )
        self.start_button.pack(pady=10, padx=20, fill=tk.X)

        self.stop_button = ttk.Button(
            self.control_frame,
            text="Остановить бота",
            command=self.stop_bot,
            state=tk.DISABLED
        )
        self.stop_button.pack(pady=10, padx=20, fill=tk.X)

        ttk.Button(
            self.control_frame,
            text="Тестовая отправка",
            command=self.test_send
        ).pack(pady=10, padx=20, fill=tk.X)

        self.status_label = ttk.Label(
            self.control_frame,
            text="Статус: Бот остановлен",
            font=('Arial', 10, 'bold')
        )
        self.status_label.pack(pady=10)

        self.last_track_frame = ttk.LabelFrame(
            self.control_frame,
            text="Последний трек",
            padding=10
        )
        self.last_track_frame.pack(pady=10, padx=20, fill=tk.BOTH, expand=True)
        
        self.last_track_label = ttk.Label(
            self.last_track_frame,
            text="Еще не отправлено ни одного трека",
            wraplength=500
        )
        self.last_track_label.pack(fill=tk.BOTH, expand=True)

    def setup_settings_tab(self):
        # Настройки Telegram
        telegram_frame = ttk.LabelFrame(self.settings_frame, text="Настройки Telegram", padding=10)
        telegram_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(telegram_frame, text="Токен бота:").grid(row=0, column=0, sticky=tk.W)
        self.token_entry = ttk.Entry(telegram_frame, width=50)
        self.token_entry.grid(row=0, column=1, sticky=tk.EW, padx=5)
        
        ttk.Label(telegram_frame, text="ID канала:").grid(row=1, column=0, sticky=tk.W)
        self.channel_entry = ttk.Entry(telegram_frame, width=50)
        self.channel_entry.grid(row=1, column=1, sticky=tk.EW, padx=5)

        # Настройки YouTube Music
        youtube_frame = ttk.LabelFrame(self.settings_frame, text="Настройки YouTube Music", padding=10)
        youtube_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(youtube_frame, text="URL плейлиста:").grid(row=0, column=0, sticky=tk.W)
        self.youtube_url_entry = ttk.Entry(youtube_frame, width=50)
        self.youtube_url_entry.grid(row=0, column=1, sticky=tk.EW, padx=5)
        
        self.use_cookies_var = tk.BooleanVar()
        ttk.Checkbutton(
            youtube_frame,
            text="Использовать cookies",
            variable=self.use_cookies_var
        ).grid(row=1, column=0, sticky=tk.W)
        
        ttk.Label(youtube_frame, text="Файл cookies:").grid(row=2, column=0, sticky=tk.W)
        self.cookies_entry = ttk.Entry(youtube_frame, width=40)
        self.cookies_entry.grid(row=2, column=1, sticky=tk.EW, padx=5)
        
        ttk.Button(
            youtube_frame,
            text="Обзор...",
            command=self.browse_cookies_file
        ).grid(row=2, column=2, padx=5)

        # Интервал отправки
        interval_frame = ttk.LabelFrame(self.settings_frame, text="Интервал отправки", padding=10)
        interval_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(interval_frame, text="Интервал (мин):").grid(row=0, column=0, sticky=tk.W)
        self.interval_entry = ttk.Entry(interval_frame, width=10)
        self.interval_entry.grid(row=0, column=1, sticky=tk.W, padx=5)

        # Кнопки сохранения
        btn_frame = ttk.Frame(self.settings_frame)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Button(
            btn_frame,
            text="Сохранить настройки",
            command=self.save_config
        ).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(
            btn_frame,
            text="Сбросить к default",
            command=self.reset_config
        ).pack(side=tk.LEFT, padx=5)

    def setup_log_tab(self):
        self.log_text = scrolledtext.ScrolledText(
            self.log_frame,
            wrap=tk.WORD,
            width=100,
            height=25,
            font=('Courier New', 9)
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        ttk.Button(
            self.log_frame,
            text="Очистить логи",
            command=self.clear_logs
        ).pack(pady=5)

    def browse_cookies_file(self):
        filepath = filedialog.askopenfilename(
            title="Выберите файл cookies",
            filetypes=(("Text files", "*.txt"), ("All files", "*.*"))
        )
        if filepath:
            self.cookies_entry.delete(0, tk.END)
            self.cookies_entry.insert(0, filepath)

    def update_logs(self):
        while not log_queue.empty():
            self.log_text.insert(tk.END, log_queue.get() + "\n")
            self.log_text.see(tk.END)
        self.root.after(500, self.update_logs)

    def clear_logs(self):
        self.log_text.delete(1.0, tk.END)

    def load_config(self):
        self.token_entry.insert(0, CONFIG['telegram_token'])
        self.channel_entry.insert(0, CONFIG['telegram_channel'])
        self.youtube_url_entry.insert(0, CONFIG['youtube_url'])
        self.interval_entry.insert(0, str(CONFIG['check_interval'] // 60))
        self.use_cookies_var.set(CONFIG['use_cookies'])
        self.cookies_entry.insert(0, CONFIG['cookies_file'])

    def save_config(self):
        try:
            CONFIG.update({
                'telegram_token': self.token_entry.get(),
                'telegram_channel': self.channel_entry.get(),
                'youtube_url': self.youtube_url_entry.get(),
                'check_interval': int(self.interval_entry.get()) * 60,
                'use_cookies': self.use_cookies_var.get(),
                'cookies_file': self.cookies_entry.get()
            })
            
            if not validate_telegram_token(CONFIG['telegram_token']):
                messagebox.showerror("Ошибка", "Неверный формат Telegram токена!")
                return
                
            messagebox.showinfo("Успех", "Настройки сохранены!")
            logger.info("Настройки обновлены")
            
        except ValueError:
            messagebox.showerror("Ошибка", "Проверьте правильность значений!")
            logger.error("Ошибка сохранения настроек")

    def reset_config(self):
        global CONFIG
        CONFIG = DEFAULT_CONFIG.copy()
        self.clear_settings_fields()
        self.load_config()
        messagebox.showinfo("Успех", "Настройки сброшены!")
        logger.info("Настройки сброшены к default")

    def clear_settings_fields(self):
        self.token_entry.delete(0, tk.END)
        self.channel_entry.delete(0, tk.END)
        self.youtube_url_entry.delete(0, tk.END)
        self.interval_entry.delete(0, tk.END)
        self.cookies_entry.delete(0, tk.END)
        self.use_cookies_var.set(False)

    def start_bot(self):
        global bot_running
        if not bot_running:
            if not validate_telegram_token(CONFIG['telegram_token']):
                messagebox.showerror("Ошибка", "Неверный формат Telegram токена!")
                return
                
            bot_running = True
            self.start_button.config(state=tk.DISABLED)
            self.stop_button.config(state=tk.NORMAL)
            self.status_label.config(text="Статус: Бот запущен", foreground="green")
            Thread(target=self.run_bot, daemon=True).start()
            logger.info("Бот запущен")
        else:
            messagebox.showwarning("Внимание", "Бот уже запущен!")

    def stop_bot(self):
        global bot_running
        bot_running = False
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.status_label.config(text="Статус: Бот остановлен", foreground="red")
        logger.info("Бот остановлен")

    def test_send(self):
        if not validate_telegram_token(CONFIG['telegram_token']):
            messagebox.showerror("Ошибка", "Неверный формат Telegram токена!")
            return
            
        if not CONFIG['youtube_url']:
            messagebox.showerror("Ошибка", "Укажите URL плейлиста YouTube Music!")
            return
            
        Thread(target=self._test_send, daemon=True).start()

    def _test_send(self):
        try:
            logger.info("Начинаем тестовую отправку...")
            
            if not (tracks := YouTubeMusicParser.get_tracks_from_url(CONFIG['youtube_url'])):
                messagebox.showerror("Ошибка", "Не удалось получить треки с YouTube Music!")
                return
                
            track = random.choice(tracks)
            logger.info(f"Выбран тестовый трек: {track['artist']} - {track['title']}")
            
            if track_data := TrackDownloader.download(track):
                if TelegramSender.send_track(track_data):
                    messagebox.showinfo("Успех", "Тестовая отправка выполнена!")
                    logger.info("Тестовая отправка успешна")
                else:
                    messagebox.showerror("Ошибка", "Ошибка тестовой отправки")
                    logger.error("Тестовая отправка не удалась")
            else:
                messagebox.showerror("Ошибка", "Не удалось загрузить трек")
                logger.error("Ошибка загрузки трека")
                
        except Exception as e:
            messagebox.showerror("Ошибка", f"Ошибка при тестовой отправке: {str(e)}")
            logger.error(f"Ошибка тестовой отправки: {e}")

    def run_bot(self):
        global bot_running
        cleanup_temp_files()
        
        while bot_running:
            try:
                logger.info("Собираем треки из YouTube Music...")
                
                if not (tracks := YouTubeMusicParser.get_tracks_from_url(CONFIG['youtube_url'])):
                    logger.warning("Не удалось получить треки. Повтор через 10 минут.")
                    time.sleep(600)
                    continue
                
                logger.info(f"Найдено треков: {len(tracks)}")
                track = random.choice(tracks)
                logger.info(f"Выбран трек: {track['artist']} - {track['title']}")
                
                self.root.after(0, lambda: self.update_last_track(track))
                
                if track_data := TrackDownloader.download(track):
                    if not TelegramSender.send_track(track_data):
                        logger.warning("Ошибка отправки трека")
                else:
                    logger.warning("Ошибка загрузки трека")
                
                logger.info(f"Ожидание {CONFIG['check_interval']//60} минут...")
                for _ in range(CONFIG['check_interval']):
                    if not bot_running:
                        return
                    time.sleep(1)
                
            except Exception as e:
                logger.error(f"Ошибка в основном цикле: {e}")
                time.sleep(300)

    def update_last_track(self, track):
        duration = track.get('duration', 'N/A')
        if isinstance(duration, int):
            duration = f"{duration // 60}:{duration % 60:02d}"
        self.last_track_label.config(
            text=f"{track.get('artist', 'Unknown')} - {track.get('title', 'Unknown')}\nДлительность: {duration}"
        )

if __name__ == "__main__":
    os.makedirs(DEFAULT_CONFIG['temp_folder'], exist_ok=True)
    root = tk.Tk()
    app = MusicBotGUI(root)
    root.mainloop()
    cleanup_temp_files()
