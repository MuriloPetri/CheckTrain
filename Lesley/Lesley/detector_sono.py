import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import numpy as np
import time
import os
import urllib.request
import ssl

# ================= CONFIGURAÇÃO =================
URL_ESP32_CAM = "http://172.20.10.2/stream"  # <-- COLOQUE O IP DO SEU ESP32-CAM AQUI

LIMIAR_OLHO_FECHADO = 0.22   # Abaixo disso considera olho fechado (ajuste entre 0.18 e 0.25)
TEMPO_SONOLENTO     = 0.5    # Segundos com olho fechado para marcar SONOLENTO
TEMPO_DORMINDO      = 1.8    # Segundos com olho fechado para disparar DORMINDO
# ================================================

# Cores ANSI para destaque no terminal do VS Code
VERDE    = "\033[92;1m"
AMARELO  = "\033[93;1m"
VERMELHO = "\033[91;1m"
CINZA    = "\033[90m"
RESET    = "\033[0m"

# Baixa o modelo oficial FaceLandmarker na primeira execução
PASTA_ATUAL = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH  = os.path.join(PASTA_ATUAL, "face_landmarker.task")
MODEL_URL   = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"

if not os.path.exists(MODEL_PATH):
    print("Baixando modelo facial do MediaPipe (apenas na primeira vez)...")
    ssl_ctx = ssl._create_unverified_context()
    with urllib.request.urlopen(MODEL_URL, context=ssl_ctx) as resp, open(MODEL_PATH, "wb") as out:
        out.write(resp.read())
    print("Modelo baixado com sucesso!\n")

with open(MODEL_PATH, "rb") as f:
    model_bytes = f.read()

base_options = python.BaseOptions(model_asset_buffer=model_bytes)
options = vision.FaceLandmarkerOptions(
    base_options=base_options,
    num_faces=1,
    min_face_detection_confidence=0.5,
    min_face_presence_confidence=0.5,
    min_tracking_confidence=0.5
)
detector = vision.FaceLandmarker.create_from_options(options)

# Pontos dos olhos na malha facial (478 pontos)
OLHO_ESQ = [362, 385, 387, 263, 373, 380]
OLHO_DIR = [33, 160, 158, 133, 153, 144]

def calcular_ear(landmarks, indices, largura, altura):
    pontos = [np.array([landmarks[i].x * largura, landmarks[i].y * altura]) for i in indices]
    v1 = np.linalg.norm(pontos[1] - pontos[5])
    v2 = np.linalg.norm(pontos[2] - pontos[4])
    h  = np.linalg.norm(pontos[0] - pontos[3])
    return (v1 + v2) / (2.0 * h) if h > 0 else 0.0

def imprimir_status_terminal(estado, ear=0.0, duracao=0.0):
    hora = time.strftime("%H:%M:%S")
    if estado == "ACORDADO":
        print(f"\n{VERDE}=================================================={RESET}")
        print(f"{VERDE}  [{hora}] STATUS ATUAL: >>> ACORDADO <<< (EAR: {ear:.2f}){RESET}")
        print(f"{VERDE}=================================================={RESET}")
    elif estado == "DORMINDO":
        print(f"\n{VERMELHO}!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!{RESET}")
        print(f"{VERMELHO}  [{hora}] ALERTA CRITICO: >>> DORMINDO <<< ({duracao:.1f}s){RESET}")
        print(f"{VERMELHO}!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!{RESET}\a")
    elif estado == "SONOLENTO":
        print(f"{AMARELO}[{hora}] Atencao: pessoa SONOLENTA (olhos fechando... {duracao:.1f}s){RESET}")
    else:
        print(f"{CINZA}[{hora}] Aguardando rosto na camera...{RESET}")

print(f"Conectando ao ESP32-CAM em {URL_ESP32_CAM} ...")
cap = cv2.VideoCapture(URL_ESP32_CAM)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

if not cap.isOpened():
    print(f"{VERMELHO}Erro: Nao foi possivel conectar ao ESP32-CAM. Verifique o IP e o Wi-Fi.{RESET}")
    exit()

estado_atual = ""
inicio_olhos_fechados = None
ultimo_print_dormindo = 0

while True:
    ret, frame = cap.read()
    if not ret:
        time.sleep(0.1)
        continue

    altura, largura, _ = frame.shape
    rgb = np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    resultado = detector.detect(mp_image)

    novo_estado = "SEM_ROSTO"
    ear_medio = 0.0
    tempo_fechado = 0.0

    if resultado.face_landmarks:
        landmarks = resultado.face_landmarks[0]
        ear_esq = calcular_ear(landmarks, OLHO_ESQ, largura, altura)
        ear_dir = calcular_ear(landmarks, OLHO_DIR, largura, altura)
        ear_medio = (ear_esq + ear_dir) / 2.0

        # Desenha os pontos dos olhos na janela de vídeo
        for idx in OLHO_ESQ + OLHO_DIR:
            cx, cy = int(landmarks[idx].x * largura), int(landmarks[idx].y * altura)
            cv2.circle(frame, (cx, cy), 2, (0, 255, 0), -1)

        if ear_medio < LIMIAR_OLHO_FECHADO:
            if inicio_olhos_fechados is None:
                inicio_olhos_fechados = time.time()
            tempo_fechado = time.time() - inicio_olhos_fechados

            if tempo_fechado >= TEMPO_DORMINDO:
                novo_estado = "DORMINDO"
            elif tempo_fechado >= TEMPO_SONOLENTO:
                novo_estado = "SONOLENTO"
            else:
                novo_estado = "ACORDADO"
        else:
            inicio_olhos_fechados = None
            novo_estado = "ACORDADO"
    else:
        inicio_olhos_fechados = None

    # Atualiza o terminal na troca de estado e reforça DORMINDO a cada 2s
    if novo_estado != estado_atual:
        estado_atual = novo_estado
        imprimir_status_terminal(estado_atual, ear_medio, tempo_fechado)
        if estado_atual == "DORMINDO":
            ultimo_print_dormindo = time.time()
    elif estado_atual == "DORMINDO" and (time.time() - ultimo_print_dormindo >= 2.0):
        imprimir_status_terminal("DORMINDO", ear_medio, tempo_fechado)
        ultimo_print_dormindo = time.time()

    cor_video = (0, 255, 0) if novo_estado == "ACORDADO" else (0, 0, 255) if novo_estado == "DORMINDO" else (0, 255, 255)
    cv2.putText(frame, f"ESTADO: {novo_estado}", (15, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.9, cor_video, 2)
    cv2.imshow("Monitoramento Maquinista - ESP32-CAM", frame)

    # Pressione 'q' na janela de vídeo para encerrar
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()