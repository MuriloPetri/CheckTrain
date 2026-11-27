"""
Sala de controle (simulação)
============================
- Cadastro de maquinistas (perfil + escala de trabalho) e motoristas, em dados.json
- Solicitações de motorista para buscar/levar um maquinista
- Alocação de motorista (manual pelo site ou pelo "agente da sala" automático)
- Corridas simuladas: A CAMINHO -> EMBARQUE -> EM VIAGEM -> CONCLUÍDA

O tempo das corridas é acelerado: SEG_POR_MIN segundos reais = 1 minuto simulado.
"""

import json
import os
import random
import time
from datetime import datetime

SEG_POR_MIN = 1.0          # 1 minuto simulado = 1 segundo real
TEMPO_EMBARQUE_MIN = 3     # minutos (simulados) parado no local para embarque
ESPERA_AGENTE_S = 3        # o agente automático "pensa" alguns segundos antes de alocar
AVISO_FIM_TURNO_MIN = 15   # gera solicitação automática X min antes do fim do turno

DIAS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
MOTIVOS = ["Fim de turno", "Início de turno", "Alerta de sono", "Outro"]

DADOS_PADRAO = {
    "maquinistas": [
        # Os 3 primeiros são os mesmos do ParteRFID.ino (mesmo EPC = login pelo crachá)
        {"id": "M01", "nome": "Deivid", "cargo": "Maquinista", "matricula": "MQ-1001",
         "epc": "E2801160600002187F218050", "telefone": "(11) 90000-0001",
         "base": "Estação Central", "endereco": "Rua das Palmeiras, 120 - Centro",
         "trem": "Trem T-102", "viagem_min": 14,
         "turno": {"inicio": "06:00", "fim": "14:00", "dias": [0, 1, 2, 3, 4]}},
        {"id": "M02", "nome": "Enzo", "cargo": "Supervisor", "matricula": "SP-2001",
         "epc": "E2801160600002187F2144F1", "telefone": "(11) 90000-0002",
         "base": "Estação Central", "endereco": "Av. Brasil, 845 - Jardim América",
         "trem": "Supervisão geral", "viagem_min": 18,
         "turno": {"inicio": "08:00", "fim": "17:00", "dias": [0, 1, 2, 3, 4]}},
        {"id": "M03", "nome": "Ana", "cargo": "Operadora", "matricula": "OP-3001",
         "epc": "300833B2DDD9014000000001", "telefone": "(11) 90000-0003",
         "base": "Pátio Norte", "endereco": "Rua dos Ipês, 33 - Vila Nova",
         "trem": "Trem T-205", "viagem_min": 10,
         "turno": {"inicio": "14:00", "fim": "22:00", "dias": [0, 1, 2, 3, 4, 5]}},
        {"id": "M04", "nome": "Carlos Mendes", "cargo": "Maquinista", "matricula": "MQ-1002",
         "epc": "", "telefone": "(11) 90000-0004",
         "base": "Pátio Norte", "endereco": "Rua Sete de Setembro, 501 - São José",
         "trem": "Trem T-310", "viagem_min": 22,
         "turno": {"inicio": "22:00", "fim": "06:00", "dias": [0, 1, 2, 3, 4, 6]}},
        {"id": "M05", "nome": "Juliana Rocha", "cargo": "Maquinista", "matricula": "MQ-1003",
         "epc": "", "telefone": "(11) 90000-0005",
         "base": "Estação Jardim", "endereco": "Rua Amazonas, 77 - Bela Vista",
         "trem": "Trem T-118", "viagem_min": 12,
         "turno": {"inicio": "14:00", "fim": "22:00", "dias": [2, 3, 4, 5, 6]}},
        {"id": "M06", "nome": "Rafael Lima", "cargo": "Maquinista", "matricula": "MQ-1004",
         "epc": "", "telefone": "(11) 90000-0006",
         "base": "Estação Central", "endereco": "Travessa das Flores, 9 - Centro",
         "trem": "Trem T-221", "viagem_min": 8,
         "turno": {"inicio": "06:00", "fim": "14:00", "dias": [3, 4, 5, 6, 0]}},
    ],
    "motoristas": [
        {"id": "D01", "nome": "Paulo Ribeiro", "veiculo": "Spin branca", "placa": "ABC-1D23",
         "telefone": "(11) 98000-0001", "distancia_min": 6},
        {"id": "D02", "nome": "Fernanda Alves", "veiculo": "Onix prata", "placa": "DEF-4G56",
         "telefone": "(11) 98000-0002", "distancia_min": 11},
        {"id": "D03", "nome": "Márcio Teixeira", "veiculo": "Doblò branca", "placa": "GHI-7J89",
         "telefone": "(11) 98000-0003", "distancia_min": 4},
        {"id": "D04", "nome": "Luciana Prado", "veiculo": "HB20 preto", "placa": "JKL-0M12",
         "telefone": "(11) 98000-0004", "distancia_min": 15},
        {"id": "D05", "nome": "Roberto Nunes", "veiculo": "Spin prata", "placa": "MNO-3P45",
         "telefone": "(11) 98000-0005", "distancia_min": 9},
    ],
}


def _minutos(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _fmt_dur(minutos):
    minutos = int(round(minutos))
    if minutos < 60:
        return f"{minutos} min"
    return f"{minutos // 60}h{minutos % 60:02d}"


def info_turno(turno, agora=None):
    """Diz se está em turno agora, quanto falta para acabar ou quando é o próximo."""
    agora = agora or datetime.now()
    ini, fim, dias = _minutos(turno["inicio"]), _minutos(turno["fim"]), turno.get("dias", [])
    t = agora.hour * 60 + agora.minute
    hoje, ontem = agora.weekday(), (agora.weekday() - 1) % 7
    noturno = fim <= ini
    resta = None
    if hoje in dias:
        if not noturno and ini <= t < fim:
            resta = fim - t
        elif noturno and t >= ini:
            resta = (1440 - t) + fim
    if resta is None and noturno and ontem in dias and t < fim:
        resta = fim - t
    if resta is not None:
        return {"em_turno": True, "minutos_restantes": resta,
                "texto": f"Em turno — termina em {_fmt_dur(resta)} ({turno['fim']})"}
    for d in range(8):
        dia = (hoje + d) % 7
        if dia in dias and (d > 0 or ini > t):
            falta = d * 1440 + ini - t
            quando = "hoje" if d == 0 else ("amanhã" if d == 1 else DIAS[dia])
            return {"em_turno": False, "minutos_ate_inicio": falta,
                    "texto": f"Folga — próximo turno {quando} às {turno['inicio']}"}
    return {"em_turno": False, "texto": "Sem escala cadastrada"}


class SalaControle:
    def __init__(self, arquivo_dados, trava, evento):
        """
        trava  -> a mesma RLock do servidor (estado consistente)
        evento -> função evento(nivel, descricao) que registra no histórico
        """
        self.arquivo = arquivo_dados
        self.trava = trava
        self.evento = evento
        self.dados = self._carregar()
        self.status_mot = {m["id"]: {"status": "DISPONIVEL", "corrida": None}
                           for m in self.dados["motoristas"]}
        self.solicitacoes = []
        self.corridas = []
        self.auto = True
        self.seq = 0
        self.avisos_turno = set()
        self._ultimo_tick = time.time()
        self.ouvintes = []  # ex: bot do Telegram -> função(tipo, dados)

    def _notificar(self, tipo, **dados):
        for f in self.ouvintes:
            try:
                f(tipo, dados)
            except Exception as err:
                print(f"[SALA] erro notificando {tipo}: {err}")

    def _livres_ordenados(self):
        return sorted((m for m in self.dados["motoristas"] if self.status_mot[m["id"]]["status"] == "DISPONIVEL"),
                      key=lambda m: m["distancia_min"])

    @staticmethod
    def _limpo(s):
        return {k: v for k, v in s.items() if k != "t"}

    # ---------------- cadastro ----------------
    def _carregar(self):
        if not os.path.exists(self.arquivo):
            with open(self.arquivo, "w", encoding="utf-8") as f:
                json.dump(DADOS_PADRAO, f, ensure_ascii=False, indent=2)
        try:
            with open(self.arquivo, encoding="utf-8") as f:
                dados = json.load(f)
        except (OSError, ValueError) as e:
            print(f"[SALA] Erro lendo {self.arquivo}: {e}. Usando dados padrão.")
            dados = json.loads(json.dumps(DADOS_PADRAO))
        dados.setdefault("maquinistas", [])
        dados.setdefault("motoristas", [])
        return dados

    def maquinista(self, mid):
        return next((m for m in self.dados["maquinistas"] if m["id"] == mid), None)

    def motorista(self, did):
        return next((m for m in self.dados["motoristas"] if m["id"] == did), None)

    def maquinista_por_epc(self, epc):
        epc = (epc or "").upper()
        return next((m for m in self.dados["maquinistas"] if epc and m.get("epc", "").upper() == epc), None)

    def _novo_id(self, prefixo):
        self.seq += 1
        return f"{prefixo}{self.seq:04d}"

    def _em_aberto(self, mid):
        return (any(s["maquinista_id"] == mid for s in self.solicitacoes) or
                any(c["maquinista_id"] == mid and c["etapa"] != "CONCLUIDA" for c in self.corridas))

    # ---------------- solicitações ----------------
    def solicitar(self, mid, motivo, prioridade="normal", criado_por="Agente da sala"):
        with self.trava:
            m = self.maquinista(mid)
            if not m:
                return None, "Maquinista não encontrado"
            if self._em_aberto(mid):
                return None, f"{m['nome']} já tem uma solicitação/corrida em aberto"
            if motivo == "Início de turno":
                origem, destino = m["endereco"], m["base"]
            else:
                origem, destino = m["base"], m["endereco"]
            if motivo == "Alerta de sono":
                prioridade = "alta"
                destino = f"{m['endereco']} (retirada por segurança)"
            s = {"id": self._novo_id("S"), "maquinista_id": mid, "maquinista": m["nome"],
                 "cargo": m["cargo"], "motivo": motivo, "prioridade": prioridade,
                 "origem": origem, "destino": destino, "criado_por": criado_por,
                 "hora": datetime.now().strftime("%H:%M:%S"), "t": time.time()}
            self.solicitacoes.append(s)
            nivel = "critico" if prioridade == "alta" else "info"
            self.evento(nivel, f"Solicitação {s['id']}: motorista para {m['nome']} — {motivo} "
                               f"({origem} → {destino})")
            self._notificar("solicitacao", solicitacao=self._limpo(s), livres=self._livres_ordenados())
            return s, None

    def cancelar_solicitacao(self, sid, por="Agente da sala"):
        with self.trava:
            s = next((x for x in self.solicitacoes if x["id"] == sid), None)
            if not s:
                return False
            self.solicitacoes.remove(s)
            self.evento("info", f"Solicitação {sid} de {s['maquinista']} cancelada por {por}")
            self._notificar("solicitacao_cancelada", solicitacao=self._limpo(s), por=por)
            return True

    def sugerir_motorista(self):
        livres = [m for m in self.dados["motoristas"] if self.status_mot[m["id"]]["status"] == "DISPONIVEL"]
        return min(livres, key=lambda m: m["distancia_min"]) if livres else None

    # ---------------- alocação / corridas ----------------
    def alocar(self, sid, did, por="Agente da sala"):
        with self.trava:
            s = next((x for x in self.solicitacoes if x["id"] == sid), None)
            d = self.motorista(did)
            if not s:
                return None, "Solicitação não encontrada"
            if not d:
                return None, "Motorista não encontrado"
            if self.status_mot[did]["status"] != "DISPONIVEL":
                return None, f"{d['nome']} não está disponível"
            m = self.maquinista(s["maquinista_id"])
            eta = max(2, d["distancia_min"] + random.randint(-1, 3))
            viagem = max(3, int(m.get("viagem_min", 15)) + random.randint(-2, 3))
            c = {"id": self._novo_id("C"), "solicitacao": s, "maquinista_id": s["maquinista_id"],
                 "maquinista": s["maquinista"], "motorista_id": did, "motorista": d["nome"],
                 "veiculo": d["veiculo"], "placa": d["placa"], "motivo": s["motivo"],
                 "prioridade": s["prioridade"], "origem": s["origem"], "destino": s["destino"],
                 "alocado_por": por, "inicio": datetime.now().strftime("%H:%M:%S"), "fim": None,
                 "etapa": "A_CAMINHO", "eta_min": eta, "viagem_min": viagem,
                 "etapa_total_s": eta * SEG_POR_MIN, "restante_s": eta * SEG_POR_MIN}
            self.solicitacoes.remove(s)
            self.corridas.insert(0, c)
            self.status_mot[did] = {"status": "EM_ROTA", "corrida": c["id"]}
            self.evento("ok", f"{por} alocou {d['nome']} ({d['veiculo']} {d['placa']}) para "
                              f"buscar {s['maquinista']} — chegada em ~{eta} min")
            self._notificar("alocada", solicitacao=self._limpo(s),
                            corrida={k: v for k, v in c.items() if k != "solicitacao"})
            return c, None

    def cancelar_corrida(self, cid):
        with self.trava:
            c = next((x for x in self.corridas if x["id"] == cid and x["etapa"] != "CONCLUIDA"), None)
            if not c:
                return False
            c["etapa"], c["fim"] = "CANCELADA", datetime.now().strftime("%H:%M:%S")
            self.status_mot[c["motorista_id"]] = {"status": "DISPONIVEL", "corrida": None}
            self.evento("atencao", f"Corrida {cid} ({c['motorista']} → {c['maquinista']}) cancelada")
            self._notificar("corrida_cancelada", solicitacao_id=c["solicitacao"]["id"],
                            corrida={k: v for k, v in c.items() if k != "solicitacao"})
            return True

    def _avancar(self, c):
        if c["etapa"] == "A_CAMINHO":
            c["etapa"] = "EMBARQUE"
            c["etapa_total_s"] = c["restante_s"] = TEMPO_EMBARQUE_MIN * SEG_POR_MIN
            self.evento("info", f"{c['motorista']} chegou em {c['origem']} para buscar {c['maquinista']}")
        elif c["etapa"] == "EMBARQUE":
            c["etapa"] = "EM_VIAGEM"
            c["etapa_total_s"] = c["restante_s"] = c["viagem_min"] * SEG_POR_MIN
            self.evento("info", f"{c['maquinista']} embarcou com {c['motorista']} — destino {c['destino']}")
        elif c["etapa"] == "EM_VIAGEM":
            c["etapa"], c["fim"] = "CONCLUIDA", datetime.now().strftime("%H:%M:%S")
            self.status_mot[c["motorista_id"]] = {"status": "DISPONIVEL", "corrida": None}
            d = self.motorista(c["motorista_id"])
            if d:  # o motorista agora está em outro ponto da cidade
                d["distancia_min"] = random.randint(4, 18)
            self.evento("ok", f"Corrida {c['id']} concluída: {c['maquinista']} entregue em {c['destino']}")
        self._notificar("etapa", solicitacao_id=c["solicitacao"]["id"],
                        corrida={k: v for k, v in c.items() if k != "solicitacao"})

    def tick(self):
        """Chamado a cada ~0,5 s pelo servidor."""
        with self.trava:
            agora = time.time()
            dt, self._ultimo_tick = agora - self._ultimo_tick, agora
            for c in self.corridas:
                if c["etapa"] in ("CONCLUIDA", "CANCELADA"):
                    continue
                c["restante_s"] -= dt
                if c["restante_s"] <= 0:
                    self._avancar(c)
            # guarda só as 30 corridas finalizadas mais recentes
            ativas = [c for c in self.corridas if c["etapa"] not in ("CONCLUIDA", "CANCELADA")]
            finais = [c for c in self.corridas if c["etapa"] in ("CONCLUIDA", "CANCELADA")][:30]
            self.corridas = ativas + finais

            # escala: pede motorista automaticamente perto do fim do turno
            hoje = datetime.now().strftime("%Y-%m-%d")
            for m in self.dados["maquinistas"]:
                it = info_turno(m["turno"])
                chave = f"{m['id']}-{hoje}-{m['turno']['fim']}"
                if (it["em_turno"] and it["minutos_restantes"] <= AVISO_FIM_TURNO_MIN
                        and chave not in self.avisos_turno):
                    self.avisos_turno.add(chave)
                    self.solicitar(m["id"], "Fim de turno", criado_por="Escala (automático)")

            # agente da sala automático
            if self.auto:
                fila = sorted(self.solicitacoes, key=lambda s: (s["prioridade"] != "alta", s["t"]))
                for s in fila:
                    if agora - s["t"] < ESPERA_AGENTE_S:
                        continue
                    d = self.sugerir_motorista()
                    if not d:
                        break
                    self.alocar(s["id"], d["id"], por="Agente automático (simulado)")

    def set_auto(self, ativo):
        with self.trava:
            self.auto = bool(ativo)
            self.evento("info", "Agente automático " + ("LIGADO" if self.auto else "DESLIGADO (alocação manual)"))

    # ---------------- dados para o site ----------------
    def snapshot(self, operador=None, alertas_por_nome=None):
        with self.trava:
            alertas_por_nome = alertas_por_nome or {}
            epc_logado = (operador or {}).get("epc", "").upper()
            maqs = []
            for m in self.dados["maquinistas"]:
                corrida = next((c for c in self.corridas if c["maquinista_id"] == m["id"]
                                and c["etapa"] not in ("CONCLUIDA", "CANCELADA")), None)
                pendente = next((s for s in self.solicitacoes if s["maquinista_id"] == m["id"]), None)
                maqs.append(dict(m, turno_info=info_turno(m["turno"]),
                                 logado=bool(m.get("epc")) and m["epc"].upper() == epc_logado,
                                 alertas=alertas_por_nome.get(m["nome"], 0),
                                 corrida_ativa=corrida["id"] if corrida else None,
                                 solicitacao_pendente=pendente["id"] if pendente else None))
            mots = [dict(d, **self.status_mot[d["id"]]) for d in self.dados["motoristas"]]
            sug = self.sugerir_motorista()
            return {"maquinistas": maqs, "motoristas": mots,
                    "solicitacoes": [{k: v for k, v in s.items() if k != "t"} for s in self.solicitacoes],
                    "corridas": [{k: v for k, v in c.items() if k != "solicitacao"} for c in self.corridas],
                    "auto": self.auto, "seg_por_min": SEG_POR_MIN, "motivos": MOTIVOS, "dias": DIAS,
                    "sugerido": sug["id"] if sug else None}
