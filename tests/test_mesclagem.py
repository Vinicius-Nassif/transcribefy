"""Mesclagem cronológica das duas faixas."""

from transcritor.mesclagem import mesclar, sobrepostas
from transcritor.transcricao import Fala


def fala(inicio, fim, texto, faixa, locutor):
    return Fala(inicio=inicio, fim=fim, texto=texto, faixa=faixa, locutor=locutor)


def test_intercala_as_faixas_por_tempo():
    gm = [fala(0, 4, "abram a porta", 0, "GM"), fala(20, 24, "rolem", 0, "GM")]
    grupo = [fala(6, 9, "eu abro", 1, "Jogador 1"), fala(12, 15, "espera", 1, "Jogador 2")]
    resultado = mesclar(gm, grupo)
    assert [f.locutor for f in resultado] == ["GM", "Jogador 1", "Jogador 2", "GM"]
    assert [f.inicio for f in resultado] == [0, 6, 12, 20]


def test_junta_falas_seguidas_do_mesmo_locutor():
    grupo = [fala(0, 3, "eu", 1, "Jogador 1"), fala(4, 7, "abro a porta", 1, "Jogador 1")]
    (unica,) = mesclar(grupo, pausa_maxima=2.0)
    assert unica.texto == "eu abro a porta"
    assert (unica.inicio, unica.fim) == (0, 7)


def test_pausa_longa_mantem_falas_separadas():
    grupo = [fala(0, 3, "eu", 1, "Jogador 1"), fala(30, 33, "abro", 1, "Jogador 1")]
    assert len(mesclar(grupo, pausa_maxima=2.0)) == 2


def test_nao_junta_locutores_diferentes():
    grupo = [fala(0, 3, "eu", 1, "Jogador 1"), fala(3.5, 6, "nao", 1, "Jogador 2")]
    assert len(mesclar(grupo, pausa_maxima=2.0)) == 2


def test_nao_junta_faixas_diferentes_mesmo_com_o_mesmo_nome():
    falas = [fala(0, 3, "oi", 0, "GM"), fala(3.2, 6, "tchau", 1, "GM")]
    assert len(mesclar(falas, pausa_maxima=2.0)) == 2


def test_sem_juntar_preserva_cada_trecho():
    grupo = [fala(0, 3, "eu", 1, "Jogador 1"), fala(4, 7, "abro", 1, "Jogador 1")]
    assert len(mesclar(grupo, juntar=False)) == 2


def test_deslocamento_ajusta_a_sincronia_da_faixa():
    gm = [fala(10, 12, "oi", 0, "GM")]
    grupo = [fala(5, 7, "tchau", 1, "Jogador 1")]
    resultado = mesclar(gm, grupo, deslocamentos={1: 8.0}, juntar=False)
    assert [f.locutor for f in resultado] == ["GM", "Jogador 1"]
    assert resultado[1].inicio == 13.0


def test_nenhuma_fala_some_quando_as_faixas_se_sobrepoem():
    """Duas pessoas falando juntas é o caso normal — as duas falas ficam."""
    gm = [fala(0.0, 12.0, "monólogo do mestre", 0, "GM")]
    grupo = [
        fala(4.0, 5.0, "peraí", 1, "Ana"),
        fala(4.5, 6.0, "deixa eu ver", 1, "Bruno"),
        fala(11.0, 13.0, "beleza", 1, "Ana"),
    ]
    unidas = mesclar(gm, grupo)
    assert [f.texto for f in unidas] == [
        "monólogo do mestre", "peraí", "deixa eu ver", "beleza",
    ]


def test_a_fala_interrompida_mantem_o_tempo_final():
    gm = [fala(0.0, 12.0, "monólogo", 0, "GM")]
    grupo = [fala(4.0, 5.0, "peraí", 1, "Ana")]
    unidas = mesclar(gm, grupo)
    assert unidas[0].fim == 12.0


def test_juntar_nunca_mistura_a_fala_de_duas_pessoas():
    """A Ana emenda com ela mesma; o que o GM diz por cima segue à parte."""
    gm = [fala(0.0, 6.0, "monólogo", 0, "GM")]
    grupo = [fala(1.0, 2.0, "peraí", 1, "Ana"), fala(3.0, 4.0, "deixa ver", 1, "Ana")]
    unidas = mesclar(gm, grupo, juntar=True)
    assert [(f.locutor, f.texto) for f in unidas] == [
        ("GM", "monólogo"),
        ("Ana", "peraí deixa ver"),
    ]


def test_sobrepostas_aponta_quem_dividiu_o_tempo():
    falas = [
        fala(0.0, 12.0, "monólogo", 0, "GM"),
        fala(4.0, 5.0, "peraí", 1, "Ana"),
        fala(20.0, 22.0, "tá", 1, "Bruno"),
    ]
    assert sobrepostas(falas) == [True, True, False]


def test_encostar_nao_e_sobrepor():
    falas = [fala(0.0, 5.0, "a", 0, "GM"), fala(5.0, 9.0, "b", 1, "Ana")]
    assert sobrepostas(falas) == [False, False]


def test_sobrepostas_marca_tres_vozes_ao_mesmo_tempo():
    falas = [
        fala(0.0, 9.0, "a", 0, "GM"),
        fala(1.0, 2.0, "b", 1, "Ana"),
        fala(1.5, 2.5, "c", 1, "Bruno"),
    ]
    assert sobrepostas(falas) == [True, True, True]


def test_sobrepostas_aceita_lista_vazia():
    assert sobrepostas([]) == []


def test_uma_interrupcao_corta_o_paragrafo_em_vez_de_sumir_dentro_dele():
    """Fundir os dois blocos do GM engoliria o aparte da Ana no meio."""
    gm = [fala(0.0, 2.0, "vocês veem a porta", 0, "GM"),
          fala(3.0, 5.0, "ela está entreaberta", 0, "GM")]
    grupo = [fala(1.0, 4.0, "eu checo armadilhas", 1, "Ana")]
    unidas = mesclar(gm, grupo, juntar=True)
    assert [(f.locutor, f.texto) for f in unidas] == [
        ("GM", "vocês veem a porta"),
        ("Ana", "eu checo armadilhas"),
        ("GM", "ela está entreaberta"),
    ]
