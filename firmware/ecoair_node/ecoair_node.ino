#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "secrets.h"

#define DHTPIN 4
#define DHTTYPE DHT22
#define RXD2 16
#define TXD2 17

// Pantalla OLED SSD1306 128x64 por I2C
#define OLED_SDA 21
#define OLED_SCL 22
#define OLED_ADDR 0x3C
#define OLED_W 128
#define OLED_H 64

const char* DEVICE_ID = "ecoair-node-01";
const char* TOPIC = "ecoair/ecoair-node-01/telemetry";
const unsigned long PUBLISH_MS = 5000;
const unsigned long RETRY_MS = 5000;
const unsigned long DHT_MS = 5000;
const unsigned long DISPLAY_MS = 1000;

DHT dht(DHTPIN, DHTTYPE);
WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);
Adafruit_SSD1306 display(OLED_W, OLED_H, &Wire, -1);

bool pantallaOk = false;
float pm25 = NAN, pm10 = NAN;
float temperatura = NAN, humedad = NAN;
unsigned long lastPublish = 0;
unsigned long lastRetry = 0;
unsigned long lastDht = 0;
unsigned long lastDisplay = 0;

void leerSDS011() {
  static uint8_t buf[10];
  static uint8_t idx = 0;

  while (Serial2.available()) {
    uint8_t b = Serial2.read();
    if (idx == 0 && b != 0xAA) continue;
    buf[idx++] = b;

    if (idx == 10) {
      idx = 0;
      if (buf[1] != 0xC0 || buf[9] != 0xAB) continue;

      uint8_t sum = 0;
      for (int i = 2; i <= 7; i++) sum += buf[i];
      if (sum != buf[8]) continue;

      pm25 = (buf[2] | (buf[3] << 8)) / 10.0;
      pm10 = (buf[4] | (buf[5] << 8)) / 10.0;
    }
  }
}

void leerDHT() {
  static bool primera = true;
  if (!primera && millis() - lastDht < DHT_MS) return;
  primera = false;
  lastDht = millis();
  temperatura = dht.readTemperature();
  humedad = dht.readHumidity();
}

void mostrarMensaje(const char* linea1, const char* linea2) {
  if (!pantallaOk) return;
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(2);
  display.setCursor(0, 8);
  display.print(linea1);
  display.setTextSize(1);
  display.setCursor(0, 36);
  display.print(linea2);
  display.display();
}

void filaPM(const char* etiqueta, float valor, int y) {
  char buf[8];
  if (isnan(valor)) snprintf(buf, sizeof(buf), "--");
  else snprintf(buf, sizeof(buf), "%.1f", valor);

  display.setTextSize(1);
  display.setCursor(0, y + 4);
  display.print(etiqueta);
  display.setTextSize(2);
  display.setCursor(36, y);
  display.print(buf);
  display.setTextSize(1);
  display.setCursor(98, y + 4);
  display.print("ug/m3");
}

void actualizarPantalla() {
  if (!pantallaOk || millis() - lastDisplay < DISPLAY_MS) return;
  lastDisplay = millis();

  char estado[16];
  snprintf(estado, sizeof(estado), "W:%s M:%s",
           WiFi.status() == WL_CONNECTED ? "OK" : "--",
           mqtt.connected() ? "OK" : "--");

  char t[8], h[8], linea[24];
  if (isnan(temperatura)) snprintf(t, sizeof(t), "--");
  else snprintf(t, sizeof(t), "%.1fC", temperatura);
  if (isnan(humedad)) snprintf(h, sizeof(h), "--");
  else snprintf(h, sizeof(h), "%.1f%%", humedad);
  snprintf(linea, sizeof(linea), "Temp %s Hum %s", t, h);

  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);

  display.setTextSize(1);
  display.setCursor(0, 0);
  display.print("EcoAir AI");
  display.setCursor(66, 0);
  display.print(estado);
  display.drawFastHLine(0, 10, OLED_W, SSD1306_WHITE);

  filaPM("PM2.5", pm25, 14);
  filaPM("PM10", pm10, 32);

  display.setTextSize(1);
  display.setCursor(0, 54);
  display.print(linea);

  display.display();
}

void conectarWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Conectando a Wi-Fi");
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
    delay(500);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\nWi-Fi OK, IP del ESP32: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("\nNo se pudo conectar al Wi-Fi, se reintenta en el loop");
  }
}

// Texto del codigo de mqtt.state() (PubSubClient) para el monitor serie.
const char* descripcionEstadoMqtt(int estado) {
  switch (estado) {
    case -4: return "sin respuesta del broker (timeout)";
    case -3: return "conexion perdida";
    case -2: return "no se pudo abrir la conexion TCP: revisa MQTT_HOST y MQTT_PORT";
    case -1: return "desconectado";
    case 1:  return "version de protocolo rechazada";
    case 2:  return "client id rechazado";
    case 3:  return "broker no disponible";
    case 4:  return "usuario o clave incorrectos: revisa MQTT_USER y MQTT_PASSWORD";
    case 5:  return "no autorizado: revisa MQTT_USER, MQTT_PASSWORD y el ACL del broker";
    default: return "desconocido";
  }
}

void setup() {
  Serial.begin(115200);
  Serial2.begin(9600, SERIAL_8N1, RXD2, TXD2);
  dht.begin();

  Wire.begin(OLED_SDA, OLED_SCL);
  pantallaOk = display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR);
  if (!pantallaOk) {
    Serial.println("OLED no detectada: el sistema sigue funcionando sin pantalla");
  }
  mostrarMensaje("EcoAir AI", "Conectando Wi-Fi...");

  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  conectarWifi();
}

void loop() {
  leerSDS011();
  leerDHT();
  actualizarPantalla();

  if (WiFi.status() != WL_CONNECTED) {
    if (millis() - lastRetry >= RETRY_MS) {
      lastRetry = millis();
      Serial.println("Wi-Fi caido, reconectando...");
      WiFi.disconnect();
      WiFi.begin(WIFI_SSID, WIFI_PASS);
    }
    return;
  }

  if (!mqtt.connected()) {
    if (millis() - lastRetry >= RETRY_MS) {
      lastRetry = millis();
      Serial.print("Conectando al broker MQTT... ");
      if (mqtt.connect(DEVICE_ID, MQTT_USER, MQTT_PASSWORD)) {
        Serial.println("OK");
      } else {
        int estado = mqtt.state();
        Serial.printf("fallo, estado=%d (%s)\n", estado, descripcionEstadoMqtt(estado));
      }
    }
    return;
  }
  mqtt.loop();

  if (millis() - lastPublish >= PUBLISH_MS) {
    lastPublish = millis();

    if (isnan(temperatura) || isnan(humedad) || isnan(pm25) || isnan(pm10)) {
      Serial.println("Datos incompletos, no se publica");
      return;
    }

    char payload[160];
    snprintf(payload, sizeof(payload),
             "{\"deviceId\":\"%s\",\"temperature\":%.1f,\"humidity\":%.1f,\"pm25\":%.1f,\"pm10\":%.1f}",
             DEVICE_ID, temperatura, humedad, pm25, pm10);

    bool ok = mqtt.publish(TOPIC, payload);
    Serial.printf("%s -> %s\n", ok ? "Publicado" : "Fallo al publicar", payload);
  }
}
