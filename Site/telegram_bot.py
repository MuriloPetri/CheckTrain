"""
Integração com bot do Telegram
==============================
- Toda solicitação de motorista criada na sala de controle vira uma mensagem
  no Telegram, com um botão para cada motorista disponível.
- Tocar no botão de um motorista ALOCA a corrida (inicia o deslocamento do
  maquinista até a estação/destino).
- As etapas da corrida (chegou, embarcou, concluída) são respondidas na mesma
  conversa.

Configuração (arquivo telegram.json, criado automaticamente):
    {"token": "123456:ABC...", "senha": "", "chats": {}}
  - token: gerado pelo @BotFather no Telegram
  - senha: opcional; se preenchida, o chat precisa mandar "/start <senha>"
  - chats: preenchido sozinho quando alguém manda /start para o bot

Usa só a biblioteca padrão do Python (urllib), sem dependências extras.
"""

import html
import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request

API = "https://api.telegram.org/bot{token}/{metodo}"
ROT_ETAPA = {"A_CAMINHO": "A caminho", "EMBARQUE": "Embarque", "EM_VIAGEM": "Em viagem",
             "CONCLUIDA": "Concluída", "CANCELADA": "Cancelada"}

e = html.escape  # para usar parse_mode=HTML com segurança


class TelegramBot:
    def __init__(self, arquivo_config, sala, log):
        """
        sala -> instância de SalaControle (recebe os eventos e executa as alocações)
        log  -> função log(nivel, descricao) para o histórico do painel
        """
        self.arquivo = arquivo_config
        self.sala = sala
        self.log = log
        self.cfg = self._carregar()
        self.status = "desligado"
        self.mensagem = "Sem token configurado"
        self.bot_nome = None
        self.offset = 0
        self.fila = queue.Queue()
        self.msgs_sol = {}       # id da solicitação -> [(chat_id, message_id)]
        self._token_versao = 0
        self._lock = threading.Lock()
        sala.ouvintes.append(self._ao_evento_sala)
        threading.Thread(target=self._loop_updates, daemon=True).start()
        threading.Thread(target=self._loop_envio, daemon=True).start()

    # ---------------- configuração ----------------
    def _carregar(self):
        cfg = {"token": "", "senha": "", "chats": {}}
        if os.path.exists(self.arquivo):
            try:
                with open(self.arquivo, encoding="utf-8") as f:
                    cfg.update(json.load(f))
            except (OSError, ValueError):
                pass
        else:
            self._salvar(cfg)
        cfg["chats"] = {str(k): v for k, v in (cfg.get("chats") or {}).items()}
        return cfg

    def _salvar(self, cfg=None):
        try:
            with open(self.arquivo, "w", encoding="utf-8") as f:
                json.dump(cfg or self.cfg, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    @property
    def configurado(self):
        return bool(self.cfg.get("token"))

    def configurar(self, token=None, senha=None):
        with self._lock:
            if token is not None and token.strip() != self.cfg.get("token"):
                self.cfg["token"] = token.strip()
                self.bot_nome, self.offset = None, 0
                self._token_versao += 1
            if senha is not None:
                self.cfg["senha"] = senha.strip()
            self._salvar()

    def info(self):
        return {"configurado": self.configurado, "status": self.status, "mensagem": self.mensagem,
                "bot": self.bot_nome, "chats": list(self.cfg["chats"].values()),
                "senha": bool(self.cfg.get("senha"))}

    # ---------------- API do Telegram ----------------
    def _api(self, metodo, http_timeout=15, **params):
        url = API.format(token=self.cfg["token"], metodo=metodo)
        req = urllib.request.Request(url, json.dumps(params).encode("utf-8"),
                                     {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=http_timeout) as r:
                resp = json.load(r)
        except urllib.error.HTTPError as err:
            try:
                desc = json.load(err).get("description", str(err))
            except ValueError:
                desc = str(err)
            raise RuntimeError(desc) from None
        if not resp.get("ok"):
            raise RuntimeError(resp.get("description", "erro desconhecido"))
        return resp["result"]

    def _enviar(self, chat_id, texto, teclado=None, responder_a=None):
        params = {"chat_id": chat_id, "text": texto, "parse_mode": "HTML",
                  "disable_web_page_preview": True}
        if teclado:
            params["reply_markup"] = {"inline_keyboard": teclado}
        if responder_a:
            params["reply_parameters"] = {"message_id": responder_a, "allow_sending_without_reply": True}
        return self._api("sendMessage", **params)

    def _editar(self, chat_id, msg_id, texto):
        try:
            self._api("editMessageText", chat_id=chat_id, message_id=msg_id, text=texto,
                      parse_mode="HTML", reply_markup={"inline_keyboard": []})
        except RuntimeError as err:
            if "not modified" not in str(err):
                raise

    def enviar_teste(self):
        if not self.configurado:
            return "Configure o token primeiro"
        if not self.cfg["chats"]:
            return "Nenhum chat cadastrado: mande /start para o bot no Telegram"
        self.fila.put(("teste", {}))
        return None

    # ---------------- recebendo mensagens/botões ----------------
    def _loop_updates(self):
        while True:
            if not self.configurado:
                self.status, self.mensagem = "desligado", "Sem token configurado"
                time.sleep(1)
                continue
            versao = self._token_versao
            try:
                if not self.bot_nome:
                    self.status, self.mensagem = "conectando", "Conectando ao Telegram..."
                    eu = self._api("getMe")
                    self.bot_nome = "@" + eu.get("username", "?")
                    n = len(self.cfg["chats"])
                    self.status = "conectado"
                    self.mensagem = (f"{self.bot_nome} · {n} chat(s) cadastrado(s)" if n else
                                     f"{self.bot_nome} · mande /start para o bot no Telegram")
                # long polling: o Telegram segura a conexão até 25 s esperando novidades
                atualizacoes = self._api("getUpdates", http_timeout=35, timeout=25, offset=self.offset,
                                         limit=50, allowed_updates=["message", "callback_query"])
                for up in atualizacoes:
                    self.offset = up["update_id"] + 1
                    if versao != self._token_versao:
                        break
                    try:
                        if "callback_query" in up:
                            self._ao_botao(up["callback_query"])
                        elif "message" in up:
                            self._ao_mensagem(up["message"])
                    except Exception as err:  # não deixa um update ruim derrubar o bot
                        print(f"[TELEGRAM] erro tratando update: {err}")
            except Exception as err:
                self.status, self.mensagem = "erro", f"Telegram: {err}"
                self.bot_nome = None
                time.sleep(5)

    def _cadastrado(self, chat_id):
        return str(chat_id) in self.cfg["chats"]

    def _ao_mensagem(self, msg):
        chat = msg["chat"]
        chat_id = chat["id"]
        texto = (msg.get("text") or "").strip()
        comando = texto.split()[0].split("@")[0].lower() if texto else ""
        nome = chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")])) or str(chat_id)

        if comando == "/start":
            senha = self.cfg.get("senha")
            arg = texto.split(maxsplit=1)[1].strip() if len(texto.split()) > 1 else ""
            if senha and arg != senha:
                self._enviar(chat_id, "🔒 Este bot é protegido. Envie <code>/start SENHA</code>.")
                return
            novo = not self._cadastrado(chat_id)
            self.cfg["chats"][str(chat_id)] = nome
            self._salvar()
            self.mensagem = f"{self.bot_nome} · {len(self.cfg['chats'])} chat(s) cadastrado(s)"
            if novo:
                self.log("info", f"Telegram: chat \"{nome}\" cadastrado para receber solicitações")
            self._enviar(chat_id,
                         "🚆 <b>Sala de controle — Painel do Maquinista</b>\n\n"
                         "Este chat vai receber as <b>solicitações de motorista</b> para buscar maquinistas. "
                         "Toque no nome de um motorista para iniciar o deslocamento.\n\n"
                         "Comandos:\n/status — solicitações e corridas\n/motoristas — motoristas\n"
                         "/sair — parar de receber avisos")
            return

        if not self._cadastrado(chat_id):
            self._enviar(chat_id, "Envie /start para começar a receber as solicitações.")
            return

        if comando == "/sair":
            self.cfg["chats"].pop(str(chat_id), None)
            self._salvar()
            self.log("info", f"Telegram: chat \"{nome}\" saiu")
            self._enviar(chat_id, "👋 Você não vai mais receber solicitações. /start para voltar.")
        elif comando == "/status":
            self._enviar(chat_id, self._texto_status())
        elif comando == "/motoristas":
            self._enviar(chat_id, self._texto_motoristas())
        else:
            self._enviar(chat_id, "Comandos: /status, /motoristas, /sair")

    def _ao_botao(self, cb):
        dados = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id")
        usuario = cb.get("from", {})
        quem = " ".join(filter(None, [usuario.get("first_name"), usuario.get("last_name")])) or "usuário"
        resposta = ""
        if not self._cadastrado(chat_id):
            resposta = "Este chat não está cadastrado (/start)."
        else:
            partes = dados.split("|")
            if partes[0] == "a" and len(partes) == 3:
                _, erro = self.sala.alocar(partes[1], partes[2], por=f"Telegram ({quem})")
                resposta = erro or "✅ Motorista alocado! Deslocamento iniciado."
            elif partes[0] == "x" and len(partes) == 2:
                ok = self.sala.cancelar_solicitacao(partes[1], por=f"Telegram ({quem})")
                resposta = "Solicitação cancelada." if ok else "Essa solicitação não está mais pendente."
            else:
                resposta = "Ação desconhecida."
        try:
            self._api("answerCallbackQuery", callback_query_id=cb["id"], text=resposta[:190],
                      show_alert=resposta.startswith(("Este", "Ação")) or "não" in resposta)
        except RuntimeError:
            pass

    # ---------------- textos ----------------
    def _texto_solicitacao(self, s):
        alta = s.get("prioridade") == "alta"
        cab = "🚨 <b>SOLICITAÇÃO URGENTE</b>" if alta else "🚆 <b>Nova solicitação de motorista</b>"
        return (f"{cab} · <code>{e(s['id'])}</code>\n\n"
                f"👷 <b>{e(s['maquinista'])}</b> ({e(s['cargo'])})\n"
                f"📋 Motivo: {e(s['motivo'])}\n"
                f"📍 Buscar em: {e(s['origem'])}\n"
                f"🏁 Levar para: {e(s['destino'])}\n"
                f"🕒 {e(s['hora'])} · por {e(s['criado_por'])}")

    def _teclado(self, s, livres):
        linhas = [[{"text": f"🚗 {d['nome']} · {d['veiculo']} (~{d['distancia_min']} min)",
                    "callback_data": f"a|{s['id']}|{d['id']}"}] for d in livres[:8]]
        linhas.append([{"text": "❌ Cancelar solicitação", "callback_data": f"x|{s['id']}"}])
        return linhas

    def _texto_status(self):
        snap = self.sala.snapshot()
        pend = snap["solicitacoes"]
        ativas = [c for c in snap["corridas"] if c["etapa"] not in ("CONCLUIDA", "CANCELADA")]
        linhas = ["📊 <b>Status da sala de controle</b>", ""]
        linhas.append(f"<b>Pendentes ({len(pend)})</b>")
        linhas += [f"• <code>{e(s['id'])}</code> {e(s['maquinista'])} — {e(s['motivo'])}" for s in pend] or ["• nenhuma"]
        linhas.append("")
        linhas.append(f"<b>Em andamento ({len(ativas)})</b>")
        linhas += [f"• {e(c['motorista'])} → {e(c['maquinista'])} — {ROT_ETAPA.get(c['etapa'], c['etapa'])}"
                   for c in ativas] or ["• nenhuma"]
        return "\n".join(linhas)

    def _texto_motoristas(self):
        snap = self.sala.snapshot()
        linhas = ["🚗 <b>Motoristas</b>", ""]
        for d in snap["motoristas"]:
            st = "🟢 disponível" if d["status"] == "DISPONIVEL" else "🟡 em rota"
            linhas.append(f"• <b>{e(d['nome'])}</b> — {e(d['veiculo'])} {e(d['placa'])} · {st} · ~{d['distancia_min']} min")
        return "\n".join(linhas)

    # ---------------- eventos vindos da sala de controle ----------------
    def _ao_evento_sala(self, tipo, dados):
        # chamado com a trava da sala; só enfileira (o envio é feito em outra thread)
        if self.configurado and self.cfg["chats"]:
            self.fila.put((tipo, json.loads(json.dumps(dados))))

    def _loop_envio(self):
        while True:
            tipo, d = self.fila.get()
            try:
                self._processar(tipo, d)
            except Exception as err:
                self.status, self.mensagem = "erro", f"Falha ao enviar: {err}"
                print(f"[TELEGRAM] falha ao enviar ({tipo}): {err}")

    def _responder_sol(self, sid, texto):
        for chat_id, msg_id in self.msgs_sol.get(sid, []):
            self._enviar(chat_id, texto, responder_a=msg_id)

    def _processar(self, tipo, d):
        chats = list(self.cfg["chats"].keys())
        if tipo == "teste":
            for chat_id in chats:
                self._enviar(chat_id, "✅ Teste do Painel do Maquinista: o bot está funcionando!")
        elif tipo == "solicitacao":
            s = d["solicitacao"]
            texto = self._texto_solicitacao(s)
            teclado = self._teclado(s, d["livres"])
            if not d["livres"]:
                texto += "\n\n⚠️ <i>Nenhum motorista livre agora.</i>"
            for chat_id in chats:
                m = self._enviar(chat_id, texto, teclado)
                self.msgs_sol.setdefault(s["id"], []).append((chat_id, m["message_id"]))
        elif tipo == "alocada":
            s, c = d["solicitacao"], d["corrida"]
            texto = (self._texto_solicitacao(s) +
                     f"\n\n✅ <b>Motorista alocado:</b> {e(c['motorista'])} — {e(c['veiculo'])} "
                     f"<code>{e(c['placa'])}</code>\n⏱ Chegada em ~{c['eta_min']} min · por {e(c['alocado_por'])}"
                     f"\n🧾 Corrida <code>{e(c['id'])}</code>")
            for chat_id, msg_id in self.msgs_sol.get(s["id"], []):
                self._editar(chat_id, msg_id, texto)
        elif tipo == "etapa":
            c = d["corrida"]
            sid = d["solicitacao_id"]
            msg = {
                "EMBARQUE": f"📍 <b>{e(c['motorista'])}</b> chegou em {e(c['origem'])} para buscar "
                            f"<b>{e(c['maquinista'])}</b>.",
                "EM_VIAGEM": f"🚗 <b>{e(c['maquinista'])}</b> embarcou. Indo para {e(c['destino'])} "
                             f"(~{c['viagem_min']} min).",
                "CONCLUIDA": f"🏁 <b>Corrida {e(c['id'])} concluída:</b> {e(c['maquinista'])} chegou em "
                             f"{e(c['destino'])}. {e(c['motorista'])} está livre de novo.",
            }.get(c["etapa"])
            if msg:
                self._responder_sol(sid, msg)
            if c["etapa"] == "CONCLUIDA":
                self.msgs_sol.pop(sid, None)
        elif tipo == "solicitacao_cancelada":
            s = d["solicitacao"]
            for chat_id, msg_id in self.msgs_sol.pop(s["id"], []):
                self._editar(chat_id, msg_id, self._texto_solicitacao(s) +
                             f"\n\n❌ <b>Cancelada</b> por {e(d.get('por', ''))}")
        elif tipo == "corrida_cancelada":
            self._responder_sol(d["solicitacao_id"], f"❌ Corrida <code>{e(d['corrida']['id'])}</code> cancelada.")
            self.msgs_sol.pop(d["solicitacao_id"], None)
