"""
Lançador do detector_sono.py (usado pelo servidor.py).

Roda o detector_sono.py ORIGINAL, sem alterar o arquivo, mas antes
"intercepta" a função cv2.imshow: cada frame que o detector mostraria na
janela (já com os pontos dos olhos e o texto do estado) também é publicado
como vídeo MJPEG em http://127.0.0.1:<VIDEO_PORTA>/stream.

Assim o site mostra o vídeo sem abrir uma segunda conexão com o ESP32-CAM
(que só aceita um cliente de stream por vez).

Uso:  python executar_detector.py caminho/do/detector_sono.py
Variáveis de ambiente:
    VIDEO_PORTA     porta local do vídeo (padrão 8092)
    CAMERA_FONTE    índice da webcam (ex: "0") para usar no lugar do ESP32-CAM;
                    vazio = usa a URL do ESP32-CAM escrita no detector
    MOSTRAR_JANELA  "1" mantém a janela do OpenCV, "0" esconde (padrão 1)
"""

import os
import runpy
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

VIDEO_PORTA = int(os.environ.get("VIDEO_PORTA", "8092"))
MOSTRAR_JANELA = os.environ.get("MOSTRAR_JANELA", "1") == "1"

ultimo = {"jpg": None, "n": 0}
cond = threading.Condition()
_imshow_original = cv2.imshow


def imshow_interceptado(nome, frame):
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    if ok:
        with cond:
            ultimo["jpg"] = buf.tobytes()
            ultimo["n"] += 1
            cond.notify_all()
    if MOSTRAR_JANELA:
        _imshow_original(nome, frame)


cv2.imshow = imshow_interceptado


# --- Troca de fonte de vídeo (webcam do PC no lugar do ESP32-CAM) ---
# Se CAMERA_FONTE tiver um número (ex: "0"), qualquer cv2.VideoCapture("http://...")
# feito pelo detector abre a webcam desse índice. Vazio = mantém a URL original.
CAMERA_FONTE = os.environ.get("CAMERA_FONTE", "").strip()
_VideoCapture_original = cv2.VideoCapture


def videocapture_interceptado(fonte, *args, **kwargs):
    if CAMERA_FONTE.isdigit() and isinstance(fonte, str) and fonte.startswith(("http://", "https://", "rtsp://")):
        indice = int(CAMERA_FONTE)
        if os.name == "nt":  # no Windows o DirectShow abre a webcam bem mais rápido
            cap = _VideoCapture_original(indice, cv2.CAP_DSHOW)
            if cap.isOpened():
                return cap
        return _VideoCapture_original(indice)
    return _VideoCapture_original(fonte, *args, **kwargs)


cv2.VideoCapture = videocapture_interceptado


class VideoHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if not self.path.startswith("/stream"):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        visto = -1
        try:
            while True:
                with cond:
                    cond.wait_for(lambda: ultimo["n"] != visto, timeout=2)
                    jpg, visto = ultimo["jpg"], ultimo["n"]
                if jpg is None:
                    continue
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(jpg)}\r\n\r\n".encode())
                self.wfile.write(jpg)
                self.wfile.write(b"\r\n")
        except OSError:
            pass  # navegador/servidor fechou a conexão


def iniciar_servidor_video():
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", VIDEO_PORTA), VideoHandler)
    except OSError as e:
        print(f"[VIDEO] Nao foi possivel abrir a porta {VIDEO_PORTA}: {e}", flush=True)
        return
    threading.Thread(target=srv.serve_forever, daemon=True).start()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python executar_detector.py caminho/do/detector_sono.py")
        sys.exit(1)
    caminho = os.path.abspath(sys.argv[1])
    iniciar_servidor_video()
    sys.argv = [caminho]
    sys.path.insert(0, os.path.dirname(caminho))
    runpy.run_path(caminho, run_name="__main__")
