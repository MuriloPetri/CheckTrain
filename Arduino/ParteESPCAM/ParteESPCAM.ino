#include "esp_camera.h"
#include <WiFi.h>
#include <WebServer.h>

// ================= CONFIGURAÇÃO DO WI-FI =================
const char* ssid     = "SEU_WIFI";      // coloque o nome da sua rede
const char* password = "SUA_SENHA";     // coloque a senha da sua rede
// =========================================================

// Pinagem padrão do modelo AI-Thinker ESP32-CAM
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22
#define FLASH_LED_PIN      4

WebServer server(80);
bool estadoFlash = false;

void iniciarCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0       = Y2_GPIO_NUM;
  config.pin_d1       = Y3_GPIO_NUM;
  config.pin_d2       = Y4_GPIO_NUM;
  config.pin_d3       = Y5_GPIO_NUM;
  config.pin_d4       = Y6_GPIO_NUM;
  config.pin_d5       = Y7_GPIO_NUM;
  config.pin_d6       = Y8_GPIO_NUM;
  config.pin_d7       = Y9_GPIO_NUM;
  config.pin_xclk     = XCLK_GPIO_NUM;
  config.pin_pclk     = PCLK_GPIO_NUM;
  config.pin_vsync    = VSYNC_GPIO_NUM;
  config.pin_href     = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn     = PWDN_GPIO_NUM;
  config.pin_reset    = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size   = FRAMESIZE_VGA; // 640x480

  if (psramFound()) {
    config.jpeg_quality = 10;
    config.fb_count     = 2;
    config.grab_mode    = CAMERA_GRAB_LATEST; // Garante sempre o frame mais recente
  } else {
    config.jpeg_quality = 12;
    config.fb_count     = 1;
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Erro ao iniciar camera: 0x%x\n", err);
    delay(2000);
    ESP.restart();
  }
}

// Rota "/" -> Página web de visualização ao vivo
void rotaPaginaInicial() {
  String html = R"rawliteral(
  <!DOCTYPE html>
  <html lang="pt-BR">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Teste ESP32-CAM</title>
    <style>
      body { font-family: sans-serif; background: #121212; color: #fff; text-align: center; margin: 20px; }
      img { width: 100%; max-width: 640px; border: 2px solid #333; border-radius: 8px; background: #000; }
      button { margin-top: 15px; padding: 12px 24px; font-size: 16px; border: none; border-radius: 6px;
               background: #007bff; color: white; cursor: pointer; }
      button:active { background: #0056b3; }
    </style>
  </head>
  <body>
    <h2>Monitoramento ESP32-CAM</h2>
    <img id="stream" src="/foto" alt="Carregando camera...">
    <br>
    <button onclick="fetch('/flash')">Alternar Flash LED</button>
    <script>
      const img = document.getElementById('stream');
      function atualizarFrame() {
        img.src = '/foto?t=' + Date.now();
      }
      img.onload = () => setTimeout(atualizarFrame, 80);
      img.onerror = () => setTimeout(atualizarFrame, 500);
    </script>
  </body>
  </html>
  )rawliteral";

  server.send(200, "text/html", html);
}

// Rota "/foto" -> Captura e envia uma imagem JPEG
void rotaFoto() {
  camera_fb_t * fb = esp_camera_fb_get();
  if (!fb) {
    server.send(500, "text/plain", "Falha ao capturar imagem");
    return;
  }

  WiFiClient client = server.client();
  client.println("HTTP/1.1 200 OK");
  client.println("Content-Type: image/jpeg");
  client.println("Content-Length: " + String(fb->len));
  client.println("Cache-Control: no-store, no-cache, must-revalidate");
  client.println();
  client.write(fb->buf, fb->len);

  esp_camera_fb_return(fb);
}

// Rota "/flash" -> Liga/desliga o LED de flash
void rotaFlash() {
  estadoFlash = !estadoFlash;
  digitalWrite(FLASH_LED_PIN, estadoFlash ? HIGH : LOW);
  server.send(200, "text/plain", estadoFlash ? "Flash LIGADO" : "Flash DESLIGADO");
}

void setup() {
  Serial.begin(115200);
  pinMode(FLASH_LED_PIN, OUTPUT);
  digitalWrite(FLASH_LED_PIN, LOW);

  iniciarCamera();

  WiFi.begin(ssid, password);
  Serial.print("Conectando ao Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  // Registra as rotas de teste
  server.on("/", HTTP_GET, rotaPaginaInicial);
  server.on("/foto", HTTP_GET, rotaFoto);
  server.on("/flash", HTTP_GET, rotaFlash);
  server.begin();

  Serial.println("\n\nESP32-CAM pronto!");
  Serial.print("Acesse no navegador: http://");
  Serial.println(WiFi.localIP());
}

void loop() {
  server.handleClient();
}