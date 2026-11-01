"""Histórico dos termos de contexto: o arquivo e a API."""

import json

import pytest
from fastapi.testclient import TestClient

from transcritor import ffmpeg_tools, historico, pipeline, web


def test_comeca_vazio(tmp_path):
    assert historico.listar(tmp_path / "h.json") == []


def test_registra_e_persiste_entre_leituras(tmp_path):
    arquivo = tmp_path / "sub" / "h.json"
    historico.registrar(["Strahd", "Barovia"], arquivo)
    assert historico.listar(arquivo) == ["Strahd", "Barovia"]
    dados = json.loads(arquivo.read_text(encoding="utf-8"))
    assert dados["versao"] == 1
    assert [t["usos"] for t in dados["termos"]] == [1, 1]


def test_o_mais_recente_vem_primeiro(tmp_path):
    arquivo = tmp_path / "h.json"
    historico.registrar(["Strahd", "Barovia"], arquivo)
    historico.registrar(["Ireena"], arquivo)
    historico.registrar(["Barovia"], arquivo)
    assert historico.listar(arquivo) == ["Barovia", "Ireena", "Strahd"]


def test_mesmo_termo_com_outra_caixa_soma_usos_e_fica_a_grafia_nova(tmp_path):
    arquivo = tmp_path / "h.json"
    historico.registrar(["strahd"], arquivo)
    historico.registrar(["  Strahd ", "  "], arquivo)
    (termo,) = json.loads(arquivo.read_text(encoding="utf-8"))["termos"]
    assert (termo["termo"], termo["usos"]) == ("Strahd", 2)


def test_o_mesmo_termo_duas_vezes_no_envio_conta_um_uso(tmp_path):
    arquivo = tmp_path / "h.json"
    historico.registrar(["Strahd", "STRAHD"], arquivo)
    (termo,) = json.loads(arquivo.read_text(encoding="utf-8"))["termos"]
    assert termo["usos"] == 1


def test_sem_termos_o_arquivo_nem_e_criado(tmp_path):
    arquivo = tmp_path / "h.json"
    historico.registrar(["", "   "], arquivo)
    assert not arquivo.exists()


def test_remover(tmp_path):
    arquivo = tmp_path / "h.json"
    historico.registrar(["Strahd", "Barovia"], arquivo)
    assert historico.remover(" strahd", arquivo)
    assert historico.listar(arquivo) == ["Barovia"]
    assert not historico.remover("Ireena", arquivo)


def test_arquivo_corrompido_vira_copia_e_recomeca(tmp_path):
    arquivo = tmp_path / "h.json"
    arquivo.write_text("{não é json", encoding="utf-8")
    assert historico.listar(arquivo) == []
    assert (tmp_path / "h.json.corrompido").is_file()
    historico.registrar(["Strahd"], arquivo)
    assert historico.listar(arquivo) == ["Strahd"]


def test_o_limite_descarta_os_usados_ha_mais_tempo(tmp_path, monkeypatch):
    """Todos no mesmo dia: o que decide é a ordem de uso, não a data."""
    monkeypatch.setattr(historico, "LIMITE", 2)
    arquivo = tmp_path / "h.json"
    for termo in ["a", "b", "c"]:
        historico.registrar([termo], arquivo)
    assert historico.listar(arquivo) == ["c", "b"]


@pytest.mark.parametrize(
    ("texto", "termos"),
    [
        ("Strahd\nBarovia\n\n  Ireena  \nstrahd", ["Strahd", "Barovia", "Ireena"]),
        ("Campanha de Ravenloft, em Barovia", ["Campanha de Ravenloft, em Barovia"]),
        ("", []),
    ],
)
def test_termos_do_contexto(texto, termos):
    assert historico.termos_do_contexto(texto) == termos


# --- Na API ------------------------------------------------------------------


class FilaImediata:
    def submit(self, funcao, *args):
        funcao(*args)


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr(web, "_executor", FilaImediata())
    monkeypatch.setattr(web, "_trabalhos", {})
    monkeypatch.setattr(
        web.ffmpeg_tools, "listar_faixas_audio",
        lambda _v: [ffmpeg_tools.FaixaAudio(ordem=i, stream=i + 1, codec="aac",
                                            sample_rate=48000, layout="stereo")
                    for i in range(2)],
    )
    recebidas = []
    monkeypatch.setattr(
        web.pipeline, "executar",
        lambda c, r=None: recebidas.append(c) or pipeline.Resultado(falas=[]),
    )
    return TestClient(web.app), recebidas


def enviar(cliente, **dados):
    return cliente.post(
        "/api/trabalhos", files={"video": ("s.mp4", b"video", "video/mp4")}, data=dados
    )


def test_o_envio_guarda_os_termos_e_os_repassa_ao_pipeline(cliente):
    http, recebidas = cliente
    assert enviar(http, termos="Strahd\nBarovia\n\nstrahd").status_code == 200
    assert list(recebidas[0].termos) == ["Strahd", "Barovia"]
    assert http.get("/api/historico/contexto").json() == {"termos": ["Strahd", "Barovia"]}
    assert web.ARQUIVO_HISTORICO.is_file()


def test_contexto_livre_continua_como_antes_e_nao_vai_ao_historico(cliente):
    """O texto livre (CLI, página antiga) chega ao pipeline igual."""
    http, recebidas = cliente
    enviar(http, contexto="Campanha de Ravenloft. Strahd, Barovia.")
    assert recebidas[0].contexto == "Campanha de Ravenloft. Strahd, Barovia."
    assert list(recebidas[0].termos) == []
    assert http.get("/api/historico/contexto").json() == {"termos": []}


def test_remover_pela_api(cliente):
    http, _ = cliente
    enviar(http, termos="Strahd\nBarovia")
    resposta = http.delete("/api/historico/contexto", params={"termo": "strahd"})
    assert resposta.json() == {"termos": ["Barovia"]}
    assert http.delete("/api/historico/contexto", params={"termo": "Ireena"}).status_code == 404


def test_falha_ao_gravar_o_historico_vira_aviso_e_o_trabalho_segue(cliente, monkeypatch):
    http, recebidas = cliente

    def sem_permissao(*_a, **_k):
        raise PermissionError("somente leitura")

    monkeypatch.setattr(web.historico, "registrar", sem_permissao)
    resposta = enviar(http, termos="Strahd")
    assert resposta.status_code == 200
    assert any("não puderam ser guardados" in a for a in resposta.json()["avisos"])
    assert len(recebidas) == 1


def test_envio_recusado_nao_guarda_os_termos(cliente):
    http, _ = cliente
    resposta = enviar(http, termos="Strahd", traduzir="klingon")
    assert resposta.status_code == 400
    assert http.get("/api/historico/contexto").json() == {"termos": []}
