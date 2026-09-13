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

from . import mesclagem
from .fala import Fala

FORMATOS_PYTRANSCRIPT = ("txt", "csv", "json", "srt", "vtt")
FORMATOS = (*FORMATOS_PYTRANSCRIPT, "md")

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


def _traduzir(
    transcript: TranscricaoComLocutores, alvo: str
) -> tuple[TranscricaoComLocutores, list[str]]:
    """Traduz via pytranscript, reinserindo as linhas que a tradução não cobriu.

    O `Transcript.translate` descarta as linhas que falharam; aqui elas voltam no
    idioma original, para que nada suma da transcrição e os tempos continuem alinhados.
    """
    simples, erros = transcript.translate(alvo)

    # O tempo final viaja junto com a linha, e não por posição: a tradução
    # devolve as linhas que deram certo e as que falharam em duas listas, e
    # remontá-las por índice desalinharia os fins assim que uma linha falhasse.
    finais: dict[float, list[float]] = {}
    for inicio, fim in zip(transcript.time, transcript.tempos_finais, strict=False):
        finais.setdefault(inicio, []).append(fim)

    def fim_de(inicio: float) -> float:
        pendentes = finais.get(inicio)
        return pendentes.pop(0) if pendentes else inicio + DURACAO_MINIMA_LEGENDA

    linhas = [
        (t, texto, fim_de(t))
        for t, texto in zip(simples.time, simples.text, strict=True)
    ]
    linhas.extend((erro.time, erro.line, fim_de(erro.time)) for erro in erros)
    linhas.sort(key=lambda item: item[0])

    traduzida = TranscricaoComLocutores(
        time=[t for t, _, _ in linhas],
        text=[texto for _, texto, _ in linhas],
        language=alvo,
        time_end=transcript.time_end,
        tempos_finais=[fim for _, _, fim in linhas],
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
            _gravar(transcript, caminho)
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
            _gravar(traduzida, caminho)
            resultado.arquivos.append(caminho)

    return resultado
