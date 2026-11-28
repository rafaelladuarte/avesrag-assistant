"""Orquestrador da conversa: máquina de estados em código (plano_assistant2.md §5).

O LLM não decide nada aqui: ele extrai (`c01`), sugere espécies (`c04`) e redige
a pergunta (`c02`). O fluxo:

- **coleta:** cada mensagem passa pelo extrator. Se havia uma pergunta à espera,
  a mensagem é a resposta (entra só como atributos); senão, é descrição: entra
  no texto da busca, e as sugestões do LLM são refeitas. Depois, a busca roda e,
  se ainda couber uma pergunta na rodada e alguma separar as candidatas, ela é
  feita; senão, as 3 primeiras são apresentadas. Sem o mínimo de atributos
  visuais, a apresentação usa o modo degradado (palavras-chave).
- **confirmacao:** "1", "2" ou "3" confirma; "não" recusa as 3 e começa outra
  rodada (perguntas de novo, sem as recusadas), até `confirmacao.rodadas`.
- **fim:** a conversa acabou.

As dependências entram prontas (`Servicos`), para os testes usarem falsos.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from avesrag_builder.core.vocabulario import RegrasBico, Vocabulario
from pymongo.database import Database

from avesrag_assistant.busca.degradado import buscar_degradado
from avesrag_assistant.busca.modelos import AtributosObservados, Candidata, Consulta
from avesrag_assistant.busca.modelos import validar_atributos as _validar
from avesrag_assistant.busca.pontuacao import Ranking
from avesrag_assistant.busca.retriever import buscar, carregar_fichas
from avesrag_assistant.conversa.config import ConfigConversa
from avesrag_assistant.conversa.estado import CandidataApresentada, EstadoConversa, Mensagem
from avesrag_assistant.conversa.perguntas import escolher
from avesrag_assistant.llm.cache import ErroLLM
from avesrag_assistant.llm.extrator import Extracao
from avesrag_assistant.llm.pergunta import reserva

APRESENTADAS = 3
DEGRADADO = "degradado"
ROTULOS = {
    "tamanho": "tamanho",
    "cores": "cores",
    "cores_por_parte": "cores de cada parte",
    "marcas": "marcas",
    "bico": "bico",
    "cauda": "cauda",
    "ambiente": "ambiente",
    "onde_estava": "onde estava",
    "o_que_fazia": "o que fazia",
    "grupo": "sozinha ou em grupo",
    "atividade": "horário",
}

# Nada a ver com aves.
FORA_DE_ESCOPO = (
    "Eu só ajudo a identificar aves a partir do que você viu. "
    "Me conte como era a ave: tamanho, cores, bico, onde ela estava."
)
# Fala de ave, mas não é identificação (expectativa de vida, o que come, fotos...): um ponto de
# evolução da aplicação (planos/plano_guardrails.md §13).
FORA_SOBRE_AVE = (
    "No momento não consigo responder a esse tipo de pergunta sobre aves: a aplicação ainda está "
    "em desenvolvimento e, por enquanto, só identifico espécies a partir do que você viu. "
    "Estamos trabalhando para melhorar! Me conte como era a ave: tamanho, cores, bico, "
    "onde ela estava."
)
# A conversa acaba quando a pessoa só manda mensagens fora do tema, em sequência.
ESCOPO_ENCERRADO = (
    "Vou encerrar esta conversa, porque as últimas mensagens não eram sobre identificar uma ave. "
    "Quando quiser identificar uma ave, comece uma nova conversa e me conte como ela era."
)
# A mensagem é sobre uma ave vista, mas não traz nenhuma característica (planos/plano_guardrails.md §11).
NADA_DESCRITO = (
    "Vamos por partes. Tente se lembrar de como era a ave: a cor, o tamanho, o bico ou "
    "o lugar onde ela estava. Qualquer detalhe ajuda."
)
NAO_ENTENDI = "Desculpe, não consegui entender agora. Pode repetir de outro jeito?"
NENHUMA = (
    "Não encontrei nenhuma ave com essa descrição. Se puder, fotografe a ave e "
    "registre no WikiAves ou no eBird: lá, outras pessoas ajudam a identificar."
)
ENCERRADA = "Esta conversa já terminou. Para identificar outra ave, comece uma nova."
PEDIR_ESCOLHA = 'É alguma delas? Responda com o número (1, 2 ou 3) ou com "não".'
SEM_MAIS_RODADAS = (
    "Não consegui encontrar a ave desta vez. Se puder, fotografe a ave e registre "
    "no WikiAves ou no eBird: lá, outras pessoas ajudam a identificar."
)


class Extrator(Protocol):
    def extrair(
        self,
        mensagem: str,
        atributos: AtributosObservados | None = None,
        descricao: str | None = None,
        pergunta: str | None = None,
    ) -> Extracao: ...


class Sugestor(Protocol):
    def especies(self, mensagem: str, indice: Mapping[str, str]) -> list[str]: ...


class Redator(Protocol):
    def redigir(self, pedido: str, atributos: AtributosObservados) -> str: ...


@dataclass
class Servicos:
    db: Database
    ranking: Ranking
    conversa: ConfigConversa
    vocabulario: Vocabulario
    regras_bico: RegrasBico
    extrator: Extrator
    redator: Redator
    vetor: Callable[[str], list[float]]  # o embedding (pelo cache)
    sugestor: Sugestor | None = None
    indice: Mapping[str, str] | None = None  # nomes → species_id, para o sugestor
    fala_de_ave: Callable[[str], bool] | None = None  # escolhe o texto da recusa de escopo


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", " ", sem_acento.lower()).strip()


def ler_confirmacao(texto: str) -> int | str | None:
    """1, 2 ou 3 (a candidata), "nao", "sim" (sem dizer qual) ou None (não entendi)."""
    palavras = _normalizar(texto).split()
    if not palavras:
        return None
    numeros = [int(p) for p in palavras if p.isdigit()]
    if len(numeros) == 1 and 1 <= numeros[0] <= APRESENTADAS:
        return numeros[0]
    if palavras[0] in ("nao", "n", "nenhuma", "nenhum"):
        return "nao"
    if palavras[0] in ("sim", "s", "e", "isso"):
        return "sim"
    return None


class Orquestrador:
    def __init__(self, servicos: Servicos, estado: EstadoConversa):
        self.s = servicos
        self.estado = estado
        self.atributos = _validar(estado.atributos, servicos.vocabulario, servicos.regras_bico)

    # -- entrada -------------------------------------------------------------

    def processar(self, texto: str) -> str:
        """Uma mensagem da pessoa → a resposta do assistente (o estado é atualizado)."""
        texto = texto.strip()
        self._registrar("usuario", texto)
        if self.estado.etapa == "fim":
            resposta = ENCERRADA
        elif self.estado.etapa == "confirmacao":
            resposta = self._confirmar(texto)
        else:
            resposta = self._coletar(texto)
        self._registrar("assistente", resposta)
        return resposta

    def _registrar(self, papel: str, texto: str) -> None:
        self.estado.mensagens.append(Mensagem(papel=papel, texto=texto))

    # -- coleta --------------------------------------------------------------

    def _coletar(self, texto: str) -> str:
        e = self.estado
        try:
            ex = self.s.extrator.extrair(
                texto, self.atributos, e.descricao_para_busca, e.pergunta_texto
            )
        except ErroLLM:
            return NAO_ENTENDI
        if ex.fora_de_escopo:
            e.fora_de_escopo_seguidas += 1
            limite = self.s.conversa.escopo.maximo_seguidas
            if limite and e.fora_de_escopo_seguidas >= limite:
                return self._encerrar("fora_de_escopo", ESCOPO_ENCERRADO)  # sem chamar mais ninguém
            fala = self.s.fala_de_ave
            return FORA_SOBRE_AVE if fala is not None and fala(texto) else FORA_DE_ESCOPO
        e.fora_de_escopo_seguidas = 0
        if e.pergunta is None and not ex.atributos.informados():
            # Descrição nova sem característica nenhuma: buscar com "ave" só gasta chamadas.
            e.local = ex.local or e.local
            e.data_observacao = ex.data_observacao or e.data_observacao
            return NADA_DESCRITO
        self.atributos = ex.atributos
        e.atributos = ex.atributos.model_dump(exclude_none=True)
        e.descricao_para_busca = ex.descricao_para_busca
        e.local = ex.local or e.local
        e.data_observacao = ex.data_observacao or e.data_observacao
        if e.pergunta is None:  # descrição nova: texto da busca e sugestões
            e.descricoes.append(texto)
            self._sugerir()
        e.pergunta = e.pergunta_texto = None
        return self._avancar()

    def _sugerir(self) -> None:
        if self.s.sugestor is None or self.s.indice is None:
            return
        try:
            self.estado.sugeridas = self.s.sugestor.especies(
                " ".join(self.estado.descricoes), self.s.indice
            )
        except ErroLLM:
            pass  # segue com as sugestões anteriores

    def _consulta(self) -> Consulta:
        return Consulta.model_construct(
            descricao=" ".join(self.estado.descricoes) or "ave",
            atributos=self.atributos,
            recusadas=list(self.estado.recusadas),
        )

    def _buscar(self, k: int) -> list[Candidata]:
        consulta = self._consulta()
        return buscar(
            self.s.db,
            consulta,
            self.s.vetor(consulta.descricao),
            self.s.ranking,
            self.s.regras_bico,
            self.s.conversa.busca.modo,
            k=k,
            sugeridas=self.estado.sugeridas,
        )

    def visuais(self) -> int:
        nomes = self.s.conversa.apresentacao.visuais
        return sum(1 for n in nomes if getattr(self.atributos, n) is not None)

    def _avancar(self) -> str:
        """Depois da busca: a próxima pergunta, ou as 3 primeiras."""
        cfg = self.s.conversa.perguntas
        e = self.estado
        candidatas = self._buscar(cfg.candidatas)
        e.buscas.append([c.species_id for c in candidatas])
        if e.perguntas_na_rodada < cfg.maximo and len(candidatas) > 1:
            fichas = carregar_fichas(self.s.db, [c.species_id for c in candidatas])
            escolha = escolher(
                [(c.nota_final, fichas[c.species_id].atributos) for c in candidatas],
                self.atributos,
                cfg,
                self.s.regras_bico,
                e.perguntas_feitas,
            )
            if escolha is not None:
                return self._perguntar(escolha.pergunta.chave)
        return self._apresentar(candidatas)

    def _perguntar(self, chave: str) -> str:
        e = self.estado
        pedido = self.s.conversa.perguntas.pedido(chave)
        try:
            texto = self.s.redator.redigir(pedido, self.atributos)
        except ErroLLM:
            texto = reserva(pedido)
        e.pergunta, e.pergunta_texto = chave, texto
        e.perguntas_feitas.append(chave)
        e.perguntas_na_rodada += 1
        return texto

    def _apresentar(self, candidatas: Sequence[Candidata]) -> str:
        e = self.estado
        if self.visuais() < self.s.conversa.apresentacao.minimo_visuais:
            candidatas = buscar_degradado(
                self.s.db, self._consulta(), self.s.ranking, self.s.regras_bico, APRESENTADAS
            )
            e.modo_busca = DEGRADADO
            e.buscas.append([c.species_id for c in candidatas])
        else:
            e.modo_busca = self.s.conversa.busca.modo
        primeiras = list(candidatas)[:APRESENTADAS]
        if not primeiras:
            return self._encerrar("sem_candidatas", NENHUMA)
        e.candidatas = [
            CandidataApresentada(
                species_id=c.species_id,
                nome=c.nome,
                nome_cientifico=c.nome_cientifico,
                nota_final=c.nota_final,
                bateram=[d.atributo for d in c.detalhes if d.resultado == "bate"],
                conflitaram=[d.atributo for d in c.detalhes if d.resultado == "conflita"],
            )
            for c in primeiras
        ]
        e.etapa = "confirmacao"
        return texto_das_candidatas(e.candidatas, e.modo_busca == DEGRADADO)

    # -- confirmação ---------------------------------------------------------

    def _confirmar(self, texto: str) -> str:
        e = self.estado
        leitura = ler_confirmacao(texto)
        if isinstance(leitura, int) and leitura <= len(e.candidatas):
            escolhida = e.candidatas[leitura - 1]
            e.confirmada = escolhida.species_id
            return self._encerrar(
                "confirmada", f"Que bom! Então era {escolhida.nome} ({escolhida.nome_cientifico})."
            )
        if leitura == "nao":
            e.recusadas += [c.species_id for c in e.candidatas if c.species_id not in e.recusadas]
            e.candidatas = []
            e.rodada_confirmacao += 1
            if e.rodada_confirmacao >= self.s.conversa.confirmacao.rodadas:
                return self._encerrar("sem_confirmacao", SEM_MAIS_RODADAS)
            e.etapa = "coleta"
            e.perguntas_na_rodada = 0
            return "Tudo bem, vou procurar de novo sem essas. " + self._avancar()
        if leitura == "sim":
            return "Qual delas? Responda com o número (1, 2 ou 3)."
        return PEDIR_ESCOLHA

    def _encerrar(self, motivo: str, texto: str) -> str:
        self.estado.etapa = "fim"
        self.estado.motivo_fim = motivo
        self.estado.pergunta = self.estado.pergunta_texto = None
        return texto


def texto_das_candidatas(
    candidatas: Sequence[CandidataApresentada], degradado: bool = False
) -> str:
    """O texto fixo da apresentação (o redator `c03` é do Assistant 3)."""
    linhas = [
        "Com o que você contou, as aves mais prováveis são:"
        if not degradado
        else "Com poucas características, busquei pelas palavras da descrição. As mais próximas são:"
    ]
    for i, c in enumerate(candidatas, start=1):
        linha = f"{i}. {c.nome} ({c.nome_cientifico})"
        detalhes = []
        if c.bateram:
            detalhes.append("combina: " + ", ".join(ROTULOS.get(a, a) for a in c.bateram))
        if c.conflitaram:
            detalhes.append("não combina: " + ", ".join(ROTULOS.get(a, a) for a in c.conflitaram))
        if detalhes:
            linha += " — " + "; ".join(detalhes)
        linhas.append(linha)
    linhas.append(PEDIR_ESCOLHA)
    return "\n".join(linhas)
