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

// Semaforo de 3 LEDs (anodo al GPIO con 330 ohm en serie; se encienden con HIGH).
// Los catodos van al GPIO 33 (no a GND): se deja en LOW todo el programa.
#define LED_VERDE 25
#define LED_AMARILLO 26
#define LED_ROJO 27
#define LED_CATODOS 33

const char* DEVICE_ID = "ecoair-node-01";
const char* TOPIC = "ecoair/ecoair-node-01/telemetry";
const unsigned long PUBLISH_MS = 5000;
const unsigned long RETRY_MS = 5000;
const unsigned long DHT_MS = 5000;
const unsigned long DISPLAY_MS = 1000;

// ---------- Semaforo: categoria ICA de PM2.5 ----------
// Cortes de PM2.5 (ug/m3) de la Tabla 6 de la Res. 2254/2017, copiados de api/ica.py
// (BREAKPOINTS["pm25"]; cifras en docs/normas/normas_aire.md). Igual que el servidor, la
// concentracion se trunca a entero antes de buscar la fila (supuesto del proyecto):
//   0-12 Buena · 13-37 Aceptable · 38-55 Danina a grupos sensibles ·
//   56-150 Danina · 151-250 Muy danina · 251-500 Peligrosa
const int PM25_MAX_BUENA = 12;
const int PM25_MAX_ACEPTABLE = 37;
const int PM25_MAX_SENSIBLES = 55;
// Como en el servidor (api/calidad_aire.py), el ICA usa la MEDIA DE LA ULTIMA HORA. Aqui se
// aproxima con una muestra de PM2.5 cada 5 s (la misma cadencia de publicacion) en 60 cubetas de 1 min.
const unsigned long MUESTRA_MS = 5000;
const uint8_t VENTANA_MIN = 60;
// Sin trama valida del SDS011 en este tiempo (envia ~1 por segundo): "sin dato" / error del sensor.
const unsigned long SDS_SIN_DATO_MS = 10000;
const unsigned long PRUEBA_LED_MS = 400;
const unsigned long PARPADEO_ROJO_MS = 500;
const unsigned long PASO_SIN_DATO_MS = 300;

enum Semaforo { SEM_SIN_DATO, SEM_BUENA, SEM_ACEPTABLE, SEM_SENSIBLES, SEM_DANINA_O_PEOR };

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
unsigned long lastSdsOk = 0;      // millis() de la ultima trama valida del SDS011 (0 = ninguna)
unsigned long lastMuestra = 0;

struct Cubeta { uint32_t minuto; float suma; uint16_t n; };
Cubeta cubetas[VENTANA_MIN];

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
      lastSdsOk = millis();
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

// ---------- Semaforo ----------

// Enciende los LEDs indicados. Nunca mas de dos a la vez (los catodos comparten el GPIO 33).
void ponerLeds(bool verde, bool amarillo, bool rojo) {
  if ((int)verde + (int)amarillo + (int)rojo > 2) verde = amarillo = rojo = false;
  digitalWrite(LED_VERDE, verde ? HIGH : LOW);
  digitalWrite(LED_AMARILLO, amarillo ? HIGH : LOW);
  digitalWrite(LED_ROJO, rojo ? HIGH : LOW);
}

void iniciarSemaforo() {
  pinMode(LED_CATODOS, OUTPUT);
  digitalWrite(LED_CATODOS, LOW);     // queda en LOW todo el programa
  pinMode(LED_VERDE, OUTPUT);
  pinMode(LED_AMARILLO, OUTPUT);
  pinMode(LED_ROJO, OUTPUT);
  ponerLeds(false, false, false);
  for (uint8_t i = 0; i < VENTANA_MIN; i++) cubetas[i] = {UINT32_MAX, 0.0f, 0};
}

// Autoprueba: verde, amarillo y rojo uno a uno (~400 ms cada uno) y luego todo apagado.
// Corre una sola vez en setup(), antes de leer sensores y de conectar Wi-Fi/MQTT.
void autopruebaSemaforo() {
  ponerLeds(true, false, false);  delay(PRUEBA_LED_MS);
  ponerLeds(false, true, false);  delay(PRUEBA_LED_MS);
  ponerLeds(false, false, true);  delay(PRUEBA_LED_MS);
  ponerLeds(false, false, false);
}

bool sdsConDato() {
  return lastSdsOk != 0 && millis() - lastSdsOk < SDS_SIN_DATO_MS && !isnan(pm25);
}

// Guarda una muestra de PM2.5 cada MUESTRA_MS en la cubeta del minuto actual.
void muestrearPm25() {
  if (millis() - lastMuestra < MUESTRA_MS) return;
  lastMuestra = millis();
  if (!sdsConDato()) return;
  uint32_t minuto = millis() / 60000UL;
  Cubeta &c = cubetas[minuto % VENTANA_MIN];
  if (c.minuto != minuto) c = {minuto, 0.0f, 0};
  c.suma += pm25;
  c.n++;
}

// Media de PM2.5 de los ultimos 60 min (NAN si no hay muestras).
float mediaPm25Hora() {
  uint32_t ahora = millis() / 60000UL;
  float suma = 0.0f;
  uint32_t n = 0;
  for (uint8_t i = 0; i < VENTANA_MIN; i++) {
    const Cubeta &c = cubetas[i];
    if (c.n == 0 || c.minuto == UINT32_MAX || ahora - c.minuto >= VENTANA_MIN) continue;
    suma += c.suma;
    n += c.n;
  }
  return n ? suma / n : NAN;
}

Semaforo categoriaSemaforo() {
  if (!sdsConDato()) return SEM_SIN_DATO;
  float media = mediaPm25Hora();
  if (isnan(media) || media < 0) return SEM_SIN_DATO;
  int c = (int)media;                 // truncado a entero, como api/ica.py
  if (c <= PM25_MAX_BUENA) return SEM_BUENA;
  if (c <= PM25_MAX_ACEPTABLE) return SEM_ACEPTABLE;
  if (c <= PM25_MAX_SENSIBLES) return SEM_SENSIBLES;
  return SEM_DANINA_O_PEOR;           // danina, muy danina y peligrosa
}

// Sin delay(): solo millis(), para no retrasar lecturas ni MQTT.
void actualizarSemaforo() {
  muestrearPm25();
  unsigned long t = millis();
  switch (categoriaSemaforo()) {
    case SEM_BUENA:     ponerLeds(true, false, false); break;
    case SEM_ACEPTABLE: ponerLeds(false, true, false); break;
    case SEM_SENSIBLES: ponerLeds(false, false, true); break;
    case SEM_DANINA_O_PEOR:
      ponerLeds(false, false, (t / PARPADEO_ROJO_MS) % 2 == 0);
      break;
    case SEM_SIN_DATO: {
      uint8_t paso = (t / PASO_SIN_DATO_MS) % 3;   // uno a uno: verde, amarillo, rojo
      ponerLeds(paso == 0, paso == 1, paso == 2);
      break;
    }
  }
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
  iniciarSemaforo();
  autopruebaSemaforo();

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
  actualizarSemaforo();
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
