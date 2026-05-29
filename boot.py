import os
import machine
import gc

# 1. Приведение файловой системы в порядок
files = os.listdir()

# Если есть маркер теста (purgatory.txt), значит, предыдущее обновление 
# прошло успешно. Удаляем маркер, так как плата загрузилась нормально.
if "purgatory.txt" in files:
    os.remove("purgatory.txt")
    print("[BOOT] Система успешно загрузилась после обновления.")

# 2. Проверка: есть ли новое обновление?
if "main_new.py" in files:
    print("[BOOT] Обнаружено обновление...")
    
    # Делаем бэкап текущего рабочего файла, если он существует
    if "main.py" in files:
        if "main_backup.py" in files:
            os.remove("main_backup.py")
        os.rename("main.py", "main_backup.py")
    
    # Заменяем старый файл на новый
    os.rename("main_new.py", "main.py")
    
    # Обновляем версию, если есть файл версии
    if "version_new.py" in files:
        if "version.py" in files:
            os.remove("version.py")
        os.rename("version_new.py", "version.py")
        
    # Ставим "испытательный маркер"
    # Если на этом этапе (в main.py) произойдет сбой (например, ошибка в коде),
    # этот файл останется, и при следующей перезагрузке мы сделаем откат.
    with open("purgatory.txt", "w") as f:
        f.write("testing")
        
    print("[BOOT] Обновление установлено. Перезапуск...")
    machine.reset()

# 3. Дополнительная защита: если произошел сбой и файл purgatory.txt остался
elif "purgatory.txt" in files:
    print("[BOOT] ОШИБКА ОБНОВЛЕНИЯ! Откат к старой версии...")
    if "main.py" in files:
        os.remove("main.py")
    if "main_backup.py" in files:
        os.rename("main_backup.py", "main.py")
    os.remove("purgatory.txt")
    machine.reset()

# Если всё хорошо, просто продолжаем загрузку системы
print("[BOOT] Запуск основной программы...")
gc.collect()