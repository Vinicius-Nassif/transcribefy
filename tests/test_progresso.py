"""Junção do progresso das etapas, cancelamento e estimativa de tempo."""

import threading

import pytest

from transcritor.progresso import Acompanhamento, Cancelado, EstimativaTempo


def test_as_etapas_pesam_conforme_o_custo():
    acompanhar = Acompanhamento()
    acompanhar.planejar({"rapida": 1, "lenta": 3})
    acompanhar.concluir("rapida")
    assert acompanhar.fracao == pytest.approx(0.25)
    acompanhar.etapa("lenta", "x")(0.5)
    assert acompanhar.fracao == pytest.approx(0.625)


def test_o_avanco_de_uma_etapa_nunca_recua():
    acompanhar = Acompanhamento()
    acompanhar.planejar({"a": 1})
    avancar = acompanhar.etapa("a", "x")
    avancar(0.6)
    avancar(0.4)
    assert acompanhar.fracao == pytest.approx(0.6)


def test_relata_mensagem_e_fracao():
    vistos = []
    acompanhar = Acompanhamento(lambda m, f: vistos.append((m, f)))
    acompanhar.planejar({"a": 1})
    acompanhar.etapa("a", "Transcrevendo")(0.5)
    assert vistos == [("Transcrevendo", 0.5)]


def test_cancelamento_vira_excecao_no_proximo_aviso():
    cancelado = threading.Event()
    acompanhar = Acompanhamento(cancelado=cancelado)
    acompanhar.planejar({"a": 1})
    avancar = acompanhar.etapa("a", "x")
    avancar(0.1)
    cancelado.set()
    with pytest.raises(Cancelado):
        avancar(0.2)


class Relogio:
    def __init__(self):
        self.agora = 0.0

    def __call__(self):
        return self.agora


def test_sem_base_nao_ha_estimativa():
    relogio = Relogio()
    estimativa = EstimativaTempo(relogio=relogio)
    estimativa.registrar(0.0)
    relogio.agora = 5
    estimativa.registrar(0.10)
    assert estimativa.restante() is None  # antes do mínimo de 10 s


def test_estima_pelo_ritmo_recente():
    relogio = Relogio()
    estimativa = EstimativaTempo(janela=60, relogio=relogio)
    # 30 s carregando o modelo sem avançar, depois 1% por segundo.
    for t, f in [(0, 0.0), (30, 0.0), (40, 0.10), (50, 0.20), (100, 0.70)]:
        relogio.agora = t
        estimativa.registrar(f)
    assert estimativa.restante() == pytest.approx(30, rel=0.05)


def test_parado_cai_para_a_media_geral():
    relogio = Relogio()
    estimativa = EstimativaTempo(janela=10, relogio=relogio)
    for t, f in [(0, 0.0), (50, 0.5), (70, 0.5), (90, 0.5)]:
        relogio.agora = t
        estimativa.registrar(f)
    assert estimativa.restante() == pytest.approx(90, rel=0.05)


def test_concluido_nao_falta_nada():
    relogio = Relogio()
    estimativa = EstimativaTempo(relogio=relogio)
    estimativa.registrar(0.0)
    relogio.agora = 20
    estimativa.registrar(1.0)
    assert estimativa.restante() == 0.0
