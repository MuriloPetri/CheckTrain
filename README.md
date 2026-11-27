# CheckTrain

Monitoramento de sono do maquinista na cabine, com login por crachá RFID, sala de controle e aviso pelo Telegram.

## Partes

| Pasta | O que faz |
|---|---|
| `Arduino/ParteESPCAM` | ESP32-CAM: página de teste da câmera e flash |
| `Arduino/Partesensores` | ESP32-CAM: stream MJPEG (`/stream`) para o detector |
| `Arduino/ParteRFID` | TTGO + leitor UHF RFID: identifica o funcionário pelo crachá |
| `Lesley/Lesley/detector_sono.py` | Detecta olhos fechados (MediaPipe) → ACORDADO / SONOLENTO / DORMINDO |
| `Site/` | Painel web que liga tudo |

## Painel (`Site/`)

- **Login pelo crachá RFID** (ou "login de teste" sem o leitor)
- **Monitoramento:** vídeo ao vivo com marcação dos olhos, estado do sono, alerta sonoro/visual
- **Maquinistas:** lista, perfil e horário de trabalho (escala da semana)
- **Motoristas:** quem busca/leva os maquinistas
- **Sala de controle:** solicitações de motorista, alocação manual ou por agente automático, corridas simuladas
- **Telegram:** cada solicitação chega no bot com um botão por motorista; tocar no botão inicia o deslocamento

Quando o maquinista logado é detectado **DORMINDO**, a sala recebe automaticamente uma solicitação urgente.

## Como rodar

Requer Python 3.10–3.12 (64 bits).

```bash
pip install -r Site/requirements.txt
cd Site
python servidor.py --webcam      # usa a câmera do computador
python servidor.py --esp32       # usa o ESP32-CAM (IP em detector_sono.py)
python servidor.py --demo        # simula tudo, sem hardware
```

Abra http://localhost:8080.

- **RFID:** ligue a placa do `ParteRFID` no USB (porta escolhida automaticamente ou no painel).
- **Wi-Fi do ESP32:** preencha `SEU_WIFI` / `SUA_SENHA` nos `.ino` antes de gravar.
- **Telegram:** crie um bot no @BotFather, cole o token no painel (Conexões) e mande `/start` para o bot.
  O token fica em `Site/telegram.json`, que **não** vai para o Git.
- Cadastro de maquinistas e motoristas: `Site/dados.json` (dados de exemplo).

## Parâmetros de conexão

Tamanho das imagens, taxa de quadros e velocidade mínima de rede para a detecção de sonolência (com medições):
[docs/PARAMETROS_CONEXAO.md](docs/PARAMETROS_CONEXAO.md).
Para medir: `py -3.12 Site/medir_conexao.py --webcam` ou `--url http://IP_DO_ESP32/stream`.
