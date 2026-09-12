"""Mesclagem cronológica das falas das duas faixas."""

from __future__ import annotations

from collections.abc import Iterable

from .transcricao import Fala

# Falas seguidas do mesmo locutor separadas por menos que isto viram um parágrafo só.
PAUSA_MAXIMA = 2.0


def mesclar(
    *grupos: Iterable[Fala],
    deslocamentos: dict[int, float] | None = None,
    juntar: bool = True,
    pausa_maxima: float = PAUSA_MAXIMA,
) -> list[Fala]:
    """Une falas de várias faixas em uma única linha do tempo.

    Args:
        grupos: listas de falas, uma por faixa.
        deslocamentos: ajuste fino de sincronia em segundos, por número de faixa.
        juntar: agrupa falas consecutivas do mesmo locutor.
        pausa_maxima: silêncio máximo, em segundos, para agrupar duas falas.
    """
    todas: list[Fala] = []
    for grupo in grupos:
        for fala in grupo:
            ajuste = (deslocamentos or {}).get(fala.faixa, 0.0)
            if ajuste:
                fala.inicio += ajuste
                fala.fim += ajuste
            todas.append(fala)

    todas.sort(key=lambda f: (f.inicio, f.faixa))
    if not juntar:
        return todas

    unidas: list[Fala] = []
    for fala in todas:
        anterior = unidas[-1] if unidas else None
        mesmo_locutor = (
            anterior is not None
            and anterior.locutor == fala.locutor
            and anterior.faixa == fala.faixa
            and fala.inicio - anterior.fim <= pausa_maxima
        )
        if mesmo_locutor:
            anterior.texto = f"{anterior.texto} {fala.texto}".strip()
            anterior.fim = max(anterior.fim, fala.fim)
            anterior.palavras.extend(fala.palavras)
            anterior.frames_voz += fala.frames_voz
        else:
            unidas.append(fala)
    return unidas
