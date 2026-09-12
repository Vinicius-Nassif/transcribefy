"""Download e cache dos modelos Vosk (reconhecimento + identificação de locutor)."""

from __future__ import annotations

import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests
from tqdm import tqdm

BASE_URL = "https://alphacephei.com/vosk/models"


@dataclass(frozen=True, slots=True)
class Modelo:
    apelido: str
    arquivo: str
    descricao: str
    tamanho_mb: int

    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.arquivo}.zip"


# Modelos de reconhecimento de fala, por apelido.
MODELOS_FALA: dict[str, Modelo] = {
    "pt-pequeno": Modelo(
        "pt-pequeno", "vosk-model-small-pt-0.3",
        "Português compacto — rápido, precisão menor", 31,
    ),
    "pt-grande": Modelo(
        "pt-grande", "vosk-model-pt-fb-v0.1.1-20220516_2113",
        "Português completo — lento e pesado, melhor precisão", 1615,
    ),
    "en-pequeno": Modelo(
        "en-pequeno", "vosk-model-small-en-us-0.15",
        "Inglês compacto", 40,
    ),
    "en-grande": Modelo(
        "en-grande", "vosk-model-en-us-0.22",
        "Inglês completo", 1800,
    ),
}

# Modelo de x-vectors usado para diferenciar locutores pela voz.
MODELO_LOCUTOR = Modelo(
    "locutor", "vosk-model-spk-0.4", "Identificação de locutor (x-vectors)", 13
)


def diretorio_padrao() -> Path:
    """Diretorio onde os modelos são guardados (configuravel por TRANSCRITOR_MODELOS)."""
    env = os.environ.get("TRANSCRITOR_MODELOS")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".cache" / "transcritor-rpg" / "modelos"


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


def garantir_modelo(modelo: Modelo, diretorio: Path | None = None) -> Path:
    """Garante que o modelo esteja disponível em disco e devolve seu caminho.

    Baixa e descompacta na primeira vez; nas seguintes reaproveita o cache.
    """
    diretorio = diretorio or diretorio_padrao()
    destino = diretorio / modelo.arquivo
    if destino.is_dir():
        return destino

    zip_path = diretorio / f"{modelo.arquivo}.zip"
    if not zip_path.is_file():
        print(f"Modelo '{modelo.apelido}' ausente (~{modelo.tamanho_mb} MB). Baixando...")
        _baixar(modelo.url, zip_path, modelo.apelido)

    temporario = diretorio / f".extraindo-{modelo.arquivo}"
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


def resolver_modelo_fala(nome: str, diretorio: Path | None = None) -> Path:
    """Aceita um apelido conhecido ou um caminho para um modelo já baixado."""
    if nome in MODELOS_FALA:
        return garantir_modelo(MODELOS_FALA[nome], diretorio)
    caminho = Path(nome).expanduser()
    if caminho.is_dir():
        return caminho
    conhecidos = ", ".join(MODELOS_FALA)
    raise ValueError(
        f"Modelo '{nome}' não encontrado. Use um apelido ({conhecidos}) "
        "ou o caminho de um modelo Vosk baixado manualmente."
    )


def resolver_modelo_locutor(diretorio: Path | None = None) -> Path:
    return garantir_modelo(MODELO_LOCUTOR, diretorio)
