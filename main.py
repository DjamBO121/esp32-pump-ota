from machine import Pin, SoftSPI, SoftI2C
import os
import time
import network
import json
import urequests
import gc

# Определяем текущую версию системы
VERSION = "1.1

# ==============================================================================
# НАСТРОЙКА ОБЛАКА
# ==============================================================================
GOOGLE_SCR_URL = "https://script.google.com/macros/s/AKfycbyZ5EGJND9ojdwtXGyHQD9AiTBIZNNkggA0OnEEMFzhUcRpQ-cqx3Ss35XwCrQAA9t_/exec"

# ==============================================================================
# НАСТРОЙКИ ПИНОВ И ОБОРУДОВАНИЯ
# ==============================================================================
PIN_SDA, PIN_SCL = 21, 22       # Часы DS3231 (I2C)

# Конфигурация MicroSD карты (SPI)
PIN_MISO = 4
PIN_MOSI = 2
PIN_CS   = 18
PIN_SCK  = 5

PIN_D0, PIN_D1 = 26, 27         # Считыватель карт Wiegand
PIN_RELAY, PIN_BUZZER = 25, 14  # Реле и Зуммер

# Двухцветный светодиод считывателя
PIN_LED_RED   = 13              # Красный светодиод
PIN_LED_GREEN = 15              # Зеленый светодиод

PIN_FLOW_SENSOR = 32            # Сигнальный провод расходомера
PULSES_PER_LITER = 60.0         # Коэффициент расходомера (импульсов на 1 литр)

# ==============================================================================
# ИНИЦИАЛИЗАЦИЯ ЖЕЛЕЗА
# ==============================================================================
relay  = Pin(PIN_RELAY, Pin.OUT, value=0)
buzzer = Pin(PIN_BUZZER, Pin.OUT, value=0)

# Настройка светодиодов: при старте горит КРАСНЫЙ (0), ЗЕЛЕНЫЙ потушен (1)
led_r  = Pin(PIN_LED_RED, Pin.OUT, value=0)
led_g  = Pin(PIN_LED_GREEN, Pin.OUT, value=1)

# Настройка часов I2C
i2c = SoftI2C(sda=Pin(PIN_SDA), scl=Pin(PIN_SCL))

# Настройка расходомера
flow_pin = Pin(PIN_FLOW_SENSOR, Pin.IN, Pin.PULL_UP)

pulse_count = 0
wiegand_buffer = []
last_bit_time = 0
last_pulse_us = 0                # Для фильтрации дребезга Wiegand

def download_update_from_github(url):
    print(f"[OTA] Скачивание обновления с: {url}")
    try:
        import urequests
        response = urequests.get(url)
        if response.status_code == 200:
            print("[OTA] Файл скачан, запись на флеш...")
            with open("main_new.py", "w") as f:
                f.write(response.text)
            response.close()
            print("[OTA] Файл сохранен как main_new.py. Перезагрузка...")
            machine.reset()
        else:
            print(f"[OTA] Ошибка сервера: {response.status_code}")
    except Exception as e:
        print(f"[OTA] Ошибка при скачивании: {e}")
        

def beep(duration=0.1, count=1, error=False):
    """Функция звувого сигнала с поддержкой мигания светодиодов"""
    for _ in range(count):
        buzzer.value(1)
        if error:
            led_r.value(1)      # Подмигиваем красным при ошибке
        else:
            led_g.value(0)      # Подмигиваем зеленым при успехе
            
        time.sleep(duration)
        buzzer.value(0)
        
        if error:
            led_r.value(0)
        else:
            led_g.value(1)
        time.sleep(0.05)

# Прерывание для расходомера
def flow_pulse_callback(pin):
    global pulse_count
    pulse_count += 1

flow_pin.irq(trigger=Pin.IRQ_FALLING, handler=flow_pulse_callback)

# Прерывание для считывателя карт с фильтром помех на 30 мкс
def wiegand_edge_callback(pin):
    global last_bit_time, last_pulse_us
    now_us = time.ticks_us()
    
    if time.ticks_diff(now_us, last_pulse_us) < 30:
        return
    last_pulse_us = now_us
    
    last_bit_time = time.ticks_ms()
    wiegand_buffer.append(0 if pin == d0_pin else 1)

d0_pin = Pin(PIN_D0, Pin.IN, Pin.PULL_UP)
d1_pin = Pin(PIN_D1, Pin.IN, Pin.PULL_UP)
d0_pin.irq(trigger=Pin.IRQ_FALLING, handler=wiegand_edge_callback)
d1_pin.irq(trigger=Pin.IRQ_FALLING, handler=wiegand_edge_callback)

# ==============================================================================
# ФУНКЦИЯ МОНТИРОВАНИЯ SD КАРТЫ
# ==============================================================================
sd_mounted = False

def try_mount_sd():
    global sd_mounted
    print("Монтирование карты памяти...")
    time.sleep(0.5)  # Стабилизация питания
    try:
        import sdcard
        spi = SoftSPI(baudrate=400000, polarity=0, phase=0, sck=Pin(PIN_SCK), mosi=Pin(PIN_MOSI), miso=Pin(PIN_MISO))
        sd = sdcard.SDCard(spi, Pin(PIN_CS))
        vfs = os.VfsFat(sd)
        os.mount(vfs, "/sd")
        print(" -> Карта памяти успешно подключена к системе!")
        sd_mounted = True
        return True
    except Exception as e:
        print(f" -> КРИТИЧЕСКАЯ ОШИБКА КАРТЫ: {e}")
        sd_mounted = False
        return False

# Первая попытка при старте платы
try_mount_sd()

# ==============================================================================
# ФУНКЦИИ РАБОТЫ С ДАННЫМИ
# ==============================================================================
def get_rtc_time_str():
    try:
        data = i2c.readfrom_mem(0x68, 0x00, 7)
        sec = ((data[0] >> 4) * 10) + (data[0] & 0x0F)
        minute = ((data[1] >> 4) * 10) + (data[1] & 0x0F)
        hour = ((data[2] >> 4) * 10) + (data[2] & 0x0F)
        day = ((data[4] >> 4) * 10) + (data[4] & 0x0F)
        month = ((data[5] >> 4) * 10) + (data[5] & 0x0F)
        year = 2000 + ((data[6] >> 4) * 10) + (data[6] & 0x0F)
        return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{sec:02d}"
    except:
        return "Время неизвестно"

def check_driver_access(card_id):
    if not sd_mounted:
        return None
    driver_name = None
    try:
        if "drivers.csv" in os.listdir("/sd"):
            with open("/sd/drivers.csv", "r") as f:
                for line in f:
                    if line.strip():
                        parts = line.strip().split(",")
                        if len(parts) >= 2 and parts[0].strip() == str(card_id).strip():
                            driver_name = parts[1].strip()
                            break
    except Exception as e:
        print("Ошибка при чтении файла водителей:", e)
    return driver_name

def log_fuel_transaction(card_id, driver, liters):
    if not sd_mounted:
        return
    timestamp = get_rtc_time_str() 
    try:
        with open("/sd/fuel_log.txt", "a") as f:
            f.write(f"[{timestamp}] Карта: {card_id} | Водитель: {driver} | Пролито: {liters:.2f} л.\n")
        print(f"-> Успешно записано в лог: [{timestamp}] - {liters:.2f} л.")
    except Exception as e:
        print("Ошибка записи локального лога:", e)

def send_to_google_sheets(card_id, driver_name, liters):
    """Отправка транзакции в облако Google с исправленным расчетом длины UTF-8"""
    if not GOOGLE_SCR_URL:
        print("[ОБЛАКО] Ссылка пустая. Пропуск отправки.")
        return False
    try:
        url = GOOGLE_SCR_URL.strip().replace('\xa0', '')
        host = "script.google.com"
        path = url.split(host)[1]
        timestamp = get_rtc_time_str()
        
        # Строго упаковываем JSON и превращаем его в БАЙТЫ, чтобы корректно измерить длину
        payload = {"command": "log_fuel",
                   "card_id": str(card_id),
                   "driver": str(driver_name),
                   "liters": float(liters),
                   "fueling_time": timestamp}
        body_bytes = json.dumps(payload).encode('utf-8')
        
        import usocket as socket
        try: import ssl
        except: import ussl as ssl
        
        print("[ОБЛАКО ДЕБАГ] 1. Разрешаем DNS...")
        ai = socket.getaddrinfo(host, 443)
        addr = ai[0][-1]
        
        s = socket.socket()
        s.settimeout(30.0)
        
        print("[ОБЛАКО ДЕБАГ] 2. Подключаемся к серверу...")
        s.connect(addr)
        
        gc.collect() 
        
        print("[ОБЛАКО ДЕБАГ] 3. Создаем SSL-туннель с поддержкой SNI...")
        s = ssl.wrap_socket(s, server_hostname=host)
        
        # Формируем заголовки. Длину берем строго от СКОМПИЛИРОВАННЫХ БАЙТ, а не от текста!
        header = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(body_bytes)}\r\n"
            f"Connection: close\r\n\r\n"
        )
        
        print("[ОБЛАКО ДЕБАГ] 4. Отправляем данные...")
        # Отправляем порционно: сначала заголовки, затем тело запроса. Так ESP32 не зависнет по памяти
        s.write(header.encode('utf-8'))
        s.write(body_bytes)
        
        print("[ОБЛАКО ДЕБАГ] 5. Ждем ответ от Google...")
        response_line = s.readline().decode('utf-8')
        s.close()
        
        gc.collect()
        
        print("[ОБЛАКО] Статус сервера Google:", response_line.strip())
        if "302" in response_line or "200" in response_line:
            print("[ОБЛАКО] Данные успешно сохранены в Google Sheets!")
            return True
        return False
    except Exception as e:
        print("[ОБЛАКО] Критическая ошибка сети при отправке:", e)
        return False
    
# ==============================================================================
# ИНИЦИАЛИЗАЦИЯ СЕТИ С ЗАПРЕТОМ РЕЖИМА СНА
# ==============================================================================
beep(0.1, 2)
wlan = network.WLAN(network.STA_IF)

if wlan.isconnected():
    print(f"Плата успешно подхватила сеть! IP: {wlan.ifconfig()[0]}")
    wlan.config(pm=network.WLAN.PM_NONE) # Wi-Fi не уснет во время пролива
else:
    print("ВНИМАНИЕ: Wi-Fi не подключен. Работаем автономно.")

if "purgatory.txt" in os.listdir():
    os.remove("purgatory.txt")

# ==============================================================================
# ОСНОВНОЙ РАБОЧИЙ ЦИКЛ СТАНЦИИ
# ==============================================================================
print("\n=== АВТОМАТ ЗАПРАВКИ v" + VERSION + " НАДЕЖНО ЗАПУЩЕН И ГОТОВ ===")

while True:
    # ОСВОБОЖДАЕМ ПАМЯТЬ: Каждые 20мс в простое вычищаем накопившийся мусор в ОЗУ
    gc.collect()
    
    # Ждем окончания передачи пакета данных Wiegand
    if wiegand_buffer and (time.ticks_ms() - last_bit_time > 50):
        bits_count = len(wiegand_buffer)
        
        if bits_count != 26 and bits_count != 34:
            print(f"\n[WIEGAND ДЕБАГ] Ошибка: Считано {bits_count} бит. Очистка.")
            wiegand_buffer.clear()
            continue
            
        card_code = 0
        for bit in wiegand_buffer:
            card_code = (card_code << 1) | bit
        wiegand_buffer.clear()
        
        print(f"\nСчитана карта: {card_code}")
        
        # ----------------------------------------------------------------------
        # КРИТИЧЕСКИЙ БЛОК ПРОВЕРКИ SD КАРТЫ ПЕРЕД ЗАПРАВКОЙ
        # ----------------------------------------------------------------------
        if not sd_mounted:
            print("[БЛОКИРОВКА] Попытка аварийного переподключения SD карты...")
            if not try_mount_sd():
                print("[ОТКАЗ] Заправка заблокирована: Локальная база данных и логи недоступны!")
                beep(0.08, 5, error=True)
                continue 
        # ----------------------------------------------------------------------
        
        driver = check_driver_access(card_code)
        
        if driver:
            print(f"ДОСТУП РАЗРЕШЕН! Водитель: {driver}")
            led_r.value(1)
            led_g.value(0)
            beep(0.3, 1)
            
            relay.value(1) # ЗАПУСК НАСОСА
            print("Насос включен. Ожидание пролива...")
            
            pulse_count = 0
            start_waiting_time = time.ticks_ms()
            last_flow_time = time.ticks_ms()
            last_pulse_check = 0
            fueling_started = False
            
            while True:
                current_pulses = pulse_count
                liters = current_pulses / PULSES_PER_LITER
                
                if current_pulses > 0 and current_pulses != last_pulse_check:
                    if not fueling_started:
                        print("\nПролив пошел!")
                        fueling_started = True
                    print(f"Налито: {liters:.2f} л. (Импульсов: {current_pulses})", end="\r")
                    last_flow_time = time.ticks_ms()
                    last_pulse_check = current_pulses
                    
                if not fueling_started and (time.ticks_ms() - start_waiting_time > 15000):
                    print("\n[ТАЙМАУТ] Отмена: нет пролива в течение 15 секунд.")
                    break
                    
                if fueling_started and (time.ticks_ms() - last_flow_time > 8000):
                    print("\nПролив прекратился (пистолет закрыт).")
                    break
                        
                time.sleep_ms(50)
            
            # Заправка окончена
            relay.value(0)
            time.sleep(15.0)
            led_g.value(1)
            led_r.value(0)
            
            final_liters = pulse_count / PULSES_PER_LITER
            print(f"Заправка окончена. Итого налито: {final_liters:.2f} литров.")
            
            if final_liters > 0.01:
                log_fuel_transaction(card_code, driver, final_liters)
                
                cloud_sent = False
                if wlan.isconnected():
                    cloud_sent = send_to_google_sheets(card_code, driver, final_liters)
                
                if cloud_sent:
                    beep(0.1, 3)
                else:
                    print("[СИСТЕМА] Сохранено только локально на SD. Нет связи с Google.")
                    beep(0.1, 1)
            else:
                beep(0.2, 1)
                
            print("\nСтанция готова к новым картам.")
            
        else:
            print("ДОСТУП ЗАПРЕЩЕН: Карты нет в базе данных!")
            beep(0.1, 4, error=True)
            time.sleep(2.0)
            
    time.sleep_ms(20)
