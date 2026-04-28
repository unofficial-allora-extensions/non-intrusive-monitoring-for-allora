# Copyright (C) 2026 Diego Rios Gomez
#
# This file is part of Non-intrusive monitoring for AlLoRa.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

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

def get_cpu_freq_hz():
    try:
        out = subprocess.check_output(
            ["vcgencmd", "measure_clock", "arm"],
            text=True,
            stderr=subprocess.DEVNULL
        ).strip()
        # formato típico: frequency(48)=1200000000
        if "=" in out:
            return int(out.split("=")[1])
    except Exception:
        pass

    # fallback genérico
    try:
        with open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", "r") as f:
            return int(f.read().strip()) * 1000
    except Exception:
        return 0

def generate_metrics_payload():
    uptime_str = get_uptime_str()
    mem = get_meminfo()
    freq_hz = get_cpu_freq_hz()

    data = {
        "type": "metrics",
        "RAM_Libre": mem["RAM_Libre"],
        "RAM_Usada": mem["RAM_Usada"],
        "RAM_Total": mem["RAM_Total"],
        "Uptime": uptime_str,
        "FreqCPU": freq_hz
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
    time.sleep(0.2)
    try:
        subprocess.run(["sudo", "-n", "reboot"], check=True)
    except subprocess.CalledProcessError as e:
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