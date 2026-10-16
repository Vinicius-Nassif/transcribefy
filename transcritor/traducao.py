"""Tradução da transcrição: offline com modelos do Argos, ou online pelo Google.

**Offline (padrão).** Os modelos do [Argos Translate](https://www.argosopentech.com/)
são CTranslate2 — o mesmo runtime do Whisper — mais um tokenizador
SentencePiece. Usamos os pacotes direto, sem a biblioteca `argostranslate`, que
traria torch, spaCy e stanza (vários GB) só para dividir sentenças: as falas já
chegam curtas. "Idioma disponível na máquina" quer dizer exatamente isto: há
pacote instalado de `origem` para o idioma, direto ou passando pelo inglês.

**Online (opcional).** O Google Tradutor, via `deep_translator`, cobre bem mais
idiomas, mas o texto sai da máquina e há limite de requisições. Só é oferecido
quando responde, e as linhas vão em lotes, não uma requisição por linha.

Os dois expõem a mesma coisa: uma função `list[str] -> list[str | None]`, em
que None marca a linha que não pôde ser traduzida.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import modelos

INDICE = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"
PIVO = "en"
GOOGLE = "google"
OFFLINE = "offline"

# Um lote para o Google: as linhas vão juntas, separadas por quebra de linha.
LOTE_GOOGLE_CARACTERES = 4500

Traduzir = Callable[[list[str]], list[str | None]]

# Nomes em português dos idiomas mais comuns; os demais ficam com o nome do pacote.
NOMES = {
    "ar": "Árabe", "de": "Alemão", "en": "Inglês", "es": "Espanhol", "fr": "Francês",
    "hi": "Hindi", "it": "Italiano", "ja": "Japonês", "ko": "Coreano", "nl": "Holandês",
    "pl": "Polonês", "pt": "Português", "ru": "Russo", "sv": "Sueco", "tr": "Turco",
    "uk": "Ucraniano", "zh": "Chinês", "zh-CN": "Chinês (simplificado)",
    "zh-TW": "Chinês (tradicional)", "ca": "Catalão", "gl": "Galego", "eo": "Esperanto",
    "el": "Grego", "he": "Hebraico", "cs": "Tcheco", "da": "Dinamarquês", "fi": "Finlandês",
    "hu": "Húngaro", "id": "Indonésio", "ro": "Romeno", "th": "Tailandês", "vi": "Vietnamita",
}


def nome(codigo: str, padrao: str | None = None) -> str:
    return NOMES.get(codigo) or padrao or codigo


@dataclass(frozen=True, slots=True)
class Pacote:
    origem: str
    destino: str
    caminho: Path


@dataclass(frozen=True, slots=True)
class Idioma:
    """Uma opção de tradução para a interface: `valor` é o que volta no pedido."""

    codigo: str
    nome: str
    servico: str  # OFFLINE ou GOOGLE

    @property
    def valor(self) -> str:
        return self.codigo if self.servico == OFFLINE else f"{GOOGLE}:{self.codigo}"


def pasta(diretorio: Path | None = None) -> Path:
    """Onde os pacotes ficam: junto do cache dos modelos de fala."""
    return (diretorio or modelos.diretorio_padrao()) / "traducao"


def instalados(diretorio: Path | None = None) -> list[Pacote]:
    pacotes = []
    for metadado in sorted(pasta(diretorio).glob("*/metadata.json")):
        try:
            dados = json.loads(metadado.read_text(encoding="utf-8"))
            pacotes.append(Pacote(dados["from_code"], dados["to_code"], metadado.parent))
        except (OSError, ValueError, KeyError):
            continue
    return pacotes


def rotas(origem: str, pacotes: Sequence[Pacote]) -> dict[str, list[Pacote]]:
    """Destinos alcançáveis a partir de `origem`: direto, ou passando pelo inglês."""
    diretos = {p.destino: p for p in pacotes if p.origem == origem}
    caminhos = {destino: [pacote] for destino, pacote in diretos.items()}
    if PIVO in diretos:
        for pacote in pacotes:
            if pacote.origem == PIVO and pacote.destino not in (origem, *caminhos):
                caminhos[pacote.destino] = [diretos[PIVO], pacote]
    return caminhos


# --- Offline -------------------------------------------------------------------

_abertos: dict[Path, tuple[object, object]] = {}
_trava = threading.Lock()


def _abrir(pacote: Pacote):
    with _trava:
        if pacote.caminho not in _abertos:
            import ctranslate2
            import sentencepiece

            # CPU: a VRAM já está com o Whisper, e a tradução é rápida o bastante
            # (~15 ms por fala, meio minuto numa sessão de três horas).
            tradutor = ctranslate2.Translator(
                str(pacote.caminho / "model"), device="cpu", compute_type="int8"
            )
            tokenizador = sentencepiece.SentencePieceProcessor(
                model_file=str(pacote.caminho / "sentencepiece.model")
            )
            _abertos[pacote.caminho] = (tradutor, tokenizador)
        return _abertos[pacote.caminho]


def _passo_offline(pacote: Pacote, linhas: list[str]) -> list[str]:
    tradutor, tokenizador = _abrir(pacote)
    tokens = [tokenizador.encode(linha, out_type=str) for linha in linhas]
    resultados = tradutor.translate_batch(tokens, beam_size=2, max_batch_size=32)
    return ["".join(r.hypotheses[0]).replace("▁", " ").strip() for r in resultados]


def tradutor_offline(caminho: Sequence[Pacote]) -> Traduzir:
    def traduzir(linhas: list[str]) -> list[str | None]:
        for pacote in caminho:
            linhas = _passo_offline(pacote, linhas)
        return list(linhas)

    return traduzir


# --- Online --------------------------------------------------------------------

_google: dict[str, float | bool] = {"quando": 0.0, "ok": False}


def google_disponivel(validade: float = 60.0) -> bool:
    """O Google Tradutor responde? A resposta vale por `validade` segundos."""
    agora = time.monotonic()
    if agora - float(_google["quando"]) < validade:
        return bool(_google["ok"])
    import requests

    try:
        ok = requests.head("https://translate.google.com", timeout=2).status_code < 500
    except requests.RequestException:
        ok = False
    _google.update(quando=agora, ok=ok)
    return ok


def idiomas_google() -> dict[str, str]:
    """{código: nome em inglês} dos idiomas do Google (lista local, sem rede)."""
    from deep_translator.constants import GOOGLE_LANGUAGES_TO_CODES

    return {codigo: nome_en.capitalize() for nome_en, codigo in GOOGLE_LANGUAGES_TO_CODES.items()}


def _lotes(linhas: list[str]) -> list[list[int]]:
    lotes, atual, tamanho = [], [], 0
    for i, linha in enumerate(linhas):
        if atual and tamanho + len(linha) + 1 > LOTE_GOOGLE_CARACTERES:
            lotes.append(atual)
            atual, tamanho = [], 0
        atual.append(i)
        tamanho += len(linha) + 1
    if atual:
        lotes.append(atual)
    return lotes


def tradutor_google(origem: str, destino: str) -> Traduzir:
    def traduzir(linhas: list[str]) -> list[str | None]:
        from deep_translator import GoogleTranslator

        google = GoogleTranslator(source=origem or "auto", target=destino)
        saida: list[str | None] = [None] * len(linhas)
        limpas = [" ".join(linha.split()) for linha in linhas]
        for lote in _lotes(limpas):
            try:
                partes = google.translate("\n".join(limpas[i] for i in lote)).split("\n")
            except Exception:  # noqa: BLE001 - linha não traduzida volta como None
                partes = []
            if len(partes) == len(lote):
                for i, parte in zip(lote, partes, strict=True):
                    saida[i] = parte.strip()
                continue
            for i in lote:  # o lote não voltou alinhado: uma por uma
                try:
                    saida[i] = google.translate(limpas[i])
                except Exception:  # noqa: BLE001
                    saida[i] = None
        return saida

    return traduzir


# --- Escolha -------------------------------------------------------------------


def disponiveis(origem: str, online: bool | None = None, diretorio: Path | None = None) -> list[Idioma]:
    """Os idiomas para os quais dá para traduzir agora, a partir de `origem`.

    Os offline vêm primeiro. Os do Google só entram se ele estiver respondendo
    (`online=None` consulta; True/False força) e se o idioma não estiver
    disponível offline.
    """
    pacotes = instalados(diretorio)
    offline = [
        Idioma(destino, nome(destino), OFFLINE)
        for destino in sorted(rotas(origem, pacotes), key=nome)
        if destino != origem
    ]
    if online is None:
        online = google_disponivel()
    if not online:
        return offline
    ja = {i.codigo for i in offline}
    return offline + [
        Idioma(codigo, nome(codigo, nome_en), GOOGLE)
        for codigo, nome_en in sorted(idiomas_google().items(), key=lambda c: nome(c[0], c[1]))
        if codigo not in ja and codigo != origem
    ]


def tradutor(valor: str, origem: str, diretorio: Path | None = None) -> Traduzir:
    """O tradutor para um valor do pedido: "en" (offline) ou "google:en"."""
    if valor.startswith(f"{GOOGLE}:"):
        return tradutor_google(origem, valor.split(":", 1)[1])
    caminho = rotas(origem, instalados(diretorio)).get(valor)
    if caminho is None:
        raise ValueError(
            f"Não há modelo de tradução de {nome(origem)} para {nome(valor)} instalado. "
            f"Instale com: transcritor idiomas --instalar {origem}-{valor}"
        )
    return tradutor_offline(caminho)


def codigo(valor: str) -> str:
    """O código do idioma de um valor do pedido ("google:en" -> "en")."""
    return valor.split(":", 1)[1] if valor.startswith(f"{GOOGLE}:") else valor


# --- Instalação ----------------------------------------------------------------


def indice() -> list[dict]:
    import requests

    resposta = requests.get(INDICE, timeout=30)
    resposta.raise_for_status()
    return resposta.json()


def instalar(origem: str, destino: str, diretorio: Path | None = None) -> list[Pacote]:
    """Baixa os pacotes para traduzir de `origem` para `destino` (com pivô se preciso)."""
    catalogo = {(p["from_code"], p["to_code"]): p for p in indice()}
    if (origem, destino) in catalogo:
        pares = [(origem, destino)]
    elif (origem, PIVO) in catalogo and (PIVO, destino) in catalogo:
        pares = [(origem, PIVO), (PIVO, destino)]
    else:
        raise ValueError(f"Não há modelo offline de {nome(origem)} para {nome(destino)}.")

    base = pasta(diretorio)
    base.mkdir(parents=True, exist_ok=True)
    ja = {(p.origem, p.destino) for p in instalados(diretorio)}
    for par in pares:
        if par in ja:
            continue
        item = catalogo[par]
        arquivo = base / f"{item['code']}.argosmodel"
        modelos._baixar(item["links"][0], arquivo, f"{par[0]}→{par[1]}")
        temporario = base / f".extraindo-{item['code']}"
        shutil.rmtree(temporario, ignore_errors=True)
        with zipfile.ZipFile(arquivo) as zf:
            zf.extractall(temporario)
        (raiz,) = [p for p in temporario.iterdir() if p.is_dir()]
        destino_final = base / item["code"]
        shutil.rmtree(destino_final, ignore_errors=True)
        raiz.replace(destino_final)
        shutil.rmtree(temporario, ignore_errors=True)
        arquivo.unlink(missing_ok=True)
    return instalados(diretorio)
