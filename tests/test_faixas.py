"""Configuração das N faixas: leitura, conferência e avisos."""

import json

import pytest

from transcritor import faixas
from transcritor.faixas import UMA, VARIAS, Faixa

# --- Sintaxe da CLI ----------------------------------------------------------


def test_uma_pessoa_com_nome():
    assert faixas.ler_especificacao("1=GM") == Faixa(0, UMA, "GM")


def test_o_nome_de_uma_pessoa_e_tudo_depois_do_igual():
    """Dois-pontos e vírgulas no nome não viram opções."""
    lida = faixas.ler_especificacao("3=Mestre: Vini, o Cruel")
    assert lida.nome == "Mestre: Vini, o Cruel"
    assert lida.tipo == UMA


def test_uma_pessoa_sem_nome_usa_o_padrao():
    lida = faixas.ler_especificacao("3=")
    assert lida.tipo == UMA
    assert lida.nome_unico == "Faixa 3"


@pytest.mark.parametrize(
    ("texto", "quantidade", "nomes"),
    [
        ("2=varias", None, ()),
        ("2=VARIAS", None, ()),
        ("2=varias:5", 5, ()),
        ("2=varias:auto", None, ()),
        ("2=varias:5:Ana,Bruno", 5, ("Ana", "Bruno")),
        ("2=varias:: Ana , Bruno ,", 2, ("Ana", "Bruno")),
        ("2=varias:auto:Ana,Bruno", 2, ("Ana", "Bruno")),
    ],
)
def test_varias_pessoas(texto, quantidade, nomes):
    lida = faixas.ler_especificacao(texto)
    assert (lida.numero, lida.tipo) == (1, VARIAS)
    assert lida.n_locutores == quantidade
    assert tuple(lida.nomes) == nomes


@pytest.mark.parametrize(
    ("texto", "mensagem"),
    [
        ("GM", "formato"),
        ("x=GM", "número de faixa"),
        ("0=GM", "começa em 1"),
        ("2=varias:cinco", "quantidade"),
        ("2=varias:0", "ao menos 1"),
    ],
)
def test_especificacao_invalida(texto, mensagem):
    with pytest.raises(ValueError, match=mensagem):
        faixas.ler_especificacao(texto)


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [("0.4", (None, 0.4)), ("-1.5", (None, -1.5)), ("2=0.4", (1, 0.4)), ("3=-2", (2, -2.0))],
)
def test_deslocamento(texto, esperado):
    assert faixas.ler_deslocamento(texto) == esperado


@pytest.mark.parametrize("texto", ["abc", "2=abc", "0=1"])
def test_deslocamento_invalido(texto):
    with pytest.raises(ValueError):
        faixas.ler_deslocamento(texto)


# --- JSON da interface web ---------------------------------------------------


def test_json_com_os_dois_tipos():
    lidas = faixas.ler_json(json.dumps([
        {"numero": 1, "tipo": "uma", "nome": "GM"},
        {"numero": 2, "tipo": "varias", "nomes": ["Ana", "Bruno"], "pessoas": 4,
         "deslocamento": 0.5},
        {"numero": 3, "tipo": "varias", "nomes": ["Caio"]},
        {"numero": 4, "tipo": "varias", "pessoas": "auto"},
    ]))
    assert lidas[0] == Faixa(0, UMA, "GM")
    assert (lidas[1].n_locutores, tuple(lidas[1].nomes), lidas[1].deslocamento) == (
        4, ("Ana", "Bruno"), 0.5
    )
    assert lidas[2].n_locutores == 1  # sem quantidade: a dos nomes
    assert lidas[3].n_locutores is None  # sem nomes nem quantidade: estima


@pytest.mark.parametrize(
    "texto",
    [
        "não é json",
        '{"numero": 1}',
        '[1, 2]',
        '[{"numero": 1, "tipo": "muitas"}]',
        '[{"numero": 0, "tipo": "uma"}]',
        '[{"numero": 2, "tipo": "varias", "nomes": "Ana,Bruno"}]',
        '[{"numero": 2, "tipo": "varias", "pessoas": "cinco"}]',
        '[{"numero": 1, "tipo": "uma", "deslocamento": "muito"}]',
    ],
)
def test_json_invalido(texto):
    with pytest.raises(ValueError):
        faixas.ler_json(texto)


# --- Regras ------------------------------------------------------------------


def test_varias_sem_quantidade_usa_o_numero_de_nomes():
    assert faixas.varias(1, ["Ana", "Bruno", "Caio"]).n_locutores == 3
    assert faixas.varias(1).n_locutores is None
    assert faixas.varias(1, ["Ana"], n_locutores=4).n_locutores == 4


def test_padrao_reproduz_as_opcoes_antigas():
    """Inclusive `auto` com nomes, que antes estimava e continua estimando."""
    gm, grupo = faixas.padrao(n_jogadores=None, nomes_jogadores=["Ana", " ", "Bruno"])
    assert gm == Faixa(0, UMA, "GM")
    assert grupo.n_locutores is None
    assert tuple(grupo.nomes) == ("Ana", "Bruno")


def test_prefixo_com_uma_faixa_de_varias_pessoas_e_jogador():
    lista = [Faixa(0, UMA, "GM"), faixas.varias(1)]
    assert faixas.prefixo_de(lista[1], lista) == "Jogador"


def test_prefixo_com_duas_faixas_de_varias_pessoas_leva_a_faixa():
    lista = [faixas.varias(1), faixas.varias(2)]
    assert faixas.prefixo_de(lista[0], lista) == "Faixa 2 · Pessoa"
    assert faixas.prefixo_de(lista[1], lista) == "Faixa 3 · Pessoa"


def test_prefixo_informado_vence_o_automatico():
    lista = [faixas.varias(1), Faixa(2, VARIAS, prefixo="Convidado")]
    assert faixas.prefixo_de(lista[1], lista) == "Convidado"


# --- Conferência -------------------------------------------------------------


def test_nenhuma_faixa_e_recusada():
    with pytest.raises(ValueError, match="ao menos uma"):
        faixas.validar([])


def test_faixa_repetida_e_recusada():
    with pytest.raises(ValueError, match="mais de uma vez"):
        faixas.validar([Faixa(1, UMA, "A"), faixas.varias(1)])


def test_faixa_alem_do_arquivo_e_recusada():
    with pytest.raises(ValueError, match="faixa 3 não existe.*2 faixa"):
        faixas.validar([Faixa(2, UMA, "A")], no_arquivo=2)


def test_arquivo_sem_audio_e_recusado():
    with pytest.raises(ValueError, match="nenhuma faixa"):
        faixas.validar([Faixa(0, UMA, "A")], no_arquivo=0)


def test_tres_faixas_validas():
    faixas.validar([Faixa(0, UMA, "GM"), faixas.varias(1), Faixa(2, UMA, "Caio")], 3)


# --- Nomes repetidos ---------------------------------------------------------


def test_nome_em_duas_faixas_gera_aviso_e_nao_erro():
    lista = [Faixa(0, UMA, "Vini"), faixas.varias(1, ["vini ", "Ana"])]
    faixas.validar(lista)  # não levanta
    (aviso,) = faixas.nomes_repetidos(lista)
    assert '"Vini"' in aviso
    assert "faixas 1 e 2" in aviso


def test_nome_em_tres_faixas_cita_todas():
    lista = [Faixa(0, UMA, "Ana"), faixas.varias(1, ["Ana"]), Faixa(3, UMA, "ANA")]
    (aviso,) = faixas.nomes_repetidos(lista)
    assert "faixas 1, 2 e 4" in aviso
    assert "as falas das 3 faixas vão aparecer com o mesmo nome" in aviso


def test_o_aviso_diz_de_forma_direta_o_que_acontece():
    (aviso,) = faixas.nomes_repetidos([Faixa(0, UMA, "Vini"), faixas.varias(1, ["Vini"])])
    assert aviso == (
        'O nome "Vini" aparece nas faixas 1 e 2: as falas das duas faixas vão '
        "aparecer com o mesmo nome."
    )


def test_nomes_padrao_de_uma_pessoa_tambem_contam():
    """Duas faixas sem nome nunca colidem: o padrão leva o número da faixa."""
    assert faixas.nomes_repetidos([Faixa(0, UMA), Faixa(1, UMA)]) == []


def test_sem_repeticao_sem_aviso():
    assert faixas.nomes_repetidos(faixas.padrao(nomes_jogadores=["Ana", "Bruno"])) == []
