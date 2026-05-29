import os
import machine
import gc
import network
import webrepl
import time

# Функция подключения к Wi-Fi
def do_connect():
    wlan = network.WLAN(network.STA_IF)
    if wlan.isconnected():
        print("Уже в сети:", wlan.ifconfig())
        return
    wlan.active(True)
    wlan.connect('4G-MIFI-533B', '1234567890')
    for _ in range(10):
        if wlan.isconnected(): break
        time.sleep(1)
    print('Network config:', wlan.ifconfig())

# Функция для безопасного чтения версии без импорта
def get_version():
    try:
        with open("version.py", "r") as f:
            content = f.read()
            if "=" in content:
                # Извлекаем значение после '=', убираем кавычки
                return content.split("=")[1].replace('"', '').replace("'", "").strip()
    except:
        pass
    return "0.0"

do_connect()
webrepl.start()

FILES = os.listdir()

# Локальная версия прошивки на плате (читаем файл, а не импортируем)
current_version = get_version()
print(f"[BOOT] Текущая версия системы: {current_version}")

# 1. ЗАЩИТА И ОТКАТ: Проверяем маркер «испытательного срока»
if "purgatory.txt" in FILES:
    print("[BOOT] ВНИМАНИЕ: Новое обновление упало при запуске! Начинаем откат...")
    
    if "main.py" in FILES:
        os.remove("main.py")
        
    if "main_backup.py" in FILES:
        os.rename("main_backup.py", "main.py")
        
    os.remove("purgatory.txt")
    print("[BOOT] Откат завершен. Восстановлена старая рабочая версия.")
    machine.reset() # Перезагрузка после отката

# 2. УСТАНОВКА ОБНОВЛЕНИЯ
elif "main_new.py" in FILES:
    print("[BOOT] Найдено свежее обновление. Подготовка...")
    
    # Делаем бэкап
    if "main.py" in FILES:
        if "main_backup.py" in FILES:
            os.remove("main_backup.py")
        os.rename("main.py", "main_backup.py")
        
    # Устанавливаем новую прошивку
    os.rename("main_new.py", "main.py")
    
    # Устанавливаем новую версию
    if "version_new.py" in FILES:
        if "version.py" in FILES: os.remove("version.py")
        os.rename("version_new.py", "version.py")
        
    # Ставим маркер
    with open("purgatory.txt", "w") as f:
        f.write("testing")
        
    print("[BOOT] Обновление установлено. Запуск в тестовом режиме...")
    machine.reset() # Перезагрузка для применения