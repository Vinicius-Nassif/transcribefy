"""Deteccao do binario ffmpeg, inspecao de faixas e extração de áudio.

Evita depender do ffprobe: todas as informações são lidas do relatório que o
próprio ffmpeg imprime em stderr ao abrir o arquivo. Assim o fallback via
`imageio-ffmpeg` (que só distribui o ffmpeg) continua funcionando.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SAMPLE_RATE = 16_000

_RE_STREAM = re.compile(
    r"Stream #(?P<file>\d+):(?P<idx>\d+)(?:\[[^\]]*\])?(?:\((?P<lang>[^)]*)\))?: "
    r"Audio: (?P<codec>[^,]+), (?P<rate>\d+) Hz, (?P<layout>[^,]+)"
)
_RE_TITLE = re.compile(r"^\s+title\s*:\s*(?P<title>.+?)\s*$")
_RE_DURACAO = re.compile(r"Duration: (\d+):(\d\d):(\d\d\.\d+)")


class FFmpegIndisponivel(RuntimeError):
    pass


@lru_cache(maxsize=1)
def localizar_ffmpeg() -> str:
    """Retorna o caminho do ffmpeg: o do sistema, ou o embutido no imageio-ffmpeg."""
    caminho = shutil.which("ffmpeg")
    if caminho:
        return caminho
    try:
        import imageio_ffmpeg
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise FFmpegIndisponivel(
            "ffmpeg não encontrado. Instale com `sudo apt install ffmpeg` "
            "ou `pip install imageio-ffmpeg`."
        ) from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


@dataclass(slots=True)
class FaixaAudio:
    """Uma faixa de áudio dentro do container."""

    ordem: int          # índice entre as faixas de áudio (0, 1, 2...) -> `-map 0:a:N`
    stream: int         # índice do stream no container
    codec: str
    sample_rate: int
    layout: str
    idioma: str | None = None
    titulo: str | None = None

    @property
    def rotulo(self) -> str:
        partes = [f"Faixa {self.ordem + 1}"]
        if self.titulo:
            partes.append(self.titulo)
        partes.append(f"{self.codec}, {self.sample_rate} Hz, {self.layout}")
        return " - ".join(partes)


def _relatorio(video: Path) -> str:
    """Roda `ffmpeg -i <vídeo>` e devolve o relatório impresso em stderr."""
    proc = subprocess.run(
        [localizar_ffmpeg(), "-hide_banner", "-i", str(video)],
        capture_output=True,
        text=True,
        errors="replace",
    )
    # ffmpeg sai com código != 0 por não haver arquivo de saída; isso é esperado.
    saida = proc.stderr
    if "Invalid data found" in saida or "No such file" in saida:
        raise ValueError(f"Não foi possível ler {video}:\n{saida.strip()[-500:]}")
    return saida


def listar_faixas_audio(video: Path) -> list[FaixaAudio]:
    """Lista as faixas de áudio do arquivo, na ordem em que o ffmpeg as expoe."""
    faixas: list[FaixaAudio] = []
    ultima: FaixaAudio | None = None
    for linha in _relatorio(video).splitlines():
        m = _RE_STREAM.search(linha)
        if m:
            idioma = m.group("lang")
            ultima = FaixaAudio(
                ordem=len(faixas),
                stream=int(m.group("idx")),
                codec=m.group("codec").strip(),
                sample_rate=int(m.group("rate")),
                layout=m.group("layout").strip(),
                idioma=idioma if idioma and idioma != "und" else None,
            )
            faixas.append(ultima)
            continue
        if ultima is not None and ultima.titulo is None:
            mt = _RE_TITLE.match(linha)
            if mt:
                ultima.titulo = mt.group("title")
        if "Stream #" in linha and ": Audio:" not in linha:
            ultima = None
    return faixas


def duracao_segundos(video: Path) -> float | None:
    """Duração total do arquivo em segundos, ou None se o ffmpeg não informar."""
    m = _RE_DURACAO.search(_relatorio(video))
    if not m:
        return None
    horas, minutos, segundos = m.groups()
    return int(horas) * 3600 + int(minutos) * 60 + float(segundos)


def extrair_faixa(
    video: Path,
    ordem: int,
    destino: Path,
    inicio: float = 0.0,
    fim: float | None = None,
) -> Path:
    """Extrai uma faixa de áudio como WAV mono PCM 16 bits / 16 kHz (formato do Vosk).

    Args:
        video: arquivo de origem.
        ordem: índice da faixa entre as faixas de áudio (0 é a primeira).
        destino: caminho do WAV a criar.
        inicio: segundo em que o recorte começa.
        fim: segundo em que o recorte termina; None vai até o final.
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    recorte = ["-ss", f"{inicio:.3f}"] if inicio else []
    if fim is not None:
        recorte += ["-to", f"{fim:.3f}"]
    comando = [
        localizar_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        *recorte,
        "-i", str(video),
        "-map", f"0:a:{ordem}",
        "-vn", "-sn", "-dn",
        "-acodec", "pcm_s16le",
        "-ac", "1",
        "-ar", str(SAMPLE_RATE),
        str(destino),
    ]
    proc = subprocess.run(comando, capture_output=True, text=True, errors="replace")
    if proc.returncode != 0 or not destino.exists():
        raise RuntimeError(
            f"Falha ao extrair a faixa de áudio {ordem + 1}:\n{proc.stderr.strip()}"
        )
    return destino
