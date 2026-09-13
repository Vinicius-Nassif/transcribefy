"""Reconhecimento de fala com Vosk — o motor leve, alternativo ao Whisper.

Roda em CPU modesta e sem GPU, mas o modelo é um reconhecedor por n-gramas:
devolve texto sem pontuação nem maiúsculas e erra mais as palavras que dependem
do contexto da frase. Fica como opção para máquinas fracas e para uso offline
com o modelo já baixado; a qualidade boa está em `motor_whisper`.

A leitura do reconhecedor é feita manualmente porque precisamos de dois dados
que o `pytranscript.transcribe` descarta: o tempo final de cada fala e o vetor
de voz (`spk`) usado na diarização.
"""

from __future__ import annotations

import json
import wave
from collections.abc import Callable
from pathlib import Path

from .fala import Fala

Progresso = Callable[[float], None]

BLOCO_FRAMES = 8_000  # 0,5 s de áudio por iteração: bom equilíbrio custo/progresso


def _fala_de_resultado(bruto: str, faixa: int) -> Fala | None:
    dados = json.loads(bruto)
    palavras = dados.get("result") or []
    texto = (dados.get("text") or "").strip()
    if not palavras or not texto:
        return None
    return Fala(
        inicio=float(palavras[0]["start"]),
        fim=float(palavras[-1]["end"]),
        texto=texto,
        faixa=faixa,
        vetor_voz=dados.get("spk"),
        frames_voz=int(dados.get("spk_frames", 0)),
        palavras=palavras,
    )


def transcrever(
    wav: Path,
    modelo: Path,
    faixa: int = 0,
    modelo_locutor: Path | None = None,
    progresso: Progresso | None = None,
) -> list[Fala]:
    """Transcreve um WAV mono 16 kHz e devolve as falas com tempo e palavras.

    Args:
        wav: arquivo WAV mono PCM 16 bits (use `transcricao.preparar_wav`).
        modelo: diretório do modelo Vosk de reconhecimento.
        faixa: número da faixa de origem, apenas para rotular as falas.
        modelo_locutor: diretório do modelo de x-vectors. Quando informado, as
            falas já saem com o vetor de voz e `vozes` não precisa rodar depois.
        progresso: callback que recebe a fração concluída (0.0 a 1.0).
    """
    import vosk

    vosk.SetLogLevel(-1)
    wav = Path(wav)
    if not wav.is_file():
        raise FileNotFoundError(f"{wav} não encontrado")

    modelo_aberto = vosk.Model(str(modelo))
    with wave.Wave_read(str(wav)) as onda:
        taxa = onda.getframerate()
        total_frames = onda.getnframes() or 1

        reconhecedor = vosk.KaldiRecognizer(modelo_aberto, taxa)
        reconhecedor.SetWords(enable_words=True)
        if modelo_locutor is not None:
            reconhecedor.SetSpkModel(vosk.SpkModel(str(modelo_locutor)))

        falas: list[Fala] = []
        lidos = 0
        while True:
            dados = onda.readframes(BLOCO_FRAMES)
            if not dados:
                break
            lidos += len(dados) // onda.getsampwidth()
            if reconhecedor.AcceptWaveform(dados):
                fala = _fala_de_resultado(reconhecedor.Result(), faixa)
                if fala is not None:
                    falas.append(fala)
            if progresso is not None:
                progresso(min(1.0, lidos / total_frames))

        fala = _fala_de_resultado(reconhecedor.FinalResult(), faixa)
        if fala is not None:
            falas.append(fala)

    if progresso is not None:
        progresso(1.0)
    return falas
