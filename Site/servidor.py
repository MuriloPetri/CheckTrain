"""
Painel de Monitoramento do Maquinista
=====================================
Liga as duas partes do projeto SEM alterar nenhum dos arquivos originais:

  1) Lesley/Lesley/detector_sono.py  -> executado aqui como subprocesso.
     A saída do terminal dele (ACORDADO / SONOLENTO / DORMINDO / sem rosto)
     é lida e interpretada em tempo real.

  2) Arduino/ParteRFID/ParteRFID.ino -> a placa (TTGO + leitor UHF) fica
     ligada no USB. As linhas "[IDENTIFICADO] Nome: ... | Cargo: ..." que ela
     manda pelo Serial (115200) são lidas aqui com pyserial.

O site (http://localhost:8080) junta as duas informações: mostra QUEM está
operando (crachá RFID) e SE ESSA PESSOA ESTÁ DORMINDO (câmera), com alerta
sonoro/visual e histórico salvo em registros.csv.

Uso:
    python servidor.py                 # normal (porta serial automática)
    python servidor.py --porta COM5    # força a porta serial do RFID
    python servidor.py --demo          # simula tudo, sem hardware
"""

import argparse
import csv
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # o site funciona mesmo sem pyserial (só sem RFID)
    serial = None
    list_ports = None

# ================= CONFIGURAÇÃO =================
PORTA_HTTP = 8080
BAUD_RFID = 115200  # mesmo valor do Serial.begin() no ParteRFID.ino

PASTA_SITE = os.path.dirname(os.path.abspath(__file__))
PASTA_PROJETO = os.path.dirname(PASTA_SITE)
CAMINHO_DETECTOR = os.path.join(PASTA_PROJETO, "Lesley", "Lesley", "detector_sono.py")
ARQUIVO_REGISTROS = os.path.join(PASTA_SITE, "registros.csv")
ARQUIVO_HTML = os.path.join(PASTA_SITE, "index.html")
LANCADOR = os.path.join(PASTA_SITE, "executar_detector.py")
PORTA_VIDEO = 8092        # porta interna onde o lançador publica o vídeo
MOSTRAR_JANELA = True     # False = esconde a janela do OpenCV (vídeo só no site)

# Fonte de vídeo do detector:
#   "webcam" -> usa a câmera do computador (índice WEBCAM_INDICE)
#   "esp32"  -> usa a URL que está dentro do detector_sono.py (ESP32-CAM)
FONTE_CAMERA = "webcam"
WEBCAM_INDICE = 0

# Lista de funcionários: lida (só leitura) do próprio ParteRFID.ino,
# usada na tela de login para o "login de teste" sem o leitor.
ARQUIVO_RFID_INO = os.path.join(PASTA_PROJETO, "Arduino", "ParteRFID", "ParteRFID.ino")
# ================================================

trava = threading.RLock()  # RLock: a sala de controle usa a mesma trava

from sala_controle import SalaControle  # noqa: E402
from telegram_bot import TelegramBot  # noqa: E402

ARQUIVO_DADOS = os.path.join(PASTA_SITE, "dados.json")
ARQUIVO_TELEGRAM = os.path.join(PASTA_SITE, "telegram.json")  # guarda o token do bot (não compartilhe)
sala = None      # criada no main()
telegram = None  # criado no main()

estado = {
    "modo_demo": False,
    "fonte_camera": "",
    "login_erro": None,      # {"epc", "hora"} quando um crachá não cadastrado tenta entrar
    "detector": {"habilitado": True, "status": "parado", "mensagem": "", "pid": None},
    "sono": {"estado": "DESCONHECIDO", "ear": None, "tempo_fechado": None, "desde": None},
    "rfid": {"status": "desconectado", "porta": None, "porta_config": "AUTO", "mensagem": ""},
    "operador": None,        # {"nome", "cargo", "epc", "desde"}
    "ultimo_cracha": None,   # última leitura, cadastrada ou não
    "alertas_por_operador": {},
    "eventos": deque(maxlen=300),
}

processo_detector = None
trocar_porta = threading.Event()


def agora_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


RE_FUNCIONARIO = re.compile(r'\{\s*"([0-9A-Fa-f]+)"\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\}')


def ler_funcionarios_ino():
    """Lê o vetor funcionarios[] do ParteRFID.ino (o arquivo não é alterado)."""
    try:
        with open(ARQUIVO_RFID_INO, encoding="utf-8", errors="replace") as f:
            texto = f.read()
    except OSError:
        return []
    return [{"epc": e.upper(), "nome": n, "cargo": c} for e, n, c in RE_FUNCIONARIO.findall(texto)]


# ---------------------------------------------------------------------------
# Registro de eventos (tela + CSV)
# ---------------------------------------------------------------------------
def registrar_evento(tipo, nivel, descricao):
    """nivel: info | ok | atencao | critico"""
    op = estado["operador"]
    evento = {
        "hora": agora_iso(),
        "tipo": tipo,
        "nivel": nivel,
        "operador": op["nome"] if op else "",
        "cargo": op["cargo"] if op else "",
        "descricao": descricao,
    }
    estado["eventos"].appendleft(evento)
    try:
        novo = not os.path.exists(ARQUIVO_REGISTROS)
        with open(ARQUIVO_REGISTROS, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            if novo:
                w.writerow(["data_hora", "tipo", "nivel", "operador", "cargo", "descricao"])
            w.writerow([evento["hora"], tipo, nivel, evento["operador"], evento["cargo"], descricao])
    except OSError:
        pass


# ---------------------------------------------------------------------------
# LÓGICA QUE LIGA AS DUAS PARTES
# ---------------------------------------------------------------------------
def ao_mudar_sono(novo, ear=None, duracao=None):
    """Chamado quando o detector de sono informa um estado."""
    with trava:
        s = estado["sono"]
        anterior = s["estado"]
        if ear is not None:
            s["ear"] = ear
        if duracao is not None:
            s["tempo_fechado"] = duracao
        if novo == "ACORDADO":
            s["tempo_fechado"] = 0.0
        if novo == anterior:
            return
        s["estado"] = novo
        s["desde"] = agora_iso()

        op = estado["operador"]
        quem = f'{op["nome"]} ({op["cargo"]})' if op else "Operador não identificado"

        if novo == "DORMINDO":
            if op:
                cont = estado["alertas_por_operador"]
                cont[op["nome"]] = cont.get(op["nome"], 0) + 1
            registrar_evento("SONO", "critico", f"{quem} DORMINDO")
            # Liga com a sala de controle: pede um motorista para retirar o maquinista
            if op and sala:
                m = sala.maquinista_por_epc(op["epc"])
                if m:
                    sala.solicitar(m["id"], "Alerta de sono", criado_por="Detector de sono (automático)")
        elif novo == "SONOLENTO":
            registrar_evento("SONO", "atencao", f"{quem} sonolento (olhos fechando)")
        elif novo == "ACORDADO" and anterior in ("DORMINDO", "SONOLENTO"):
            registrar_evento("SONO", "ok", f"{quem} voltou a ficar acordado")
        elif novo == "SEM_ROSTO" and op:
            registrar_evento("SONO", "atencao", f"Rosto de {quem} não aparece na câmera")


def ao_ler_cracha(nome, cargo, epc, rssi, origem="RFID"):
    """Chamado quando o RFID identifica um funcionário cadastrado (= login)."""
    with trava:
        estado["login_erro"] = None
        estado["ultimo_cracha"] = {"nome": nome, "cargo": cargo, "epc": epc,
                                   "rssi": rssi, "hora": agora_iso(), "cadastrado": True}
        op = estado["operador"]
        if op and op["epc"] == epc:
            return  # mesmo crachá lido de novo, nada muda
        anterior = op["nome"] if op else None
        estado["operador"] = {"nome": nome, "cargo": cargo, "epc": epc, "desde": agora_iso(),
                              "origem": origem}
        extra = " (login de teste, sem crachá)" if origem == "TESTE" else ""
        if anterior:
            registrar_evento("RFID", "info", f"{nome} ({cargo}) assumiu no lugar de {anterior}{extra}")
        else:
            registrar_evento("RFID", "info", f"{nome} ({cargo}) fez login e iniciou o turno{extra}")


def login_teste(epc):
    """Login pela tela, sem o leitor físico (para testes)."""
    for f in ler_funcionarios_ino():
        if f["epc"] == str(epc).upper():
            ao_ler_cracha(f["nome"], f["cargo"], f["epc"], None, origem="TESTE")
            return True
    return False


def ao_ler_cracha_desconhecido(epc, rssi):
    with trava:
        if not estado["operador"]:
            estado["login_erro"] = {"epc": epc, "hora": agora_iso()}
        estado["ultimo_cracha"] = {"nome": None, "cargo": None, "epc": epc,
                                   "rssi": rssi, "hora": agora_iso(), "cadastrado": False}
        registrar_evento("RFID", "atencao", f"Crachá não cadastrado: {epc}")


def encerrar_turno():
    with trava:
        op = estado["operador"]
        if op:
            registrar_evento("RFID", "info", f'{op["nome"]} encerrou o turno')
            estado["operador"] = None


# ---------------------------------------------------------------------------
# Parte 1: detector_sono.py (executado sem modificação)
# ---------------------------------------------------------------------------
RE_ANSI = re.compile(r"\x1b\[[0-9;]*m")
RE_ACORDADO = re.compile(r">>> ACORDADO <<<.*?EAR:\s*([\d.]+)")
RE_DORMINDO = re.compile(r">>> DORMINDO <<<.*?\(([\d.]+)s\)")
RE_SONOLENTO = re.compile(r"SONOLENTA.*?([\d.]+)s\)")


def set_detector(status, mensagem=None):
    with trava:
        estado["detector"]["status"] = status
        if mensagem is not None:
            estado["detector"]["mensagem"] = mensagem


def processar_linha_detector(linha):
    linha = RE_ANSI.sub("", linha).replace("\a", "").strip()
    if not linha or set(linha) <= set("=!"):
        return

    m = RE_ACORDADO.search(linha)
    if m:
        set_detector("rodando", "Analisando rosto")
        ao_mudar_sono("ACORDADO", ear=float(m.group(1)))
        return
    m = RE_DORMINDO.search(linha)
    if m:
        set_detector("rodando", "Analisando rosto")
        ao_mudar_sono("DORMINDO", duracao=float(m.group(1)))
        return
    m = RE_SONOLENTO.search(linha)
    if m:
        set_detector("rodando", "Analisando rosto")
        ao_mudar_sono("SONOLENTO", duracao=float(m.group(1)))
        return
    if linha.startswith("[JANELA]"):
        return  # tratado quando o processo termina (código 0)
    if "Aguardando rosto" in linha:
        set_detector("rodando", "Nenhum rosto na câmera")
        ao_mudar_sono("SEM_ROSTO")
        return
    if "Conectando ao ESP32-CAM" in linha:
        if FONTE_CAMERA == "webcam":
            set_detector("conectando", f"Abrindo a webcam do computador (câmera {WEBCAM_INDICE})...")
        else:
            set_detector("conectando", linha)
        return
    if "Nao foi possivel conectar" in linha and FONTE_CAMERA == "webcam":
        set_detector("erro", "Não abriu a webcam. Ela está em uso por outro programa? Tente outro WEBCAM_INDICE.")
        return
    if "Baixando modelo" in linha:
        set_detector("conectando", "Baixando modelo facial (primeira vez)...")
        return
    if "Nao foi possivel conectar" in linha:
        set_detector("erro", "Não conectou ao ESP32-CAM. Confira o IP em detector_sono.py e o Wi-Fi.")
        return
    # Qualquer outra coisa (avisos, traceback de erro) fica visível no painel
    with trava:
        estado["detector"]["mensagem"] = linha[-300:]


def thread_detector():
    global processo_detector
    while True:
        if not estado["detector"]["habilitado"]:
            time.sleep(0.5)
            continue
        if not os.path.exists(CAMINHO_DETECTOR):
            set_detector("erro", f"Arquivo não encontrado: {CAMINHO_DETECTOR}")
            time.sleep(3)
            continue

        set_detector("conectando", "Iniciando detector_sono.py...")
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8",
                   VIDEO_PORTA=str(PORTA_VIDEO), MOSTRAR_JANELA="1" if MOSTRAR_JANELA else "0",
                   CAMERA_FONTE=str(WEBCAM_INDICE) if FONTE_CAMERA == "webcam" else "",
                   VIGIAR_SERVIDOR="1")
        try:
            processo_detector = subprocess.Popen(
                [sys.executable, "-u", LANCADOR, CAMINHO_DETECTOR],
                cwd=os.path.dirname(CAMINHO_DETECTOR),
                stdin=subprocess.PIPE,  # se o servidor morrer, o detector percebe e fecha junto
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=env,
            )
        except OSError as e:
            set_detector("erro", f"Falha ao iniciar o detector: {e}")
            time.sleep(3)
            continue

        with trava:
            estado["detector"]["pid"] = processo_detector.pid
        for linha in processo_detector.stdout:
            processar_linha_detector(linha)
        codigo = processo_detector.wait()

        with trava:
            estado["detector"]["pid"] = None
            if estado["detector"]["status"] != "erro":
                estado["detector"]["status"] = "parado"
                if codigo == 0 and estado["detector"]["habilitado"]:
                    # saída normal = usuário apertou 'q' ou fechou a janela: NÃO reabrir sozinho
                    estado["detector"]["habilitado"] = False
                    estado["detector"]["mensagem"] = ("Detector fechado pela janela (tecla q ou X). "
                                                      "Clique em Reiniciar para abrir de novo.")
                elif estado["detector"]["habilitado"]:
                    estado["detector"]["mensagem"] = (
                        f"detector_sono.py encerrou (código {codigo}). Reiniciando...")
        ao_mudar_sono("DESCONHECIDO")
        time.sleep(3)


def parar_detector():
    with trava:
        estado["detector"]["habilitado"] = False
        estado["detector"]["mensagem"] = "Parado pelo painel"
    p = processo_detector
    if p and p.poll() is None:
        p.terminate()


def iniciar_detector():
    with trava:
        estado["detector"]["habilitado"] = True
        if estado["detector"]["status"] == "erro":
            estado["detector"]["status"] = "parado"
    # se já existia um rodando, mata para reiniciar limpo
    p = processo_detector
    if p and p.poll() is None:
        p.terminate()


# ---------------------------------------------------------------------------
# Parte 2: ParteRFID.ino (lido pela porta Serial)
# ---------------------------------------------------------------------------
RE_IDENT = re.compile(
    r"\[IDENTIFICADO\]\s*Nome:\s*(.*?)\s*\|\s*Cargo:\s*(.*?)\s*\|\s*RSSI:\s*(-?\d+).*?EPC:\s*([0-9A-Fa-f]+)")
RE_DESC = re.compile(r"\[NAO CADASTRADO\].*?RSSI:\s*(-?\d+).*?EPC:\s*([0-9A-Fa-f]+)")


def listar_portas():
    if not list_ports:
        return []
    return [{"porta": p.device, "descricao": p.description} for p in list_ports.comports()]


def detectar_porta():
    """Escolhe a primeira porta com cara de ESP32 (CH340, CP210x, CH9102...)."""
    portas = list_ports.comports() if list_ports else []
    chaves = ("CH340", "CH910", "CP210", "USB-SERIAL", "USB SERIAL", "UART", "SILICON LABS", "WCH")
    for p in portas:
        texto = f"{p.description} {p.manufacturer or ''}".upper()
        if any(k in texto for k in chaves):
            return p.device
    usb = [p for p in portas if p.vid is not None]  # ignora COM1 da placa-mãe etc.
    return usb[0].device if len(usb) == 1 else None


def set_rfid(status, mensagem=None, porta="__manter__"):
    with trava:
        estado["rfid"]["status"] = status
        if mensagem is not None:
            estado["rfid"]["mensagem"] = mensagem
        if porta != "__manter__":
            estado["rfid"]["porta"] = porta


def processar_linha_rfid(linha):
    m = RE_IDENT.search(linha)
    if m:
        ao_ler_cracha(m.group(1), m.group(2), m.group(4).upper(), int(m.group(3)))
        return
    m = RE_DESC.search(linha)
    if m:
        ao_ler_cracha_desconhecido(m.group(2).upper(), int(m.group(1)))
        return
    if linha:
        with trava:
            estado["rfid"]["mensagem"] = linha[-200:]


def thread_rfid():
    if serial is None:
        set_rfid("erro", "pyserial não instalado. Rode: pip install pyserial")
        return
    while True:
        trocar_porta.clear()
        config = estado["rfid"]["porta_config"]
        if config == "DESLIGADO":
            set_rfid("desconectado", "Leitura RFID desligada", None)
            trocar_porta.wait(1)
            continue
        porta = detectar_porta() if config == "AUTO" else config
        if not porta:
            set_rfid("desconectado", "Nenhuma placa encontrada no USB. Escolha a porta no painel.", None)
            trocar_porta.wait(2)
            continue
        try:
            with serial.Serial(porta, BAUD_RFID, timeout=0.5) as ser:
                set_rfid("conectado", f"Lendo {porta}", porta)
                while not trocar_porta.is_set():
                    bruto = ser.readline()
                    if bruto:
                        processar_linha_rfid(bruto.decode("utf-8", errors="replace").strip())
        except (serial.SerialException, OSError) as e:
            set_rfid("erro", f"{porta}: {e}", porta)
            trocar_porta.wait(3)


# ---------------------------------------------------------------------------
# Modo demonstração (sem hardware)
# ---------------------------------------------------------------------------
def thread_demo():
    set_detector("rodando", "MODO DEMO - dados simulados")
    set_rfid("conectado", "MODO DEMO - dados simulados", "DEMO")
    funcionarios = [("Deivid", "Maquinista", "E2801160600002187F218050"),
                    ("Enzo", "Supervisor", "E2801160600002187F2144F1")]
    roteiro = ["ACORDADO", "ACORDADO", "SONOLENTO", "DORMINDO", "DORMINDO",
               "ACORDADO", "ACORDADO", "SEM_ROSTO", "ACORDADO"]
    time.sleep(1)
    ao_ler_cracha(*funcionarios[0], -42)
    i = 0
    while True:
        st = roteiro[i % len(roteiro)]
        if st == "ACORDADO":
            ao_mudar_sono(st, ear=round(random.uniform(0.26, 0.33), 2))
        elif st in ("SONOLENTO", "DORMINDO"):
            ao_mudar_sono(st, ear=round(random.uniform(0.12, 0.2), 2),
                          duracao=round(random.uniform(0.6, 1.2) if st == "SONOLENTO" else random.uniform(1.9, 4), 1))
        else:
            ao_mudar_sono(st)
        i += 1
        if i % 14 == 0:
            ao_ler_cracha(*funcionarios[(i // 14) % 2], -40)
        if i % 23 == 0:
            ao_ler_cracha_desconhecido("300833B2DDD90140000000FF", -44)
        time.sleep(2.5)


# ---------------------------------------------------------------------------
# Servidor HTTP (site + API)
# ---------------------------------------------------------------------------
def snapshot():
    with trava:
        dados = {k: v for k, v in estado.items() if k != "eventos"}
        dados = json.loads(json.dumps(dados))
        dados["eventos"] = list(estado["eventos"])[:100]
        dados["hora_servidor"] = agora_iso()
        if sala:
            dados["sala"] = sala.snapshot(estado["operador"], estado["alertas_por_operador"])
        if telegram:
            dados["telegram"] = telegram.info()
    return dados


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # não poluir o terminal com cada requisição

    def _json(self, obj, codigo=200):
        corpo = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def _corpo_json(self):
        tam = int(self.headers.get("Content-Length") or 0)
        if not tam:
            return {}
        try:
            return json.loads(self.rfile.read(tam).decode("utf-8"))
        except ValueError:
            return {}

    def do_GET(self):
        rota = self.path.split("?")[0]
        if rota in ("/", "/index.html"):
            with open(ARQUIVO_HTML, "rb") as f:
                corpo = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
        elif rota == "/api/estado":
            self._json(snapshot())
        elif rota == "/api/portas":
            self._json(listar_portas())
        elif rota == "/api/funcionarios":
            self._json(ler_funcionarios_ino())
        elif rota == "/video":
            self._repassar_video()
        else:
            self._json({"erro": "rota não encontrada"}, 404)

    def _repassar_video(self):
        """Repassa ao navegador o MJPEG publicado pelo executar_detector.py."""
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{PORTA_VIDEO}/stream", timeout=5)
        except OSError:
            return self._json({"erro": "vídeo indisponível"}, 503)
        try:
            self.send_response(200)
            self.send_header("Content-Type", resp.headers.get("Content-Type", "multipart/x-mixed-replace"))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            while True:
                pedaco = resp.read1(65536)
                if not pedaco:
                    break
                self.wfile.write(pedaco)
        except OSError:
            pass  # navegador fechou a aba, detector reiniciou, etc.
        finally:
            resp.close()

    def _rotas_sala(self, rota, corpo):
        erro = None
        if rota == "/api/sala/solicitar":
            _, erro = sala.solicitar(corpo.get("maquinista_id"), corpo.get("motivo") or "Outro",
                                     criado_por=corpo.get("por") or "Agente da sala (manual)")
        elif rota == "/api/sala/alocar":
            _, erro = sala.alocar(corpo.get("solicitacao_id"), corpo.get("motorista_id"),
                                  por="Agente da sala (manual)")
        elif rota == "/api/sala/cancelar_solicitacao":
            if not sala.cancelar_solicitacao(corpo.get("id"), por="Agente da sala (site)"):
                erro = "Solicitação não encontrada"
        elif rota == "/api/sala/cancelar_corrida":
            if not sala.cancelar_corrida(corpo.get("id")):
                erro = "Corrida não encontrada"
        elif rota == "/api/sala/auto":
            sala.set_auto(corpo.get("ativo"))
        else:
            return self._json({"erro": "rota não encontrada"}, 404)
        if erro:
            return self._json({"ok": False, "erro": erro}, 400)
        return self._json({"ok": True})

    def do_POST(self):
        rota = self.path.split("?")[0]
        if estado["modo_demo"] and rota.startswith(("/api/detector", "/api/rfid")):
            return self._json({"ok": False, "erro": "Indisponível no modo demo"}, 400)
        if rota == "/api/detector/iniciar":
            iniciar_detector()
        elif rota == "/api/detector/parar":
            parar_detector()
        elif rota == "/api/rfid/porta":
            porta = str(self._corpo_json().get("porta") or "AUTO")
            with trava:
                estado["rfid"]["porta_config"] = porta
            trocar_porta.set()
        elif rota in ("/api/operador/encerrar", "/api/logout"):
            encerrar_turno()
        elif rota == "/api/login/teste":
            if not login_teste(self._corpo_json().get("epc", "")):
                return self._json({"ok": False, "erro": "Funcionário não encontrado"}, 400)
        elif rota == "/api/eventos/limpar":
            with trava:
                estado["eventos"].clear()
        elif rota.startswith("/api/sala/"):
            return self._rotas_sala(rota, self._corpo_json())
        elif rota == "/api/telegram/config":
            corpo = self._corpo_json()
            telegram.configurar(token=corpo.get("token"), senha=corpo.get("senha"))
            if telegram.configurado and sala.auto:
                sala.set_auto(False)  # quem decide agora é quem está no Telegram
        elif rota == "/api/telegram/teste":
            erro = telegram.enviar_teste()
            if erro:
                return self._json({"ok": False, "erro": erro}, 400)
        else:
            return self._json({"erro": "rota não encontrada"}, 404)
        self._json({"ok": True})


def main():
    ap = argparse.ArgumentParser(description="Painel que liga o detector de sono ao leitor RFID")
    ap.add_argument("--porta", help="porta serial do RFID (ex: COM5). Padrão: automática")
    ap.add_argument("--http", type=int, default=PORTA_HTTP, help="porta do site (padrão 8080)")
    ap.add_argument("--demo", action="store_true", help="simula câmera e RFID, sem hardware")
    ap.add_argument("--sem-detector", action="store_true", help="não inicia o detector_sono.py")
    ap.add_argument("--sem-janela", action="store_true", help="esconde a janela do OpenCV (vídeo só no site)")
    ap.add_argument("--detector", help="caminho de outro detector_sono.py (opcional)")
    ap.add_argument("--webcam", type=int, nargs="?", const=0, default=None,
                    help="usa a webcam do PC (opcional: índice, ex: --webcam 1)")
    ap.add_argument("--esp32", action="store_true", help="usa o ESP32-CAM (URL do detector_sono.py)")
    ap.add_argument("--telegram-token", help="token do bot do Telegram (fica salvo em telegram.json)")
    args = ap.parse_args()

    global MOSTRAR_JANELA, CAMINHO_DETECTOR, FONTE_CAMERA, WEBCAM_INDICE
    if args.esp32:
        FONTE_CAMERA = "esp32"
    if args.webcam is not None:
        FONTE_CAMERA, WEBCAM_INDICE = "webcam", args.webcam
    estado["fonte_camera"] = (f"Webcam do computador (câmera {WEBCAM_INDICE})"
                              if FONTE_CAMERA == "webcam" else "ESP32-CAM")
    if args.sem_janela:
        MOSTRAR_JANELA = False
    if args.detector:
        CAMINHO_DETECTOR = os.path.abspath(args.detector)
    if args.porta:
        estado["rfid"]["porta_config"] = args.porta

    global sala
    sala = SalaControle(ARQUIVO_DADOS, trava,
                        lambda nivel, desc: registrar_evento("SALA", nivel, desc))

    def thread_sala():
        while True:
            sala.tick()
            time.sleep(0.5)

    threading.Thread(target=thread_sala, daemon=True).start()

    global telegram
    def log_telegram(nivel, desc):
        with trava:
            registrar_evento("SALA", nivel, desc)

    telegram = TelegramBot(ARQUIVO_TELEGRAM, sala, log_telegram)
    if args.telegram_token:
        telegram.configurar(token=args.telegram_token)
    if telegram.configurado:
        # com o bot ativo, a alocação é feita pelo Telegram (agente automático começa desligado)
        sala.auto = False

    if args.demo:
        estado["modo_demo"] = True
        threading.Thread(target=thread_demo, daemon=True).start()
    else:
        if args.sem_detector:
            estado["detector"]["habilitado"] = False
        threading.Thread(target=thread_detector, daemon=True).start()
        threading.Thread(target=thread_rfid, daemon=True).start()

    servidor = ThreadingHTTPServer(("0.0.0.0", args.http), Handler)
    print("=" * 56)
    print("  Painel do Maquinista rodando!")
    print(f"  Abra no navegador:  http://localhost:{args.http}")
    print("  (outros aparelhos na mesma rede: use o IP deste PC)")
    print("  Ctrl+C para encerrar")
    print("=" * 56)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        p = processo_detector
        if p and p.poll() is None:
            p.terminate()
        servidor.server_close()


if __name__ == "__main__":
    main()
