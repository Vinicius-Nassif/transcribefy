"""Catálogo, download e cache dos modelos de reconhecimento.

Dois motores convivem aqui. Os modelos Whisper vêm do Hugging Face já
convertidos para CTranslate2 e são baixados pela própria biblioteca; os modelos
Vosk são zips no site do projeto. Em ambos os casos o cache é o mesmo diretório,
para que uma única montagem de volume (`TRANSCRITOR_MODELOS`) baste no container.
"""

from __future__ import annotations

import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests
from tqdm import tqdm

BASE_URL_VOSK = "https://alphacephei.com/vosk/models"

MOTOR_WHISPER = "whisper"
MOTOR_VOSK = "vosk"


@dataclass(frozen=True, slots=True)
class Modelo:
    """Uma opção do catálogo: o que é, de onde vem e quanto ocupa."""

    apelido: str
    motor: str
    referencia: str   # id do modelo Whisper no Hugging Face, ou nome do zip Vosk
    descricao: str
    tamanho_mb: int

    @property
    def url(self) -> str:
        if self.motor != MOTOR_VOSK:
            raise ValueError(f"O modelo '{self.apelido}' não é baixado por URL.")
        return f"{BASE_URL_VOSK}/{self.referencia}.zip"


@dataclass(frozen=True, slots=True)
class ModeloPronto:
    """Um modelo já em disco, pronto para ser aberto pelo motor correspondente."""

    apelido: str
    motor: str
    caminho: Path


# Modelos de reconhecimento de fala, por apelido. O Whisper é o padrão porque
# transcreve levando o contexto da frase em conta: sai pontuado e erra bem menos
# as palavras parecidas. O Vosk fica como alternativa leve.
MODELOS_FALA: dict[str, Modelo] = {
    "preciso": Modelo(
        "preciso", MOTOR_WHISPER, "large-v3",
        "Whisper large-v3 — a melhor transcrição; confortável com GPU", 3090,
    ),
    "rapido": Modelo(
        "rapido", MOTOR_WHISPER, "large-v3-turbo",
        "Whisper turbo — quase a precisão do large-v3, várias vezes mais rápido", 1620,
    ),
    "leve": Modelo(
        "leve", MOTOR_WHISPER, "small",
        "Whisper small — opção de CPU sem GPU; erra mais nomes próprios", 490,
    ),
    "vosk-pt": Modelo(
        "vosk-pt", MOTOR_VOSK, "vosk-model-small-pt-0.3",
        "Vosk compacto — muito leve, sem pontuação e com bem mais erros", 31,
    ),
    "vosk-pt-grande": Modelo(
        "vosk-pt-grande", MOTOR_VOSK, "vosk-model-pt-fb-v0.1.1-20220516_2113",
        "Vosk completo — pesado e ainda sem pontuação", 1615,
    ),
}

PADRAO = "preciso"

# Nomes usados antes da entrada do Whisper, mantidos para não quebrar comandos
# e scripts já escritos.
APELIDOS_ANTIGOS = {
    "pt-pequeno": "vosk-pt",
    "pt-grande": "vosk-pt-grande",
    "en-pequeno": "leve",
    "en-grande": "preciso",
}

# Modelo de x-vectors usado para diferenciar locutores pela voz (ver `vozes`).
MODELO_LOCUTOR = Modelo(
    "locutor", MOTOR_VOSK, "vosk-model-spk-0.4",
    "Identificação de locutor (x-vectors)", 13,
)


def diretorio_padrao() -> Path:
    """Diretório onde os modelos são guardados (configurável por TRANSCRITOR_MODELOS)."""
    env = os.environ.get("TRANSCRITOR_MODELOS")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".cache" / "transcritor-rpg" / "modelos"


def _cache_whisper(diretorio: Path) -> Path:
    """Subpasta própria: o formato de cache do Hugging Face não é o do Vosk."""
    return diretorio / "whisper"


def _baixar(url: str, destino: Path, descricao: str) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_suffix(destino.suffix + ".parcial")
    with requests.get(url, stream=True, timeout=60) as resposta:
        resposta.raise_for_status()
        total = int(resposta.headers.get("content-length", 0))
        barra = tqdm(
            total=total, unit="B", unit_scale=True, desc=f"Baixando {descricao}"
        )
        with parcial.open("wb") as arquivo:
            for bloco in resposta.iter_content(chunk_size=1 << 20):
                arquivo.write(bloco)
                barra.update(len(bloco))
        barra.close()
    parcial.replace(destino)


def _garantir_vosk(modelo: Modelo, diretorio: Path) -> Path:
    destino = diretorio / modelo.referencia
    if destino.is_dir():
        return destino

    zip_path = diretorio / f"{modelo.referencia}.zip"
    if not zip_path.is_file():
        print(f"Modelo '{modelo.apelido}' ausente (~{modelo.tamanho_mb} MB). Baixando...")
        _baixar(modelo.url, zip_path, modelo.apelido)

    temporario = diretorio / f".extraindo-{modelo.referencia}"
    if temporario.exists():
        shutil.rmtree(temporario)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(temporario)

    # O zip traz uma única pasta raiz com o nome do modelo.
    conteudo = list(temporario.iterdir())
    raiz = conteudo[0] if len(conteudo) == 1 and conteudo[0].is_dir() else temporario
    raiz.replace(destino)
    shutil.rmtree(temporario, ignore_errors=True)
    zip_path.unlink(missing_ok=True)
    return destino


def _garantir_whisper(modelo: Modelo, diretorio: Path, so_local: bool = False) -> Path:
    from faster_whisper import download_model

    cache = _cache_whisper(diretorio)
    cache.mkdir(parents=True, exist_ok=True)
    if not so_local and not baixado(modelo, diretorio):
        print(f"Modelo '{modelo.apelido}' ausente (~{modelo.tamanho_mb} MB). Baixando...")
    return Path(
        download_model(modelo.referencia, cache_dir=str(cache), local_files_only=so_local)
    )


def garantir_modelo(modelo: Modelo, diretorio: Path | None = None) -> Path:
    """Garante que o modelo esteja em disco e devolve seu caminho.

    Baixa na primeira vez; nas seguintes reaproveita o cache.
    """
    diretorio = diretorio or diretorio_padrao()
    diretorio.mkdir(parents=True, exist_ok=True)
    if modelo.motor == MOTOR_WHISPER:
        return _garantir_whisper(modelo, diretorio)
    return _garantir_vosk(modelo, diretorio)


def baixado(modelo: Modelo, diretorio: Path | None = None) -> bool:
    """True se o modelo já está no cache e nada precisa ser baixado."""
    diretorio = diretorio or diretorio_padrao()
    if modelo.motor == MOTOR_VOSK:
        return (diretorio / modelo.referencia).is_dir()
    try:
        _garantir_whisper(modelo, diretorio, so_local=True)
    except Exception:  # noqa: BLE001 - qualquer falha aqui significa "ainda não baixado"
        return False
    return True


def _motor_do_caminho(caminho: Path) -> str:
    """Descobre o motor olhando o conteúdo de um diretório de modelo."""
    if (caminho / "model.bin").is_file():
        return MOTOR_WHISPER
    if (caminho / "am").is_dir() or (caminho / "conf").is_dir():
        return MOTOR_VOSK
    raise ValueError(
        f"{caminho} não parece um modelo: falta 'model.bin' (Whisper) "
        "ou as pastas 'am'/'conf' (Vosk)."
    )


def resolver_modelo_fala(nome: str, diretorio: Path | None = None) -> ModeloPronto:
    """Aceita um apelido do catálogo, um tamanho do Whisper ou um caminho em disco."""
    nome = (nome or PADRAO).strip()
    apelido = APELIDOS_ANTIGOS.get(nome, nome)

    if apelido in MODELOS_FALA:
        modelo = MODELOS_FALA[apelido]
        return ModeloPronto(apelido, modelo.motor, garantir_modelo(modelo, diretorio))

    caminho = Path(nome).expanduser()
    if caminho.is_dir():
        return ModeloPronto(caminho.name, _motor_do_caminho(caminho), caminho)

    from faster_whisper import available_models

    if nome in available_models():  # ex.: "medium", "distil-large-v3"
        avulso = Modelo(nome, MOTOR_WHISPER, nome, f"Whisper {nome}", 0)
        return ModeloPronto(nome, MOTOR_WHISPER, garantir_modelo(avulso, diretorio))

    conhecidos = ", ".join(MODELOS_FALA)
    raise ValueError(
        f"Modelo '{nome}' não encontrado. Use um apelido ({conhecidos}), um "
        "tamanho do Whisper (ex.: medium) ou o caminho de um modelo já baixado."
    )


def resolver_modelo_locutor(diretorio: Path | None = None) -> Path:
    return garantir_modelo(MODELO_LOCUTOR, diretorio)
