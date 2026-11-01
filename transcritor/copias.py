"""Cópias de vídeo recebidas pela interface web.

O navegador não entrega ao servidor o caminho do arquivo original: ele envia o
conteúdo, que o `web.py` grava numa pasta própria do trabalho. Essa cópia é
temporária:

- **transcrição concluída** — a cópia é apagada; as transcrições ficam.
- **transcrição falhou** — a cópia fica, para tentar de novo sem reenviar.
- **cópias de falhas antigas** — `remover` apaga as que passaram de um prazo,
  chamado pelo `transcritor limpar` e pela interface web ao subir.

Cada pasta de trabalho guarda um `trabalho.json` com o nome da cópia, o estado e
os parâmetros do envio. É ele que autoriza apagar: **só se apaga o arquivo que
o próprio registro aponta, dentro da pasta do registro.** Pastas sem registro —
inclusive as criadas antes desta regra — e qualquer arquivo fora delas nunca são
tocados. A linha de comando não passa por aqui: ela lê o arquivo do usuário onde
ele estiver e não o apaga em hipótese nenhuma.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

RAIZ_PADRAO = Path("dados/trabalhos")
REGISTRO = "trabalho.json"
DIAS_PADRAO = 7

AGUARDANDO = "aguardando"
FALHOU = "falhou"


@dataclass(slots=True)
class Registro:
    """O que fica no `trabalho.json` de uma pasta de trabalho."""

    video: str
    estado: str = AGUARDANDO
    atualizado_em: float = field(default_factory=time.time)
    parametros: dict = field(default_factory=dict)
    erro: str | None = None


@dataclass(slots=True)
class CopiaAntiga:
    """Uma cópia candidata à limpeza, com o que ajuda a decidir removê-la."""

    pasta: Path
    video: Path
    tamanho: int
    dias: float
    erro: str | None


def _nome_seguro(nome: str) -> bool:
    return bool(nome) and nome == Path(nome).name and nome not in {".", "..", REGISTRO}


def _gravar(pasta: Path, registro: Registro) -> None:
    """Grava o registro de uma vez: um registro pela metade seria ilegível."""
    temporario = pasta / f".{REGISTRO}.tmp"
    temporario.write_text(
        json.dumps(asdict(registro), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporario, pasta / REGISTRO)


def registrar(pasta: Path, video: str, parametros: dict) -> Registro:
    """Marca `pasta/video` como cópia temporária de um envio pela web."""
    if not _nome_seguro(video):
        raise ValueError(f"Nome de vídeo inválido: {video!r}")
    registro = Registro(video=video, parametros=dict(parametros))
    _gravar(Path(pasta), registro)
    return registro


def ler(pasta: Path) -> Registro | None:
    """O registro da pasta, ou None se não houver um legível."""
    try:
        dados = json.loads((Path(pasta) / REGISTRO).read_text(encoding="utf-8"))
        registro = Registro(**dados)
    except (OSError, ValueError, TypeError):
        return None
    if not _nome_seguro(registro.video):
        return None
    return registro


def copia(pasta: Path) -> Path | None:
    """O vídeo que o registro aponta, se ainda existir como arquivo comum."""
    registro = ler(pasta)
    if registro is None:
        return None
    caminho = Path(pasta) / registro.video
    if caminho.is_symlink() or not caminho.is_file():
        return None
    return caminho


def marcar(pasta: Path, estado: str, erro: str | None = None) -> None:
    """Atualiza o estado e a data do registro, se ele existir."""
    registro = ler(pasta)
    if registro is None:
        return
    registro.estado = estado
    registro.erro = erro
    registro.atualizado_em = time.time()
    _gravar(Path(pasta), registro)


def marcar_falha(pasta: Path, erro: str) -> None:
    marcar(pasta, FALHOU, erro)


def concluir(pasta: Path) -> None:
    """Transcrição concluída: apaga a cópia e o registro, mantém as transcrições."""
    video = copia(pasta)
    if video is not None:
        video.unlink()
    (Path(pasta) / REGISTRO).unlink(missing_ok=True)


def antigas(raiz: Path = RAIZ_PADRAO, dias: float = DIAS_PADRAO,
            agora: float | None = None) -> list[CopiaAntiga]:
    """Cópias cujo registro não muda há mais de `dias` dias.

    A idade conta da última mudança de estado, e não do envio: uma tentativa
    nova dá mais um prazo inteiro à cópia. Vale para qualquer estado — um
    registro "aguardando" tão antigo é de um servidor que caiu no meio.
    """
    raiz = Path(raiz)
    if not raiz.is_dir():
        return []
    agora = time.time() if agora is None else agora
    encontradas = []
    for pasta in sorted(raiz.iterdir()):
        if pasta.is_symlink() or not pasta.is_dir():
            continue
        registro = ler(pasta)
        video = copia(pasta)
        if registro is None or video is None:
            continue
        idade = (agora - registro.atualizado_em) / 86400
        if idade > dias:
            encontradas.append(CopiaAntiga(
                pasta=pasta, video=video, tamanho=video.stat().st_size,
                dias=idade, erro=registro.erro,
            ))
    return encontradas


def remover(copias: list[CopiaAntiga]) -> list[Path]:
    """Apaga as cópias listadas por `antigas` e devolve as que foram removidas.

    O registro vai junto, porque sem a cópia não há o que tentar de novo. A
    pasta só é removida se ficar vazia: transcrições parciais continuam lá.
    """
    removidas = []
    for item in copias:
        if copia(item.pasta) != item.video:
            continue  # o registro mudou desde a listagem: não é mais a mesma cópia
        item.video.unlink()
        (item.pasta / REGISTRO).unlink(missing_ok=True)
        try:
            item.pasta.rmdir()
        except OSError:
            pass  # há outros arquivos na pasta
        removidas.append(item.video)
    return removidas
