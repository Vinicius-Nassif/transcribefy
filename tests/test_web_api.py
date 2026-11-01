"""Endpoints do bloco da página nova: faixas, idiomas e reenvio da configuração."""

import json

import pytest
from fastapi.testclient import TestClient

from transcritor import copias, ffmpeg_tools, pipeline, web


class FilaImediata:
    def submit(self, funcao, *args):
        funcao(*args)


def faixas_do_arquivo(quantas):
    return [
        ffmpeg_tools.FaixaAudio(ordem=i, stream=i + 1, codec="aac",
                                sample_rate=48000, layout="stereo")
        for i in range(quantas)
    ]


@pytest.fixture
def ambiente(monkeypatch):
    monkeypatch.setattr(web, "_executor", FilaImediata())
    monkeypatch.setattr(web, "_trabalhos", {})
    estado = {"faixas": 2, "recebidas": [], "falhar": False}
    monkeypatch.setattr(
        web.ffmpeg_tools, "listar_faixas_audio", lambda _v: faixas_do_arquivo(estado["faixas"])
    )

    def executar(config, relatar=None):
        if estado["falhar"]:
            raise RuntimeError("falhou")
        estado["recebidas"].append(config)
        return pipeline.Resultado(falas=[])

    monkeypatch.setattr(web.pipeline, "executar", executar)
    return TestClient(web.app), estado


TRES_FAIXAS = json.dumps([
    {"numero": 1, "tipo": "uma", "nome": "GM"},
    {"numero": 2, "tipo": "varias"},
    {"numero": 3, "tipo": "uma", "nome": "Caio"},
])
DUAS_FAIXAS = json.dumps([
    {"numero": 1, "tipo": "uma", "nome": "GM"},
    {"numero": 2, "tipo": "varias", "nomes": ["Ana"]},
])


def enviar(http, **dados):
    return http.post(
        "/api/trabalhos", files={"video": ("sessao.mp4", b"video", "video/mp4")}, data=dados
    )


# --- Faixa que o arquivo não tem: a cópia fica --------------------------------


def test_faixa_que_o_arquivo_nao_tem_guarda_a_copia_e_informa_as_faixas(ambiente):
    http, estado = ambiente
    resposta = enviar(http, faixas=TRES_FAIXAS)

    assert resposta.status_code == 422
    corpo = resposta.json()
    assert "faixa 3 não existe" in corpo["detail"]
    assert corpo["total_faixas"] == 2
    assert len(corpo["faixas_no_arquivo"]) == 2
    pasta = web.RAIZ_TRABALHOS / corpo["id"]
    assert (pasta / "sessao.mp4").is_file()
    assert copias.ler(pasta).estado == copias.RECUSADO
    assert estado["recebidas"] == []


def test_reenviar_so_a_configuracao_usa_a_copia_guardada(ambiente):
    http, estado = ambiente
    identificador = enviar(http, faixas=TRES_FAIXAS, termos="Strahd").json()["id"]

    resposta = http.post(
        f"/api/trabalhos/{identificador}/configuracao",
        data={"faixas": DUAS_FAIXAS, "termos": "Strahd"},
    )
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["id"] == identificador
    (config,) = estado["recebidas"]
    assert [f.numero for f in config.faixas] == [0, 1]
    assert http.get(f"/api/trabalhos/{identificador}").json()["estado"] == "concluido"
    assert not (web.RAIZ_TRABALHOS / identificador / "sessao.mp4").exists()  # sucesso: some


def test_a_configuracao_nova_fica_no_registro(ambiente):
    """Se a nova configuração falhar, "tentar novamente" usa ela, não a recusada."""
    http, estado = ambiente
    identificador = enviar(http, faixas=TRES_FAIXAS).json()["id"]
    estado["falhar"] = True
    http.post(f"/api/trabalhos/{identificador}/configuracao", data={"faixas": DUAS_FAIXAS})

    registro = copias.ler(web.RAIZ_TRABALHOS / identificador)
    assert registro.estado == copias.FALHOU
    assert registro.parametros["faixas"] == DUAS_FAIXAS

    estado["falhar"] = False
    assert http.post(f"/api/trabalhos/{identificador}/tentar-novamente").status_code == 200
    assert len(estado["recebidas"][0].faixas) == 2


def test_reenviar_ainda_errado_continua_guardando_a_copia(ambiente):
    http, _ = ambiente
    identificador = enviar(http, faixas=TRES_FAIXAS).json()["id"]
    resposta = http.post(
        f"/api/trabalhos/{identificador}/configuracao", data={"faixas": TRES_FAIXAS}
    )
    assert resposta.status_code == 422
    assert (web.RAIZ_TRABALHOS / identificador / "sessao.mp4").is_file()


def test_configuracao_invalida_no_reenvio_e_400_e_a_copia_fica(ambiente):
    http, _ = ambiente
    identificador = enviar(http, faixas=TRES_FAIXAS).json()["id"]
    resposta = http.post(f"/api/trabalhos/{identificador}/configuracao", data={"faixas": "[]"})
    assert resposta.status_code == 400
    assert (web.RAIZ_TRABALHOS / identificador / "sessao.mp4").is_file()


def test_nao_reconfigura_trabalho_concluido(ambiente):
    http, _ = ambiente
    identificador = enviar(http, faixas=DUAS_FAIXAS).json()["id"]
    resposta = http.post(
        f"/api/trabalhos/{identificador}/configuracao", data={"faixas": DUAS_FAIXAS}
    )
    assert resposta.status_code == 409


def test_copias_recusadas_entram_na_limpeza(ambiente):
    http, _ = ambiente
    identificador = enviar(http, faixas=TRES_FAIXAS).json()["id"]
    pasta = web.RAIZ_TRABALHOS / identificador
    assert copias.antigas(web.RAIZ_TRABALHOS, dias=-1)[0].pasta == pasta  # qualquer idade
    assert copias.antigas(web.RAIZ_TRABALHOS, dias=7) == []  # recente: ainda fica


# --- Verificação das faixas antes do envio ----------------------------------


def test_verificar_aponta_os_nomes_repetidos_e_as_faixas(ambiente):
    http, _ = ambiente
    corpo = http.post("/api/faixas/verificar", data={"faixas": json.dumps([
        {"numero": 1, "tipo": "uma", "nome": "Vini"},
        {"numero": 2, "tipo": "varias", "nomes": ["vini", "Ana"]},
        {"numero": 3, "tipo": "uma", "nome": "Ana"},
    ])}).json()
    assert corpo["erros"] == []
    assert corpo["repetidos"] == [
        {"nome": "Vini", "faixas": [1, 2]}, {"nome": "Ana", "faixas": [2, 3]},
    ]
    assert len(corpo["avisos"]) == 2


def test_verificar_devolve_o_erro_pronto_sem_falhar(ambiente):
    http, _ = ambiente
    resposta = http.post("/api/faixas/verificar", data={"faixas": "[]"})
    assert resposta.status_code == 200
    assert resposta.json()["erros"] == ["Escolha ao menos uma faixa de áudio para transcrever."]


def test_verificar_sem_repeticao(ambiente):
    http, _ = ambiente
    corpo = http.post("/api/faixas/verificar", data={"faixas": DUAS_FAIXAS}).json()
    assert corpo == {"erros": [], "avisos": [], "repetidos": []}


# --- Idiomas -----------------------------------------------------------------


def test_idiomas_do_audio_e_da_traducao(ambiente):
    http, _ = ambiente
    corpo = http.get("/api/idiomas").json()

    audio = corpo["audio"]
    assert audio["padrao"] == "pt"
    assert audio["idiomas"][0] == {"codigo": "auto", "nome": "Detectar automaticamente"}
    codigos_audio = {i["codigo"] for i in audio["idiomas"]}
    assert {"pt", "en", "yue"} <= codigos_audio
    assert all(i["nome"] != i["codigo"] for i in audio["idiomas"])  # todos com nome

    traducao = corpo["traducao"]
    assert (traducao["motor"], traducao["online"]) == ("google", True)
    nomes = [i["nome"] for i in traducao["idiomas"]]
    assert {"codigo": "en", "nome": "Inglês"} in traducao["idiomas"]
    assert nomes.index("Árabe") < nomes.index("Basco")  # ordem ignora o acento


def test_os_idiomas_vem_do_motor_ativo(ambiente, monkeypatch):
    from transcritor import traducao

    class Offline:
        nome, descricao, online = "offline", "teste", False

        def idiomas(self):
            return {"eo": "Esperanto"}

        def traduzir(self, linhas, origem, destino):
            return list(linhas)

    monkeypatch.setitem(traducao.MOTORES, "offline", Offline())
    monkeypatch.setattr(traducao, "PADRAO", "offline")
    http, _ = ambiente
    corpo = http.get("/api/idiomas").json()["traducao"]
    assert corpo["motor"] == "offline" and corpo["online"] is False
    assert corpo["idiomas"] == [{"codigo": "eo", "nome": "Esperanto"}]
