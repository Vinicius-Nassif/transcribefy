"""Geração dos arquivos de transcrição.

O `pytranscript.Transcript` cuida dos formatos txt/csv/json/srt/vtt. Estendemos a
classe apenas para usar o tempo final real de cada fala nas legendas, em vez da
estimativa de 5 segundos do original. A tradução vem de fora (`traducao`), como
uma função que traduz uma lista de textos.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytranscript

from . import mesclagem
from .fala import Fala

FORMATOS_PYTRANSCRIPT = ("txt", "csv", "json", "srt", "vtt")
FORMATOS = (*FORMATOS_PYTRANSCRIPT, "md")

# Recebe os textos de todas as falas e devolve a tradução de cada um; None
# marca o que não pôde ser traduzido.
Traduzir = Callable[[list[str]], list[str | None]]

# Piso de duração de uma legenda, para que uma resposta de uma palavra ainda
# apareça tempo suficiente na tela.
DURACAO_MINIMA_LEGENDA = 0.3


class TranscricaoComLocutores(pytranscript.Transcript):
    """Transcript do pytranscript ciente do tempo final exato de cada fala."""

    def __init__(self, *args, tempos_finais: list[float] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tempos_finais: list[float] = tempos_finais or []

    def srt_generator(self):
        """Gera as legendas com o tempo final real de cada fala.

        Uma legenda **não** é encurtada porque a seguinte começa antes de ela
        terminar. As duas faixas correm em paralelo: cortar a fala do GM no
        instante em que um jogador o interrompe apagaria a interrupção da linha
        do tempo e faria a fala parecer mais curta do que foi. Legendas
        sobrepostas são válidas em SRT e em VTT.
        """
        for i, (inicio, texto) in enumerate(
            zip(self.time, self.text, strict=True), start=1
        ):
            fim = self.tempos_finais[i - 1] if i <= len(self.tempos_finais) else inicio + 5
            fim = max(fim, inicio + DURACAO_MINIMA_LEGENDA)
            inicio_str = pytranscript.seconds_to_srt_time(inicio)
            fim_str = pytranscript.seconds_to_srt_time(fim)
            yield f"{i}\n{inicio_str} --> {fim_str}\n{texto}\n\n"


def construir_transcript(
    falas: Sequence[Fala], idioma: str = "auto"
) -> TranscricaoComLocutores:
    """Converte as falas mescladas em um Transcript do pytranscript."""
    transcript = TranscricaoComLocutores(
        language=idioma,
        tempos_finais=[f.fim for f in falas],
        time_end=max((f.fim for f in falas), default=0.0),
    )
    for fala in falas:
        transcript.append(fala.inicio, f"{fala.locutor}: {fala.texto}")
    return transcript


def para_markdown(falas: Sequence[Fala], titulo: str = "Transcrição") -> str:
    linhas = [f"# {titulo}", ""]
    for fala in falas:
        carimbo = pytranscript.seconds_to_time(fala.inicio)
        linhas.append(f"**[{carimbo}] {fala.locutor}:** {fala.texto}")
        linhas.append("")
    return "\n".join(linhas)


def para_json_detalhado(falas: Sequence[Fala]) -> str:
    """Formato completo: uma entrada por fala, com a marca de sobreposição.

    A lista é ordenada pelo início, então uma interjeição no meio de um monólogo
    aparece depois dele. O campo `sobreposta` é o que distingue esse caso de uma
    sequência de falas que não se tocam.
    """
    marcas = mesclagem.sobrepostas(falas)
    return json.dumps(
        {
            "locutores": sorted({f.locutor for f in falas}),
            "total_falas": len(falas),
            "total_sobrepostas": sum(marcas),
            "duracao": max((f.fim for f in falas), default=0.0),
            "falas": [
                {
                    "inicio": round(f.inicio, 3),
                    "fim": round(f.fim, 3),
                    "faixa": f.faixa + 1,
                    "locutor": f.locutor,
                    "sobreposta": marca,
                    "texto": f.texto,
                }
                for f, marca in zip(falas, marcas, strict=True)
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def _gravar(transcript: TranscricaoComLocutores, caminho: Path) -> None:
    """Grava um dos formatos do `pytranscript` sempre em UTF-8.

    O `Transcript.write` usa a codificação padrão do sistema. No Linux isso já é
    UTF-8, mas no Windows é cp1252: os acentos sairiam num arquivo que quase
    nenhum editor abre certo, e um travessão ou reticências — que o Whisper
    produz o tempo todo — interromperiam a gravação com `UnicodeEncodeError`.
    """
    formato = caminho.suffix[1:]
    if formato not in FORMATOS_PYTRANSCRIPT:
        raise ValueError(f"Formato desconhecido para {caminho.name}: {formato}")
    caminho.write_text(getattr(transcript, f"to_{formato}")(), encoding="utf-8")


@dataclass(slots=True)
class ArquivosGerados:
    """Arquivos escritos e avisos acumulados durante a escrita."""

    arquivos: list[Path] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


def traduzir_falas(falas: Sequence[Fala], traduzir: Traduzir) -> tuple[list[Fala], list[str]]:
    """Copia as falas com o texto traduzido. Devolve (falas, avisos).

    Só o texto é traduzido, nunca o nome do locutor — "Mestre" não pode virar
    "Master" na legenda. Uma fala que a tradução não cobre fica no idioma
    original, no mesmo lugar e com o mesmo tempo: nada some da transcrição.
    """
    traduzidos = traduzir([f.texto for f in falas])
    copias = [
        replace(fala, texto=novo if novo else fala.texto, palavras=[])
        for fala, novo in zip(falas, traduzidos, strict=True)
    ]
    falhas = sum(1 for novo in traduzidos if not novo)
    avisos = []
    if falhas:
        avisos.append(
            f"{falhas} linha(s) não puderam ser traduzidas e ficaram no idioma original."
        )
    return copias, avisos


def _escrever_formatos(
    falas: Sequence[Fala], base: Path, sufixo: str, formatos: Sequence[str], idioma: str
) -> list[Path]:
    transcript = construir_transcript(falas, idioma=idioma)
    arquivos = []
    for formato in formatos:
        caminho = base.with_name(f"{base.name}{sufixo}.{formato}")
        if formato == "md":
            caminho.write_text(para_markdown(falas, base.stem), encoding="utf-8")
        else:
            _gravar(transcript, caminho)
        arquivos.append(caminho)
    return arquivos


def escrever_traducao(
    falas: Sequence[Fala],
    destino_base: Path,
    codigo: str,
    traduzir: Traduzir,
    formatos: Sequence[str] = FORMATOS,
) -> ArquivosGerados:
    """Escreve uma tradução nos formatos pedidos (`sessao-01.en.srt` etc.).

    Levanta a exceção do tradutor se ele falhar por inteiro; quem chama decide
    se isso derruba a execução — no pipeline, nunca derruba.
    """
    traduzidas, avisos = traduzir_falas(falas, traduzir)
    return ArquivosGerados(
        arquivos=_escrever_formatos(traduzidas, Path(destino_base), f".{codigo}", formatos, codigo),
        avisos=[f"{codigo}: {aviso}" for aviso in avisos],
    )


def escrever(
    falas: Sequence[Fala],
    destino_base: Path,
    formatos: Sequence[str] = FORMATOS,
    idioma: str = "auto",
    traducoes: Sequence[tuple[str, Traduzir]] = (),
    progresso: Callable[[float], None] | None = None,
) -> ArquivosGerados:
    """Escreve a transcrição mesclada em todos os formatos pedidos.

    Args:
        falas: falas já mescladas e rotuladas.
        destino_base: caminho sem extensão (ex.: `saida/sessao-01`).
        formatos: subconjunto de `FORMATOS`.
        idioma: idioma de origem, gravado nos metadados.
        traducoes: pares (código do idioma, função de tradução). Cada um gera
            os mesmos formatos com o código no nome (`sessao-01.en.srt`). Uma
            tradução que falha vira aviso e não leva junto a transcrição nem as
            outras traduções.
        progresso: recebe a fração concluída, uma vez por tradução.

    Returns:
        Os caminhos dos arquivos gerados e eventuais avisos.
    """
    destino_base = Path(destino_base)
    destino_base.parent.mkdir(parents=True, exist_ok=True)
    for formato in formatos:
        if formato not in FORMATOS:
            raise ValueError(f"Formato desconhecido: {formato}")
    resultado = ArquivosGerados()
    resultado.arquivos.extend(_escrever_formatos(falas, destino_base, "", formatos, idioma))

    # with_name, e não with_suffix: num nome com ponto ("sessao.01"), with_suffix
    # trocaria o ".01" e o detalhado sairia com nome diferente dos outros.
    detalhado = destino_base.with_name(f"{destino_base.name}.detalhado.json")
    detalhado.write_text(para_json_detalhado(falas), encoding="utf-8")
    resultado.arquivos.append(detalhado)

    for feitas, (codigo, traduzir) in enumerate(traducoes, start=1):
        try:
            traduzida = escrever_traducao(falas, destino_base, codigo, traduzir, formatos)
        except Exception as erro:  # noqa: BLE001 - uma tradução não derruba o resto
            resultado.avisos.append(f"A tradução para {codigo} falhou e foi pulada: {erro}")
        else:
            resultado.arquivos.extend(traduzida.arquivos)
            resultado.avisos.extend(traduzida.avisos)
        if progresso is not None:
            progresso(feitas / len(traducoes))
    return resultado
