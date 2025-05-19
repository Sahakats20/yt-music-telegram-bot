#!/bin/bash

sudo apt install python3-tk ffmpeg sqlite3
sudo apt-get install python3-tk

# Обновление pip
python3 -m pip install --upgrade pip --break-system-packages

# Установка зависимостей
pip install -r requirements.txt --break-system-packages


python3 cookies.py ~/.config/google-chrome/Default/Cookies
# Запуск программы
python3 main.py
