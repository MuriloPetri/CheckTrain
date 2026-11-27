"""
Medição dos parâmetros de conexão para a detecção de sonolência
=================================================================
Responde: qual o tamanho de cada imagem, quantas imagens por segundo são
necessárias e qual a velocidade mínima de conexão para o detector funcionar.

Modos:
  python medir_conexao.py --webcam                      # mede com a webcam do PC
  python medir_conexao.py --url http://IP_DO_ESP/stream  # mede o stream real do ESP32-CAM

Nada é salvo em disco além do relatório (opcional: --saida resultado.json).
Nenhuma imagem é gravada.

Usa o mesmo modelo (face_landmarker.task), os mesmos pontos dos olhos e o mesmo
cálculo de EAR do detector_sono.py.
"""

import argparse
import json
import os
import statistics as st
import time
import urllib.request

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

PASTA = os.path.dirname(os.path.abspath(__file__))
MODELO = os.path.join(os.path.dirname(PASTA), "Lesley", "Lesley", "face_landmarker.task")

# Mesmos parâmetros do detector_sono.py
OLHO_ESQ = [362, 385, 387, 263, 373, 380]
OLHO_DIR = [33, 160, 158, 133, 153, 144]
LIMIAR_OLHO_FECHADO = 0.22
TEMPO_SONOLENTO = 0.5
TEMPO_DORMINDO = 1.8

RESOLUCOES = [("QQVGA", 160, 120), ("QVGA", 320, 240), ("VGA", 640, 480), ("HD", 1280, 720)]
QUALIDADES = [50, 75, 90]          # qualidade JPEG do OpenCV (0-100, maior = melhor)
FPS_ALVO = [5, 10, 15, 20, 30]
OVERHEAD = 1.10                    # ~10% de cabeçalhos HTTP/TCP/IP/Wi-Fi


def criar_detector():
    with open(MODELO, "rb") as f:
        base = mp_python.BaseOptions(model_asset_buffer=f.read())
    opcoes = vision.FaceLandmarkerOptions(base_options=base, num_faces=1,
                                          min_face_detection_confidence=0.5,
                                          min_face_presence_confidence=0.5,
                                          min_tracking_confidence=0.5)
    return vision.FaceLandmarker.create_from_options(opcoes)


def ear(landmarks, idx, w, h):
    p = [np.array([landmarks[i].x * w, landmarks[i].y * h]) for i in idx]
    v1, v2, hz = np.linalg.norm(p[1] - p[5]), np.linalg.norm(p[2] - p[4]), np.linalg.norm(p[0] - p[3])
    return (v1 + v2) / (2.0 * hz) if hz > 0 else 0.0


def analisar(detector, frame_bgr):
    """Retorna (rosto_detectado, ear_medio, tempo_ms, largura_olho_px)."""
    h, w = frame_bgr.shape[:2]
    rgb = np.ascontiguousarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
    t0 = time.perf_counter()
    res = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    ms = (time.perf_counter() - t0) * 1000
    if not res.face_landmarks:
        return False, None, ms, None
    lm = res.face_landmarks[0]
    e = (ear(lm, OLHO_ESQ, w, h) + ear(lm, OLHO_DIR, w, h)) / 2
    olho_px = abs(lm[263].x - lm[362].x) * w
    return True, e, ms, olho_px


def mbps(kb_por_frame, fps):
    return kb_por_frame * 1024 * 8 * fps * OVERHEAD / 1_000_000


def resumo(valores):
    if not valores:
        return {"media": None, "min": None, "max": None}
    return {"media": round(st.mean(valores), 2), "min": round(min(valores), 2), "max": round(max(valores), 2)}


# ---------------------------------------------------------------------------
def medir_webcam(indice, segundos, intervalo):
    cap = cv2.VideoCapture(indice, cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(indice)
    if not cap.isOpened():
        cap = cv2.VideoCapture(indice)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    if not cap.isOpened():
        raise SystemExit("Não abriu a webcam.")
    for _ in range(10):  # deixa a câmera ajustar exposição
        cap.read()
    ok, f = cap.read()
    if not ok:
        raise SystemExit("A webcam não retornou imagem.")
    alt, larg = f.shape[:2]
    print(f"Webcam: {larg}x{alt}. Fique de frente para a câmera por {segundos}s "
          "(pisque normalmente e feche os olhos por ~2 s em algum momento)...\n")

    detector = criar_detector()
    configs = [(n, w, h, q) for n, w, h in RESOLUCOES if w <= larg and h <= alt for q in QUALIDADES]
    dados = {c: {"kb": [], "det": [], "ms": [], "ear_err": [], "olho": []} for c in configs}
    fps_cam, n_amostras, t_fim, prox = 0, 0, time.time() + segundos, 0
    t_ini, quadros = time.time(), 0
    while time.time() < t_fim:
        ok, frame = cap.read()
        if not ok:
            continue
        quadros += 1
        if time.time() < prox:
            continue
        prox = time.time() + intervalo
        n_amostras += 1
        # referência: imagem original sem compressão
        _, ear_ref, _, _ = analisar(detector, frame)
        for c in configs:
            n, w, h, q = c
            img = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
            dados[c]["kb"].append(len(buf) / 1024)
            dec = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            achou, e, ms, olho = analisar(detector, dec)
            dados[c]["det"].append(1 if achou else 0)
            dados[c]["ms"].append(ms)
            if achou:
                dados[c]["olho"].append(olho)
                if ear_ref is not None:
                    dados[c]["ear_err"].append(abs(e - ear_ref))
        print(f"\r  amostras: {n_amostras}", end="", flush=True)
    fps_cam = quadros / (time.time() - t_ini)
    cap.release()
    print("\n")

    linhas = []
    for c in configs:
        n, w, h, q = c
        d = dados[c]
        kb = st.mean(d["kb"])
        linhas.append({
            "resolucao": f"{n} {w}x{h}", "qualidade_jpeg": q,
            "kb_por_imagem": round(kb, 1),
            "deteccao_rosto_pct": round(100 * st.mean(d["det"]), 0),
            "erro_ear_medio": round(st.mean(d["ear_err"]), 3) if d["ear_err"] else None,
            "largura_olho_px": round(st.mean(d["olho"]), 0) if d["olho"] else None,
            "tempo_processamento_ms": round(st.mean(d["ms"]), 1),
            "mbps_por_fps": {f: round(mbps(kb, f), 2) for f in FPS_ALVO},
        })
    return {"fonte": f"webcam {larg}x{alt}", "fps_camera": round(fps_cam, 1),
            "amostras": n_amostras, "configuracoes": linhas}


# ---------------------------------------------------------------------------
def medir_url(url, segundos):
    print(f"Conectando em {url} por {segundos}s...\n")
    resp = urllib.request.urlopen(url, timeout=10)
    detector = criar_detector()
    buf = b""
    tamanhos, tempos_chegada, det, ms_l, ears, olhos = [], [], [], [], [], []
    bytes_total, t_ini = 0, time.time()
    resolucao = None
    while time.time() - t_ini < segundos:
        pedaco = resp.read1(65536) if hasattr(resp, "read1") else resp.read(4096)
        if not pedaco:
            break
        bytes_total += len(pedaco)
        buf += pedaco
        while True:
            a = buf.find(b"\xff\xd8")
            b = buf.find(b"\xff\xd9", a + 2)
            if a < 0 or b < 0:
                break
            jpg, buf = buf[a:b + 2], buf[b + 2:]
            tamanhos.append(len(jpg) / 1024)
            tempos_chegada.append(time.time())
            img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue
            resolucao = f"{img.shape[1]}x{img.shape[0]}"
            achou, e, ms, olho = analisar(detector, img)
            det.append(1 if achou else 0)
            ms_l.append(ms)
            if achou:
                ears.append(e)
                olhos.append(olho)
        print(f"\r  imagens: {len(tamanhos)}", end="", flush=True)
    dur = time.time() - t_ini
    resp.close()
    print("\n")
    intervalos = [(b - a) * 1000 for a, b in zip(tempos_chegada, tempos_chegada[1:])]
    kb = st.mean(tamanhos) if tamanhos else 0
    return {
        "fonte": url, "resolucao": resolucao, "duracao_s": round(dur, 1),
        "imagens": len(tamanhos), "fps_real": round(len(tamanhos) / dur, 1) if dur else 0,
        "kb_por_imagem": resumo(tamanhos),
        "taxa_real_mbps": round(bytes_total * 8 / dur / 1_000_000, 2) if dur else 0,
        "intervalo_entre_imagens_ms": resumo(intervalos),
        "deteccao_rosto_pct": round(100 * st.mean(det), 0) if det else 0,
        "largura_olho_px": round(st.mean(olhos), 0) if olhos else None,
        "tempo_processamento_ms": resumo(ms_l),
        "mbps_por_fps": {f: round(mbps(kb, f), 2) for f in FPS_ALVO},
    }


# ---------------------------------------------------------------------------
def imprimir_webcam(r):
    print(f"Fonte: {r['fonte']} · câmera entrega {r['fps_camera']} fps · {r['amostras']} amostras\n")
    cab = f"{'Resolução':<18}{'JPEG':>5}{'KB/img':>8}{'Rosto':>7}{'Erro EAR':>9}{'Olho px':>8}{'Proc ms':>8}  " + \
          "  ".join(f"{f}fps" for f in FPS_ALVO) + "  (Mbps)"
    print(cab)
    print("-" * len(cab))
    for l in r["configuracoes"]:
        erro = f"{l['erro_ear_medio']:.3f}" if l["erro_ear_medio"] is not None else "  —"
        olho = f"{l['largura_olho_px']:.0f}" if l["largura_olho_px"] else "—"
        print(f"{l['resolucao']:<18}{l['qualidade_jpeg']:>5}{l['kb_por_imagem']:>8.1f}"
              f"{l['deteccao_rosto_pct']:>6.0f}%{erro:>9}{olho:>8}{l['tempo_processamento_ms']:>8.1f}  " +
              "  ".join(f"{l['mbps_por_fps'][f]:>4.2f}" for f in FPS_ALVO))


def imprimir_url(r):
    for k, v in r.items():
        print(f"{k:>28}: {v}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--webcam", type=int, nargs="?", const=0, help="índice da webcam (padrão 0)")
    g.add_argument("--url", help="URL do stream MJPEG do ESP32-CAM")
    ap.add_argument("--segundos", type=int, default=20)
    ap.add_argument("--intervalo", type=float, default=0.5, help="segundos entre amostras (modo webcam)")
    ap.add_argument("--saida", help="salva o resultado em JSON")
    a = ap.parse_args()

    if a.url:
        r = medir_url(a.url, a.segundos)
        imprimir_url(r)
    else:
        r = medir_webcam(a.webcam, a.segundos, a.intervalo)
        imprimir_webcam(r)
    print(f"\nLimiar EAR: {LIMIAR_OLHO_FECHADO} · sonolento ≥ {TEMPO_SONOLENTO}s · dormindo ≥ {TEMPO_DORMINDO}s"
          f" · Mbps inclui ~{int((OVERHEAD - 1) * 100)}% de overhead de rede")
    if a.saida:
        with open(a.saida, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)
        print(f"Resultado salvo em {a.saida}")


if __name__ == "__main__":
    main()
