"""Porta de entrada do reconhecimento de fala, comum aos dois motores.

Quem chama (o `pipeline`) não precisa saber qual motor está em uso: pede a
transcrição de um WAV informando o modelo já resolvido e recebe uma lista de
`Fala`. A escolha entre Whisper e Vosk mora no modelo, não em quem transcreve.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytranscript

from . import motor_vosk, motor_whisper
from .fala import Fala
from .modelos import ModeloPronto

__all__ = ["SAMPLE_RATE", "Fala", "preparar_wav", "transcrever", "wav_valido"]

SAMPLE_RATE = pytranscript.SAMPLE_RATE_AUDIO

Progresso = Callable[[float], None]


def wav_valido(caminho: Path) -> bool:
    """True se o arquivo já é um WAV mono PCM 16 bits aceito pelos motores."""
    return pytranscript._is_valid_wav_file(Path(caminho))


def preparar_wav(origem: Path, destino: Path | None = None) -> Path:
    """Converte qualquer áudio/vídeo de faixa única para o WAV que os motores exigem."""
    if wav_valido(Path(origem)):
        return Path(origem)
    return pytranscript.to_valid_wav(origem, destino)


def transcrever(
    wav: Path,
    modelo: ModeloPronto,
    faixa: int = 0,
    idioma: str | None = "pt",
    contexto: str = "",
    dispositivo: str = "auto",
    modelo_locutor: Path | None = None,
    progresso: Progresso | None = None,
) -> list[Fala]:
    """Transcreve um WAV mono 16 kHz com o motor do modelo informado.

    Args:
        wav: arquivo WAV mono PCM 16 bits (use `preparar_wav`).
        modelo: modelo já baixado, com o motor que o acompanha.
        faixa: número da faixa de origem, apenas para rotular as falas.
        idioma: código do idioma do áudio; "auto" deixa o modelo detectar.
        contexto: nomes e termos da mesa, usados só pelo Whisper.
        dispositivo: "auto", "cuda" ou "cpu"; usado só pelo Whisper.
        modelo_locutor: atalho do motor Vosk para já devolver os vetores de voz.
        progresso: callback que recebe a fração concluída (0.0 a 1.0).
    """
    wav = Path(wav)
    if not wav.is_file():
        raise FileNotFoundError(f"{wav} não encontrado")
    if not wav_valido(wav):
        raise TypeError(f"{wav} não é um WAV mono PCM 16 bits válido")

    if modelo.motor == "vosk":
        return motor_vosk.transcrever(
            wav, modelo.caminho, faixa=faixa,
            modelo_locutor=modelo_locutor, progresso=progresso,
        )
    return motor_whisper.transcrever(
        wav, modelo.caminho, faixa=faixa, idioma=idioma,
        contexto=contexto, dispositivo=dispositivo, progresso=progresso,
    )
