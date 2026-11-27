#include <TFT_eSPI.h>
#include <SPI.h>

#define UHF_RX_PIN 21 // Fio Branco (TX do leitor)
#define UHF_TX_PIN 22 // Fio Amarelo (RX do leitor)

#define BOTAO_ESQ 0   // Botão esquerdo (voltar para tela inicial)
#define BOTAO_DIR 35  // Botão direito (mostrar último EPC no Serial)

TFT_eSPI tft = TFT_eSPI();

// ================= AJUSTE DE DISTÂNCIA (~15 cm) =================
// Quanto MAIOR (menos negativo) o valor, MAIS PERTO o crachá precisa estar.
// Exemplos típicos: -38 (bem encostado ~5cm) | -45 (~15cm) | -55 (~35cm)
const int RSSI_MINIMO = -45;
// ================================================================

// ================= CADASTRO DE FUNCIONÁRIOS =====================
struct Funcionario {
  const char* epc;   // Código EPC completo do adesivo (24 caracteres hex)
  const char* nome;
  const char* cargo;
};

// Substitua pelos códigos EPC de cada adesivo que aparecerem no Monitor Serial:
Funcionario funcionarios[] = {
  {"E2801160600002187F218050", "Deivid",   "Maquinista"},
  {"E2801160600002187F2144F1", "Enzo", "Supervisor"},
  {"300833B2DDD9014000000001", "Ana",    "Operadora"}
};
const int TOTAL_FUNCIONARIOS = sizeof(funcionarios) / sizeof(funcionarios[0]);
// ================================================================

// Comandos do módulo UHF-RFID (M100 / JRD-4035)
const byte CMD_PARAR_CONT[]   = {0xBB, 0x00, 0x28, 0x00, 0x00, 0x28, 0x7E};
const byte CMD_POTENCIA_MIN[] = {0xBB, 0x00, 0xB6, 0x00, 0x02, 0x05, 0xDC, 0x99, 0x7E}; // 15.00 dBm (mínimo)
const byte CMD_LER_UMA_VEZ[]  = {0xBB, 0x00, 0x22, 0x00, 0x00, 0x22, 0x7E};

enum EstadoTela {
  TELA_NENHUMA,
  TELA_ESPERA,
  TELA_IDENTIFICADO,
  TELA_DESCONHECIDO
};

EstadoTela telaAtual = TELA_NENHUMA;

String ultimoEPC = "";
String ultimoNomeRegistrado = "Nenhum";
unsigned long ultimaLeituraMs = 0;
unsigned long ultimoPollMs = 0;
unsigned long tempoTelaStatusMs = 0;

void desenharTelaEspera(bool forcar = false) {
  if (telaAtual == TELA_ESPERA && !forcar) return;
  telaAtual = TELA_ESPERA;

  tft.fillScreen(TFT_BLACK);
  tft.fillRect(0, 0, 240, 32, TFT_NAVY);
  tft.setTextColor(TFT_WHITE, TFT_NAVY);
  tft.setTextSize(2);
  tft.setCursor(12, 8);
  tft.println("PONTO / ACESSO UHF");

  tft.setTextColor(TFT_CYAN, TFT_BLACK);
  tft.setTextSize(2);
  tft.setCursor(12, 52);
  tft.println("Aproxime o cracha");

  tft.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
  tft.setTextSize(1);
  tft.setCursor(12, 92);
  tft.print("Ultimo registro: ");
  tft.println(ultimoNomeRegistrado);

  tft.setCursor(12, 115);
  tft.println("Alcance configurado: ~15 cm");
}

void desenharTelaIdentificado(const char* nome, const char* cargo, const char* epc) {
  telaAtual = TELA_IDENTIFICADO;
  tempoTelaStatusMs = millis();

  tft.fillScreen(TFT_DARKGREEN);
  tft.setTextColor(TFT_WHITE, TFT_DARKGREEN);
  tft.setTextSize(2);
  tft.setCursor(12, 10);
  tft.println("FUNCIONARIO OK");

  tft.drawFastHLine(12, 34, 216, TFT_WHITE);

  tft.setTextSize(3);
  tft.setCursor(12, 46);
  tft.println(nome);

  tft.setTextSize(2);
  tft.setCursor(12, 80);
  tft.println(cargo);

  tft.setTextSize(1);
  tft.setCursor(12, 114);
  tft.print("EPC: ");
  tft.println(epc);
}

void desenharTelaDesconhecido(const char* epc) {
  telaAtual = TELA_DESCONHECIDO;
  tempoTelaStatusMs = millis();

  tft.fillScreen(TFT_MAROON);
  tft.setTextColor(TFT_WHITE, TFT_MAROON);
  tft.setTextSize(2);
  tft.setCursor(12, 12);
  tft.println("CRACHA NAO CADASTRADO");

  tft.drawFastHLine(12, 36, 216, TFT_WHITE);

  tft.setTextSize(1);
  tft.setCursor(12, 54);
  tft.println("Copie este EPC do Serial para cadastrar:");

  tft.setTextColor(TFT_YELLOW, TFT_MAROON);
  tft.setCursor(12, 78);
  tft.println(epc);
}

int buscarFuncionario(const char* epcLido) {
  for (int i = 0; i < TOTAL_FUNCIONARIOS; i++) {
    if (strcmp(funcionarios[i].epc, epcLido) == 0) {
      return i;
    }
  }
  return -1;
}

void setup() {
  Serial.begin(115200);
  Serial2.begin(115200, SERIAL_8N1, UHF_RX_PIN, UHF_TX_PIN);

  pinMode(BOTAO_ESQ, INPUT_PULLUP);
  pinMode(BOTAO_DIR, INPUT);

  tft.init();
  tft.setRotation(1);
  desenharTelaEspera(true);

  delay(200);
  // Garante que qualquer leitura contínua anterior pare e reduz potência para 15 dBm
  Serial2.write(CMD_PARAR_CONT, sizeof(CMD_PARAR_CONT));
  delay(80);
  Serial2.write(CMD_POTENCIA_MIN, sizeof(CMD_POTENCIA_MIN));
  delay(80);

  // Limpa respostas de configuração do buffer serial
  while (Serial2.available()) Serial2.read();

  Serial.println("Leitor UHF configurado (15 dBm / curto alcance).");
  Serial.printf("Filtro RSSI ativo: >= %d dBm\n", RSSI_MINIMO);
}

void loop() {
  // Botão Esquerdo: volta imediatamente para a tela de espera
  if (digitalRead(BOTAO_ESQ) == LOW && telaAtual != TELA_ESPERA) {
    desenharTelaEspera();
    delay(250);
  }

  // Botão Direito: imprime o último EPC no Serial
  if (digitalRead(BOTAO_DIR) == LOW && ultimoEPC != "") {
    Serial.println("Ultimo EPC lido: " + ultimoEPC);
    delay(300);
  }

  // Volta para a tela de espera após 4 segundos
  if (telaAtual != TELA_ESPERA && (millis() - tempoTelaStatusMs >= 4000)) {
    desenharTelaEspera();
  }

  // Envia um pulso rápido de leitura a cada 150 ms (evita consumo contínuo e flicker na tela)
  if (millis() - ultimoPollMs >= 150) {
    ultimoPollMs = millis();
    Serial2.write(CMD_LER_UMA_VEZ, sizeof(CMD_LER_UMA_VEZ));
  }

  // Lê resposta do M5Stack UHF-RFID
  while (Serial2.available() >= 5) {
    if (Serial2.read() != 0xBB) continue;

    byte tipo   = Serial2.read();
    byte cmd    = Serial2.read();
    byte pl_msb = Serial2.read();
    byte pl_lsb = Serial2.read();
    int payloadLen = (pl_msb << 8) | pl_lsb;

    if (payloadLen <= 0 || payloadLen > 64) continue;

    unsigned long inicio = millis();
    while (Serial2.available() < (payloadLen + 2) && (millis() - inicio < 50)) {}

    if (Serial2.available() >= (payloadLen + 2)) {
      byte payload[64];
      Serial2.readBytes(payload, payloadLen);
      byte checksum = Serial2.read();
      byte fim      = Serial2.read();

      // Pacote de tag detectada
      if (fim == 0x7E && tipo == 0x02 && cmd == 0x22 && payloadLen > 5) {
        int8_t rssi = (int8_t)payload[0]; // Intensidade do sinal em dBm

        // Ignora a leitura se o crachá estiver mais longe que o limite (~15 cm)
        if (rssi < RSSI_MINIMO) {
          continue;
        }

        int epcLen = payloadLen - 5;
        char epcStr[33] = "";

        for (int i = 0; i < epcLen && i < 16; i++) {
          char hexByte[3];
          sprintf(hexByte, "%02X", payload[3 + i]);
          strcat(epcStr, hexByte);
        }

        String epcAtual = String(epcStr);

        if (epcAtual != ultimoEPC || (millis() - ultimaLeituraMs > 3000)) {
          ultimoEPC = epcAtual;
          ultimaLeituraMs = millis();

          int idx = buscarFuncionario(epcStr);

          if (idx >= 0) {
            ultimoNomeRegistrado = String(funcionarios[idx].nome);
            Serial.printf("[IDENTIFICADO] Nome: %s | Cargo: %s | RSSI: %d dBm | EPC: %s\n",
                          funcionarios[idx].nome, funcionarios[idx].cargo, rssi, epcStr);
            desenharTelaIdentificado(funcionarios[idx].nome, funcionarios[idx].cargo, epcStr);
          } else {
            Serial.printf("[NAO CADASTRADO] RSSI: %d dBm | EPC: %s\n", rssi, epcStr);
            desenharTelaDesconhecido(epcStr);
          }
        }
      }
    }
  }

  delay(5);
}