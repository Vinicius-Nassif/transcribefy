"""Motores de tradução da transcrição.

Um motor é qualquer objeto com três coisas: um `nome`, `idiomas()` — os
destinos que ele oferece, `{código: nome em português}` — e
`traduzir(linhas, origem, destino)`, que devolve uma lista do mesmo tamanho, com
None nas linhas que não puderam ser traduzidas. `saida` só conversa com essa
interface, e a interface web monta a lista de idiomas a partir de `idiomas()`
do motor ativo. Trocar de motor é mudar `PADRAO`; somar um — por exemplo, um
offline — é registrar mais uma entrada em `MOTORES`.

O único motor por ora é o Google Tradutor, pelo `deep-translator`. É online: o
texto sai da máquina, e há limite de requisições. Por isso as linhas vão em
lotes, e não uma requisição por linha.
"""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Callable, Sequence
from typing import Protocol

log = logging.getLogger(__name__)

# Nomes em português de todos os idiomas que algum motor oferece. Começou com a
# tabela do branch evolucao-faixas-e-desempenho; um código sem nome aqui aparece
# com o nome que o próprio motor dá.
NOMES = {
    "af": "Africâner", "ak": "Akan", "am": "Amárico", "ar": "Árabe",
    "as": "Assamês", "ay": "Aimará", "az": "Azerbaijano", "be": "Bielorrusso",
    "bg": "Búlgaro", "bho": "Bhojpuri", "bm": "Bambara", "bn": "Bengali",
    "bs": "Bósnio", "ca": "Catalão", "ceb": "Cebuano", "ckb": "Curdo (sorani)",
    "co": "Corso", "cs": "Tcheco", "cy": "Galês", "da": "Dinamarquês",
    "de": "Alemão", "doi": "Dogri", "dv": "Divehi", "ee": "Jeje", "el": "Grego",
    "en": "Inglês", "eo": "Esperanto", "es": "Espanhol", "et": "Estoniano",
    "eu": "Basco", "fa": "Persa", "fi": "Finlandês", "fr": "Francês",
    "fy": "Frísio", "ga": "Irlandês", "gd": "Gaélico escocês", "gl": "Galego",
    "gn": "Guarani", "gom": "Concani", "gu": "Guzerate", "ha": "Hauçá",
    "haw": "Havaiano", "he": "Hebraico", "hi": "Hindi", "hmn": "Hmong",
    "hr": "Croata", "ht": "Crioulo haitiano", "hu": "Húngaro", "hy": "Armênio",
    "id": "Indonésio", "ig": "Igbo", "ilo": "Ilocano", "is": "Islandês",
    "it": "Italiano", "iw": "Hebraico", "ja": "Japonês", "jw": "Javanês",
    "ka": "Georgiano", "kk": "Cazaque", "km": "Khmer", "kn": "Canarês",
    "ko": "Coreano", "kri": "Krio", "ku": "Curdo (kurmanji)", "ky": "Quirguiz",
    "la": "Latim", "lb": "Luxemburguês", "lg": "Luganda", "ln": "Lingala",
    "lo": "Laosiano", "lt": "Lituano", "lus": "Mizo", "lv": "Letão",
    "mai": "Maithili", "mg": "Malgaxe", "mi": "Maori", "mk": "Macedônio",
    "ml": "Malaiala", "mn": "Mongol", "mni-Mtei": "Manipuri", "mr": "Marati",
    "ms": "Malaio", "mt": "Maltês", "my": "Birmanês", "ne": "Nepalês",
    "nl": "Holandês", "no": "Norueguês", "nso": "Sepedi", "ny": "Chichewa",
    "om": "Oromo", "or": "Oriá", "pa": "Punjabi", "pl": "Polonês",
    "ps": "Pashto", "pt": "Português", "qu": "Quíchua", "ro": "Romeno",
    "ru": "Russo", "rw": "Kinyarwanda", "sa": "Sânscrito", "sd": "Sindi",
    "si": "Cingalês", "sk": "Eslovaco", "sl": "Esloveno", "sm": "Samoano",
    "sn": "Xona", "so": "Somali", "sq": "Albanês", "sr": "Sérvio",
    "st": "Sesoto", "su": "Sundanês", "sv": "Sueco", "sw": "Suaíli",
    "ta": "Tâmil", "te": "Telugo", "tg": "Tadjique", "th": "Tailandês",
    "ti": "Tigrínia", "tk": "Turcomeno", "tl": "Filipino", "tr": "Turco",
    "ts": "Tsonga", "tt": "Tártaro", "ug": "Uigur", "uk": "Ucraniano",
    "ur": "Urdu", "uz": "Uzbeque", "vi": "Vietnamita", "xh": "Xhosa",
    "yi": "Iídiche", "yo": "Iorubá", "zh": "Chinês",
    "zh-CN": "Chinês (simplificado)", "zh-TW": "Chinês (tradicional)", "zu": "Zulu",
    # Só o Whisper reconhece estes (idiomas do áudio):
    "ba": "Bashkir", "bo": "Tibetano", "br": "Bretão", "fo": "Feroês",
    "nn": "Norueguês (nynorsk)", "oc": "Occitano", "yue": "Cantonês",
}


def nome(codigo: str, padrao: str | None = None) -> str:
    """Nome em português de um código de idioma."""
    return NOMES.get(codigo) or padrao or codigo


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).casefold()


def em_ordem(idiomas: dict[str, str]) -> list[dict[str, str]]:
    """`[{codigo, nome}]` em ordem alfabética do nome, ignorando acentos."""
    return [
        {"codigo": codigo, "nome": nome_}
        for codigo, nome_ in sorted(idiomas.items(), key=lambda par: _sem_acento(par[1]))
    ]


class Motor(Protocol):
    nome: str
    descricao: str
    online: bool

    def idiomas(self) -> dict[str, str]: ...

    def traduzir(
        self, linhas: Sequence[str], origem: str, destino: str
    ) -> list[str | None]: ...


# --- Google ------------------------------------------------------------------

# Teto de um lote: o Google recusa textos acima de 5.000 caracteres.
LOTE_CARACTERES = 4500
# Lotes seguidos que falham por erro (sem rede, bloqueio) antes de desistir do
# resto: insistir só multiplicaria as falhas e a espera.
FALHAS_SEGUIDAS = 3

Tradutor = Callable[[str], str]


def _lotes(linhas: Sequence[str], limite: int = LOTE_CARACTERES) -> list[list[int]]:
    """Agrupa os índices das linhas em lotes que, unidos por quebra de linha, cabem no limite."""
    lotes: list[list[int]] = []
    atual: list[int] = []
    tamanho = 0
    for i, linha in enumerate(linhas):
        custo = len(linha) + 1
        if atual and tamanho + custo > limite:
            lotes.append(atual)
            atual, tamanho = [], 0
        atual.append(i)
        tamanho += custo
    if atual:
        lotes.append(atual)
    return lotes


def traduzir_em_lotes(
    linhas: Sequence[str], tradutor: Tradutor, limite: int = LOTE_CARACTERES
) -> list[str | None]:
    """Traduz em lotes unidos por quebra de linha, conferindo cada lote.

    Se a quantidade de linhas devolvidas não bater com a enviada — o tradutor
    juntou ou partiu alguma —, não há como saber qual tradução é de qual fala:
    aquele lote é refeito linha por linha. Um lote que falha por erro marca as
    suas linhas como None; após `FALHAS_SEGUIDAS` lotes assim, o resto também.

    Linhas vazias não vão para o tradutor (ele as descartaria e desalinharia o
    lote) e voltam vazias. Quebras de linha dentro de uma fala viram espaço.
    """
    resultado: list[str | None] = [None] * len(linhas)
    a_traduzir = []
    for i, linha in enumerate(linhas):
        limpa = " ".join(linha.split())
        if limpa:
            a_traduzir.append((i, limpa))
        else:
            resultado[i] = linha

    textos = [texto for _, texto in a_traduzir]
    falhas = 0
    for lote in _lotes(textos, limite):
        if falhas >= FALHAS_SEGUIDAS:
            break
        enviadas = [textos[j] for j in lote]
        try:
            devolvidas = tradutor("\n".join(enviadas)).split("\n")
        except Exception as erro:  # noqa: BLE001 - rede, cota, resposta inesperada
            log.warning("Lote de tradução falhou: %s", erro)
            falhas += 1
            continue
        falhas = 0
        if len(devolvidas) == len(enviadas):
            for j, traduzida in zip(lote, devolvidas, strict=True):
                resultado[a_traduzir[j][0]] = traduzida.strip()
            continue
        log.info(
            "Lote devolveu %d linhas para %d enviadas; traduzindo linha a linha",
            len(devolvidas), len(enviadas),
        )
        for j in lote:
            try:
                resultado[a_traduzir[j][0]] = tradutor(textos[j]).strip()
            except Exception as erro:  # noqa: BLE001
                log.warning("Linha de tradução falhou: %s", erro)
    return resultado


class Google:
    """Google Tradutor, via `deep-translator`. Online."""

    nome = "google"
    descricao = "Google Tradutor (online: o texto sai da máquina)"
    online = True

    def idiomas(self) -> dict[str, str]:
        from deep_translator.constants import GOOGLE_LANGUAGES_TO_CODES

        return {
            codigo: nome(codigo, ingles.title())
            for ingles, codigo in GOOGLE_LANGUAGES_TO_CODES.items()
        }

    def traduzir(
        self, linhas: Sequence[str], origem: str, destino: str
    ) -> list[str | None]:
        from deep_translator import GoogleTranslator

        tradutor = GoogleTranslator(source=origem or "auto", target=destino)
        return traduzir_em_lotes(linhas, tradutor.translate)


MOTORES: dict[str, Motor] = {"google": Google()}
PADRAO = "google"


def motor(nome_motor: str | None = None) -> Motor:
    """O motor pedido, ou o ativo (`PADRAO`)."""
    escolhido = nome_motor or PADRAO
    try:
        return MOTORES[escolhido]
    except KeyError:
        raise ValueError(
            f"Motor de tradução desconhecido: '{escolhido}'. "
            f"Disponíveis: {', '.join(MOTORES)}."
        ) from None


def validar_destinos(destinos: Sequence[str], nome_motor: str | None = None) -> list[str]:
    """Normaliza os destinos e recusa os que o motor não oferece."""
    oferecidos = motor(nome_motor).idiomas()
    normalizados = list(dict.fromkeys(d.strip() for d in destinos if d.strip()))
    desconhecidos = [d for d in normalizados if d not in oferecidos]
    if desconhecidos:
        raise ValueError(
            f"Idioma de tradução não disponível: {', '.join(desconhecidos)}. "
            "Veja os códigos em GET /api/idiomas."
        )
    return normalizados
