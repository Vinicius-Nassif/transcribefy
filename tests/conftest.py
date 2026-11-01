"""Configuração comum a todos os testes."""

import pytest


@pytest.fixture(autouse=True)
def _dados_isolados(tmp_path_factory, monkeypatch):
    """Nenhum teste escreve na pasta `dados/` real do projeto.

    A interface web grava os envios em `dados/trabalhos` e o histórico do
    contexto em `dados/historico-contexto.json`, relativos à pasta de onde roda.
    Aqui os dois apontam para uma pasta temporária; um teste que precise de
    outro lugar ainda pode sobrescrevê-los.
    """
    from transcritor import web

    dados = tmp_path_factory.mktemp("dados")
    monkeypatch.setattr(web, "RAIZ_TRABALHOS", dados / "trabalhos")
    monkeypatch.setattr(web, "ARQUIVO_HISTORICO", dados / "historico-contexto.json")
