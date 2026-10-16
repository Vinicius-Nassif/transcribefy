"""Mede uma transcrição de ponta a ponta: tempo por etapa, memória e progresso.

É a régua das otimizações de desempenho: rode antes e depois de uma mudança, com
o mesmo arquivo e o mesmo modelo, e compare as linhas de JSON.

    .venv/bin/python scripts/medir.py gravacao.mp4 [-m preciso] [--repetir 10]

`--bloco S` encurta os blocos do Whisper, para que um arquivo curto já tenha
várias fronteiras entre blocos e dê para comparar o texto com o de bloco único.

`--repetir N` monta, numa pasta ignorada pelo git (`.medicoes/`), um arquivo com
o original repetido N vezes — é como medir uma sessão longa a partir de um
trecho curto, inclusive o crescimento da memória com a duração.

Medidas:
  total_s                 tempo de parede da execução inteira
  etapas_s                tempo somado de cada função do pipeline
  pico_rss_mb             maior memória residente do processo
  pico_vram_mb            maior uso de VRAM da placa (inclui outros processos)
  maior_intervalo_s       maior tempo sem nenhum aviso de progresso — é o que a
                          interface mostra como "travado"
"""

from __future__ import annotations

import argparse
import functools
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from transcritor import diarizacao, ffmpeg_tools, mesclagem, pipeline, saida, transcricao, vozes

PASTA = Path(".medicoes")


def _instrumentar(tempos: dict[str, float]) -> None:
    """Embrulha as funções de cada etapa para somar o tempo gasto nelas."""
    alvos = [
        (ffmpeg_tools, "extrair_faixa", "extracao"),
        (transcricao, "transcrever", "transcricao"),
        (vozes, "anexar_vetores", "vozes"),
        (diarizacao, "atribuir_locutores", "diarizacao"),
        (mesclagem, "mesclar", "mesclagem"),
        (saida, "escrever", "escrita"),
    ]
    for modulo, nome, rotulo in alvos:
        original = getattr(modulo, nome)

        @functools.wraps(original)
        def medido(*args, _original=original, _rotulo=rotulo, **kwargs):
            inicio = time.perf_counter()
            try:
                return _original(*args, **kwargs)
            finally:
                tempos[_rotulo] = round(
                    tempos.get(_rotulo, 0.0) + time.perf_counter() - inicio, 2
                )

        setattr(modulo, nome, medido)


def _rss_mb() -> int:
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for linha in status:
                if linha.startswith("VmRSS"):
                    return int(linha.split()[1]) // 1024
    except OSError:  # fora do Linux não há /proc; o pico fica em 0
        pass
    return 0


def _vram_mb() -> int:
    try:
        saida_smi = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        return int(saida_smi.split()[0])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return 0


def _repetido(video: Path, vezes: int) -> Path:
    PASTA.mkdir(exist_ok=True)
    destino = PASTA / f"{video.stem}-x{vezes}{video.suffix}"
    if not destino.is_file():
        subprocess.run(
            [ffmpeg_tools.localizar_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
             "-stream_loop", str(vezes - 1), "-i", str(video), "-map", "0", "-c", "copy",
             str(destino)],
            check=True,
        )
    return destino


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("video", type=Path)
    parser.add_argument("-m", "--modelo", default="preciso")
    parser.add_argument("--repetir", type=int, default=1)
    parser.add_argument(
        "--bloco", type=float, default=None,
        help="duração dos blocos do Whisper em segundos (para testar as fronteiras)",
    )
    args = parser.parse_args()
    if args.bloco:
        from transcritor import motor_whisper

        motor_whisper.BLOCO_S = args.bloco

    video = _repetido(args.video, args.repetir) if args.repetir > 1 else args.video
    tempos: dict[str, float] = {}
    _instrumentar(tempos)

    picos = {"rss": 0, "vram": 0}
    parar = threading.Event()

    def amostrar() -> None:
        while not parar.is_set():
            picos["rss"] = max(picos["rss"], _rss_mb())
            picos["vram"] = max(picos["vram"], _vram_mb())
            parar.wait(0.5)

    threading.Thread(target=amostrar, daemon=True).start()

    avisos: list[float] = []
    inicio = time.perf_counter()
    resultado = pipeline.executar(
        pipeline.Configuracao(
            video=video, destino=PASTA / "saida" / video.stem, modelo_fala=args.modelo
        ),
        lambda mensagem, fracao: avisos.append(time.perf_counter()),
    )
    total = time.perf_counter() - inicio
    parar.set()

    marcos = [inicio, *avisos, inicio + total]
    print(json.dumps({
        "video": video.name,
        "modelo": args.modelo,
        "total_s": round(total, 1),
        "etapas_s": tempos,
        "falas": len(resultado.falas),
        "duracao_audio_s": round(resultado.duracao, 1),
        "pico_rss_mb": picos["rss"],
        "pico_vram_mb": picos["vram"],
        "maior_intervalo_s": round(max(b - a for a, b in zip(marcos, marcos[1:])), 1),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
