"""Linha de comando: as duas formas de descrever as faixas, sem misturar."""

from pathlib import Path

import pytest

from transcritor import cli, faixas, pipeline
from transcritor.faixas import UMA, Faixa


def faixas_de(*argumentos):
    args = cli._parser().parse_args(["transcrever", "s.mp4", *argumentos])
    return cli._faixas_dos_argumentos(args)


def test_sem_opcoes_e_o_arranjo_de_sempre():
    assert faixas_de() == faixas.padrao()


def test_opcoes_antigas_continuam_valendo():
    assert faixas_de(
        "--faixa-gm", "2", "--faixa-grupo", "1", "--nome-gm", "Mestre",
        "-n", "auto", "--nomes", "Ana,Bruno", "--deslocamento", "1.5",
    ) == faixas.padrao(
        faixa_gm=1, faixa_grupo=0, nome_gm="Mestre", n_jogadores=None,
        nomes_jogadores=["Ana", "Bruno"], deslocamento_grupo=1.5,
    )


def test_faixa_gm_zero_e_recusada_em_vez_de_virar_a_padrao():
    with pytest.raises(ValueError, match="começa em 1"):
        faixas_de("--faixa-gm", "0")


def test_estilo_novo_com_n_faixas():
    lidas = faixas_de(
        "--faixa", "1=Mestre", "--faixa", "2=varias:3:Ana,Bruno", "--faixa", "4=Duda",
    )
    assert lidas[0] == Faixa(0, UMA, "Mestre")
    assert (lidas[1].numero, lidas[1].n_locutores, tuple(lidas[1].nomes)) == (
        1, 3, ("Ana", "Bruno")
    )
    assert lidas[2] == Faixa(3, UMA, "Duda")


def test_deslocamento_por_faixa_no_estilo_novo():
    lidas = faixas_de(
        "--faixa", "1=GM", "--faixa", "2=varias",
        "--deslocamento", "2=0.4", "--deslocamento", "1=-0.1",
    )
    assert [f.deslocamento for f in lidas] == [-0.1, 0.4]


def test_deslocamento_por_faixa_tambem_vale_no_estilo_antigo():
    gm, grupo = faixas_de("--deslocamento", "1=0.3", "--deslocamento", "0.8")
    assert (gm.deslocamento, grupo.deslocamento) == (0.3, 0.8)


def test_deslocamento_de_faixa_que_nao_foi_escolhida():
    with pytest.raises(ValueError, match="faixa 3 não está entre"):
        faixas_de("--faixa", "1=GM", "--deslocamento", "3=1")


def test_deslocamento_sem_faixa_duas_vezes():
    with pytest.raises(ValueError, match="uma vez só"):
        faixas_de("--deslocamento", "1", "--deslocamento", "2")


@pytest.mark.parametrize(
    "antiga",
    [
        ["--faixa-gm", "1"], ["--faixa-grupo", "2"], ["--nome-gm", "GM"],
        ["-n", "5"], ["--nomes", "Ana"], ["--deslocamento", "0.4"],
    ],
)
def test_misturar_os_estilos_e_erro(antiga):
    with pytest.raises(ValueError, match="não combina"):
        faixas_de("--faixa", "1=GM", *antiga)


def test_faixa_repetida_e_erro():
    with pytest.raises(ValueError, match="mais de uma vez"):
        faixas_de("--faixa", "1=GM", "--faixa", "1=varias")


def test_especificacao_invalida_e_erro_do_argparse(capsys):
    with pytest.raises(SystemExit):
        faixas_de("--faixa", "GM")
    assert "FAIXA=NOME" in capsys.readouterr().err


def executar_falso(monkeypatch, recebidas):
    def executar(config, relatar=None):
        recebidas.append(config)
        return pipeline.Resultado(falas=[])

    monkeypatch.setattr(cli.pipeline, "executar", executar)


def test_nome_repetido_avisa_antes_e_transcreve_assim_mesmo(monkeypatch, capsys, tmp_path):
    recebidas = []
    executar_falso(monkeypatch, recebidas)
    codigo = cli.main([
        "transcrever", "s.mp4", "-o", str(tmp_path / "s"),
        "--faixa", "1=Vini", "--faixa", "2=varias::Vini,Ana",
    ])
    assert codigo == 0
    assert len(recebidas) == 1
    saida = capsys.readouterr().out
    assert saida.index('Aviso: O nome "Vini" aparece nas faixas 1 e 2') < saida.index("falas")


def test_erro_de_estilos_misturados_sai_com_codigo_1(monkeypatch, capsys):
    recebidas = []
    executar_falso(monkeypatch, recebidas)
    assert cli.main(["transcrever", "s.mp4", "--faixa", "1=GM", "--nome-gm", "X"]) == 1
    assert recebidas == []
    assert "não combina" in capsys.readouterr().err


def test_a_configuracao_leva_as_faixas_lidas(monkeypatch, tmp_path):
    recebidas = []
    executar_falso(monkeypatch, recebidas)
    cli.main(["transcrever", "s.mp4", "-o", str(tmp_path / "s"), "--faixa", "3=Caio"])
    (config,) = recebidas
    assert list(config.faixas) == [Faixa(2, UMA, "Caio")]
    assert config.video == Path("s.mp4")
