"""Pastas temporárias de trabalho e a limpeza das que ficaram para trás.

Vídeos enviados, faixas extraídas e cortes vivem numa pasta do diretório
temporário do sistema, removida ao fim da execução — com sucesso, erro ou
cancelamento. Uma execução morta à força (queda de energia, `kill -9`) não tem
como limpar a própria pasta; `limpar_orfas` faz isso na inicialização seguinte.

Para não apagar a pasta de uma execução viva (a CLI e a interface web podem
rodar ao mesmo tempo), cada pasta guarda um arquivo travado pelo processo dono
enquanto ele existir. Conseguir travá-lo é a prova de que o dono morreu: o
sistema operacional solta a trava junto com o processo, seja como ele terminar.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

PREFIXO = "transcritor-"
TRAVA = ".em-uso"
IDADE_SEM_TRAVA = 3600  # segundos

if os.name == "nt":
    import msvcrt

    def _travar(arquivo) -> None:
        msvcrt.locking(arquivo.fileno(), msvcrt.LK_NBLCK, 1)

    def _destravar(arquivo) -> None:
        arquivo.seek(0)
        msvcrt.locking(arquivo.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _travar(arquivo) -> None:
        fcntl.flock(arquivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _destravar(arquivo) -> None:
        fcntl.flock(arquivo.fileno(), fcntl.LOCK_UN)


def raiz() -> Path:
    """Diretório temporário do sistema (respeita TMPDIR/TEMP)."""
    return Path(tempfile.gettempdir())


class PastaTemporaria:
    """Uma pasta de trabalho travada por este processo até `remover()`."""

    def __init__(self, base: Path | None = None):
        self.caminho = Path(tempfile.mkdtemp(prefix=PREFIXO, dir=base))
        self._trava = (self.caminho / TRAVA).open("w")
        _travar(self._trava)

    def remover(self) -> None:
        if self._trava is not None and not self._trava.closed:
            with contextlib.suppress(OSError):
                _destravar(self._trava)
            self._trava.close()
        shutil.rmtree(self.caminho, ignore_errors=True)


@contextlib.contextmanager
def pasta_temporaria(base: Path | None = None) -> Iterator[Path]:
    """Cria a pasta de trabalho e a remove ao sair, aconteça o que acontecer."""
    pasta = PastaTemporaria(base)
    try:
        yield pasta.caminho
    finally:
        pasta.remover()


def _orfa(pasta: Path) -> bool:
    trava = pasta / TRAVA
    if not trava.is_file():
        # Sem trava não há dono declarado: sobra de uma versão antiga, ou uma
        # pasta criada neste instante e ainda sem a trava. A idade separa os dois.
        return time.time() - pasta.stat().st_mtime > IDADE_SEM_TRAVA
    try:
        with trava.open("r+") as arquivo:
            _travar(arquivo)
            _destravar(arquivo)
    except OSError:
        return False  # alguém vivo segura a trava
    return True


def limpar_orfas(base: Path | None = None) -> list[Path]:
    """Remove as pastas de execuções que morreram sem limpar. Devolve as removidas."""
    removidas = []
    for pasta in (base or raiz()).glob(f"{PREFIXO}*"):
        if pasta.is_dir() and _orfa(pasta):
            shutil.rmtree(pasta, ignore_errors=True)
            removidas.append(pasta)
    return removidas
