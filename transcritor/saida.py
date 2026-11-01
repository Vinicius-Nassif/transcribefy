"""Geração dos arquivos de transcrição.

O `pytranscript.Transcript` cuida dos formatos txt/csv/json/srt/vtt. Estendemos a
classe apenas para usar o tempo final real de cada fala nas legendas, em vez da
estimativa de 5 segundos do original. A tradução é do motor ativo em `traducao`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytranscript

from . import mesclagem, traducao
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
    falas: Sequence[Fala], origem: str, destino: str, motor: traducao.Motor
) -> tuple[TranscricaoComLocutores, list[str]]:
    """Traduz o texto de cada fala, mantendo locutor, início e fim.

    Só o texto vai para o motor — o nome de quem fala não é traduzido. Uma
    linha que o motor não conseguiu traduzir volta no idioma original, no
    mesmo lugar: nada some da transcrição e os tempos continuam alinhados.
    """
    traduzidas = motor.traduzir([f.texto for f in falas], origem, destino)
    falhas = 0
    transcript = TranscricaoComLocutores(
        language=destino,
        tempos_finais=[f.fim for f in falas],
        time_end=max((f.fim for f in falas), default=0.0),
    )
    for fala, texto in zip(falas, traduzidas, strict=True):
        if texto is None:
            falhas += 1
            texto = fala.texto
        transcript.append(fala.inicio, f"{fala.locutor}: {texto}")

    avisos = []
    if falhas and falhas == len(falas):
        avisos.append(
            f"A tradução para {traducao.nome(destino)} falhou por inteiro "
            f"({motor.descricao}); os arquivos .{destino}.* ficaram no idioma "
            "original. Confira a conexão, ou tente mais tarde se o serviço "
            "estiver limitando as requisições."
        )
    elif falhas:
        avisos.append(
            f"{falhas} linha(s) não puderam ser traduzidas para "
            f"{traducao.nome(destino)} e ficaram no idioma original."
        )
    return transcript, avisos


def escrever(
    falas: Sequence[Fala],
    destino_base: Path,
    formatos: Sequence[str] = FORMATOS,
    idioma: str = "auto",
    traducoes: Sequence[str] = (),
    motor_traducao: traducao.Motor | None = None,
) -> ArquivosGerados:
    """Escreve a transcrição mesclada em todos os formatos pedidos.

    Args:
        falas: falas já mescladas e rotuladas.
        destino_base: caminho sem extensão (ex.: `saida/sessao-01`).
        formatos: subconjunto de `FORMATOS`.
        idioma: idioma de origem, usado na tradução.
        traducoes: códigos dos idiomas de destino (ex.: ["en", "es"]); vazio
            desativa a tradução. Cada um gera `<base>.<código>.<formato>`.
        motor_traducao: o motor a usar; o padrão é o ativo em `traducao`.

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

    motor = motor_traducao or (traducao.motor() if traducoes else None)
    for destino in traducoes:
        traduzida, avisos = _traduzir(falas, idioma, destino, motor)
        resultado.avisos.extend(avisos)
        for formato in formatos:
            if formato == "md":
                continue
            caminho = destino_base.with_suffix(f".{destino}.{formato}")
            _gravar(traduzida, caminho)
            resultado.arquivos.append(caminho)

    return resultado
