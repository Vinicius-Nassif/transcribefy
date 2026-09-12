"""Mesclagem cronológica das duas faixas."""

from transcritor.mesclagem import mesclar
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
