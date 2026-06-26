from machine import Pin, UART, I2C, SPI
import time
import os
import gc
import machine
import socket
import ssl
import json
import urequests
import network
from ds3231 import DS3231
from sdcard import SDCard

MAIN_URL = "https://raw.githubusercontent.com/DjamBO121/esp32-pump-ota/refs/heads/main/main.py"
BASE_URL = "https://script.google.com/macros/s/AKfycbxgUXeezVTChMpPMqKrzmwuBcK_B0LPxx8yJghvgyBK_1_FrHZiK9EpyVJn-7vShRL4/exec"

# Инициализация пинов
reset_btn = Pin(4, Pin.IN, Pin.PULL_UP)
uart = UART(2, baudrate=9600, tx=17, rx=16)
i2c = I2C(0, scl=Pin(22), sda=Pin(21))
rtc = DS3231(i2c)
relay = Pin(25, Pin.OUT)
green_led = Pin(15, Pin.OUT)
red_led = Pin(13, Pin.OUT)
buzzer = Pin(14, Pin.OUT)
flow_sensor = Pin(32, Pin.IN, Pin.PULL_UP)

try:
    status = i2c.readfrom_mem(0x68, 0x0F, 1)[0]
    i2c.writeto_mem(0x68, 0x0F, bytes([status & 0x7F]))
except Exception as e:
    print("Ошибка сброса флага RTC:", e)

# Глобальные переменные состояния
is_fueling = False
flow_pulses = 0
last_pulse_time = 0
current_car_num = ""
current_card = ""
sd_mounted = False
last_card_id = ""
last_read_time = 0
relay.value(0)
emergency_flag = False
last_press_time = 0

def urlencode(s):
    result = ""
    for char in str(s):
        if 'a' <= char <= 'z' or 'A' <= char <= 'Z' or '0' <= char <= '9' or char in "-_.~":
            result += char
        else:
            for b in char.encode('utf-8'):
                result += "%{:02X}".format(b)
    return result

def send_to_google(card_id, car_num, liters, timestamp):
    gc.collect()
    ts_enc = urlencode(timestamp)
    card_enc = urlencode(str(card_id))
    car_enc = urlencode(car_num)
    liters_enc = urlencode(liters)
    
    # Вырезаем хост и путь вручную из BASE_URL
    host = "script.google.com"
    path = BASE_URL.replace("https://script.google.com", "") + f"?ts={ts_enc}&card={card_enc}&car={car_enc}&liters={liters_enc}"
    
    try:
        print("DEBUG: Снайперский GET-запрос...")
        s = socket.socket()
        s.settimeout(11.0)  # ЖЕСТКИЙ ПРЕДОХРАНИТЕЛЬ: ровно 7 секунд на всё
        
        addr = socket.getaddrinfo(host, 443)[0][-1]
        s.connect(addr)
        s = ssl.wrap_socket(s, server_hostname=host)
        
        request = f"GET {path} HTTP/1.0\r\nHost: {host}\r\nUser-Agent: ESP32\r\nConnection: close\r\n\r\n"
        s.write(request.encode())
        
        # Читаем ТОЛЬКО верхушку заголовка (первые 40 байт)
        response_head = s.read(40).decode('utf-8', 'ignore')
        
        s.close()
        del s
        gc.collect()
        
        # Google Apps Script при успешном приеме ВСЕГДА отвечает кодом 302
        if "302" in response_head or "200" in response_head:
            print("DEBUG: Успех! Сервер подтвердил прием.")
            return True
        else:
            print(f"DEBUG: Странный заголовок: {response_head[:25]}")
            return False

    except Exception as e:
        print("DEBUG: Таймаут или сбой сокета:", e)
        gc.collect()
        return False

def get_web_text(url):
    gc.collect()
    for _ in range(3):
        parts = url.split('/')
        host = parts[2]
        path = '/' + '/'.join(parts[3:])
        
        try:
            s = socket.socket()
            s.settimeout(15.0)
            addr = socket.getaddrinfo(host, 443)[0][-1]
            s.connect(addr)
            s = ssl.wrap_socket(s, server_hostname=host)
            s.write(b"GET %s HTTP/1.0\r\nHost: %s\r\nUser-Agent: ESP32\r\nConnection: close\r\n\r\n" % (path, host))
            
            res = b""
            while b"\r\n\r\n" not in res:
                res += s.read(32)
            
            header, body = res.split(b"\r\n\r\n", 1)
            header_str = header.decode()
            
            if "302" in header_str or "301" in header_str:
                for line in header_str.split('\r\n'):
                    if "Location:" in line:
                        new_url = line.split("Location: ")[1].strip()
                        if "action=get_users" not in new_url and "action=" in url:
                            new_url += "&action=get_users"
                        url = new_url
                        s.close()
                        break
            else:
                while True:
                    data = s.read(128)
                    if not data: break
                    body += data
                s.close()
                return body.decode().strip()
        except Exception as e:
            print("Ошибка чтения web-текста:", e)
            return None
    return None

def download_file_streamed(url, target_filename):
    gc.collect()
    parts = url.split('/')
    host = parts[2]
    path = '/' + '/'.join(parts[3:])
    
    try:
        s = socket.socket()
        s.settimeout(20.0)
        addr = socket.getaddrinfo(host, 443)[0][-1]
        s.connect(addr)
        s = ssl.wrap_socket(s, server_hostname=host)
        s.write(b"GET %s HTTP/1.0\r\nHost: %s\r\nUser-Agent: ESP32\r\nConnection: close\r\n\r\n" % (path, host))
        
        header = b""
        while b"\r\n\r\n" not in header:
            header += s.read(1)
            
        with open(target_filename, 'w') as f:
            while True:
                data = s.read(512)
                if not data: break
                f.write(data)
                gc.collect()
        s.close()
        return True
    except Exception as e:
        print("Ошибка потоковой загрузки OTA:", e)
        return False

def save_to_local_db(card_id, car_num):
    try:
        with open('/sd/users.txt', 'r') as f:
            if any(card_id in line for line in f):
                return
        with open('/sd/users.txt', 'a') as f:
            f.write(f"{card_id},{car_num}\n")
    except OSError:
        with open('/sd/users.txt', 'w') as f:
            f.write(f"{card_id},{car_num}\n")

def mount_sd():
    global sd_mounted
    try:
        spi = SPI(1, baudrate=1000000, sck=Pin(18), mosi=Pin(23), miso=Pin(19))
        cs = Pin(5, Pin.OUT)
        os.mount(SDCard(spi, cs), '/sd')
        print("SD карта смонтирована.")
        sd_mounted = True
        return True
    except Exception as e:
        print(f" -> КРИТИЧЕСКАЯ ОШИБКА SD-КАРТЫ: {e}")
        sd_mounted = False
        return False

def play_tone(duration):
    buzzer.value(1)
    time.sleep(duration)
    buzzer.value(0)

def play_success():
    play_tone(0.1)
    time.sleep(0.1)
    play_tone(0.1)
    green_led.value(0)
    time.sleep(2)
    green_led.value(1)

def play_decline():
    play_tone(0.6)
    red_led.value(0)
    time.sleep(3)
    red_led.value(1)
    time.sleep(2)
    red_led.value(0)

def emergency_reset(pin):
    global emergency_flag, last_press_time
    curr = time.ticks_ms()
    if time.ticks_diff(curr, last_press_time) > 500:
        red_led.value(0)
        emergency_flag = True
        last_press_time = curr

def count_pulse(p):
    global flow_pulses, last_pulse_time
    if is_fueling:
        flow_pulses += 1
        last_pulse_time = time.time()

reset_btn.irq(trigger=Pin.IRQ_FALLING, handler=emergency_reset)
flow_sensor.irq(trigger=Pin.IRQ_FALLING, handler=count_pulse)

def get_allowed_user(card_id):
    target_id = "".join([c for c in card_id if c.isdigit()])
    try:
        with open('/sd/users.txt', 'r') as f:
            for line in f:
                clean = line.strip()
                if ',' not in clean: continue
                parts = clean.split(',')
                file_id = "".join([c for c in parts[0] if c.isdigit()])
                if file_id == target_id:
                    return parts[1].strip()
    except Exception as e:
        print("Ошибка чтения локальной базы:", e)
    return None

def sync_logs():
    print("DEBUG: Запуск проверки очереди логов...")
    wlan = network.WLAN(network.STA_IF)
    if not wlan.isconnected():
        print("DEBUG: Нет Wi-Fi соединения.")
        return

    try:
        files = os.listdir('/sd/logs')
    except Exception:
        return

    for file in files:
        if file.endswith(".json"):
            path = f"/sd/logs/{file}"
            try:
                with open(path, "r") as f:
                    content = json.loads(f.read())
                
                if content["status"] == "PENDING":
                    d = content["data"]
                    if send_to_google(d["card"], d["car"], d["liters"], d["ts"]):
                        os.remove(path)
                        gc.collect()
                        print(f"DEBUG: Очередь очищена: {file} удален.")
            except Exception as e:
                print(f"DEBUG: Ошибка обработки файла {file}: {e}")

def log_transaction(card_id, car_num, liters):
    if not sd_mounted: return
    t = rtc.datetime()
    ts_str = "{:04d}{:02d}{:02d}_{:02d}{:02d}{:02d}".format(*t[:3], *t[4:7])
    ts_fmt = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*t[:3], *t[4:7])
    
    liters = "{:.1f}".format(liters).replace('.', ',')
    
    try:
        with open('/sd/log.csv', 'a') as f:
            f.write(f"{ts_str};{card_id};{car_num};{liters}\n")
    except Exception: pass

    try: os.mkdir('/sd/logs')
    except Exception: pass
        
    filename = f"/sd/logs/log_{ts_str}.json"
    data = {
    "ts": ts_fmt,
    "card": card_id,
    "car": car_num,
    "liters": liters
    }
    with open(filename, "w") as f:
        f.write(json.dumps({"status": "PENDING", "data": data}))
    
    print(f"Сохранен офлайн-чек: {filename}")
    sync_logs()

def run_ota_check():
    print("Проверка обновлений прошивки...")
    url = f"https://raw.githubusercontent.com/DjamBO121/esp32-pump-ota/refs/heads/main/version.txt?t={time.time()}"
    remote_ver = get_web_text(url)
    if not remote_ver: return
    
    remote_ver = remote_ver.strip()
    try:
        with open('version.txt', 'r') as f: local_ver = f.read().strip()
    except Exception: local_ver = "0"
        
    if remote_ver != local_ver:
        print(f"Скачивание прошивки версии {remote_ver}...")
        if download_file_streamed(MAIN_URL, 'main.new'):
            if 'main.py' in os.listdir(): os.rename('main.py', 'main.old')
            os.rename('main.new', 'main.py')
            with open('version.txt', 'w') as f: f.write(remote_ver)
            print("Обновление применено. Перезапуск...")
            machine.reset()

def sync_users_from_google():
    print("Обновление белого списка карт...")
    raw = get_web_text(BASE_URL + "?action=get_users")
    if not raw or "HTML" in raw or "Error" in raw: return
    
    local_users = {}
    try:
        with open('/sd/users.txt', 'r') as f:
            for line in f:
                if ',' in line:
                    p = line.strip().split(',')
                    local_users[p[0].strip()] = p[1].strip()
    except Exception: pass

    db_changed = False  # Флаг, чтобы зря не насиловать SD-карту перезаписью файлов

    for line in raw.strip().split('\n'):
        parts = line.split(',')
        if len(parts) < 2: continue
        c_id, c_car = parts[0].strip(), parts[1].strip()
        c_stat = parts[2].strip() if len(parts) > 2 else ""

        # РЕЖИМ 1: УДАЛЕНИЕ КАРТЫ
        if c_stat == "Удалить":
            if c_id in local_users:
                del local_users[c_id]
                db_changed = True
                print(f"Карта {c_id} удалена локально.")
            get_web_text(f"{BASE_URL}?action=update_status&id={c_id}&status=Удалено")
        
        # РЕЖИМ 2: ИЗМЕНЕНИЕ НОМЕРА МАШИНЫ
        elif c_stat == "Изменить":
            # Находим по ID и перезаписываем номер авто (даже если карты почему-то не было, она запишется корректно)
            local_users[c_id] = c_car
            db_changed = True
            print(f"Карта {c_id}: изменен номер машины на {c_car}.")
            get_web_text(f"{BASE_URL}?action=update_status&id={c_id}&status=Изменено")
        
        # РЕЖИМ 3: ДОБАВЛЕНИЕ НОВОЙ КАРТЫ
        elif c_stat == "":
            local_users[c_id] = c_car
            db_changed = True
            print(f"Карта {c_id}: добавлена в базу для машины {c_car}.")
            get_web_text(f"{BASE_URL}?action=update_status&id={c_id}&status=Добавлено")

    # Перезаписываем локальную базу на SD-карте только если были реальные изменения
    if db_changed:
        try:
            with open('/sd/users.txt', 'w') as f:
                for uid, ucar in local_users.items():
                    f.write(f"{uid},{ucar}\n")
            print("Локальный список карт на SD успешно обновлен.")
        except Exception as e:
            print("Критическая ошибка сохранения базы карт на SD:", e)

def main():
    global is_fueling, flow_pulses, last_pulse_time, current_car_num, current_card
    global last_card_id, last_read_time, emergency_flag
    
    print("Контроллер АЗС запущен.")
    while sd_mounted:
        green_led.value(1)
        red_led.value(1)
        
        if emergency_flag:
            emergency_flag = False
            relay.value(0)
            green_led.value(1)
            red_led.value(0)
            if is_fueling:
                l = flow_pulses / 60
                if l > 0.01:
                    log_transaction(current_card, current_car_num, l)
            time.sleep(0.5)
            machine.reset()
            
        if not is_fueling:
            if uart.any():
                time.sleep(0.05)
                data = uart.read()
                while uart.any(): uart.read()
                
                data_str = data.decode('ascii', 'ignore')
                hex_p = "".join([c for c in data_str if c in "0123456789ABCDEFabcdef"])
                
                if len(hex_p) >= 12:
                    card_id = "{:010d}".format(int(hex_p[4:10], 16))
                    if card_id == last_card_id and (time.time() - last_read_time < 2): continue
                    
                    last_card_id = card_id
                    last_read_time = time.time()
                    
                    if not sd_mounted:
                        if not mount_sd():
                            play_decline()
                            continue
                    
                    car_num = get_allowed_user(card_id)
                    if car_num:
                        relay.value(1)
                        play_success()
                        current_card, current_car_num = card_id, car_num
                        flow_pulses = 0
                        is_fueling = True
                        last_pulse_time = time.time()
                    else:
                        play_decline()
                        time.sleep(0.5)
        else:
            if time.time() - last_pulse_time > 10:
                relay.value(0)
                green_led.value(1)
                liters = flow_pulses / 60
                is_fueling = False
                
                if liters > 0.01:
                    log_transaction(current_card, current_car_num, liters)
                if emergency_flag: machine.reset()
                
                print("Заправка окончена. Уберите карту...")
                time.sleep(3)
                start_w = time.time()
                while True:
                    if emergency_flag: machine.reset()
                    if uart.any():
                        uart.read()
                        start_w = time.time()
                    if time.time() - start_w > 1: break
                    time.sleep(0.1)
                last_card_id = ""
        time.sleep(0.1)

mount_sd()

if __name__ == "__main__":
    if sd_mounted:
        try: sync_logs()
        except Exception as e: print("Сбой отправки логов:", e)
        try: sync_users_from_google()
        except Exception as e: print("Сбой синхронизации карт:", e)
        try: run_ota_check()
        except Exception as e: print("Сбой OTA:", e)
        main()
