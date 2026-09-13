"""Vetores de voz (x-vectors) de cada fala, independentes do motor de texto.

O Vosk sabe devolver um vetor de 128 dimensões que caracteriza a voz de um
trecho. O motor de texto padrão é o Whisper, que não calcula nada disso, então a
extração vive aqui: recebe falas já reconhecidas (por qualquer motor) e preenche
`vetor_voz`/`frames_voz` de cada uma, que é o que a diarização consome.

Rodar num trecho de cada vez, em vez de uma passada única pela faixa inteira,
mantém cada vetor alinhado exatamente com a fala que o motor de texto delimitou.
"""

from __future__ import annotations

import json
import wave
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np

from .fala import Fala

Progresso = Callable[[float], None]

BLOCO_FRAMES = 8_000  # 0,5 s de áudio por iteração, como no motor Vosk


def _acumular(bruto: str, soma: np.ndarray | None, total: int) -> tuple[np.ndarray | None, int]:
    """Soma o vetor de um resultado parcial, pesado pelos frames que o geraram."""
    dados = json.loads(bruto)
    vetor = dados.get("spk")
    frames = int(dados.get("spk_frames", 0))
    if not vetor or frames <= 0:
        return soma, total
    parcela = np.asarray(vetor, dtype=np.float64) * frames
    return (parcela if soma is None else soma + parcela), total + frames


def _vetor_do_trecho(reconhecedor, dados: bytes, largura: int) -> tuple[list[float] | None, int]:
    """Extrai o vetor médio de voz de um trecho de áudio bruto.

    Um trecho longo produz vários resultados parciais; a média ponderada pelos
    frames de cada um descreve a voz melhor que qualquer resultado isolado.
    """
    soma: np.ndarray | None = None
    total = 0
    passo = BLOCO_FRAMES * largura
    for inicio in range(0, len(dados), passo):
        if reconhecedor.AcceptWaveform(dados[inicio : inicio + passo]):
            soma, total = _acumular(reconhecedor.Result(), soma, total)
    soma, total = _acumular(reconhecedor.FinalResult(), soma, total)
    reconhecedor.Reset()
    if soma is None or total <= 0:
        return None, 0
    return (soma / total).tolist(), total


def anexar_vetores(
    wav: Path,
    falas: Sequence[Fala],
    modelo_fala: Path,
    modelo_locutor: Path,
    progresso: Progresso | None = None,
) -> list[Fala]:
    """Preenche `vetor_voz` e `frames_voz` de cada fala, lendo o trecho no WAV.

    Args:
        wav: a mesma faixa, em WAV mono PCM 16 bits, que gerou as falas.
        falas: falas com `inicio`/`fim` em segundos relativos a esse WAV.
        modelo_fala: modelo Vosk de reconhecimento (o compacto já basta — ele só
            alimenta o reconhecedor, o vetor vem do modelo de locutor).
        modelo_locutor: modelo Vosk de x-vectors.
        progresso: callback que recebe a fração concluída (0.0 a 1.0).

    Returns:
        A mesma lista de falas, já com os vetores preenchidos. Trechos curtos
        demais para gerar um vetor confiável ficam com `vetor_voz` em `None`.
    """
    falas = list(falas)
    if not falas:
        return falas

    import vosk

    vosk.SetLogLevel(-1)
    modelo = vosk.Model(str(modelo_fala))
    locutor = vosk.SpkModel(str(modelo_locutor))

    with wave.Wave_read(str(wav)) as onda:
        taxa = onda.getframerate()
        largura = onda.getsampwidth()
        ultimo_frame = onda.getnframes()
        reconhecedor = vosk.KaldiRecognizer(modelo, taxa, locutor)

        for posicao, fala in enumerate(falas):
            comeco = max(0, min(int(fala.inicio * taxa), ultimo_frame))
            quantidade = min(int(fala.duracao * taxa), ultimo_frame - comeco)
            if quantidade > 0:
                onda.setpos(comeco)
                vetor, frames = _vetor_do_trecho(
                    reconhecedor, onda.readframes(quantidade), largura
                )
                fala.vetor_voz = vetor
                fala.frames_voz = frames
            if progresso is not None:
                progresso((posicao + 1) / len(falas))

    if progresso is not None:
        progresso(1.0)
    return falas
