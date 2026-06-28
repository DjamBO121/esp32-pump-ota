import network
import os
import machine
import time
from machine import WDT

WIFI_SSID = "4G-MIFI-533B"
WIFI_PASS = "1234567890"
wdt = WDT(timeout=30000)

if 'ota_status.txt' in os.listdir():
    try:
        with open('ota_status.txt', 'r') as f:
            status = f.read().strip()
    except Exception:
        status = "1"

    if status == "0":
        with open('ota_status.txt', 'w') as f:
            f.write("1")
            
    elif status == "1":
        try:
            if 'main.py' in os.listdir(): 
                os.remove('main.py')
            if 'main.old' in os.listdir(): 
                os.rename('main.old', 'main.py')
            os.remove('ota_status.txt')
            time.sleep(1)
            machine.reset()
        except Exception as e:
            print("Ошибка файловой системы при откате:", e)

wlan = network.WLAN(network.STA_IF)
wlan.active(False)
time.sleep(1)
wlan.active(True)

try:
    wlan.connect(WIFI_SSID, WIFI_PASS)
    # Ждем максимум 5 секунд, чтобы не тормозить запуск main.py
    for i in range(5):
        if wlan.isconnected():
            print("Wi-Fi подключен!")
            break
        time.sleep(1)
except Exception as e:
    print("Ошибка Wi-Fi в boot.py:", e)
