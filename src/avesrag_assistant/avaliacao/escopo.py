"""Leitura e validação do `avaliacao/escopo.yaml` (planos/plano_guardrails.md §4.2, GR3).

O conjunto mede o `fora_de_escopo` do `c01`: o que ele barra por engano e o que deixa
passar. As descrições do `casos.yaml` não são copiadas para cá: entram por referência.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from avesrag_builder.core.vocabulario import VocabularioInvalido, _erros_pydantic, _ler_yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

Grupo = Literal["resposta_curta", "descricao_atipica", "fora", "fronteira"]
Esperado = Literal["escopo", "fora"]
Texto = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
IdMensagem = Annotated[str, StringConstraints(pattern=r"^[rafb]\d{3}$")]


class EscopoInvalido(ValueError):
    """O arquivo de escopo não passou na validação."""

    def __init__(self, arquivo: Path | str, erros: list[str]):
        self.arquivo = str(arquivo)
        self.erros = erros
        lista = "\n".join(f"  - {e}" for e in erros)
        super().__init__(f"{self.arquivo}: {len(erros)} erro(s)\n{lista}")


class MensagemEscopo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: IdMensagem
    grupo: Grupo
    esperado: Esperado
    texto: Texto
    pergunta: Texto | None = None  # a última pergunta do assistente (só nas respostas curtas)
    fala_de_ave: bool | None = None  # só nas `fora`: a mensagem cita ave (texto da recusa)
    revisar: bool = False  # fronteira: o esperado é proposta, e a decisão é da usuária
    nota: str | None = None


class EscopoArquivo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    versao: str
    mensagens: list[MensagemEscopo] = Field(min_length=1)


def _erros_cruzados(mensagens: list[MensagemEscopo]) -> list[str]:
    erros = []
    for id_, n in Counter(m.id for m in mensagens).items():
        if n > 1:
            erros.append(f"mensagens: id {id_!r} repetido")
    for m in mensagens:
        prefixo = {"resposta_curta": "r", "descricao_atipica": "a", "fora": "f", "fronteira": "b"}
        if not m.id.startswith(prefixo[m.grupo]):
            erros.append(f"{m.id}: o id de {m.grupo} começa com {prefixo[m.grupo]!r}")
        if (m.grupo == "resposta_curta") != (m.pergunta is not None):
            erros.append(f"{m.id}: só a resposta_curta tem pergunta, e ela precisa ter")
        if m.grupo == "fronteira" and not m.revisar:
            erros.append(f"{m.id}: a fronteira é para a usuária revisar (revisar: true)")
        if m.grupo in ("resposta_curta", "descricao_atipica") and m.esperado != "escopo":
            erros.append(f"{m.id}: {m.grupo} é sempre do escopo")
        if (m.esperado == "fora") != (m.fala_de_ave is not None):
            erros.append(f"{m.id}: fala_de_ave só vale (e é obrigatório) nas mensagens fora")
        if m.grupo == "fora" and m.esperado != "fora":
            erros.append(f"{m.id}: o grupo fora é sempre fora do escopo")
    return erros


def carregar_escopo(path: Path | str) -> list[MensagemEscopo]:
    path = Path(path)
    try:
        dados = _ler_yaml(path)
    except VocabularioInvalido as e:
        raise EscopoInvalido(path, e.erros) from e
    try:
        arquivo = EscopoArquivo.model_validate(dados)
    except ValidationError as e:
        raise EscopoInvalido(path, _erros_pydantic(e)) from e
    erros = _erros_cruzados(arquivo.mensagens)
    if erros:
        raise EscopoInvalido(path, erros)
    return arquivo.mensagens


# ---------------------------------------------------------------------------
# Medida do `fora_de_escopo` do c01 (GR3)
# ---------------------------------------------------------------------------

MAXIMO_FALSO_POSITIVO = 0.02  # do que é do escopo, no máximo 2% marcado como fora
MINIMO_ACERTO_FORA = 0.90  # do que é fora, pelo menos 90% marcado como fora
GRUPO_CASOS = "casos"  # as descrições do casos.yaml, por referência

LinhaEscopo = tuple[str, str, str, str, str]  # id, grupo, esperado, obtido, texto


class _Extrator(Protocol):
    def extrair(
        self,
        mensagem: str,
        atributos: Any = None,
        descricao: str | None = None,
        pergunta: str | None = None,
    ) -> Any: ...


@dataclass(frozen=True)
class ResultadoEscopo:
    id: str
    grupo: str
    esperado: str
    obtido: str  # escopo | fora | erro
    texto: str

    @property
    def certo(self) -> bool:
        return self.obtido == self.esperado


def medir(
    extrator: _Extrator,
    mensagens: Sequence[MensagemEscopo],
    descricoes: Sequence[tuple[str, str]] = (),
    erro: type[Exception] = Exception,
) -> list[ResultadoEscopo]:
    """Roda o `c01` em cada mensagem (as respostas curtas, com a pergunta pendente) e em cada
    descrição do `casos.yaml` (id, texto; todas do escopo). `erro`: a exceção de falha do LLM."""
    pedidos = [(m.id, m.grupo, m.esperado, m.texto, m.pergunta) for m in mensagens]
    pedidos += [(i, GRUPO_CASOS, "escopo", t, None) for i, t in descricoes]
    saida = []
    for id_, grupo, esperado, texto, pergunta in pedidos:
        try:
            obtido = (
                "fora" if extrator.extrair(texto, None, None, pergunta).fora_de_escopo else "escopo"
            )
        except erro:
            obtido = "erro"
        saida.append(ResultadoEscopo(id_, grupo, esperado, obtido, texto))
    return saida


@dataclass(frozen=True)
class ResumoEscopo:
    por_grupo: dict[str, tuple[int, int]]  # grupo -> (certos, total)
    falso_positivo: tuple[int, int]  # do escopo, marcados como fora (sem a fronteira)
    acerto_fora: tuple[int, int]  # do fora, marcados como fora (sem a fronteira)
    fronteira: tuple[int, int]  # certos e total da fronteira, só informativo
    erros: int  # chamadas que falharam

    @property
    def taxa_falso_positivo(self) -> float:
        n, total = self.falso_positivo
        return n / total if total else 0.0

    @property
    def taxa_acerto_fora(self) -> float:
        n, total = self.acerto_fora
        return n / total if total else 1.0

    @property
    def aprovado(self) -> bool:
        return (
            self.erros == 0
            and self.taxa_falso_positivo <= MAXIMO_FALSO_POSITIVO
            and self.taxa_acerto_fora >= MINIMO_ACERTO_FORA
        )


def resumir(resultados: Sequence[ResultadoEscopo]) -> ResumoEscopo:
    por_grupo: dict[str, list[int]] = {}
    for r in resultados:
        certos_total = por_grupo.setdefault(r.grupo, [0, 0])
        certos_total[0] += r.certo
        certos_total[1] += 1
    nucleo = [r for r in resultados if r.grupo != "fronteira"]
    escopo = [r for r in nucleo if r.esperado == "escopo"]
    fora = [r for r in nucleo if r.esperado == "fora"]
    fronteira = [r for r in resultados if r.grupo == "fronteira"]
    return ResumoEscopo(
        por_grupo={g: (c, t) for g, (c, t) in por_grupo.items()},
        falso_positivo=(sum(r.obtido == "fora" for r in escopo), len(escopo)),
        acerto_fora=(sum(r.certo for r in fora), len(fora)),
        fronteira=(sum(r.certo for r in fronteira), len(fronteira)),
        erros=sum(r.obtido == "erro" for r in resultados),
    )


def tabela_escopo(resumo: ResumoEscopo) -> list[str]:
    def pct(n: int, total: int) -> str:
        return f"{n / total:.0%}" if total else "-"

    linhas = ["grupo               certos  total  acerto"]
    for grupo, (c, t) in sorted(resumo.por_grupo.items()):
        linhas.append(f"{grupo:<19} {c:>6} {t:>6}  {pct(c, t):>6}")
    n, t = resumo.falso_positivo
    linhas.append(
        f"falso positivo (do escopo, barrado): {n} de {t} = {pct(n, t)} (máximo {MAXIMO_FALSO_POSITIVO:.0%})"
    )
    n, t = resumo.acerto_fora
    linhas.append(
        f"fora do escopo reconhecido: {n} de {t} = {pct(n, t)} (mínimo {MINIMO_ACERTO_FORA:.0%})"
    )
    n, t = resumo.fronteira
    linhas.append(f"fronteira (só informativo, decisão de produto): {n} de {t} = {pct(n, t)}")
    if resumo.erros:
        linhas.append(f"chamadas que falharam: {resumo.erros}")
    linhas.append("veredito: " + ("APROVADO" if resumo.aprovado else "REPROVADO (ver as erradas)"))
    return linhas


def gravar_tsv_escopo(resultados: Sequence[ResultadoEscopo], destino: Path | str) -> Path:
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    cabecalho = "id\tgrupo\tesperado\tobtido\tcerto\ttexto"
    linhas = [
        f"{r.id}\t{r.grupo}\t{r.esperado}\t{r.obtido}\t{'sim' if r.certo else 'NAO'}\t"
        + " ".join(r.texto.split())
        for r in resultados
    ]
    destino.write_text("\n".join([cabecalho, *linhas]) + "\n", encoding="utf-8")
    return destino
