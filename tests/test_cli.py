"""Como a linha de comando vira a lista de faixas do pipeline."""

from pathlib import Path

import pytest

from transcritor import cli


def faixas(*argv):
    args = cli._parser().parse_args(["transcrever", "s.mp4", *argv])
    return cli.faixas_dos_argumentos(args)


def test_sem_nada_e_a_mesa_de_duas_faixas_de_sempre():
    gm, grupo = faixas()
    assert (gm.trilha, gm.nomes, gm.varias_pessoas) == (0, ["GM"], False)
    assert (grupo.trilha, grupo.varias_pessoas, grupo.vozes, grupo.nomes) == (1, True, 5, [])


def test_os_atalhos_antigos_continuam_valendo():
    gm, grupo = faixas(
        "--faixa-gm", "2", "--faixa-grupo", "1", "--nome-gm", "Vini",
        "-n", "auto", "--nomes", "Ana, Bruno", "--deslocamento", "0.4",
    )
    assert (gm.trilha, gm.nomes) == (1, ["Vini"])
    assert (grupo.trilha, grupo.vozes, grupo.nomes, grupo.ajuste) == (0, None, ["Ana", "Bruno"], 0.4)


def test_faixa_de_uma_pessoa():
    (faixa,) = faixas("--faixa", "3; nomes=Ana")
    assert (faixa.trilha, faixa.nomes, faixa.varias_pessoas) == (2, ["Ana"], False)


def test_vozes_marca_a_faixa_como_de_varias_pessoas():
    (faixa,) = faixas("--faixa", "2; nomes=Ana,Bruno; vozes=auto; ajuste=-0.2; arquivo=b.flac")
    assert faixa.varias_pessoas and faixa.vozes is None
    assert (faixa.nomes, faixa.ajuste, faixa.arquivo) == (["Ana", "Bruno"], -0.2, Path("b.flac"))


def test_varias_faixas_na_ordem_dada():
    lista = faixas("--faixa", "1; nomes=GM", "--faixa", "2", "--faixa", "3; vozes=4")
    assert [f.trilha for f in lista] == [0, 1, 2]
    assert lista[2].vozes == 4


@pytest.mark.parametrize(
    ("espec", "mensagem"),
    [("x", "número inteiro"), ("1; cor=azul", "chave desconhecida"),
     ("1; nomes", "chave=valor"), ("1; vozes=muitas", "número inteiro")],
)
def test_especificacoes_invalidas_explicam_o_erro(espec, mensagem):
    with pytest.raises(ValueError, match=mensagem):
        faixas("--faixa", espec)


def test_faixa_e_atalho_juntos_sao_recusados():
    with pytest.raises(ValueError, match="não os dois"):
        faixas("--faixa", "1", "--nomes", "Ana")


def test_duracao_legivel():
    assert cli._duracao_legivel(42) == "42s"
    assert cli._duracao_legivel(125) == "2min05s"
    assert cli._duracao_legivel(3 * 3600 + 7 * 60) == "3h07"
