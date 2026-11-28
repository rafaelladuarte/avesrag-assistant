"""Curadoria da ficha pela página (plano_assistant4.md §13).

Uma correção é gravada em `species_overrides` (o que o `fuse` aplica por cima de todas
as fontes, então sobrevive a toda reconstrução) e a ficha da espécie é refeita na hora
pelo `refundir_uma` do builder, com os filtros dos documentos de busca. Cada mudança
entra no histórico, com quem fez, quando, o valor anterior e o motivo.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from avesrag_builder.core.regras_ficha import carregar_regras_ficha
from avesrag_builder.core.regras_fusao import carregar_regras_fusao
from avesrag_builder.core.regras_midia import carregar_regras_midia
from avesrag_builder.core.settings import Settings as SettingsDoBuilder
from avesrag_builder.core.vocabulario import RegrasBico, Vocabulario
from avesrag_builder.db.collections import SPECIES, SPECIES_OVERRIDES, garantir_indices
from avesrag_builder.fuse import overrides as ov
from avesrag_builder.fuse import regras as r
from avesrag_builder.fuse.species import carregar as carregar_dados
from avesrag_builder.fuse.species import refundir_uma
from pymongo.database import Database

from avesrag_assistant.api.curadoria_erro import ErroCuradoria
from avesrag_assistant.api.especies import _foto, textos_gerados
from avesrag_assistant.api.exibicao import exibir
from avesrag_assistant.api.guardrails import conferir_edicao

ROTULOS = {
    "atributos.cores": "Cores",
    "atributos.cores_por_parte": "Cor de cada parte",
    "atributos.marcas": "Marcas",
    "atributos.cauda": "Cauda",
    "atributos.tamanho": "Tamanho",
    "atributos.bico_tipo": "Tipo de bico",
    "atributos.bico": "Forma do bico (comprimento e espessura)",
    "atributos.ambiente": "Ambiente",
    "atributos.onde_estava": "Onde costuma estar",
    "atributos.o_que_fazia": "O que costuma fazer",
    "atributos.grupo": "Sozinha ou em grupo",
    "atributos.atividade": "Horário de atividade",
    "perfil.massa_g": "Peso (g)",
    "perfil.dieta": "Dieta",
    "descricao": "Descrição",
    "comportamento": "Comportamento e ambiente",
    "voz": "Voz e hábitos",
    "midia.fotos": "Fotos",
}
# Domínio do documento de busca de cada texto (o texto gerado que a ficha mostra sem correção).
DOMINIO_DO_TEXTO = {
    "descricao": "aparencia",
    "comportamento": "comportamento_ambiente",
    "voz": "voz_habitos",
}
ORIGENS_FOTO = {
    "brasil": "iNaturalist, Brasil",
    "mundo": "iNaturalist, fora do Brasil",
    "p18": "Wikimedia Commons",
}
Refundir = Callable[[Database, str], Any]
Candidatas = Callable[[str], list[tuple[str, dict[str, Any]]]]


def _valor_atual(doc: dict[str, Any], caminho: str) -> Any:
    bloco: Any = doc
    for parte in caminho.split("."):
        bloco = (bloco or {}).get(parte) if isinstance(bloco, dict) else None
    return bloco


def _como_texto(dt: Any) -> str:
    return dt.strftime("%d/%m/%Y %H:%M") if isinstance(dt, datetime) else str(dt or "")


class Curadoria:
    def __init__(
        self,
        db: Database,
        vocabulario: Vocabulario,
        regras_bico: RegrasBico,
        refundir: Refundir | None = None,
        config_dir: Path | None = None,
        candidatas: Candidatas | None = None,
    ):
        self.db = db
        self.voc = vocabulario
        self.bico = regras_bico
        # As regras vêm da pasta config/ do builder, sem ler o .env dele.
        self.settings = SettingsDoBuilder(_env_file=None)
        pasta = Path(config_dir or self.settings.CONFIG_DIR)
        self.midia = carregar_regras_midia(pasta / "regras_midia.yaml")
        self.ficha = carregar_regras_ficha(pasta / "regras_ficha.yaml")
        self.fusao = carregar_regras_fusao(pasta / "regras_fusao.yaml", vocabulario)
        self._refundir = refundir or self._refundir_do_builder
        self._candidatas = candidatas or self._candidatas_do_builder

    def preparar(self) -> None:
        """Cria o índice único de `species_overrides` (idempotente)."""
        garantir_indices(self.db)

    def _refundir_do_builder(self, db: Database, species_id: str) -> Any:
        return refundir_uma(
            db,
            species_id,
            settings=self.settings,
            vocab=self.voc,
            bico=self.bico,
            regras=self.fusao,
            midia_regras=self.midia,
            ficha_regras=self.ficha,
        )

    # -- leitura ---------------------------------------------------------------

    def _opcoes(self, caminho: str, tipo: str) -> Any:
        def lista(valores: list[str]) -> list[dict[str, str]]:
            return [{"valor": v, "rotulo": exibir(v)} for v in valores]

        if tipo == "lista":
            return lista(self.voc.valores(caminho.removeprefix("atributos.")))
        if tipo == "escolha":
            return lista(self.voc.valores("bico_tipo"))
        if tipo == "bico":
            dimensoes = self.bico.arquivo.dimensoes
            return {d: lista(dimensoes[d]) for d in ("comprimento", "espessura")}
        if tipo == "pares":
            return {"partes": lista(self.voc.partes()), "cores": lista(self.voc.valores("cores"))}
        return None

    def _candidatas_do_builder(self, species_id: str) -> list[tuple[str, dict[str, Any]]]:
        """As fotos válidas das fontes (origem, foto), para escolher na curadoria."""
        dados = carregar_dados(self.db, [species_id], {})
        return r.fotos_candidatas(dados[0], self.midia) if dados else []

    def _opcoes_fotos(self, species_id: str) -> dict[str, Any]:
        return {
            "candidatas": [
                {"origem": ORIGENS_FOTO.get(o, o), **_foto(f, miniatura=True)}  # type: ignore[arg-type]
                for o, f in self._candidatas(species_id)
            ],
            "licencas": [lic.nome for lic in self.midia.arquivo.licencas],
        }

    def _resolver_fotos(self, species_id: str, doc: dict[str, Any], valor: Any) -> Any:
        """O pedido traz só o `link` de uma foto já conhecida (das fontes ou da ficha) ou os
        dados de uma foto nova; o servidor monta a foto completa e o crédito."""
        if not isinstance(valor, list):
            return valor  # a validação recusa
        conhecidas = {f["link"]: f for _, f in self._candidatas(species_id)}
        midia = doc.get("midia") or {}
        for f in [midia.get("foto"), *(midia.get("fotos") or [])]:
            if f:
                conhecidas.setdefault(f["link"], dict(f))
        saida = []
        for item in valor:
            if isinstance(item, dict) and set(item) == {"link"}:
                if item["link"] not in conhecidas:
                    raise ErroCuradoria("Essa foto não está entre as disponíveis.")
                saida.append(conhecidas[item["link"]])
            elif isinstance(item, dict) and set(item) == {"url", "autor", "licenca", "link"}:
                autor, licenca = str(item["autor"]).strip(), item["licenca"]
                saida.append(
                    {
                        **item,
                        "autor": autor,
                        "fonte": "manual",
                        "credito": f"Foto: {autor} · {licenca}",
                    }
                )
            else:
                raise ErroCuradoria("Cada foto precisa do link ou dos dados completos.")
        return saida

    def campos(self, species_id: str) -> dict[str, Any]:
        doc = self.db[SPECIES].find_one({"species_id": species_id})
        if doc is None:
            raise ErroCuradoria("Não encontrei essa espécie.")
        corrigidos = (self.db[SPECIES_OVERRIDES].find_one({"species_id": species_id}) or {}).get(
            "campos"
        ) or {}
        gerados = textos_gerados(self.db, species_id)
        saida = []
        for caminho, tipo in ov.EDITAVEIS.items():
            entrada = corrigidos.get(ov.chave_de(caminho))
            manual = None
            if entrada:
                manual = {
                    "por": entrada.get("por", ""),
                    "em": _como_texto(entrada.get("em")),
                    "motivo": entrada.get("motivo") or "",
                }
            valor = _valor_atual(doc, caminho)
            opcoes = self._opcoes(caminho, tipo)
            if tipo == "fotos":
                midia = doc.get("midia") or {}
                valor = [
                    _foto(f) for f in (valor or ([midia["foto"]] if midia.get("foto") else []))
                ]
                opcoes = self._opcoes_fotos(species_id)
            saida.append(
                {
                    "caminho": caminho,
                    "rotulo": ROTULOS[caminho],
                    "tipo": tipo,
                    "valor": valor,
                    "opcoes": opcoes,
                    "manual": manual,
                    "automatico": entrada.get("automatico") if entrada else None,
                    "sugestao": gerados.get(DOMINIO_DO_TEXTO.get(caminho, "")),
                }
            )
        return conferir_edicao({"species_id": species_id, "campos": saida})

    # -- escrita ---------------------------------------------------------------

    def definir(
        self, species_id: str, caminho: str, valor: Any, por: str, motivo: str | None = None
    ) -> dict[str, Any]:
        """Corrige um campo (`valor` None remove o campo da ficha) e refaz a ficha."""
        doc = self.db[SPECIES].find_one({"species_id": species_id})
        if doc is None:
            raise ErroCuradoria("Não encontrei essa espécie.")
        existente = (self.db[SPECIES_OVERRIDES].find_one({"species_id": species_id}) or {}).get(
            "campos"
        ) or {}
        if caminho == "midia.fotos":
            valor = self._resolver_fotos(species_id, doc, valor)
        if caminho == "atributos.bico" and isinstance(valor, dict):
            # Uma correção guarda uma dimensão só ou as duas: a nova se soma à que já existe.
            gravado = (existente.get(ov.chave_de(caminho)) or {}).get("valor")
            valor = {**(gravado if isinstance(gravado, dict) else {}), **valor}
        try:
            normalizado = ov.validar_valor(caminho, valor, self.voc, self.midia, self.bico)
        except ov.OverrideInvalido as e:
            raise ErroCuradoria(str(e)) from e
        motivo = (motivo or "").strip()[:300] or None
        agora = datetime.now(UTC)
        anterior = _valor_atual(doc, caminho)
        if caminho == "midia.fotos" and not anterior:
            anterior = [doc["midia"]["foto"]] if (doc.get("midia") or {}).get("foto") else None
        # O valor automático é o que a ficha tinha antes da primeira correção do campo.
        previa = existente.get(ov.chave_de(caminho))
        automatico = previa.get("automatico") if previa else anterior
        entrada = {
            "valor": normalizado,
            "por": por,
            "em": agora,
            "motivo": motivo,
            "automatico": automatico,
        }
        self.db[SPECIES_OVERRIDES].update_one(
            {"species_id": species_id},
            {
                "$set": {f"campos.{ov.chave_de(caminho)}": entrada},
                "$push": {
                    "historico": {
                        "caminho": caminho,
                        "acao": "removeu" if normalizado is None else "definiu",
                        "valor": normalizado,
                        "anterior": anterior,
                        "por": por,
                        "em": agora,
                        "motivo": motivo,
                    }
                },
            },
            upsert=True,
        )
        self._refundir(self.db, species_id)
        return self.campos(species_id)

    def reverter(self, species_id: str, caminho: str, por: str) -> dict[str, Any]:
        """Tira a correção do campo: a ficha volta ao valor das fontes."""
        if caminho not in ov.EDITAVEIS:
            raise ErroCuradoria("Esse campo não é editável.")
        existente = (self.db[SPECIES_OVERRIDES].find_one({"species_id": species_id}) or {}).get(
            "campos"
        ) or {}
        if ov.chave_de(caminho) not in existente:
            raise ErroCuradoria("Esse campo não tem correção manual.")
        doc = self.db[SPECIES].find_one({"species_id": species_id}) or {}
        self.db[SPECIES_OVERRIDES].update_one(
            {"species_id": species_id},
            {
                "$unset": {f"campos.{ov.chave_de(caminho)}": ""},
                "$push": {
                    "historico": {
                        "caminho": caminho,
                        "acao": "reverteu",
                        "valor": None,
                        "anterior": _valor_atual(doc, caminho),
                        "por": por,
                        "em": datetime.now(UTC),
                        "motivo": None,
                    }
                },
            },
        )
        self._refundir(self.db, species_id)
        return self.campos(species_id)
