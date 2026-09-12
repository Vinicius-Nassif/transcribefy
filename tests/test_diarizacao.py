"""Agrupamento das falas por voz."""

import numpy as np

from transcritor.diarizacao import atribuir_locutores
from transcritor.transcricao import Fala

DIMENSOES = 16


def voz(semente: int) -> list[float]:
    """Um vetor de voz estável e bem separado dos demais.

    Vetores aleatórios em poucas dimensões ficam perto demais uns dos outros;
    usar direções quase ortogonais reproduz a separação dos x-vectors reais.
    """
    vetor = np.full(DIMENSOES, 0.05)
    vetor[semente % DIMENSOES] = 1.0
    return vetor.tolist()


def ruido(base: list[float], escala: float, semente: int) -> list[float]:
    gerador = np.random.default_rng(semente)
    return (np.array(base) + gerador.normal(scale=escala, size=DIMENSOES)).tolist()


def fala(inicio: float, vetor: list[float] | None, frames: int = 200) -> Fala:
    return Fala(
        inicio=inicio, fim=inicio + 3, texto="oi", faixa=1,
        vetor_voz=vetor, frames_voz=frames,
    )


def test_agrupa_a_mesma_voz_e_separa_vozes_diferentes():
    a, b = voz(1), voz(2)
    falas = [
        fala(0, ruido(a, 0.05, 10)),
        fala(10, ruido(b, 0.05, 11)),
        fala(20, ruido(a, 0.05, 12)),
        fala(30, ruido(b, 0.05, 13)),
    ]
    atribuir_locutores(falas, n_locutores=2, suavizar=False)
    assert falas[0].locutor == falas[2].locutor
    assert falas[1].locutor == falas[3].locutor
    assert falas[0].locutor != falas[1].locutor


def test_nomeia_na_ordem_de_aparicao():
    a, b = voz(1), voz(2)
    falas = [fala(0, ruido(b, 0.05, 20)), fala(10, ruido(a, 0.05, 21))]
    atribuir_locutores(falas, n_locutores=2, suavizar=False)
    assert falas[0].locutor == "Jogador 1"
    assert falas[1].locutor == "Jogador 2"


def test_usa_os_nomes_informados():
    a, b = voz(1), voz(2)
    falas = [fala(0, ruido(a, 0.05, 30)), fala(10, ruido(b, 0.05, 31))]
    atribuir_locutores(falas, n_locutores=2, nomes=["Ana", "Bruno"], suavizar=False)
    assert [f.locutor for f in falas] == ["Ana", "Bruno"]


def test_nomes_incompletos_caem_no_prefixo():
    falas = [fala(0, voz(1)), fala(10, voz(2)), fala(20, voz(3))]
    atribuir_locutores(falas, n_locutores=3, nomes=["Ana"], suavizar=False)
    assert falas[0].locutor == "Ana"
    assert {f.locutor for f in falas[1:]} == {"Jogador 2", "Jogador 3"}


def test_falas_curtas_herdam_o_locutor_mais_proximo():
    a, b = voz(1), voz(2)
    falas = [
        fala(0, ruido(a, 0.05, 40)),
        fala(10, ruido(b, 0.05, 41)),
        fala(20, ruido(a, 0.05, 42), frames=5),  # curta demais para agrupar
    ]
    atribuir_locutores(falas, n_locutores=2, suavizar=False)
    assert falas[2].locutor == falas[0].locutor


def test_sem_vetores_tudo_vira_um_locutor():
    falas = [fala(0, None), fala(10, None)]
    atribuir_locutores(falas, n_locutores=5)
    assert {f.locutor for f in falas} == {"Jogador 1"}


def test_modo_automatico_descobre_a_quantidade():
    falas = [fala(i * 10, ruido(voz(i), 0.03, 50 + i)) for i in range(1, 4)]
    atribuir_locutores(falas, n_locutores=None, suavizar=False)
    assert len({f.locutor for f in falas}) == 3


def cenario(vetor_do_meio, frames_do_meio=10):
    """Duas vozes bem representadas, com uma fala curta encaixada na voz 1."""
    return [
        Fala(0.0, 3.0, "a", 1, locutor="", vetor_voz=voz(1), frames_voz=200),
        Fala(3.5, 4.2, "b", 1, locutor="", vetor_voz=vetor_do_meio,
             frames_voz=frames_do_meio),
        Fala(4.8, 8.0, "c", 1, locutor="", vetor_voz=voz(1), frames_voz=200),
        Fala(20.0, 24.0, "d", 1, locutor="", vetor_voz=voz(2), frames_voz=200),
        Fala(30.0, 34.0, "e", 1, locutor="", vetor_voz=voz(2), frames_voz=200),
    ]


def mistura(peso: float) -> list[float]:
    """Voz entre a 1 e a 2; peso 0 é toda voz 1 e peso 1 é toda voz 2."""
    return ((1 - peso) * np.array(voz(1)) + peso * np.array(voz(2))).tolist()


def test_suavizacao_absorve_fala_curta_de_voz_ambigua():
    # Levemente inclinada para a voz 2, mas dentro da margem: o contexto decide.
    falas = cenario(mistura(0.51))
    atribuir_locutores(falas, n_locutores=2, suavizar=True)
    assert falas[1].locutor == falas[0].locutor == falas[2].locutor
    assert falas[3].locutor != falas[0].locutor


def test_suavizacao_respeita_voz_claramente_diferente():
    """O contexto não pode apagar um locutor que a voz identifica com folga."""
    falas = cenario(voz(2), frames_do_meio=200)
    atribuir_locutores(falas, n_locutores=2, suavizar=True)
    assert falas[1].locutor == falas[3].locutor
    assert falas[1].locutor != falas[0].locutor


def test_suavizacao_nunca_reduz_o_numero_de_locutores_agrupados():
    falas = [
        Fala(i * 2.0, i * 2.0 + 1.0, "x", 1, locutor="",
             vetor_voz=voz(1 + i % 3), frames_voz=200)
        for i in range(9)
    ]
    atribuir_locutores(falas, n_locutores=3, suavizar=True)
    assert len({f.locutor for f in falas}) == 3


def test_lista_vazia_nao_quebra():
    assert atribuir_locutores([], n_locutores=5) == []
