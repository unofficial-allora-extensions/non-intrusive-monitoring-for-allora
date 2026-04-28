 /*
  Copyright (C) 2026 Diego Rios Gomez
 
  This file is part of Non-intrusive monitoring for AlLoRa.
 
  This program is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
 
  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
  See the GNU General Public License for more details.
 
  You should have received a copy of the GNU General Public License
  along with this program. If not, see <https://www.gnu.org/licenses/>.
 */

// source_serial_i2c_dosNucleos.ino
#include <Wire.h>
#include "esp_heap_caps.h"
#include "esp_system.h"

//para obtener la temperatura interna
#include "driver/temperature_sensor.h"
#include "esp_err.h"

#define SLAVE_ADDR 0x28
#define BUF_SIZE 512
#define MAX_PAYLOAD 120

#define CMD_CONN_ACK  0x01
#define CMD_RESET     0x02

volatile uint8_t last_command = 0;
volatile bool reboot_pending = false;
TaskHandle_t metricsTaskHandle = NULL;

temperature_sensor_handle_t temp_handle = NULL;
bool temp_sensor_ready = false;

char bufA[BUF_SIZE];
char bufB[BUF_SIZE];

volatile char* active_buf = bufA;
volatile uint16_t active_len = 0;
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;

void generate_metrics_response(){
  char* write_buf;
  uint16_t local_len = 0;

  // Elegir el buffer que no está activo
  portENTER_CRITICAL(&mux);
  write_buf = (active_buf == bufA) ? bufB : bufA;
  portEXIT_CRITICAL(&mux);

  // Preparar métricas en buffer no activo
  size_t free_heap = heap_caps_get_free_size(MALLOC_CAP_DEFAULT);
  size_t total_heap = heap_caps_get_total_size(MALLOC_CAP_DEFAULT);
  size_t used_heap  = total_heap - free_heap;

  unsigned long uptime_ms = millis();
  unsigned long uptime_s  = uptime_ms / 1000UL;
  unsigned long uptime_h  = uptime_s / 3600UL;
  unsigned long uptime_m  = (uptime_s % 3600UL) / 60UL;
  unsigned long uptime_s_rem = uptime_s % 60UL;

  char uptime_str[24];
  snprintf(uptime_str, sizeof(uptime_str), "%02lu:%02lu:%02lu",
           uptime_h, uptime_m, uptime_s_rem);

  float temp_c = 0.0f;
  bool temp_ok = false;

  if (temp_sensor_ready) {
    esp_err_t err = temperature_sensor_get_celsius(temp_handle, &temp_c);
    if (err == ESP_OK) {
      temp_ok = true;
    } else {
      Serial.printf("Error leyendo temperatura interna: %d\n", err);
    }
  }

  if (temp_ok) {
    local_len = snprintf(write_buf, BUF_SIZE,
    "{\"type\":\"metrics\",\"RAM_Libre\":%u,\"RAM_Usada\":%u,\"RAM_Total\":%u,\"Uptime\":\"%s\",\"Temperature\":%.2f}",
    (unsigned int)free_heap,
    (unsigned int)used_heap,
    (unsigned int)total_heap,
    uptime_str,
    temp_c
  );
  } else {
    local_len = snprintf(
      write_buf, BUF_SIZE,
      "{\"type\":\"metrics\",\"RAM_Libre\":%u,\"RAM_Usada\":%u,\"RAM_Total\":%u,\"Uptime\":\"%s\",\"Temperature\":null}",
      (unsigned int)free_heap,
      (unsigned int)used_heap,
      (unsigned int)total_heap,
      uptime_str
    );
  }

  if (local_len < 0) local_len = 0;
  if (local_len >= BUF_SIZE) local_len = BUF_SIZE - 1;

  Serial.print("Longitud JSON: ");
  Serial.println(local_len);

  // Swap atómico (sección crítica muy corta): solo actualizamos la longitud "global" y cambiamos el puntero del buffer activo al nuevo 
  //(más rápido que hacer memcpy, minimizar el bloquear al otro core con el lock)
  portENTER_CRITICAL(&mux);
  active_buf = write_buf;
  active_len = local_len;
  portEXIT_CRITICAL(&mux);
}

void metricsTask(void *pvParameters) {
  for(;;) {
    generate_metrics_response();
    
    // Espera 10s o hasta que alguien lo despierte
    ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(10000));
  }
}

void prepare_simple_response(const char* msg) {
  char* write_buf;
  uint16_t len = 0;

  //Escoger buffer inactivo
  portENTER_CRITICAL(&mux);
  write_buf = (active_buf == bufA) ? bufB : bufA;
  portEXIT_CRITICAL(&mux);

  len = snprintf(write_buf, BUF_SIZE, "%s", msg);

  if (len < 0) len = 0;
  if (len >= BUF_SIZE) len = BUF_SIZE - 1;

  portENTER_CRITICAL(&mux);
  active_buf = write_buf;
  active_len = len;
  portEXIT_CRITICAL(&mux);
}

void receiveEvent(int howMany) {
  if (Wire.available()) {
    last_command = Wire.read();
  }
  while (Wire.available()) Wire.read();
}

void requestEvent() {
  char* buf;
  uint16_t len = 0;

  //Ahora hay que evitar condición de carrera al acceder tanto al buffer como a la variable, al ser usados por ambos cores
  portENTER_CRITICAL_ISR(&mux);
  len = active_len;
  buf = (char*)active_buf;

  uint8_t header[3];
  header[0] = 0xAA;
  header[1] = len & 0xFF;
  header[2] = (len >> 8) & 0xFF;

  Wire.write(header, 3);
  Wire.write((uint8_t*)buf, len);

  portEXIT_CRITICAL_ISR(&mux);
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("Iniciando I2C slave...");

  for (int i=0; i<5; ++i) {
  Serial.println("Boot message");
  delay(50);
}

  temperature_sensor_config_t temp_cfg = TEMPERATURE_SENSOR_CONFIG_DEFAULT(-10, 80);
  
    esp_err_t err = temperature_sensor_install(&temp_cfg, &temp_handle);
    if (err == ESP_OK) {
      err = temperature_sensor_enable(temp_handle);
    }
  
    if (err == ESP_OK) {
      temp_sensor_ready = true;
      Serial.println("Sensor de temperatura interna OK");
    } else {
      temp_sensor_ready = false;
      Serial.printf("Error iniciando sensor de temperatura: %d\n", err);
    }

  xTaskCreatePinnedToCore(
    metricsTask,
    "MetricsTask",
    4096,
    NULL,
    1,
    &metricsTaskHandle,
    1  // Core 1
  );

  Serial.println("Gestión I2C: Core 0; Generación periódica de métricas (cada 10s): Core 1");


  Wire.setPins(15,16);
  Wire.begin(SLAVE_ADDR);
  Wire.setBufferSize(BUF_SIZE);
  
  Wire.onReceive(receiveEvent);
  Wire.onRequest(requestEvent);

  //prepare_metrics();
  Serial.println("I2C slave listo");
}

void loop() {
  if (last_command != 0) {

    switch (last_command) {

      case CMD_CONN_ACK:
        //prepare_simple_response("CONN-ACK");
        
        if (metricsTaskHandle != NULL) {
          Serial.println("Despertando al Core 1 (métricas)");
          xTaskNotifyGive(metricsTaskHandle);
        }
        
        break;

      case CMD_RESET:
        prepare_simple_response("RESET");
        reboot_pending = true;
        
        break;
    }

    last_command = 0;
  }
  
  if(reboot_pending){
    reboot_pending = false;
    
    delay(200); //suficiente tiempo para que el master ya haya leído la confirmación de reseteo...
    ESP.restart();   // soft reboot
  }

  delay(10);
}
