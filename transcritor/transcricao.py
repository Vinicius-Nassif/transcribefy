"""Reconhecimento de fala com Vosk, preservando tempos e x-vectors de locutor.

O `pytranscript` é usado como base: dele vem a conversão para WAV valido, a
validação do formato é o modelo `Transcript` que gera as saídas finais
(ver `transcritor.saída`). Aqui a leitura do reconhecedor é feita manualmente
porque precisamos de dois dados que o `pytranscript.transcribe` descarta: o
tempo final de cada fala é o vetor de voz (`spk`) usado na diarizacao.
"""

from __future__ import annotations

import json
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytranscript
import vosk

SAMPLE_RATE = pytranscript.SAMPLE_RATE_AUDIO
BLOCO_FRAMES = 8_000  # 0,5 s de áudio por iteração: bom equilibrio custo/progresso

vosk.SetLogLevel(-1)

Progresso = Callable[[float], None]


@dataclass(slots=True)
class Fala:
    """Um trecho continuo de fala reconhecido em uma das faixas."""

    inicio: float
    fim: float
    texto: str
    faixa: int
    locutor: str = ""
    vetor_voz: list[float] | None = None
    frames_voz: int = 0
    palavras: list[dict] = field(default_factory=list)

    @property
    def duracao(self) -> float:
        return max(0.0, self.fim - self.inicio)


def wav_valido(caminho: Path) -> bool:
    """True se o arquivo já é um WAV mono PCM 16 bits aceito pelo Vosk."""
    return pytranscript._is_valid_wav_file(Path(caminho))


def preparar_wav(origem: Path, destino: Path | None = None) -> Path:
    """Converte qualquer áudio/vídeo de faixa única para o WAV que o Vosk exige."""
    if wav_valido(Path(origem)):
        return Path(origem)
    return pytranscript.to_valid_wav(origem, destino)


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
    modelo_fala: Path,
    modelo_locutor: Path | None = None,
    faixa: int = 0,
    progresso: Progresso | None = None,
) -> list[Fala]:
    """Transcreve um WAV mono 16 kHz e devolve as falas com tempo e vetor de voz.

    Args:
        wav: arquivo WAV mono PCM 16 bits (use `preparar_wav` ou `extrair_faixa`).
        modelo_fala: diretório do modelo Vosk de reconhecimento.
        modelo_locutor: diretório do modelo de x-vectors. Se None, nenhuma
            informacao de voz e coletada (use para faixas de locutor único).
        faixa: número da faixa de origem, apenas para rotular as falas.
        progresso: callback que recebe a fracao concluida (0.0 a 1.0).
    """
    wav = Path(wav)
    if not wav.is_file():
        raise FileNotFoundError(f"{wav} não encontrado")
    if not wav_valido(wav):
        raise TypeError(f"{wav} não é um WAV mono PCM 16 bits valido")

    modelo = vosk.Model(str(modelo_fala))
    with wave.Wave_read(str(wav)) as onda:
        taxa = onda.getframerate()
        total_frames = onda.getnframes() or 1

        reconhecedor = vosk.KaldiRecognizer(modelo, taxa)
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
