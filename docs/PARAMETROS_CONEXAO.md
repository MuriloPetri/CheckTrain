# Parâmetros de conexão na cabine — detecção de sonolência

Este documento responde:

1. Qual o **tamanho das imagens** usadas para verificar a sonolência?
2. Quantas **imagens por segundo** são necessárias?
3. Qual a **velocidade mínima de conexão** para rodar a verificação?
4. Quais os demais **parâmetros de conexão dentro da cabine**?

Os números da seção 3 foram **medidos** com o script `Site/medir_conexao.py`, usando a webcam de um notebook.
Ele usa o mesmo modelo (MediaPipe Face Landmarker), os mesmos pontos dos olhos e o mesmo cálculo de EAR do
`detector_sono.py`. Para medir o ESP32-CAM real dentro da cabine, veja a seção 7.

---

## 1. Como a verificação funciona (o que trafega na rede)

```
ESP32-CAM ──Wi-Fi (rede local)──> Computador da cabine ──> detector_sono.py (MediaPipe)
  câmera       stream MJPEG             recebe as imagens        calcula o EAR dos olhos
  640x480      (uma imagem JPEG          e analisa cada uma      e decide ACORDADO /
  JPEG         atrás da outra)                                   SONOLENTO / DORMINDO
```

- O ESP32-CAM (`Partesensores.ino`) envia um **stream MJPEG**: uma sequência de imagens JPEG independentes.
- Cada imagem é analisada no computador: o MediaPipe acha 478 pontos do rosto, e o **EAR** (Eye Aspect Ratio,
  a razão altura/largura do olho) é calculado.
- **EAR < 0,22** = olho fechado. Olho fechado por **≥ 0,5 s** = SONOLENTO; por **≥ 1,8 s** = DORMINDO.
- **A análise é feita localmente**, sem internet. A internet só é usada para o aviso no Telegram (seção 5).

O tráfego que importa é **ESP32-CAM → computador**, e ele depende de três fatores:

```
velocidade necessária  =  tamanho de cada imagem  ×  imagens por segundo  ×  ~1,10 (cabeçalhos de rede)
```

---

## 2. Quantas imagens por segundo são necessárias (fps)

Os tempos de decisão do detector são 0,5 s e 1,8 s. A taxa de quadros define com que **precisão** esses tempos são
medidos e se um fechamento curto de olhos é visto.

| fps | Intervalo entre imagens | Imagens em 0,5 s (sonolento) | Erro no tempo de 1,8 s | Avaliação |
|---:|---:|---:|---:|---|
| 2  | 500 ms | 1 | até ±0,5 s | Insuficiente: um fechamento de olhos pode passar despercebido |
| **5**  | **200 ms** | **2–3** | **±0,2 s** | **Mínimo aceitável** |
| **10** | **100 ms** | **5** | **±0,1 s** | **Recomendado** |
| 15 | 67 ms | 7–8 | ±0,07 s | Ótimo (sobra margem) |
| 30 | 33 ms | 15 | ±0,03 s | Desnecessário para esta aplicação |

Uma piscada normal dura de 100 a 400 ms. Para **não confundir piscada com sonolência**, é preciso ver o olho
reabrir. A 10 fps a piscada aparece em 1 a 4 imagens, e o detector só marca SONOLENTO a partir de 5 imagens
seguidas com o olho fechado.

**Conclusão: mínimo de 5 fps; recomendado de 10 a 15 fps.**

O tempo de processamento não limita isso. O MediaPipe levou **~9 a 11 ms por imagem** no notebook do teste,
o que daria para analisar cerca de 90 imagens por segundo.

---

## 3. Tamanho das imagens (medido)

Medição com webcam 1280x720, 38 amostras em 20 s, rosto de frente para a câmera.
"Qualidade JPEG" é a do OpenCV (0–100, maior = melhor).

| Resolução | JPEG | **KB por imagem** | Rosto detectado | Erro do EAR* | Largura do olho | Processamento |
|---|---:|---:|---:|---:|---:|---:|
| QQVGA 160x120 | 50 | 2,4 | 100% | 0,073 | 8 px | 9,2 ms |
| QQVGA 160x120 | 75 | 3,4 | 100% | 0,073 | 8 px | 9,4 ms |
| QVGA 320x240 | 50 | 7,3 | 100% | 0,067 | 15 px | 9,2 ms |
| QVGA 320x240 | 75 | 12,2 | 100% | 0,057 | 15 px | 9,2 ms |
| QVGA 320x240 | 90 | 24,5 | 100% | 0,051 | 15 px | 9,1 ms |
| **VGA 640x480** | **50** | **33,8** | **100%** | **0,044** | **30 px** | **9,8 ms** |
| VGA 640x480 | 75 | 57,6 | 100% | 0,041 | 30 px | 9,6 ms |
| VGA 640x480 | 90 | 108,5 | 100% | 0,041 | 30 px | 9,6 ms |
| HD 1280x720 | 50 | 135,3 | 100% | 0,007 | 56 px | 10,9 ms |
| HD 1280x720 | 75 | 167,8 | 100% | 0,007 | 56 px | 10,7 ms |

\* **Erro do EAR**: diferença média entre o EAR medido na imagem reduzida/comprimida e o EAR da imagem original
(1280x720 sem compressão). O erro no HD é baixo também porque ele tem a mesma resolução da referência.

**Como interpretar.** O limiar é 0,22, e um olho aberto costuma ficar entre 0,28 e 0,32. Isso dá uma margem de
apenas ~0,06–0,10. Um erro de 0,07 (160x120) é do tamanho da margem e gera falsos alarmes ou alarmes perdidos.

- **160x120**: o rosto é encontrado, mas o olho tem só ~8 pixels de largura e o EAR fica impreciso. **Não serve.**
- **320x240**: funciona com o rosto perto da câmera, mas o erro (~0,05–0,06) ainda é alto. **Mínimo absoluto.**
- **640x480**: olho com ~30 px e erro de ~0,04. **Recomendado.** É o que o `Partesensores.ino` já usa
  (`FRAMESIZE_VGA`, `jpeg_quality = 12`).
- **1280x720**: mais preciso, mas cada imagem é 4 a 5 vezes maior. O ESP32-CAM não sustenta isso com boa taxa de quadros.

**Regra prática:** o olho precisa ter pelo menos **~25–30 pixels de largura** na imagem. Na cabine a câmera
fica mais longe do rosto do que no teste, então **não use menos que 640x480**.

**Tamanho de cada imagem no projeto:** com 640x480 e a compressão do ESP32-CAM, a estimativa é de
**~25 a 40 KB por imagem**. A qualidade 12 do ESP32 equivale aproximadamente à faixa 50–60 do OpenCV. Esse valor
deve ser confirmado medindo o ESP32 real (seção 7).

---

## 4. Velocidade mínima de conexão (ESP32-CAM → computador)

Calculada com os tamanhos medidos (inclui ~10% de cabeçalhos HTTP/TCP/IP/Wi-Fi):

| Resolução / JPEG | KB/img | 5 fps | 10 fps | 15 fps | 20 fps |
|---|---:|---:|---:|---:|---:|
| QVGA 320x240 / 75 | 12,2 | 0,55 Mbps | 1,10 Mbps | 1,65 Mbps | 2,20 Mbps |
| **VGA 640x480 / 50** | **33,8** | **1,52 Mbps** | **3,04 Mbps** | **4,56 Mbps** | 6,09 Mbps |
| VGA 640x480 / 75 | 57,6 | 2,60 Mbps | 5,19 Mbps | 7,79 Mbps | 10,39 Mbps |
| HD 1280x720 / 50 | 135,3 | 6,10 Mbps | 12,19 Mbps | 18,29 Mbps | 24,39 Mbps |

### Resposta

| | Configuração | Velocidade |
|---|---|---|
| **Mínimo absoluto** | 320x240, 5 fps | **≈ 0,6 Mbps** (precisão do EAR baixa) |
| **Mínimo recomendado** | **640x480, 5 fps** | **≈ 1,5 Mbps** |
| **Recomendado (operação)** | **640x480, 10 fps** | **≈ 3 Mbps** |
| Com folga | 640x480, 15 fps | ≈ 4,5–5 Mbps |

Para considerar picos e a variação do Wi-Fi, **dimensione a rede da cabine para pelo menos 2x o valor
recomendado, ou seja, ~6 Mbps reais** entre o ESP32-CAM e o computador.

Limite do hardware: o ESP32-CAM, em Wi-Fi 2,4 GHz (802.11n), costuma entregar **na prática ~5 a 10 Mbps** em
streaming. Por isso 640x480 a 10–15 fps é o ponto de equilíbrio do sistema. HD não cabe com boa taxa de quadros.

---

## 5. Outros parâmetros de conexão na cabine

| Ligação | Meio | Taxa necessária | Latência | Precisa de internet? |
|---|---|---|---|---|
| **ESP32-CAM → computador** (imagens) | Wi-Fi 2,4 GHz local | **≥ 3 Mbps** (recomendado), 1,5 Mbps mínimo | < 100 ms | **Não** |
| **Leitor RFID → computador** | Cabo USB (serial 115200 baud) | < 1 kbps (comando de 7 bytes a cada 150 ms) | desprezível | Não |
| **Painel web → navegador** (status) | Rede local | ~10 KB/s (consulta a cada 0,4 s) | < 500 ms | Não |
| **Painel web → navegador** (vídeo) | Rede local | igual ao stream da câmera (~3–8 Mbps) | < 200 ms | Não |
| **Bot do Telegram** (alertas e alocação) | Internet (4G/3G/Wi-Fi) | ~1–3 KB por mensagem | 1–3 s aceitável | **Sim** (só esta parte) |

### Recomendações para a rede dentro da cabine

- **Sinal Wi-Fi no ESP32-CAM:** RSSI de **-67 dBm ou melhor**. Abaixo de -75 dBm a taxa cai e as imagens chegam com atraso.
- **Banda de 2,4 GHz:** o ESP32 não suporta 5 GHz. Use um canal pouco congestionado (1, 6 ou 11).
- **Perda de pacotes < 1%** e **latência < 100 ms** no enlace câmera → computador.
- **Rede local dedicada:** roteador ou hotspot na própria cabine. A detecção **não pode depender de internet
  externa**, porque ela cai em túneis e áreas rurais.
- **Tempo total até o alarme:** cerca de 1,8 s (regra do DORMINDO) + ~0,1–0,2 s (rede e processamento) ≈ **2 s**.
  Esse tempo vem da regra de decisão, não da conexão.
- **Sem internet:** o alarme local (som e tela) funciona normalmente. Só o aviso no Telegram atrasa até a
  conexão voltar.

---

## 6. Resumo para a apresentação

- **Imagem usada:** JPEG de **640x480**, **~25–40 KB** cada (medido: 33,8 KB com compressão equivalente).
- **Taxa de imagens:** mínimo **5 fps**; recomendado **10 fps**.
- **Velocidade mínima:** **~1,5 Mbps** (640x480 a 5 fps); **recomendado ~3 Mbps** (10 fps); dimensionar a
  rede para **~6 Mbps**.
- **Processamento:** ~10 ms por imagem, feito localmente na cabine, sem depender de internet.
- **Internet:** só para o Telegram, com poucos KB por mensagem.
- **Qualidade mínima da imagem:** o olho precisa ter **≥ 25–30 px** de largura. Abaixo de 320x240 o EAR fica impreciso.

---

## 7. Como medir o ESP32-CAM real na cabine

Com o `Partesensores.ino` gravado e o ESP32 na rede:

```bash
cd Site
py -3.12 medir_conexao.py --url http://IP_DO_ESP32/stream --segundos 30 --saida medicao_esp32.json
```

O script mostra:

- `fps_real`: quantas imagens por segundo o ESP32 entregou de fato;
- `kb_por_imagem`: tamanho real de cada imagem;
- `taxa_real_mbps`: velocidade usada pela câmera;
- `intervalo_entre_imagens_ms`: regularidade do Wi-Fi;
- `deteccao_rosto_pct` e `largura_olho_px`: se a posição da câmera na cabine está boa;
- `mbps_por_fps`: velocidade necessária para cada taxa de quadros.

**Atenção:** o `detector_sono.py` também usa o stream, e o ESP32 só atende uma conexão por vez. Feche o painel
(`servidor.py`) antes de medir.

Para repetir a medição feita neste documento (webcam):

```bash
py -3.12 medir_conexao.py --webcam --segundos 20
```
