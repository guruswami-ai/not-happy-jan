/*
 * Not-Happy-Jan ESP32 Push Bell
 *
 * Listens for POST /bell and fires a relay to ring an electromechanical bell.
 * Configure your device IP in .env: ESP32_BELL_URL=http://<ip>/bell
 *
 * Wiring:
 *   GPIO_PIN (default 26) → relay IN
 *   Relay NO → bell solenoid + 5-12V supply
 *   Relay COM → supply negative
 *
 * Board: ESP32 Dev Module, 80 MHz, 4MB flash
 */

#include <WiFi.h>
#include <WebServer.h>
#include <ArduinoJson.h>

const char* SSID     = "YOUR_SSID";
const char* PASSWORD = "YOUR_WIFI_PASSWORD";

const int  GPIO_PIN     = 26;
const int  PULSE_MS     = 150;   // bell strike duration (ms)
const int  COOLDOWN_MS  = 500;   // minimum gap between strikes

WebServer server(80);
unsigned long lastStrike = 0;

void handleBell() {
  String body = server.arg("plain");
  StaticJsonDocument<256> doc;
  deserializeJson(doc, body);

  const char* intent = doc["intent"] | "ok";
  int pulses = 1;
  if (strcmp(intent, "err") == 0 || strcmp(intent, "attn") == 0) {
    pulses = 3;  // three rings for errors
  } else if (strcmp(intent, "celebrate") == 0) {
    pulses = 2;
  }

  unsigned long now = millis();
  if (now - lastStrike < COOLDOWN_MS) {
    server.send(429, "application/json", "{\"error\":\"cooldown\"}");
    return;
  }
  lastStrike = now;

  for (int i = 0; i < pulses; i++) {
    digitalWrite(GPIO_PIN, HIGH);
    delay(PULSE_MS);
    digitalWrite(GPIO_PIN, LOW);
    if (i < pulses - 1) delay(PULSE_MS);
  }

  server.send(200, "application/json", "{\"ok\":true}");
}

void handleRoot() {
  server.send(200, "text/plain", "Not-Happy-Jan Bell\nPOST /bell to ring");
}

void setup() {
  Serial.begin(115200);
  pinMode(GPIO_PIN, OUTPUT);
  digitalWrite(GPIO_PIN, LOW);

  WiFi.begin(SSID, PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nConnected — IP: %s\n", WiFi.localIP().toString().c_str());

  server.on("/",     HTTP_GET,  handleRoot);
  server.on("/bell", HTTP_POST, handleBell);
  server.begin();
  Serial.println("Server started");
}

void loop() {
  server.handleClient();
}
