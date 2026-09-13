"""Mesclagem cronológica das falas das duas faixas.

As faixas são gravadas em paralelo, então elas se sobrepõem no tempo o tempo
todo: numa mesa animada as pessoas se interrompem, respondem por cima e falam
juntas. **Nada aqui descarta uma fala por se sobrepor a outra** — as duas entram
na linha do tempo, ordenadas pelo início. `sobrepostas` diz quais são essas.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .fala import Fala

# Falas seguidas do mesmo locutor separadas por menos que isto viram um parágrafo só.
PAUSA_MAXIMA = 2.0


def mesclar(
    *grupos: Iterable[Fala],
    deslocamentos: dict[int, float] | None = None,
    juntar: bool = True,
    pausa_maxima: float = PAUSA_MAXIMA,
) -> list[Fala]:
    """Une falas de várias faixas em uma única linha do tempo.

    Toda fala recebida sai na lista devolvida: o `juntar` funde falas seguidas
    **do mesmo locutor e da mesma faixa**, concatenando o texto, e nunca elimina
    uma fala por ela coincidir no tempo com a de outra pessoa.

    A fusão só olha a fala imediatamente anterior na linha do tempo, então uma
    interrupção corta o parágrafo em vez de ser engolida por ele: quem foi
    interrompido recomeça num bloco novo, e a interrupção continua visível entre
    os dois.

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


def sobrepostas(falas: Sequence[Fala]) -> list[bool]:
    """Diz, para cada fala, se ela divide o tempo com alguma outra.

    A lista de saída é ordenada pelo início de cada fala e não mostra onde cada
    uma termina, então uma interjeição no meio de um monólogo parece vir depois
    dele. Este marcador é o que permite a quem lê — ou a um pós-processamento —
    reconhecer que as duas aconteceram ao mesmo tempo.

    Encostar não é sobrepor: uma fala que começa exatamente quando a outra
    termina não marca nenhuma das duas.
    """
    marcas = [False] * len(falas)
    ordem = sorted(range(len(falas)), key=lambda i: falas[i].inicio)
    no_ar: list[int] = []
    for i in ordem:
        no_ar = [j for j in no_ar if falas[j].fim > falas[i].inicio]
        if no_ar:
            marcas[i] = True
            for j in no_ar:
                marcas[j] = True
        no_ar.append(i)
    return marcas
