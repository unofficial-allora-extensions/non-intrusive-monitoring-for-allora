#!/usr/bin/env python3
import os
import json
import time
import shutil
import serial
import threading
import subprocess

SERIAL_PORT = "/dev/serial0"
BAUDRATE = 115200
BUF_SIZE = 512

CMD_CONN_ACK = 0x01
CMD_RESET = 0x02

active_payload = b""
payload_lock = threading.Lock()


def get_uptime_str():
    try:
        with open("/proc/uptime", "r") as f:
            uptime_seconds = int(float(f.readline().split()[0]))
    except Exception:
        uptime_seconds = 0

    h = uptime_seconds // 3600
    m = (uptime_seconds % 3600) // 60
    s = uptime_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def get_meminfo():
    mem_total_kb = 0
    mem_available_kb = 0

    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total_kb = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    mem_available_kb = int(line.split()[1])
    except Exception:
        pass

    mem_used_kb = max(0, mem_total_kb - mem_available_kb)

    return {
        "RAM_Libre": mem_available_kb * 1024,
        "RAM_Usada": mem_used_kb * 1024,
        "RAM_Total": mem_total_kb * 1024,
    }

def get_cpu_temp_c():
    # Ruta Linux típica en Raspberry Pi
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return round(int(f.read().strip()) / 1000.0, 2)
    except Exception:
        pass

    # Fallback con vcgencmd: temp=48.7'C
    try:
        out = subprocess.check_output(
            ["vcgencmd", "measure_temp"],
            text=True,
            stderr=subprocess.DEVNULL
        ).strip()
        if "=" in out:
            return round(float(out.split("=")[1].split("'")[0]), 2)
    except Exception:
        pass

    return None

def generate_metrics_payload():
    uptime_str = get_uptime_str()
    mem = get_meminfo()
    temp_c = get_cpu_temp_c()

    data = {
        "type": "metrics",
        "RAM_Libre": mem["RAM_Libre"],
        "RAM_Usada": mem["RAM_Usada"],
        "RAM_Total": mem["RAM_Total"],
        "Uptime": uptime_str,
        "Temperature": temp_c
    }

    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    if len(raw) >= BUF_SIZE:
        raw = raw[:BUF_SIZE - 1]
    return raw

def update_metrics_loop():
    global active_payload
    while True:
        payload = generate_metrics_payload()
        with payload_lock:
            active_payload = payload
        print(f"[RPi] Métricas actualizadas, longitud JSON: {len(payload)}")
        time.sleep(10)

def build_frame(payload: bytes) -> bytes:
    length = len(payload)
    header = bytes([0xAA, length & 0xFF, (length >> 8) & 0xFF])
    return header + payload

def do_reboot():
    time.sleep(1.0)
    try:
        subprocess.run(["sync"], check=False)
        subprocess.Popen(["sudo", "-n", "systemctl", "reboot", "-i"])
    except Exception as e:
        print("[RPi] Error lanzando reboot:", e)

def main():
    global active_payload

    with payload_lock:
        active_payload = generate_metrics_payload()

    ser = serial.Serial(
        port=SERIAL_PORT,
        baudrate=BAUDRATE,
        timeout=0.1,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE
    )

    print(f"[RPi] Escuchando UART en {SERIAL_PORT} @ {BAUDRATE}")

    t = threading.Thread(target=update_metrics_loop, daemon=True)
    t.start()

    while True:
        cmd = ser.read(1)
        if not cmd:
            continue

        cmd_byte = cmd[0]

        if cmd_byte == CMD_CONN_ACK:
            with payload_lock:
                payload = active_payload
            frame = build_frame(payload)
            ser.write(frame)
            ser.flush()
            print("[RPi] Enviadas métricas por UART")

        elif cmd_byte == CMD_RESET:
            payload = b"RESET"
            frame = build_frame(payload)
            ser.write(frame)
            ser.flush()
            print("[RPi] Confirmado RESET por UART")
            threading.Thread(target=do_reboot, daemon=True).start()

        else:
            print(f"[RPi] Comando desconocido: 0x{cmd_byte:02X}")

if __name__ == "__main__":
    main()