"""Histórico local dos termos de contexto, para sugerir nas próximas sessões.

Os nomes e o jargão da mesa se repetem de uma sessão para a outra: Strahd,
Barovia, "teste de resistência". A interface web guarda cada termo enviado num
JSON em `dados/` — no computador de quem roda o servidor, e não no navegador —,
então as sugestões sobrevivem à troca de navegador e à reconstrução do container
(`dados/` é um volume). O arquivo pode ser lido e editado à mão:

    {"versao": 1, "termos": [{"termo": "Strahd", "usos": 3, "ultimo_uso": "2026-11-01"}]}

A ordem da lista é a de uso, do mais recente para o mais antigo: é ela que
decide as sugestões e quem sai quando o limite é atingido.

A lógica veio do branch evolucao-faixas-e-desempenho; lá o arquivo ficava na
pasta de dados do sistema operacional.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterable
from pathlib import Path

ARQUIVO_PADRAO = Path("dados/historico-contexto.json")
VERSAO = 1
LIMITE = 500  # termos guardados; os usados há mais tempo saem primeiro

_trava = threading.Lock()


def termos_do_contexto(texto: str) -> list[str]:
    """Os termos enviados pela interface web: um por linha, sem repetir."""
    vistos: dict[str, str] = {}
    for linha in texto.splitlines():
        limpo = " ".join(linha.split())
        if limpo:
            vistos.setdefault(limpo.casefold(), limpo)
    return list(vistos.values())


def _limpo(termo: str) -> str:
    return " ".join(str(termo).split())


def _ler(arquivo: Path) -> list[dict]:
    try:
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
        termos = dados.get("termos", [])
        return [t for t in termos if isinstance(t, dict) and _limpo(t.get("termo", ""))]
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


def listar(arquivo: Path | None = None) -> list[str]:
    """Termos já usados, do mais recente para o mais antigo."""
    with _trava:
        return [_limpo(t["termo"]) for t in _ler(arquivo or ARQUIVO_PADRAO)]


def registrar(novos: Iterable[str], arquivo: Path | None = None) -> None:
    """Soma um uso a cada termo e os leva para o topo, criando os que faltam.

    Termos que só diferem em maiúsculas ou espaços são o mesmo; fica a grafia
    mais recente. Os novos entram na ordem em que vieram.
    """
    arquivo = arquivo or ARQUIVO_PADRAO
    hoje = time.strftime("%Y-%m-%d")
    with _trava:
        termos = _ler(arquivo)
        por_chave = {_limpo(t["termo"]).casefold(): t for t in termos}
        usados: dict[str, dict] = {}
        for termo in novos:
            limpo = _limpo(termo)
            chave = limpo.casefold()
            if not limpo or chave in usados:
                continue
            entrada = dict(por_chave.get(chave, {"usos": 0}))
            entrada.update(termo=limpo, usos=int(entrada.get("usos", 0)) + 1, ultimo_uso=hoje)
            usados[chave] = entrada
        if not usados:
            return
        restantes = [t for t in termos if _limpo(t["termo"]).casefold() not in usados]
        _gravar(arquivo, [*usados.values(), *restantes][:LIMITE])


def remover(termo: str, arquivo: Path | None = None) -> bool:
    """Apaga um termo do histórico. Devolve se ele existia."""
    arquivo = arquivo or ARQUIVO_PADRAO
    chave = _limpo(termo).casefold()
    with _trava:
        termos = _ler(arquivo)
        restantes = [t for t in termos if _limpo(t["termo"]).casefold() != chave]
        if len(restantes) == len(termos):
            return False
        _gravar(arquivo, restantes)
        return True
