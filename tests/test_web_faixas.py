"""API web com N faixas: o campo `faixas`, os campos antigos e os avisos."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from transcritor import copias, ffmpeg_tools, pipeline, web
from transcritor.faixas import UMA, Faixa


class FilaImediata:
    def submit(self, funcao, *args):
        funcao(*args)


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    raiz = tmp_path / "trabalhos"
    monkeypatch.setattr(web, "RAIZ_TRABALHOS", raiz)
    monkeypatch.setattr(web, "_executor", FilaImediata())
    monkeypatch.setattr(web, "_trabalhos", {})
    monkeypatch.setattr(
        web.ffmpeg_tools, "listar_faixas_audio",
        lambda _v: [
            ffmpeg_tools.FaixaAudio(ordem=i, stream=i + 1, codec="aac",
                                    sample_rate=48000, layout="stereo")
            for i in range(3)
        ],
    )
    recebidas = []

    def executar(config, relatar=None):
        recebidas.append(config)
        return pipeline.Resultado(falas=[])

    monkeypatch.setattr(web.pipeline, "executar", executar)
    return raiz, recebidas


def enviar(dados):
    return TestClient(web.app).post(
        "/api/trabalhos",
        files={"video": ("sessao.mkv", b"video", "video/x-matroska")},
        data=dados,
    )


def test_faixas_em_json(ambiente):
    _, recebidas = ambiente
    resposta = enviar({"faixas": json.dumps([
        {"numero": 1, "tipo": "uma", "nome": "Mestre"},
        {"numero": 2, "tipo": "varias", "nomes": ["Ana", "Bruno"], "deslocamento": 0.4},
        {"numero": 3, "tipo": "uma", "nome": "Caio"},
    ])})
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["avisos"] == []
    (config,) = recebidas
    assert config.faixas[0] == Faixa(0, UMA, "Mestre")
    assert config.faixas[1].n_locutores == 2
    assert config.faixas[1].deslocamento == 0.4
    assert config.faixas[2] == Faixa(2, UMA, "Caio")


def test_sem_o_campo_faixas_valem_os_campos_antigos(ambiente):
    """É o que a página atual envia, e o que registros antigos guardaram."""
    _, recebidas = ambiente
    resposta = enviar({"nome_gm": "Mestre", "jogadores": "auto", "nomes": "Ana,Bruno",
                       "deslocamento": "1.5"})
    assert resposta.status_code == 200, resposta.text
    gm, grupo = recebidas[0].faixas
    assert gm == Faixa(0, UMA, "Mestre")
    assert (grupo.numero, grupo.n_locutores, grupo.deslocamento) == (1, None, 1.5)


def test_nome_repetido_volta_como_aviso_e_o_trabalho_segue(ambiente):
    _, recebidas = ambiente
    resposta = enviar({"faixas": json.dumps([
        {"numero": 1, "tipo": "uma", "nome": "Vini"},
        {"numero": 2, "tipo": "varias", "nomes": ["vini", "Ana"]},
    ])})
    assert resposta.status_code == 200
    (aviso,) = resposta.json()["avisos"]
    assert "faixas 1 e 2" in aviso
    assert len(recebidas) == 1

    estado = TestClient(web.app).get(f"/api/trabalhos/{resposta.json()['id']}").json()
    assert aviso in estado["avisos"]  # a página de resultado também o mostra


@pytest.mark.parametrize(
    ("faixas", "mensagem"),
    [
        ("não é json", "JSON"),
        ("[]", "ao menos uma"),
        ('[{"numero": 1, "tipo": "uma"}, {"numero": 1, "tipo": "varias"}]', "mais de uma vez"),
    ],
)
def test_faixas_invalidas_sao_recusadas_sem_deixar_copia(ambiente, faixas, mensagem):
    raiz, recebidas = ambiente
    resposta = enviar({"faixas": faixas})
    assert resposta.status_code == 400
    assert mensagem in resposta.json()["detail"]
    assert recebidas == []
    assert list(raiz.iterdir()) == []


def test_registro_anterior_ao_campo_faixas_ainda_pode_ser_tentado_de_novo(ambiente):
    """Uma cópia guardada antes desta mudança não tem `faixas` nos parâmetros."""
    raiz, recebidas = ambiente
    identificador = "abcdefabcdef"
    pasta = raiz / identificador
    pasta.mkdir(parents=True)
    (pasta / "sessao.mp4").write_bytes(b"video")
    antigos = {
        "modelo": "leve", "faixa_gm": 1, "faixa_grupo": 2, "nome_gm": "GM",
        "jogadores": "5", "nomes": "Ana", "formatos": "md", "idioma": "pt",
        "traduzir": "", "contexto": "", "deslocamento": 0.0, "inicio": 0.0,
        "fim": "", "juntar": True,
    }
    copias.registrar(pasta, "sessao.mp4", antigos)
    copias.marcar_falha(pasta, "falhou")

    resposta = TestClient(web.app).post(f"/api/trabalhos/{identificador}/tentar-novamente")
    assert resposta.status_code == 200, resposta.text
    gm, grupo = recebidas[0].faixas
    assert gm.nome == "GM"
    assert (grupo.n_locutores, tuple(grupo.nomes)) == (5, ("Ana",))
    assert recebidas[0].video == Path(pasta / "sessao.mp4")


def test_idioma_de_traducao_desconhecido_e_recusado(ambiente):
    raiz, recebidas = ambiente
    resposta = enviar({"traduzir": "en,klingon"})
    assert resposta.status_code == 400
    assert "klingon" in resposta.json()["detail"]
    assert recebidas == [] and list(raiz.iterdir()) == []


def test_varios_idiomas_de_traducao(ambiente):
    _, recebidas = ambiente
    assert enviar({"traduzir": "en,es"}).status_code == 200
    assert list(recebidas[0].traducoes) == ["en", "es"]
