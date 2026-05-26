from machine import Pin, SoftSPI, SoftI2C
import os
import time

# Определяем текущую версию этого файла (должна совпадать с CURRENT_VERSION в boot.py)
VERSION = "1.0"
# ==============================================================================
# НАСТРОЙКИ ПИНОВ И ОБОРУДОВАНИЯ
# ==============================================================================
PIN_SDA, PIN_SCL = 21, 22    # Часы DS3231 (I2C)

# Конфигурация твоей MicroSD карты (SPI)
PIN_MISO = 2
PIN_MOSI = 4
PIN_CS   = 18
PIN_SCK  = 5

PIN_D0, PIN_D1 = 26, 27      # Считыватель карт Wiegand
PIN_RELAY, PIN_BUZZER = 25, 14  # Реле и Зуммер

# Двухцветный светодиод считывателя
PIN_LED_RED   = 13           # Красный светодиод на GPIO 13
PIN_LED_GREEN = 15           # Зеленый светодиод на GPIO 15

PIN_FLOW_SENSOR = 32         # Сигнальный провод расходомера (GPIO 32)

PULSES_PER_LITER = 60.0      # Коэффициент расходомера (импульсов на 1 литр)

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

def beep(duration=0.1, count=1, error=False):
    """Функция звукового сигнала с поддержкой мигания светодиодов"""
    for _ in range(count):
        buzzer.value(1)
        if error:
            led_r.value(1)  # Подмигиваем красным при ошибке
        else:
            led_g.value(0)  # Подмигиваем зеленым при успехе
            
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

# Прерывание для считывателя карт
def wiegand_edge_callback(pin):
    global last_bit_time
    last_bit_time = time.ticks_ms()
    wiegand_buffer.append(0 if pin == d0_pin else 1)

d0_pin = Pin(PIN_D0, Pin.IN)
d1_pin = Pin(PIN_D1, Pin.IN)
d0_pin.irq(trigger=Pin.IRQ_FALLING, handler=wiegand_edge_callback)
d1_pin.irq(trigger=Pin.IRQ_FALLING, handler=wiegand_edge_callback)

# ==============================================================================
# МОНТИРУЕМ КАРТУ ПАМЯТИ ПРИ СТАРТЕ
# ==============================================================================
sd_mounted = False
print("Монтирование карты памяти...")
try:
    import sdcard
    spi = SoftSPI(baudrate=1000000, polarity=0, phase=0, sck=Pin(PIN_SCK), mosi=Pin(PIN_MOSI), miso=Pin(PIN_MISO))
    sd = sdcard.SDCard(spi, Pin(PIN_CS))
    vfs = os.VfsFat(sd)
    os.mount(vfs, "/sd")
    print(" -> Карта памяти успешно подключена к системе!")
    sd_mounted = True
except Exception as e:
    print(f" -> КРИТИЧЕСКАЯ ОШИБКА КАРТЫ: {e}")
    print(" -> Работа без логирования.")

# ==============================================================================
# ФУНКЦИИ РАБОТЫ С ДАННЫМИ (RTC и Логирование)
# ==============================================================================
def get_rtc_time_str():
    """Читает точное время из модуля DS3231"""
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
    """Проверяет наличие карты в базе drivers.csv"""
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
    """Записывает успешную заправку в fuel_log.txt с точным временем"""
    if not sd_mounted:
        return
    timestamp = get_rtc_time_str() 
    try:
        with open("/sd/fuel_log.txt", "a") as f:
            f.write(f"[{timestamp}] Карта: {card_id} | Водитель: {driver} | Пролито: {liters:.2f} л.\n")
        print(f"-> Успешно записано в лог: [{timestamp}] - {liters:.2f} л.")
    except Exception as e:
        print("Ошибка записи лога:", e)

# ==============================================================================
# ОСНОВНОЙ РАБОЧИЙ ЦИКЛ СТАНЦИИ
# ==============================================================================
# Приветственный писк: система готова к работе
beep(0.1, 2)
# В самый верх файла к импортам добавляем:
import ota


# ... (весь твой код инициализации железа, реле, часов и SD-карты) ...

# ==============================================================================
# ПОДТВЕРЖДЕНИЕ УСПЕШНОГО ЗАПУСКА (Защита от отката)
# ==============================================================================
if "purgatory.txt" in os.listdir():
    os.remove("purgatory.txt")
    print("[SYSTEM] Новый код отработал успешно! Испытательный срок пройден.")

# ==============================================================================
# ПОДКЛЮЧЕНИЕ К WI-FI И ПРОВЕРКА ОБНОВЛЕНИЯ
# ==============================================================================
# Здесь должен быть твой блок подключения к Wi-Fi (WLAN). 
# Как только плата успешно подключилась к сети и получила IP:
if wifi_connected: # Твоя переменная статуса сети
    print(f"Плата в сети. IP: {wifi_ip}")
    
    # Запускаем проверку обновлений с GitHub
    ota.check_and_update(VERSION)

# ==============================================================================
# ОСНОВНОЙ РАБОЧИЙ ЦИКЛ СТАНЦИИ (Wiegand, Карты, Реле, Расходомер)
# ==============================================================================
print("\n=== АВТОМАТ ЗАПРАВКИ НАДЕЖНО ЗАПУЩЕН И ГОТОВ ===")
while True:
    # ... твой рабочий цикл заправки ...
    # Ожидаем окончания пакета данных от Wiegand (пауза > 50мс)
    if wiegand_buffer and (time.ticks_ms() - last_bit_time > 50):
        card_code = 0
        for bit in wiegand_buffer:
            card_code = (card_code << 1) | bit
        wiegand_buffer.clear()
        
        print(f"\nСчитана карта: {card_code} (HEX: {hex(card_code)})")
        
        # Проверяем базу данных
        driver = check_driver_access(card_code)
        
        if driver:
            # ДОСТУП РАЗРЕШЕН
            print(f"ДОСТУП РАЗРЕШЕН! Водитель: {driver}")
            
            # Переключаем индикацию: гасим красный, включаем зеленый
            led_r.value(1)
            led_g.value(0)
            
            beep(0.3, 1) # Один длинный сигнал одобрения
            
            # Включаем реле насоса
            relay.value(1)
            print("Насос включен. Ожидание пролива топлива...")
            
            pulse_count = 0
            last_flow_time = time.ticks_ms()
            last_pulse_check = 0
            
            # Цикл контроля заправки
            while True:
                current_pulses = pulse_count
                liters = current_pulses / PULSES_PER_LITER
                
                # Если импульсы идут — обновляем экран и сбрасываем таймер таймаута
                if current_pulses > 0 and current_pulses != last_pulse_check:
                    print(f"Налито: {liters:.2f} л. (Импульсов: {current_pulses})", end="\r")
                    last_flow_time = time.ticks_ms()
                    last_pulse_check = current_pulses
                    
                # Тайм-аут: если топливо не течет более 8 секунд — завершаем налив
                if time.ticks_ms() - last_flow_time > 8000:
                    if current_pulses > 0:
                        break # Налили бензин и закрыли пистолет
                    elif time.ticks_ms() - last_flow_time > 15000:
                        print("\nЗаправка отменена по таймауту (нет пролива).")
                        break # Вообще не нажали пистолет за 15 секунд
                        
                time.sleep_ms(100)
            
            # Выключаем насос по окончании
            relay.value(0)
            
            # Возвращаем индикацию в исходное состояние (Красный горит, Зеленый тухнет)
            led_g.value(1)
            led_r.value(0)
            
            final_liters = pulse_count / PULSES_PER_LITER
            print(f"\nЗаправка окончена. Итого налито: {final_liters:.2f} литров.")
            
            # Если пролив был реальным, сохраняем в отчет
            if final_liters > 0.1:
                log_fuel_transaction(card_code, driver, final_liters)
                beep(0.1, 3) # Три быстрых писка — лог сохранен успешно
            else:
                beep(0.2, 1)
                
            print("\nСтанция готова к новому считыванию.")
            
        else:
            # ДОСТУП ЗАПРЕЩЕН
            print("ДОСТУП ЗАПРЕЩЕН: Карты нет в базе данных!")
            # 4 раза быстро пищим и моргаем красным светодиодом
            beep(0.1, 4, error=True)
            
    time.sleep_ms(20)
    
# Внутри main.py, после успешного подключения к Wi-Fi и проверки SD-карты:


