"""Tempo restante: quando há base para estimar, e como ele aparece."""

import pytest

from transcritor import cli, progresso, web
from transcritor.progresso import EstimativaTempo


class Relogio:
    def __init__(self):
        self.agora = 0.0

    def __call__(self):
        return self.agora


def estimativa_com(amostras, **opcoes):
    relogio = Relogio()
    estimativa = EstimativaTempo(relogio=relogio, **opcoes)
    for t, f in amostras:
        relogio.agora = t
        estimativa.registrar(f)
    return estimativa


def test_sem_base_nao_ha_estimativa():
    """Antes do mínimo de 10 s, mesmo com avanço."""
    assert estimativa_com([(0, 0.0), (5, 0.10)]).restante() is None


def test_avanco_minusculo_nao_basta():
    """Baixando o modelo: muito tempo, quase nada de avanço."""
    assert estimativa_com([(0, 0.0), (120, 0.01)]).restante() is None


def test_uma_amostra_so_nao_basta():
    assert estimativa_com([(0, 0.0)]).restante() is None


def test_estima_pelo_ritmo_recente():
    # 30 s carregando o modelo sem avançar, depois 1% por segundo.
    estimativa = estimativa_com(
        [(0, 0.0), (30, 0.0), (40, 0.10), (50, 0.20), (100, 0.70)], janela=60
    )
    assert estimativa.restante() == pytest.approx(30, rel=0.05)


def test_parado_cai_para_a_media_geral():
    estimativa = estimativa_com([(0, 0.0), (50, 0.5), (70, 0.5), (90, 0.5)], janela=10)
    assert estimativa.restante() == pytest.approx(90, rel=0.05)


def test_concluido_nao_falta_nada():
    assert estimativa_com([(0, 0.0), (20, 1.0)]).restante() == 0.0


def test_custo_da_transcricao_por_motor_e_dispositivo():
    assert progresso.custo_transcricao("whisper", "cpu") > progresso.custo_transcricao(
        "whisper", "cuda"
    )
    assert progresso.custo_transcricao("vosk", "cuda") == progresso.CUSTO_VOSK
    assert progresso.custo_transcricao("whisper", "outro") == progresso.CUSTO_WHISPER["cpu"]


@pytest.mark.parametrize(
    ("segundos", "texto"),
    [
        (None, "calculando..."),
        (0.4, "terminando"),
        (2, "faltam ~5 s"),
        (42, "faltam ~40 s"),
        (61, "faltam ~1 min"),
        (14 * 60 + 40, "faltam ~15 min"),
        (2 * 3600 + 5 * 60, "faltam ~2 h 05 min"),
    ],
)
def test_texto_do_tempo_restante(segundos, texto):
    assert progresso.texto_restante(segundos) == texto


# --- Na API ------------------------------------------------------------------


def trabalho_processando(amostras):
    relogio = Relogio()
    trabalho = web.Trabalho(id="x", nome_video="v.mp4", estado="processando")
    trabalho.estimativa = EstimativaTempo(relogio=relogio)
    for t, f in amostras:
        relogio.agora = t
        trabalho.estimativa.registrar(f)
        trabalho.progresso = f
    return trabalho


def test_a_api_mostra_calculando_no_comeco():
    estado = trabalho_processando([(0, 0.0), (3, 0.01)]).como_dict()
    assert estado["restante"] is None
    assert estado["restante_texto"] == "calculando..."


def test_a_api_entrega_o_tempo_restante_pronto():
    estado = trabalho_processando([(0, 0.0), (60, 0.25), (120, 0.50)]).como_dict()
    assert estado["restante"] == 120
    assert estado["restante_texto"] == "faltam ~2 min"
    assert estado["decorrido"] == 120


def test_concluido_nao_tem_tempo_restante_para_mostrar():
    trabalho = trabalho_processando([(0, 0.0), (60, 0.5)])
    trabalho.estado = "concluido"
    estado = trabalho.como_dict()
    assert (estado["restante"], estado["restante_texto"]) == (0, "")


def test_erro_nao_tem_estimativa():
    trabalho = trabalho_processando([(0, 0.0), (60, 0.5)])
    trabalho.estado = "erro"
    assert trabalho.como_dict()["restante"] is None


def test_na_fila_nao_tem_estimativa():
    estado = web.Trabalho(id="x", nome_video="v.mp4").como_dict()
    assert (estado["restante"], estado["restante_texto"], estado["decorrido"]) == (None, "", 0)


# --- Na CLI ------------------------------------------------------------------


def test_a_linha_da_cli_mostra_porcentagem_tempo_e_etapa():
    linha = cli._linha_de_progresso("Transcrevendo a faixa 2 (várias pessoas), 2 de 3", 0.5, 300)
    assert "50.0%" in linha
    assert "faltam ~5 min" in linha
    assert "2 de 3" in linha


def test_a_linha_da_cli_tem_largura_fixa():
    """É reescrita no lugar com \\r: uma linha mais curta deixaria restos da anterior."""
    curta = cli._linha_de_progresso("x", 0.1, None)
    longa = cli._linha_de_progresso("Transcrevendo " * 10, 0.9, 7200)
    assert len(curta) == len(longa)
    assert "calculando..." in curta


def test_mensagem_longa_perde_primeiro_a_parte_descritiva():
    linha = cli._linha_de_progresso(
        "Analisando as vozes da faixa 2 (várias pessoas), 2 de 3", 0.6, 30
    )
    assert linha.rstrip().endswith("Analisando as vozes da faixa 2, 2 de 3")
    assert "…" not in linha


def test_mensagem_que_cabe_fica_inteira():
    assert cli._encurtar("Transcrevendo a faixa 3 (Caio), 2 de 3") == (
        "Transcrevendo a faixa 3 (Caio), 2 de 3"
    )


@pytest.mark.parametrize(
    "mensagem",
    [
        "Analisando as vozes da faixa 12 (várias pessoas), 11 de 12",
        "Transcrevendo a faixa 2 (Maria Eduarda de Albuquerque Figueiredo), 2 de 10",
        "Transcrevendo a faixa com uma descrição enorme que não tem parênteses, 3 de 4",
        "Etapa sem posição com um texto comprido demais para caber na linha toda",
    ],
)
def test_nunca_corta_no_meio_de_palavra_nem_perde_a_posicao(mensagem):
    curta = cli._encurtar(mensagem)
    assert len(curta) <= cli._LARGURA_MENSAGEM
    posicao = cli._POSICAO.search(mensagem)
    if posicao:
        assert curta.endswith(posicao.group(0).lstrip(", "))
    palavras = set(mensagem.replace(",", " ").replace("(", " ").replace(")", " ").split())
    for pedaco in curta.replace("…", " ").replace(",", " ").split():
        assert pedaco in palavras, pedaco  # nenhuma palavra pela metade
