"""Geração dos arquivos de transcrição.

O `pytranscript.Transcript` cuida dos formatos txt/csv/json/srt/vtt e da
tradução. Estendemos a classe apenas para usar o tempo final real de cada fala
nas legendas, em vez da estimativa de 5 segundos do original.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytranscript

from .transcricao import Fala

FORMATOS_PYTRANSCRIPT = ("txt", "csv", "json", "srt", "vtt")
FORMATOS = (*FORMATOS_PYTRANSCRIPT, "md")


class TranscricaoComLocutores(pytranscript.Transcript):
    """Transcript do pytranscript ciente do tempo final exato de cada fala."""

    def __init__(self, *args, tempos_finais: list[float] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tempos_finais: list[float] = tempos_finais or []

    def srt_generator(self):
        for i, (inicio, texto) in enumerate(
            zip(self.time, self.text, strict=True), start=1
        ):
            fim = self.tempos_finais[i - 1] if i <= len(self.tempos_finais) else inicio + 5
            if i < len(self.time):  # não invade a legenda seguinte
                fim = min(fim, self.time[i])
            fim = max(fim, inicio + 0.3)
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
    return json.dumps(
        {
            "locutores": sorted({f.locutor for f in falas}),
            "total_falas": len(falas),
            "duracao": max((f.fim for f in falas), default=0.0),
            "falas": [
                {
                    "inicio": round(f.inicio, 3),
                    "fim": round(f.fim, 3),
                    "faixa": f.faixa + 1,
                    "locutor": f.locutor,
                    "texto": f.texto,
                }
                for f in falas
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


@dataclass(slots=True)
class ArquivosGerados:
    """Arquivos escritos e avisos acumulados durante a escrita."""

    arquivos: list[Path] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


def _traduzir(
    transcript: TranscricaoComLocutores, alvo: str
) -> tuple[TranscricaoComLocutores, list[str]]:
    """Traduz via pytranscript, reinserindo as linhas que a tradução não cobriu.

    O `Transcript.translate` descarta as linhas que falharam; aqui elas voltam no
    idioma original, para que nada suma da transcrição e os tempos continuem alinhados.
    """
    simples, erros = transcript.translate(alvo)
    linhas = list(zip(simples.time, simples.text, strict=True))
    linhas.extend((erro.time, erro.line) for erro in erros)
    linhas.sort(key=lambda par: par[0])

    traduzida = TranscricaoComLocutores(
        time=[t for t, _ in linhas],
        text=[texto for _, texto in linhas],
        language=alvo,
        time_end=transcript.time_end,
        tempos_finais=transcript.tempos_finais,
    )
    avisos = []
    if erros:
        avisos.append(
            f"{len(erros)} linha(s) não puderam ser traduzidas e ficaram no "
            f"idioma original."
        )
    return traduzida, avisos


def escrever(
    falas: Sequence[Fala],
    destino_base: Path,
    formatos: Sequence[str] = FORMATOS,
    idioma: str = "auto",
    traduzir_para: str | None = None,
) -> ArquivosGerados:
    """Escreve a transcrição mesclada em todos os formatos pedidos.

    Args:
        falas: falas já mescladas e rotuladas.
        destino_base: caminho sem extensão (ex.: `saida/sessao-01`).
        formatos: subconjunto de `FORMATOS`.
        idioma: idioma de origem, usado na tradução.
        traduzir_para: código de idioma alvo (ex.: "en"); None desativa a tradução.

    Returns:
        Os caminhos dos arquivos gerados e eventuais avisos.
    """
    destino_base = Path(destino_base)
    destino_base.parent.mkdir(parents=True, exist_ok=True)
    transcript = construir_transcript(falas, idioma=idioma)
    resultado = ArquivosGerados()

    for formato in formatos:
        caminho = destino_base.with_suffix(f".{formato}")
        if formato == "md":
            caminho.write_text(para_markdown(falas, destino_base.stem), encoding="utf-8")
        else:
            transcript.write(caminho)
        resultado.arquivos.append(caminho)

    detalhado = destino_base.with_suffix(".detalhado.json")
    detalhado.write_text(para_json_detalhado(falas), encoding="utf-8")
    resultado.arquivos.append(detalhado)

    if traduzir_para:
        traduzida, avisos = _traduzir(transcript, traduzir_para)
        resultado.avisos.extend(avisos)
        for formato in formatos:
            if formato == "md":
                continue
            caminho = destino_base.with_suffix(f".{traduzir_para}.{formato}")
            traduzida.write(caminho)
            resultado.arquivos.append(caminho)

    return resultado
