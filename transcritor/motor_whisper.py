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
import importlib.util
import os
import re
import unicodedata
import wave
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .fala import Fala

Progresso = Callable[[float], None]

# Uma pausa maior que isto dentro de um segmento do Whisper separa duas falas.
# O Whisper junta frases distantes num segmento só quando há silêncio no meio;
# quebrar aqui mantém os carimbos de tempo colados na fala de verdade.
PAUSA_INTERNA = 1.5

# Silêncio mínimo que o filtro de voz (VAD) considera uma pausa, em milissegundos.
SILENCIO_MINIMO_MS = 500

# O áudio é entregue ao Whisper em blocos desta duração, em segundos. Inteiro, o
# faster-whisper decodifica a faixa toda em float32 e roda o filtro de voz sobre
# ela de uma vez: a memória crescia ~12 MB por minuto (2,4 GB numa sessão de 3 h)
# e o primeiro aviso de progresso só saía depois de mais de um minuto.
BLOCO_S = 600.0

# O corte entre blocos cai no ponto mais silencioso a até esta distância do
# alvo, para não partir uma palavra ao meio.
MARGEM_CORTE_S = 30.0
JANELA_SILENCIO_S = 0.25

# Cada bloco recebe só o contexto da mesa como prompt, e não o fim do bloco
# anterior. Medido em docs/arquitetura.md (“Whisper em blocos”):
# repassar a cauda fazia o Whisper pular minutos inteiros (similaridade 0,73),
# enquanto o contexto sozinho ficou igual ao de bloco único (0,94 contra 0,93).

# Com idioma "auto", o idioma detectado num bloco só passa a valer para os
# seguintes com esta confiança e com pelo menos esta fala (depois do filtro de
# voz). Um bloco que abre com música ou silêncio dá um palpite fraco; travá-lo
# transcreveria a faixa inteira no idioma errado.
CONFIANCA_IDIOMA = 0.8
FALA_MINIMA_IDIOMA_S = 30.0

# Taxa de amostragem que o Whisper espera — a mesma em que as faixas são extraídas.
TAXA = 16_000

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
_diagnostico_cuda: DiagnosticoCuda | None = None

# Componentes CUDA que o CTranslate2 procura, na ordem de dependência.
COMPONENTES_CUDA = ("cublas", "cuda_nvrtc", "cudnn")

# As bibliotecas sem as quais o CTranslate2 falha na GPU — e com os nomes exatos
# pelos quais ele as procura. Conferir por nome, e não por arquivo em disco, é o
# que reproduz a busca que ele faz: só assim sabemos que vai encontrá-las.
ESSENCIAIS_WINDOWS = ("cublas64_12.dll", "cudnn64_9.dll")
ESSENCIAIS_LINUX = ("libcublas.so.12", "libcudnn.so.9")


def _essenciais() -> tuple[str, ...]:
    return ESSENCIAIS_WINDOWS if os.name == "nt" else ESSENCIAIS_LINUX


def _carregar(alvo: str) -> None:
    """Carrega uma biblioteca por caminho ou por nome, como o sistema exige."""
    if os.name == "nt":
        ctypes.WinDLL(alvo)
    else:
        ctypes.CDLL(alvo, mode=ctypes.RTLD_GLOBAL)


def _pasta_dos_pacotes_nvidia() -> Path | None:
    """Onde o pip pôs os pacotes `nvidia-*`, perguntando ao importador.

    Deduzir o caminho a partir de `sysconfig` erra em ambientes que não seguem o
    layout padrão; o importador sabe a resposta certa por construção.
    """
    try:
        especificacao = importlib.util.find_spec("nvidia")
    except (ImportError, ValueError):
        return None
    if especificacao is None or not especificacao.submodule_search_locations:
        return None
    return Path(next(iter(especificacao.submodule_search_locations)))


def _carregar_bibliotecas_cuda() -> tuple[Path | None, tuple[str, ...]]:
    """Torna as bibliotecas CUDA do pip visíveis e devolve (pasta, o que falta).

    O CTranslate2 procura `cublas`/`cudnn` pelo carregador do sistema, que não
    enxerga os pacotes `nvidia-*` dentro do venv. Resolver isso aqui evita ter de
    preparar o ambiente (LD_LIBRARY_PATH, PATH) antes de cada comando.

    No Windows não basta registrar o diretório: fazemos as três coisas que o
    carregador aceita — registrar a pasta, pô-la no PATH e pré-carregar cada DLL
    pelo caminho absoluto. É a última que garante o resultado, porque uma
    biblioteca já carregada é encontrada pelo nome sem busca nenhuma.

    Em ambos os sistemas o carregamento é feito em duas passagens: as
    bibliotecas dependem umas das outras e a ordem alfabética não respeita isso.
    """
    raiz = _pasta_dos_pacotes_nvidia()
    if raiz is not None:
        subpasta, padrao = ("bin", "*.dll") if os.name == "nt" else ("lib", "*.so*")
        arquivos = [
            arquivo
            for componente in COMPONENTES_CUDA
            for arquivo in sorted((raiz / componente / subpasta).glob(padrao))
        ]
        if os.name == "nt":
            for pasta in sorted({arquivo.parent for arquivo in arquivos}):
                _diretorios_dll.append(os.add_dll_directory(str(pasta)))
                os.environ["PATH"] = f"{pasta}{os.pathsep}{os.environ.get('PATH', '')}"
        for _ in range(2):
            for arquivo in arquivos:
                try:
                    _carregar(str(arquivo))
                except OSError:
                    continue

    faltando = []
    for nome in _essenciais():
        try:
            _carregar(nome)
        except OSError:
            faltando.append(nome)
    return raiz, tuple(faltando)


@dataclass(frozen=True, slots=True)
class DiagnosticoCuda:
    """O que separa 'tem GPU' de 'dá para usar a GPU'."""

    gpus: int
    pasta_bibliotecas: Path | None
    faltando: tuple[str, ...]

    @property
    def utilizavel(self) -> bool:
        return self.gpus > 0 and not self.faltando

    @property
    def explicacao(self) -> str:
        if self.gpus < 1:
            return "Nenhuma GPU NVIDIA visível."
        if not self.faltando:
            return f"{self.gpus} GPU(s) NVIDIA prontas para uso."
        ausentes = ", ".join(self.faltando)
        if self.pasta_bibliotecas is None:
            return (
                f"Há {self.gpus} GPU(s) NVIDIA, mas as bibliotecas CUDA não estão "
                f"instaladas ({ausentes}). Instale com:\n"
                "    .venv\\Scripts\\python.exe -m pip install -r requirements-gpu.txt\n"
                "  (no Linux: .venv/bin/python -m pip install -r requirements-gpu.txt)"
            )
        return (
            f"Há {self.gpus} GPU(s) NVIDIA e os pacotes CUDA estão em "
            f"{self.pasta_bibliotecas}, mas o sistema não consegue carregar "
            f"{ausentes}. Costuma ser driver NVIDIA antigo demais para o CUDA 12: "
            "atualize-o (no Windows, pelo GeForce Experience ou pelo site da NVIDIA)."
        )


def diagnosticar_cuda() -> DiagnosticoCuda:
    """Descobre — uma vez por processo — se a GPU pode mesmo ser usada."""
    global _diagnostico_cuda

    if _diagnostico_cuda is not None:
        return _diagnostico_cuda

    import ctranslate2

    gpus = ctranslate2.get_cuda_device_count()
    if gpus < 1:
        _diagnostico_cuda = DiagnosticoCuda(0, None, ())
        return _diagnostico_cuda

    pasta, faltando = _carregar_bibliotecas_cuda()
    _diagnostico_cuda = DiagnosticoCuda(gpus, pasta, faltando)
    return _diagnostico_cuda


def escolher_dispositivo(preferencia: str = "auto") -> tuple[str, str]:
    """Decide onde rodar e em que precisão, devolvendo (dispositivo, precisão).

    `auto` só devolve "cuda" quando a GPU está de fato utilizável — ter placa não
    basta, as bibliotecas CUDA precisam carregar. Sem elas a queda para a CPU
    acontece aqui, antes de abrir o modelo, em vez de virar um erro no meio de
    uma transcrição de três horas. Quem pediu "cuda" explicitamente recebe o
    motivo em vez da queda silenciosa.
    """
    if preferencia == "cpu":
        return "cpu", "int8"

    diagnostico = diagnosticar_cuda()
    if not diagnostico.utilizavel:
        if preferencia == "cuda":
            raise RuntimeError(diagnostico.explicacao)
        return "cpu", "int8"

    import ctranslate2

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


def carregar(modelo: Path, dispositivo: str = "auto") -> None:
    """Abre o modelo antes de transcrever, para a carga ter etapa própria.

    A frio, o large-v3 leva ~40 s para carregar. Dentro da primeira transcrição,
    esse tempo passaria sem aviso de progresso nenhum.
    """
    _abrir_modelo(Path(modelo), *escolher_dispositivo(dispositivo))


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


def _ponto_mais_silencioso(audio: np.ndarray, taxa: int) -> int:
    """Índice (em amostras) do centro da janela de menor energia."""
    janela = max(1, int(JANELA_SILENCIO_S * taxa))
    quantas = len(audio) // janela
    if quantas < 1:
        return len(audio) // 2
    energia = np.square(audio[: quantas * janela].astype(np.float64)).reshape(quantas, janela)
    return int(energia.mean(axis=1).argmin()) * janela + janela // 2


def blocos(wav: Path) -> Iterator[tuple[float, np.ndarray]]:
    """Lê o WAV em blocos de ~`BLOCO_S`, cortados num silêncio.

    Devolve (segundo em que o bloco começa, amostras float32 em [-1, 1]). Só um
    bloco fica em memória por vez.
    """
    with wave.Wave_read(str(wav)) as onda:
        taxa = onda.getframerate()
        total = onda.getnframes()
        bloco = int(BLOCO_S * taxa)
        margem = int(MARGEM_CORTE_S * taxa)
        inicio = 0
        while inicio < total:
            ultimo = inicio + bloco + margem >= total
            fim_leitura = total if ultimo else inicio + bloco + margem
            onda.setpos(inicio)
            bruto = np.frombuffer(onda.readframes(fim_leitura - inicio), dtype=np.int16)
            audio = bruto.astype(np.float32) / 32768.0
            if ultimo:
                corte = len(audio)
            else:
                busca = audio[bloco - margem :]
                corte = bloco - margem + _ponto_mais_silencioso(busca, taxa)
            yield inicio / taxa, audio[:corte]
            inicio += corte


def _deslocar(fala: Fala, segundos: float) -> Fala:
    fala.inicio += segundos
    fala.fim += segundos
    for palavra in fala.palavras:
        palavra["start"] = round(palavra["start"] + segundos, 3)
        palavra["end"] = round(palavra["end"] + segundos, 3)
    return fala


def transcrever(
    wav: Path,
    modelo: Path,
    faixa: int = 0,
    idioma: str | None = "pt",
    contexto: str = "",
    dispositivo: str = "auto",
    progresso: Progresso | None = None,
    ao_detectar_idioma: Callable[[str], None] | None = None,
) -> list[Fala]:
    """Transcreve um WAV mono 16 kHz e devolve as falas com tempo e palavras.

    O áudio vai ao modelo em blocos (ver `BLOCO_S`): a memória não cresce com a
    duração da sessão, e o progresso avança desde o primeiro bloco. Todo bloco
    recebe o contexto da mesa como prompt.

    Args:
        wav: arquivo WAV mono PCM 16 bits (use `transcricao.preparar_wav`).
        modelo: diretório do modelo Whisper convertido para CTranslate2.
        faixa: número da faixa de origem, apenas para rotular as falas.
        idioma: código do idioma do áudio; `None` ou "auto" deixa o modelo
            detectar. A detecção é refeita a cada bloco até sair confiável
            (ver `CONFIANCA_IDIOMA`); daí em diante o idioma fica fixo.
        contexto: nomes próprios, termos e jargão da mesa. Entra como prompt
            inicial e enviesa a escolha das palavras parecidas.
        dispositivo: "auto", "cuda" ou "cpu".
        progresso: callback que recebe a fração concluída (0.0 a 1.0).
        ao_detectar_idioma: recebe, ao fim, o idioma detectado (o confiável,
            ou o palpite de maior confiança), quando `idioma` é "auto".
    """
    wav = Path(wav)
    if not wav.is_file():
        raise FileNotFoundError(f"{wav} não encontrado")

    dispositivo, precisao = escolher_dispositivo(dispositivo)
    modelo_aberto = _abrir_modelo(Path(modelo), dispositivo, precisao)
    duracao = _duracao(wav) or 1.0
    lingua = None if idioma in (None, "", "auto") else idioma
    detectar = lingua is None
    palpite: tuple[float, str | None] = (-1.0, None)
    prompt = contexto.strip() or None

    falas: list[Fala] = []
    for deslocamento, audio in blocos(wav):
        segmentos, info = modelo_aberto.transcribe(
            audio,
            language=lingua,
            beam_size=5,
            # O contexto é o motivo de usar o Whisper: cada janela é decodificada
            # sabendo o que veio antes, e é isso que corrige palavra por semelhança.
            condition_on_previous_text=True,
            initial_prompt=prompt,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": SILENCIO_MINIMO_MS},
        )
        for segmento in segmentos:  # gerador preguiçoso: o progresso sai daqui
            falas.extend(_deslocar(f, deslocamento) for f in _quebrar(segmento, faixa))
            if progresso is not None:
                progresso(min(1.0, (deslocamento + float(segmento.end)) / duracao))
        if progresso is not None:
            progresso(min(1.0, (deslocamento + len(audio) / TAXA) / duracao))
        if lingua is None:
            confianca = float(info.language_probability or 0.0)
            if confianca > palpite[0]:
                palpite = (confianca, info.language)
            fala = float(getattr(info, "duration_after_vad", 0.0) or 0.0)
            if confianca >= CONFIANCA_IDIOMA and fala >= FALA_MINIMA_IDIOMA_S:
                lingua = info.language

    if detectar and ao_detectar_idioma is not None and (lingua or palpite[1]):
        ao_detectar_idioma(lingua or palpite[1])
    if progresso is not None:
        progresso(1.0)
    return falas
