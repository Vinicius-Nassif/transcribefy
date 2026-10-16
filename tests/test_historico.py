"""Histórico dos termos de contexto."""

import json

from transcritor import historico


def test_comeca_vazio(tmp_path):
    assert historico.listar(tmp_path / "h.json") == []


def test_registra_e_persiste_entre_leituras(tmp_path):
    arquivo = tmp_path / "sub" / "h.json"
    historico.registrar(["Strahd", "Barovia"], arquivo)
    assert sorted(historico.listar(arquivo)) == ["Barovia", "Strahd"]
    dados = json.loads(arquivo.read_text(encoding="utf-8"))
    assert dados["versao"] == 1


def test_mesmo_termo_com_outra_caixa_soma_usos_e_fica_a_grafia_nova(tmp_path):
    arquivo = tmp_path / "h.json"
    historico.registrar(["strahd"], arquivo)
    historico.registrar(["Strahd", "  "], arquivo)
    dados = json.loads(arquivo.read_text(encoding="utf-8"))
    assert dados["termos"] == [{"termo": "Strahd", "usos": 2, "ultimo_uso": dados["termos"][0]["ultimo_uso"]}]


def test_remover(tmp_path):
    arquivo = tmp_path / "h.json"
    historico.registrar(["Strahd", "Barovia"], arquivo)
    assert historico.remover("STRAHD", arquivo)
    assert historico.listar(arquivo) == ["Barovia"]
    assert not historico.remover("Ireena", arquivo)


def test_arquivo_corrompido_vira_copia_e_recomeca(tmp_path):
    arquivo = tmp_path / "h.json"
    arquivo.write_text("{não é json", encoding="utf-8")
    assert historico.listar(arquivo) == []
    assert (tmp_path / "h.json.corrompido").is_file()
    historico.registrar(["Strahd"], arquivo)
    assert historico.listar(arquivo) == ["Strahd"]


def test_limite_descarta_os_mais_antigos(tmp_path, monkeypatch):
    monkeypatch.setattr(historico, "LIMITE", 2)
    arquivo = tmp_path / "h.json"
    for termo in ["a", "b", "c"]:
        historico.registrar([termo], arquivo)
    assert len(historico.listar(arquivo)) == 2


def test_pasta_de_dados_respeita_a_variavel(monkeypatch, tmp_path):
    monkeypatch.setenv("TRANSCRITOR_DADOS", str(tmp_path))
    assert historico.arquivo_padrao() == tmp_path / "historico-contexto.json"
