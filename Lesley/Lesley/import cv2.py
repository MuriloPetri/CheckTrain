import cv2
import numpy as np
import mediapipe as mp
import time
from flask import Flask, jsonify, request

app = Flask(__name__)

# Configuração do MediaPipe Face Mesh
mp_face_mesh = mp.solutions.face_mesh
face_mesh = mp_face_mesh.FaceMesh(
    max_num_faces=1,
    refine_landmarks=True,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)

# Índices dos pontos faciais (Landmarks) no MediaPipe
LEFT_EYE = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]
MOUTH = [61, 291, 13, 14]

# Variáveis globais para controle de tempo de microsono
tempo_olho_fechado_inicio = None

def distância_euclidiana(pt1, pt2):
    return np.linalg.norm(np.array(pt1) - np.array(pt2))

def calcular_ear(landmarks, eye_indices, img_w, img_h):
    pts = [(int(landmarks[idx].x * img_w), int(landmarks[idx].y * img_h)) for idx in eye_indices]
    v1 = distância_euclidiana(pts[1], pts[5])
    v2 = distância_euclidiana(pts[2], pts[4])
    h = distância_euclidiana(pts[0], pts[3])
    return (v1 + v2) / (2.0 * h) if h > 0 else 0.0

def calcular_mar(landmarks, mouth_indices, img_w, img_h):
    pts = [(int(landmarks[idx].x * img_w), int(landmarks[idx].y * img_h)) for idx in mouth_indices]
    v = distância_euclidiana(pts[2], pts[3])
    h = distância_euclidiana(pts[0], pts[1])
    return v / h if h > 0 else 0.0

def analisar_imagem(frame):
    global tempo_olho_fechado_inicio
    
    h, w, _ = frame.shape
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    results = face_mesh.process(rgb_frame)

    timestamp_atual = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Caso 1: Maquinista ausente / Rosto não detectado
    if not results.multi_face_landmarks:
        tempo_olho_fechado_inicio = None
        return {
            "timestamp": timestamp_atual,
            "status_maquinista": "AUSENTE",
            "alerta_nivel": "CRITICO",
            "alerta_sonoro": True,
            "metricas": {
                "rosto_detectado": False,
                "ear": 0.0,
                "mar": 0.0,
                "tempo_olhos_fechados_s": 0.0
            }
        }

    landmarks = results.multi_face_landmarks[0].landmark

    # Cálculo do EAR (média dos dois olhos) e MAR
    ear_esq = calcular_ear(landmarks, LEFT_EYE, w, h)
    ear_dir = calcular_ear(landmarks, RIGHT_EYE, w, h)
    ear_media = round((ear_esq + ear_dir) / 2.0, 3)
    mar = round(calcular_mar(landmarks, MOUTH, w, h), 3)

    olhos_fechados = ear_media < 0.20
    bocejando = mar > 0.55
    tempo_fechado = 0.0

    # Lógica de contagem do tempo de microsono
    if olhos_fechados:
        if tempo_olho_fechado_inicio is None:
            tempo_olho_fechado_inicio = time.time()
        tempo_fechado = round(time.time() - tempo_olho_fechado_inicio, 2)
    else:
        tempo_olho_fechado_inicio = None

    # Lógica de decisão dos estados
    if tempo_fechado >= 1.5:
        status = "DORMINDO"
        alerta = "CRITICO"
        som = True
    elif bocejando:
        status = "SONOLENTO"
        alerta = "ATENCAO"
        som = False
    elif olhos_fechados:
        status = "PISCANDO"
        alerta = "NORMAL"
        som = False
    else:
        status = "ACORDADO"
        alerta = "NORMAL"
        som = False

    return {
        "timestamp": timestamp_atual,
        "status_maquinista": status,
        "alerta_nivel": alerta,
        "alerta_sonoro": som,
        "metricas": {
            "rosto_detectado": True,
            "ear": ear_media,
            "mar": mar,
            "tempo_olhos_fechados_s": tempo_fechado
        }
    }

@app.route('/analise_frame', methods=['POST'])
def processar_frame():
    # Rota para receber uma imagem enviada via requisição HTTP POST (multipart/form-data)
    if 'file' not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado"}), 400
    
    file = request.files['file']
    npimg = np.frombuffer(file.read(), np.uint8)
    frame = cv2.imdecode(npimg, cv2.IMREAD_COLOR)
    
    resultado = analisar_imagem(frame)
    return jsonify(resultado)

@app.route('/teste_webcam', methods=['GET'])
def teste_webcam():
    # Rota de teste rápido usando a webcam do próprio computador
    cap = cv2.VideoCapture(0)
    ret, frame = cap.read()
    cap.release()
    
    if not ret:
        return jsonify({"erro": "Não foi possível capturar frame da webcam"}), 500
        
    resultado = analisar_imagem(frame)
    return jsonify(resultado)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)