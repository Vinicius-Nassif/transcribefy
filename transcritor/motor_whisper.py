"""Reconhecimento de fala com Whisper (via faster-whisper/CTranslate2).

É o motor padrão. Diferente de um reconhecedor por n-gramas, o Whisper decodifica
janelas de 30 s levando em conta o que já transcreveu, então devolve texto
pontuado, com maiúsculas e com as palavras escolhidas pelo contexto da frase —
que é justamente o que falta num modelo compacto.

Aqui não se calcula nada de locutor: os vetores de voz saem de
`transcritor.vozes`, para que a separação de vozes independa do motor de texto.
"""

from __future__ import annotations

import ctypes
import os
import re
import sysconfig
import unicodedata
import wave
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

from .fala import Fala

Progresso = Callable[[float], None]

# Uma pausa maior que isto dentro de um segmento do Whisper separa duas falas.
# O Whisper junta frases distantes num segmento só quando há silêncio no meio;
# quebrar aqui mantém os carimbos de tempo colados na fala de verdade.
PAUSA_INTERNA = 1.5

# Silêncio mínimo que o filtro de voz (VAD) considera uma pausa, em milissegundos.
SILENCIO_MINIMO_MS = 500

# Em trechos sem fala o Whisper às vezes inventa o crédito de quem legendou o
# vídeo em que ele foi treinado. Só descartamos a fala quando o texto inteiro é
# um desses — nunca um pedaço.
#
# A lista é deliberadamente curta e só tem crédito de legenda: nenhuma dessas
# frases aparece numa mesa de RPG. Bordões comuns ("obrigado", "tchau", "até a
# próxima") ficaram de fora de propósito — são coisas que alguém realmente diz,
# muitas vezes por cima de outra pessoa, e descartar uma delas custa uma fala
# verdadeira. Na dúvida, o texto fica.
ALUCINACOES = {
    "legendas pela comunidade amara org",
    "legendado pela comunidade amara org",
    "legendas pelo amara org",
    "legendas pela amara org",
    "subtitles by the amara org community",
    "legendas by the amara org community",
}

# Um modelo aberto por vez. As duas faixas de um vídeo usam o mesmo, então o cache
# de tamanho 1 evita recarregar 3 GB no meio da execução; guardar mais de um
# encheria a memória da GPU quando a interface web alterna entre modelos.
_modelo_aberto: tuple[tuple[str, str, str], object] | None = None


# Handles de `os.add_dll_directory` (Windows): soltá-los desfaz o registro, então
# eles precisam viver enquanto o processo viver.
_diretorios_dll: list = []
_cuda_preparada = False

# Componentes CUDA que o CTranslate2 procura, na ordem de dependência.
COMPONENTES_CUDA = ("cublas", "cuda_nvrtc", "cudnn")


def _carregar_bibliotecas_cuda() -> None:
    """Deixa as bibliotecas CUDA instaladas via pip visíveis para o CTranslate2.

    O CTranslate2 procura `cublas`/`cudnn` pelo carregador do sistema, que não
    enxerga os pacotes `nvidia-*` dentro do venv. Resolver isso aqui evita ter
    de preparar o ambiente (LD_LIBRARY_PATH, PATH) antes de cada comando.

    Os dois sistemas guardam os arquivos em lugares diferentes e carregam de
    formas diferentes: no Linux são `.so` em `lib/`, pré-carregados com
    RTLD_GLOBAL em duas passagens porque dependem uns dos outros; no Windows são
    `.dll` em `bin/`, e basta registrar o diretório no carregador.
    """
    global _cuda_preparada

    if _cuda_preparada:
        return
    _cuda_preparada = True

    raiz = Path(sysconfig.get_paths()["purelib"]) / "nvidia"
    if os.name == "nt":
        for componente in COMPONENTES_CUDA:
            pasta = raiz / componente / "bin"
            if pasta.is_dir():
                _diretorios_dll.append(os.add_dll_directory(str(pasta)))
        return

    arquivos = [
        arquivo
        for componente in COMPONENTES_CUDA
        for arquivo in sorted((raiz / componente / "lib").glob("*.so*"))
    ]
    for _ in range(2):
        for arquivo in arquivos:
            try:
                ctypes.CDLL(str(arquivo), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                continue


def escolher_dispositivo(preferencia: str = "auto") -> tuple[str, str]:
    """Decide onde rodar e em que precisão, devolvendo (dispositivo, precisão).

    `auto` usa a GPU quando há uma utilizável e cai para a CPU quando não há.
    A queda para a CPU é silenciosa de propósito: o resultado é o mesmo, só mais
    lento, e não faz sentido interromper uma transcrição de três horas por isso.
    """
    if preferencia == "cpu":
        return "cpu", "int8"

    import ctranslate2

    if ctranslate2.get_cuda_device_count() < 1:
        if preferencia == "cuda":
            raise RuntimeError(
                "Nenhuma GPU NVIDIA visível. Use --dispositivo cpu ou verifique "
                "os drivers (no WSL, o CUDA vem do driver do Windows)."
            )
        return "cpu", "int8"

    _carregar_bibliotecas_cuda()
    if "int8_float16" in ctranslate2.get_supported_compute_types("cuda"):
        return "cuda", "int8_float16"
    return "cuda", "float16"


def _abrir_modelo(caminho: Path, dispositivo: str, precisao: str):
    """Carrega o modelo, reaproveitando o que já estiver aberto."""
    global _modelo_aberto

    chave = (str(caminho), dispositivo, precisao)
    if _modelo_aberto is not None and _modelo_aberto[0] == chave:
        return _modelo_aberto[1]

    from faster_whisper import WhisperModel

    _modelo_aberto = None  # libera o anterior antes de alocar o novo
    _modelo_aberto = (
        chave, WhisperModel(str(caminho), device=dispositivo, compute_type=precisao)
    )
    return _modelo_aberto[1]


def _duracao(wav: Path) -> float:
    with wave.Wave_read(str(wav)) as onda:
        return onda.getnframes() / (onda.getframerate() or 1)


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto.casefold())
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", sem_acento).strip()


def _e_alucinacao(texto: str) -> bool:
    return _normalizar(texto) in ALUCINACOES


def _montar(bloco: Sequence, faixa: int) -> Fala | None:
    texto = "".join(p.word for p in bloco).strip()
    if not texto or _e_alucinacao(texto):
        return None
    return Fala(
        inicio=float(bloco[0].start),
        fim=float(bloco[-1].end),
        texto=texto,
        faixa=faixa,
        palavras=[
            {
                "word": p.word.strip(),
                "start": round(float(p.start), 3),
                "end": round(float(p.end), 3),
                "conf": round(float(p.probability), 3),
            }
            for p in bloco
        ],
    )


def _quebrar(segmento, faixa: int) -> Iterator[Fala]:
    """Converte um segmento do Whisper em uma ou mais falas.

    Os tempos vêm das palavras, não das bordas do segmento: o Whisper estende a
    borda até o próximo trecho com voz, o que criaria falas de dezenas de
    segundos para uma frase de dois.
    """
    palavras = [p for p in (segmento.words or []) if p.word.strip()]
    if not palavras:
        texto = segmento.text.strip()
        if texto and not _e_alucinacao(texto):
            yield Fala(
                inicio=float(segmento.start), fim=float(segmento.end),
                texto=texto, faixa=faixa,
            )
        return

    bloco = [palavras[0]]
    for palavra in palavras[1:]:
        if float(palavra.start) - float(bloco[-1].end) > PAUSA_INTERNA:
            fala = _montar(bloco, faixa)
            if fala is not None:
                yield fala
            bloco = []
        bloco.append(palavra)
    fala = _montar(bloco, faixa)
    if fala is not None:
        yield fala


def transcrever(
    wav: Path,
    modelo: Path,
    faixa: int = 0,
    idioma: str | None = "pt",
    contexto: str = "",
    dispositivo: str = "auto",
    progresso: Progresso | None = None,
) -> list[Fala]:
    """Transcreve um WAV mono 16 kHz e devolve as falas com tempo e palavras.

    Args:
        wav: arquivo WAV mono PCM 16 bits (use `transcricao.preparar_wav`).
        modelo: diretório do modelo Whisper convertido para CTranslate2.
        faixa: número da faixa de origem, apenas para rotular as falas.
        idioma: código do idioma do áudio; `None` ou "auto" deixa o modelo detectar.
        contexto: nomes próprios, termos e jargão da mesa. Entra como prompt
            inicial e enviesa a escolha das palavras parecidas.
        dispositivo: "auto", "cuda" ou "cpu".
        progresso: callback que recebe a fração concluída (0.0 a 1.0).
    """
    wav = Path(wav)
    if not wav.is_file():
        raise FileNotFoundError(f"{wav} não encontrado")

    dispositivo, precisao = escolher_dispositivo(dispositivo)
    modelo_aberto = _abrir_modelo(Path(modelo), dispositivo, precisao)
    duracao = _duracao(wav) or 1.0

    segmentos, _ = modelo_aberto.transcribe(
        str(wav),
        language=None if idioma in (None, "", "auto") else idioma,
        beam_size=5,
        # O contexto é o motivo de trocar de motor: cada janela é decodificada
        # sabendo o que veio antes, e é isso que corrige palavra por semelhança.
        condition_on_previous_text=True,
        initial_prompt=contexto.strip() or None,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": SILENCIO_MINIMO_MS},
    )

    falas: list[Fala] = []
    for segmento in segmentos:  # gerador preguiçoso: o progresso sai daqui
        falas.extend(_quebrar(segmento, faixa))
        if progresso is not None:
            progresso(min(1.0, float(segmento.end) / duracao))

    if progresso is not None:
        progresso(1.0)
    return falas
