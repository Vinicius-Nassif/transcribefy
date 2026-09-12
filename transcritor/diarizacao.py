"""Separação de locutores por voz a partir dos x-vectors do Vosk.

Cada fala reconhecida vem acompanhada de um vetor de 128 dimensões que
caracteriza a voz. Agrupando esses vetores por similaridade de cosseno
chegamos a um rótulo de locutor por fala.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import pdist

from .transcricao import Fala

# Falas muito curtas geram vetores instáveis: elas não entram no agrupamento,
# mas depois são atribuídas ao locutor mais próximo.
MIN_FRAMES_CONFIAVEL = 40

# Distância de cosseno usada quando o número de locutores é desconhecido.
# Medida sobre x-vectors de trechos de 1 a 2 s: a distância média fica em torno
# de 0,40 para o mesmo locutor e 0,74 para locutores diferentes.
LIMIAR_AUTO = 0.55

# Só trocamos o locutor de uma fala curta pelo do contexto quando a voz dela não
# favorece claramente o locutor original. Margem em similaridade de cosseno.
MARGEM_SUAVIZACAO = 0.05

# Uma fala só é candidata à suavização se for curta e colada nas vizinhas.
JANELA_SUAVIZACAO = 1.5


def _matriz_normalizada(vetores: Sequence[Sequence[float]]) -> np.ndarray:
    matriz = np.asarray(vetores, dtype=np.float64)
    normas = np.linalg.norm(matriz, axis=1, keepdims=True)
    normas[normas == 0] = 1.0
    return matriz / normas


def _agrupar(matriz: np.ndarray, n_locutores: int | None) -> np.ndarray:
    """Devolve um rótulo inteiro (1..k) para cada linha da matriz."""
    if len(matriz) == 1:
        return np.array([1])
    ligacao = linkage(pdist(matriz, metric="cosine"), method="average")
    if n_locutores is None:
        return fcluster(ligacao, t=LIMIAR_AUTO, criterion="distance")
    return fcluster(ligacao, t=min(n_locutores, len(matriz)), criterion="maxclust")


def _centroides(matriz: np.ndarray, rotulos: np.ndarray) -> tuple[list[int], np.ndarray]:
    """Centroide unitário de cada grupo, na ordem dos rótulos."""
    ordem = sorted(int(r) for r in np.unique(rotulos))
    pilha = []
    for rotulo in ordem:
        centroide = matriz[rotulos == rotulo].mean(axis=0)
        pilha.append(centroide / (np.linalg.norm(centroide) or 1.0))
    return ordem, np.stack(pilha)


def _suavizar(
    falas: Sequence[Fala],
    grupos: list[int | None],
    similaridades: dict[int, np.ndarray],
    ordem: list[int],
) -> None:
    """Corrige trocas isoladas usando o contexto, sem apagar um locutor.

    Uma fala curta cercada por duas do mesmo locutor passa para esse locutor,
    mas só quando a própria voz dela não indica o contrário com folga.
    """
    posicao = {rotulo: i for i, rotulo in enumerate(ordem)}
    for i in range(1, len(falas) - 1):
        anterior, atual, seguinte = falas[i - 1], falas[i], falas[i + 1]
        vizinho = grupos[i - 1]
        if vizinho is None or vizinho != grupos[i + 1] or vizinho == grupos[i]:
            continue
        if atual.duracao > JANELA_SUAVIZACAO:
            continue
        if (atual.inicio - anterior.fim) >= JANELA_SUAVIZACAO:
            continue
        if (seguinte.inicio - atual.fim) >= JANELA_SUAVIZACAO:
            continue

        similaridade = similaridades.get(i)
        if similaridade is not None and grupos[i] is not None:
            propria = similaridade[posicao[grupos[i]]]
            do_vizinho = similaridade[posicao[vizinho]]
            if do_vizinho < propria - MARGEM_SUAVIZACAO:
                continue  # a voz desmente o contexto: mantém o que foi agrupado
        grupos[i] = vizinho


def atribuir_locutores(
    falas: list[Fala],
    n_locutores: int | None = 5,
    nomes: Sequence[str] | None = None,
    prefixo: str = "Jogador",
    suavizar: bool = True,
) -> list[Fala]:
    """Rotula cada fala com um locutor, agrupando as vozes por similaridade.

    Args:
        falas: falas de uma mesma faixa, em ordem cronológica.
        n_locutores: quantidade esperada de vozes. `None` estima automaticamente.
        nomes: nomes a usar, na ordem em que cada locutor aparece pela primeira vez.
        prefixo: usado para nomear os locutores sem nome informado.
        suavizar: corrige atribuições isoladas usando o contexto temporal.

    Returns:
        A mesma lista, com o campo `locutor` preenchido.
    """
    if not falas:
        return falas

    confiaveis = [
        i for i, f in enumerate(falas)
        if f.vetor_voz and f.frames_voz >= MIN_FRAMES_CONFIAVEL
    ]
    if not confiaveis:  # nenhum vetor longo o bastante: aceita os curtos
        confiaveis = [i for i, f in enumerate(falas) if f.vetor_voz]
    if not confiaveis:  # sem vetores não há como separar vozes
        for fala in falas:
            fala.locutor = _nome(0, nomes, prefixo)
        return falas

    matriz = _matriz_normalizada([falas[i].vetor_voz for i in confiaveis])
    rotulos = _agrupar(matriz, n_locutores)
    ordem, centroides = _centroides(matriz, rotulos)

    grupos: list[int | None] = [None] * len(falas)
    similaridades: dict[int, np.ndarray] = {}
    for indice, rotulo in zip(confiaveis, rotulos, strict=True):
        grupos[indice] = int(rotulo)

    # As falas curtas ficam com o locutor de voz mais parecida.
    restantes = [i for i, g in enumerate(grupos) if g is None and falas[i].vetor_voz]
    if restantes:
        parecidas = _matriz_normalizada([falas[i].vetor_voz for i in restantes])
        for i, linha in zip(restantes, parecidas @ centroides.T, strict=True):
            grupos[i] = ordem[int(linha.argmax())]
            similaridades[i] = linha

    if suavizar:
        for i, indice in enumerate(confiaveis):
            similaridades[indice] = matriz[i] @ centroides.T
        _suavizar(falas, grupos, similaridades, ordem)

    # Renumera pela ordem de aparição para que "Jogador 1" seja o primeiro a falar.
    vistos: dict[int, int] = {}
    for fala, grupo in zip(falas, grupos, strict=True):
        chave = ordem[0] if grupo is None else grupo
        if chave not in vistos:
            vistos[chave] = len(vistos)
        fala.locutor = _nome(vistos[chave], nomes, prefixo)
    return falas


def _nome(posicao: int, nomes: Sequence[str] | None, prefixo: str) -> str:
    if nomes and posicao < len(nomes):
        return nomes[posicao]
    return f"{prefixo} {posicao + 1}"
