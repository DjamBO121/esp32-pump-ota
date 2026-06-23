from machine import Pin, UART, PWM, I2C, SPI
import time
import os
from ds3231 import DS3231
from sdcard import SDCard
import urequests
import network, socket, ssl, gc, machine

MAIN_URL = "https://raw.githubusercontent.com/DjamBO121/esp32-pump-ota/refs/heads/main/main.py"

# --- Настройки ---
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
    print("Ошибка при сбросе флага RTC:", e)

# Состояния
is_fueling = False
flow_pulses = 0
last_pulse_time = 0
current_car_num = ""
current_card = ""
sd_mounted=False
last_card_id = ""
last_read_time = 0
relay.value(0)
green_led.value(1)
red_led.value(1)
emergency_flag = False
last_press_time = 0

def get_web_text(url):
    gc.collect()
    parts = url.split('/')
    host = parts[2]
    path = '/' + '/'.join(parts[3:])
    
    try:
        s = socket.socket()
        s.settimeout(10.0)
        addr = socket.getaddrinfo(host, 443)[0][-1]
        s.connect(addr)
        s = ssl.wrap_socket(s, server_hostname=host)
        s.write(b"GET %s HTTP/1.0\r\nHost: %s\r\nUser-Agent: ESP32\r\nConnection: close\r\n\r\n" % (path, host))
        
        res = b""
        while True:
            data = s.read(128)
            if not data: break
            res += data
        s.close()
        return res.split(b"\r\n\r\n", 1)[1].decode().strip()
    except Exception as e:
        print("Ошибка сети:", e)
        return None

def run_ota_check():
    print("Проверка наличия обновлений...")
    import time
    url = "https://raw.githubusercontent.com/DjamBO121/esp32-pump-ota/refs/heads/main/version.txt?t=" + str(time.time())
    remote_ver = get_web_text(url)
    if remote_ver is None: # Явно проверяем на None
        print("Не удалось получить версию, пропускаем обновление.")
        return
    
    remote_ver = remote_ver.strip()
    try:
        with open('version.txt', 'r') as f: local_ver = f.read().strip()
    except: local_ver = "0"
        
    if remote_ver.strip() == local_ver:
        return

    print(f"Найдено обновление {remote_ver}. Скачиваю...")
    
    # 3. Скачиваем новый код во временный файл main.new
    code = get_web_text(MAIN_URL)
    
    # Проверка: получили ли мы вообще данные?
    if code and len(code) > 100: # 100 байт - защита от пустого ответа
        with open('main.new', 'w') as f:
            f.write(code)
        
        # 4. Безопасная подмена
        if 'main.py' in os.listdir():
            if 'main.old' in os.listdir():
                os.remove('main.old') # Удаляем старый бэкап
            os.rename('main.py', 'main.old')
        
        os.rename('main.new', 'main.py')
        
        # 5. Обновляем версию
        with open('version.txt', 'w') as f:
            f.write(remote_ver)
            
        print("Обновление успешно. Перезагрузка.")
        machine.reset()
    else:
        print("Ошибка: скачанный файл пуст или поврежден.")


def emergency_reset(pin):
    global emergency_flag, last_press_time
    current_time = time.ticks_ms()
    # Если нажатие было менее 500мс назад, игнорируем (защита от помех)
    if time.ticks_diff(current_time, last_press_time) > 500:
        red_led.value(0)
        emergency_flag = True
        last_press_time = current_time
reset_btn.irq(trigger=Pin.IRQ_FALLING, handler=emergency_reset)

def mount_sd():
    global sd_mounted
    try:
        spi = SPI(1, baudrate=1000000, sck=Pin(18), mosi=Pin(23), miso=Pin(19))
        cs = Pin(5, Pin.OUT)
        os.mount(SDCard(spi, cs), '/sd')
        print("SD карта смонтирована.")
        sd_mounted=True
        return True
    except Exception as e:
        print(f" -> КРИТИЧЕСКАЯ ОШИБКА КАРТЫ: {e}")
        sd_mounted=False
        return False

mount_sd()

def play_tone(duration):
    buzzer.value(1) # Подаем 3.3В - он пищит сам
    time.sleep(duration)
    buzzer.value(0) # Выключаем

def play_success():
    # Два коротких сигнала успеха
    play_tone(0.1)
    time.sleep(0.1)
    play_tone(0.1)
    green_led.value(0)

def play_decline():
    # Один длинный сигнал ошибки
    play_tone(0.6)
    red_led.value(0)
    time.sleep(3)
    red_led.value(1)
def count_pulse(p):
    global flow_pulses, last_pulse_time
    if is_fueling:
        flow_pulses += 1
        last_pulse_time = time.time()

flow_sensor.irq(trigger=Pin.IRQ_FALLING, handler=count_pulse)

def get_allowed_user(card_id):
    # Очищаем ID от всего, кроме цифр
    target_id = "".join([c for c in card_id if c.isdigit()])
    print(f"DEBUG: Ищем в базе строго цифры: '{target_id}'")
    
    try:
        with open('/sd/users.txt', 'r') as f:
            for line in f:
                # Очищаем строку из файла от всего, кроме цифр и запятых
                clean_line = line.strip()
                if ',' not in clean_line:
                    continue
                
                parts = clean_line.split(',')
                file_id = "".join([c for c in parts[0] if c.isdigit()])
                car_num = parts[1].strip()
                
                # Сравниваем только очищенные цифры
                if file_id == target_id:
                    print(f"DEBUG: НАШЛИ! ID={file_id}, Машина={car_num}")
                    return car_num
                    
                # Печатаем для диагностики, с чем сравниваем
                # print(f"DEBUG: Сравниваем '{target_id}' с '{file_id}'")
                
    except Exception as e:
        print("Ошибка при чтении файла:", e)
    
    print(f"DEBUG: Карта {target_id} не найдена в базе!")
    return None

def log_transaction(card_id, car_num, liters):
    if not sd_mounted:
        return None
    t = rtc.datetime()
    ts = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*t[:3], *t[4:7])
    try:
        with open('/sd/log.csv', 'a') as f:
            f.write(f"{ts};{card_id};{car_num};{liters:.2f}\n")
    except:
        print("Ошибка записи лога")

def main():
    print("Запуск основной программы...")
    global is_fueling, flow_pulses, last_pulse_time, current_car_num, current_card
    global sd_mounted, last_card_id, last_read_time, emergency_flag, last_press_time
    # 1. Фоновая проверка обновлений (один раз при старте)
    wlan = network.WLAN(network.STA_IF)
    if wlan.isconnected():
        try:
            run_ota_check()
        except Exception as e:
            print("Ошибка при фоновой проверке обновлений:", e)
    
    # 2. ОСНОВНОЙ ЦИКЛ (здесь ваш код насоса/управления)
    print("Система готова.")

    while sd_mounted:
        
        if emergency_flag:
            emergency_flag = False
            print("\n!!! АВАРИЙНЫЙ ПЕРЕХВАТ !!!")
            if is_fueling:
                liters = flow_pulses / 60
                if liters > 0.01:
                    log_transaction(current_card, current_car_num, liters)
                print(f"Данные сохранены: {liters:.2f} л.")
            
            print("Перезагрузка...")
            time.sleep(0.5)
            import machine
            machine.reset()
            
        if not is_fueling:
            if uart.any():
                time.sleep(0.05)
                data = uart.read()
                # Очистка буфера от "мусора"
                while uart.any(): uart.read()
                
                data_str = data.decode('ascii', 'ignore')
                hex_part = "".join([c for c in data_str if c in "0123456789ABCDEFabcdef"])
                
                if len(hex_part) >= 12:
                    card_id = "{:010d}".format(int(hex_part[4:10], 16))
                    
                    current_time = time.time()
                    if card_id == last_card_id and (current_time - last_read_time < 2):
                        continue
                    
                    last_card_id = card_id
                    last_read_time = current_time
                    
                    if not sd_mounted:
                        print("[БЛОКИРОВКА] Попытка аварийного переподключения SD карты...")
                        if not mount_sd():
                            print("[ОТКАЗ] Заправка заблокирована: Локальная база данных и логи недоступны!")
                            play_decline()
                            continue
                    
                    car_num = get_allowed_user(card_id)
                    if car_num:
                        print("Разрешено:", car_num)
                        relay.value(1)
                        play_success()
                        current_card, current_car_num = card_id, car_num
                        flow_pulses = 0
                        is_fueling = True
                        last_pulse_time = time.time()
                    else:
                        print("Доступ запрещен!")
                        play_decline()
        else:
            # Логика заправки
            # Если прошло более 10 секунд после последнего импульса
            if time.time() - last_pulse_time > 10:
                relay.value(0)
                green_led.value(1)
                liters = flow_pulses/60
                is_fueling = False
                
                if liters > 0.01:
                    log_transaction(current_card, current_car_num, liters)
                if emergency_flag:
                    import machine
                    machine.reset()
                    emergency_flag= False
                print("Заправка завершена. Ожидание удаления карты...")
                time.sleep(3)
                last_card_id = ""
                
                print(f"Заправка завершена. Литров: {liters:.2f}")
                
                start_wait = time.time()
                while True:
                    if emergency_flag:
                        import machine
                        machine.reset()
                    if uart.any():
                        uart.read()
                        start_wait = time.time() # Обновляем время, если считыватель "кричит"
                    
                    # Если прошло 2 секунды тишины от считывателя - значит карту убрали
                    if time.time() - start_wait > 1:
                        break
                    time.sleep(0.1)
                
                last_card_id = "" # Теперь можно сбросить
                print("Карта убрана. Готов к работе.")
                
        time.sleep(0.1)

if __name__ == "__main__":
    # Вызываем обновление один раз при запуске
    try:
        run_ota_check()
    except Exception as e:
        print("Обновление не удалось:", e)
    main()
