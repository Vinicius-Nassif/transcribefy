"""Histórico local dos termos de contexto, para sugerir nas próximas sessões.

Fica num JSON na pasta de dados do usuário (`pasta_dados`), e não no navegador:
assim ele sobrevive à troca de navegador, de porta e à reconstrução do
container, e pode ser lido ou editado à mão. Formato:

    {"versao": 1, "termos": [{"termo": "Strahd", "usos": 3, "ultimo_uso": "2026-10-15"}]}
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Iterable
from pathlib import Path

VERSAO = 1
LIMITE = 500  # termos guardados; os menos recentes saem primeiro

_trava = threading.Lock()


def pasta_dados() -> Path:
    """Pasta de dados do usuário, segundo o costume de cada sistema.

    TRANSCRITOR_DADOS tem precedência — é o que o container usa para gravar
    num volume.
    """
    env = os.environ.get("TRANSCRITOR_DADOS")
    if env:
        return Path(env).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home())) / "transcritor-rpg"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "transcritor-rpg"
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "transcritor-rpg"


def arquivo_padrao() -> Path:
    return pasta_dados() / "historico-contexto.json"


def _ler(arquivo: Path) -> list[dict]:
    try:
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
        termos = dados.get("termos", [])
        return [t for t in termos if isinstance(t, dict) and str(t.get("termo", "")).strip()]
    except FileNotFoundError:
        return []
    except (OSError, ValueError, AttributeError):
        # Arquivo corrompido: guarda uma cópia para quem quiser recuperar à mão e
        # recomeça, em vez de impedir a transcrição por causa de sugestões.
        try:
            arquivo.replace(arquivo.with_suffix(".json.corrompido"))
        except OSError:
            pass  # sem permissão para guardar a cópia: segue sem ela
        return []


def _gravar(arquivo: Path, termos: list[dict]) -> None:
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    parcial = arquivo.with_suffix(".json.parcial")
    parcial.write_text(
        json.dumps({"versao": VERSAO, "termos": termos}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    parcial.replace(arquivo)  # troca atômica: nunca fica meio arquivo no disco


def _ordenar(termos: list[dict]) -> list[dict]:
    return sorted(termos, key=lambda t: (t.get("ultimo_uso", ""), t.get("usos", 0)), reverse=True)


def listar(arquivo: Path | None = None) -> list[str]:
    """Termos já usados, do mais recente para o mais antigo."""
    with _trava:
        return [t["termo"] for t in _ordenar(_ler(arquivo or arquivo_padrao()))]


def registrar(novos: Iterable[str], arquivo: Path | None = None) -> None:
    """Soma um uso a cada termo, criando os que ainda não existem.

    Termos iguais sem diferença de maiúsculas são o mesmo; vale a grafia mais recente.
    """
    arquivo = arquivo or arquivo_padrao()
    hoje = time.strftime("%Y-%m-%d")
    with _trava:
        termos = _ler(arquivo)
        por_chave = {t["termo"].casefold(): t for t in termos}
        for termo in novos:
            limpo = " ".join(str(termo).split())
            if not limpo:
                continue
            existente = por_chave.get(limpo.casefold())
            if existente is None:
                existente = {"termo": limpo, "usos": 0}
                por_chave[limpo.casefold()] = existente
                termos.append(existente)
            existente.update(termo=limpo, usos=int(existente.get("usos", 0)) + 1, ultimo_uso=hoje)
        _gravar(arquivo, _ordenar(termos)[:LIMITE])


def remover(termo: str, arquivo: Path | None = None) -> bool:
    """Apaga um termo do histórico. Devolve se ele existia."""
    arquivo = arquivo or arquivo_padrao()
    chave = " ".join(termo.split()).casefold()
    with _trava:
        termos = _ler(arquivo)
        restantes = [t for t in termos if t["termo"].casefold() != chave]
        if len(restantes) == len(termos):
            return False
        _gravar(arquivo, restantes)
        return True
