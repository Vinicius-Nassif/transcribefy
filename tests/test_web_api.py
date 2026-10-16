"""A API web de ponta a ponta, com um arquivo de verdade e o pipeline falso.

O arquivo de duas trilhas é gerado pelo ffmpeg (3 s de tom), então o envio, a
inspeção e a validação são reais; só a transcrição é substituída.
"""

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from transcritor import ffmpeg_tools, pipeline, temporarios, web
from transcritor.fala import Fala


@pytest.fixture(scope="module")
def gravacao(tmp_path_factory) -> Path:
    destino = tmp_path_factory.mktemp("midia") / "sessao.mka"
    tom = "sine=frequency={}:duration=3"
    subprocess.run(
        [ffmpeg_tools.localizar_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", tom.format(440), "-f", "lavfi", "-i", tom.format(660),
         "-map", "0", "-map", "1", "-c:a", "pcm_s16le", str(destino)],
        check=True,
    )
    return destino


@pytest.fixture
def cliente(monkeypatch, tmp_path):
    monkeypatch.setenv("TRANSCRITOR_SAIDA", str(tmp_path / "saida"))
    monkeypatch.setenv("TRANSCRITOR_DADOS", str(tmp_path / "dados"))
    monkeypatch.setattr(temporarios, "raiz", lambda: tmp_path / "tmp")
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(web.temporarios.tempfile, "tempdir", str(tmp_path / "tmp"))
    web._trabalhos.clear()
    with TestClient(web.app) as cliente:
        yield cliente


def pedido(**extra) -> str:
    base = {
        "faixas": [
            {"trilha": 1, "nomes": ["GM"]},
            {"trilha": 2, "nomes": ["Ana"], "varias_pessoas": True, "vozes": 2},
        ],
        "formatos": ["txt"],
    }
    return json.dumps({**base, **extra})


def enviar(cliente, gravacao, config: str):
    with gravacao.open("rb") as arquivo:
        return cliente.post(
            "/api/trabalhos", data={"config": config},
            files={"video": (gravacao.name, arquivo, "audio/x-matroska")},
        )


def esperar(cliente, identificador, estados=("concluido", "erro", "cancelado")):
    for _ in range(200):
        estado = cliente.get(f"/api/trabalhos/{identificador}").json()
        if estado["estado"] in estados:
            return estado
        time.sleep(0.02)
    raise AssertionError(f"trabalho parado em {estado['estado']}")


def pastas_temporarias(tmp_path) -> list[Path]:
    return list((tmp_path / "tmp").glob(f"{temporarios.PREFIXO}*"))


def falso_executar(capturado: dict):
    def executar(config, relatar=None, cancelado=None):
        capturado["config"] = config
        capturado["arquivos_durante"] = sorted(p.name for p in Path(config.video).parent.iterdir())
        destino = Path(config.destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.with_suffix(".txt").write_text("GM: oi", encoding="utf-8")
        relatar("Transcrevendo", 0.5)
        return pipeline.Resultado(
            falas=[Fala(0.0, 1.0, "oi", 0, locutor="GM")],
            arquivos=[destino.with_suffix(".txt")], locutores=["GM"], duracao=1.0,
        )
    return executar


def test_sucesso_guarda_o_resultado_e_apaga_o_envio(cliente, gravacao, monkeypatch, tmp_path):
    capturado = {}
    monkeypatch.setattr(pipeline, "executar", falso_executar(capturado))
    resposta = enviar(cliente, gravacao, pedido())
    assert resposta.status_code == 200, resposta.text
    estado = esperar(cliente, resposta.json()["id"])

    assert estado["estado"] == "concluido"
    assert "0-sessao.mka" in capturado["arquivos_durante"]
    assert pastas_temporarias(tmp_path) == []  # envio removido
    pasta = Path(estado["pasta"])
    assert pasta.parent == (tmp_path / "saida").resolve()  # D5: em saida/<nome>-<data>/
    assert pasta.name.startswith("sessao-")
    baixado = cliente.get(f"/api/trabalhos/{estado['id']}/arquivos/sessao.txt")
    assert baixado.text == "GM: oi"


def test_o_toggle_desligado_ignora_o_intervalo(cliente, gravacao, monkeypatch):
    capturado = {}
    monkeypatch.setattr(pipeline, "executar", falso_executar(capturado))
    config = pedido(intervalo={"ativo": False, "inicio": 1, "fim": 2})
    esperar(cliente, enviar(cliente, gravacao, config).json()["id"])
    assert (capturado["config"].inicio, capturado["config"].fim) == (0.0, None)


def test_o_toggle_ligado_aplica_o_intervalo(cliente, gravacao, monkeypatch):
    capturado = {}
    monkeypatch.setattr(pipeline, "executar", falso_executar(capturado))
    config = pedido(intervalo={"ativo": True, "inicio": 1, "fim": 2})
    esperar(cliente, enviar(cliente, gravacao, config).json()["id"])
    assert (capturado["config"].inicio, capturado["config"].fim) == (1.0, 2.0)


@pytest.mark.parametrize(
    ("config", "trecho"),
    [
        (pedido(intervalo={"ativo": True, "inicio": 2, "fim": 1}), "depois do início"),
        (pedido(intervalo={"ativo": True, "inicio": 0, "fim": 60}), "passa da duração"),
        (pedido(faixas=[{"trilha": 3}]), "tem 2 trilha"),
        (pedido(faixas=[]), "faixas"),
        (pedido(faixas=[{"trilha": 1, "vozes": 0}]), "vozes"),
        ("não é json", "Configuração inválida"),
    ],
)
def test_erros_de_preenchimento_voltam_na_hora_e_nada_sobra(
    cliente, gravacao, tmp_path, config, trecho
):
    resposta = enviar(cliente, gravacao, config)
    assert resposta.status_code in (400, 422)
    assert trecho in resposta.json()["detail"]
    assert pastas_temporarias(tmp_path) == []


def test_erro_no_processamento_limpa_tudo(cliente, gravacao, monkeypatch, tmp_path):
    def quebra(config, relatar=None, cancelado=None):
        destino = Path(config.destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.with_suffix(".txt").write_text("pela metade", encoding="utf-8")
        raise RuntimeError("motor caiu")

    monkeypatch.setattr(pipeline, "executar", quebra)
    estado = esperar(cliente, enviar(cliente, gravacao, pedido()).json()["id"])
    assert (estado["estado"], estado["erro"]) == ("erro", "motor caiu")
    assert pastas_temporarias(tmp_path) == []
    assert not any((tmp_path / "saida").glob("*/*"))  # nada pela metade


def test_cancelar_interrompe_e_limpa(cliente, gravacao, monkeypatch, tmp_path):
    comecou = threading.Event()

    def demorado(config, relatar=None, cancelado=None):
        comecou.set()
        for _ in range(500):
            relatar("Transcrevendo", 0.1)
            if cancelado.is_set():
                raise pipeline.Cancelado()
            time.sleep(0.01)
        raise AssertionError("não foi cancelado")

    monkeypatch.setattr(pipeline, "executar", demorado)
    identificador = enviar(cliente, gravacao, pedido()).json()["id"]
    assert comecou.wait(2)
    assert cliente.post(f"/api/trabalhos/{identificador}/cancelar").json()["estado"] == "cancelando"
    assert esperar(cliente, identificador)["estado"] == "cancelado"
    assert pastas_temporarias(tmp_path) == []


def test_o_progresso_traz_o_tempo_decorrido_e_o_restante(cliente, gravacao, monkeypatch):
    monkeypatch.setattr(pipeline, "executar", falso_executar({}))
    estado = esperar(cliente, enviar(cliente, gravacao, pedido()).json()["id"])
    assert "decorrido_s" in estado and "restante_s" in estado


def test_arquivo_por_faixa(cliente, gravacao, monkeypatch):
    """D1: uma faixa pode vir de um arquivo próprio, enviado em `arquivos`."""
    capturado = {}
    monkeypatch.setattr(pipeline, "executar", falso_executar(capturado))
    config = pedido(faixas=[{"trilha": 1, "nomes": ["GM"]}, {"trilha": 1, "arquivo": 0}])
    with gravacao.open("rb") as a, gravacao.open("rb") as b:
        resposta = cliente.post(
            "/api/trabalhos", data={"config": config},
            files=[("video", ("sessao.mka", a, "audio/x-matroska")),
                   ("arquivos", ("ana.mka", b, "audio/x-matroska"))],
        )
    assert resposta.status_code == 200, resposta.text
    esperar(cliente, resposta.json()["id"])
    assert Path(capturado["config"].faixas[1].arquivo).name == "1-ana.mka"


def test_trabalho_inexistente(cliente):
    assert cliente.get("/api/trabalhos/nada").status_code == 404
    assert cliente.post("/api/trabalhos/nada/cancelar").status_code == 404


def test_termos_de_contexto_entram_no_historico_e_podem_ser_apagados(
    cliente, gravacao, monkeypatch
):
    monkeypatch.setattr(pipeline, "executar", falso_executar({}))
    esperar(cliente, enviar(cliente, gravacao, pedido(contexto=["Strahd", "Barovia"])).json()["id"])
    assert sorted(cliente.get("/api/historico/contexto").json()["termos"]) == ["Barovia", "Strahd"]
    restantes = cliente.delete("/api/historico/contexto", params={"termo": "Strahd"}).json()
    assert restantes["termos"] == ["Barovia"]


def test_o_contexto_vira_a_dica_do_whisper(cliente, gravacao, monkeypatch):
    capturado = {}
    monkeypatch.setattr(pipeline, "executar", falso_executar(capturado))
    config = pedido(contexto=["Strahd", "strahd", "Barovia"])
    esperar(cliente, enviar(cliente, gravacao, config).json()["id"])
    assert capturado["config"].contexto == "Strahd, Barovia."


def test_idiomas_lista_so_o_que_funciona(cliente, monkeypatch, tmp_path):
    from transcritor import traducao

    monkeypatch.setattr(traducao, "google_disponivel", lambda: False)
    monkeypatch.setenv("TRANSCRITOR_MODELOS", str(tmp_path / "modelos"))
    assert cliente.get("/api/idiomas", params={"origem": "pt"}).json()["idiomas"] == []


def test_nenhum_idioma_selecionado_e_sem_traducao(cliente, gravacao, monkeypatch):
    capturado = {}
    monkeypatch.setattr(pipeline, "executar", falso_executar(capturado))
    esperar(cliente, enviar(cliente, gravacao, pedido(traducoes=[])).json()["id"])
    assert list(capturado["config"].traducoes) == []


def test_dois_envios_do_mesmo_arquivo_nao_dividem_a_pasta(cliente, gravacao, monkeypatch):
    monkeypatch.setattr(pipeline, "executar", falso_executar({}))
    monkeypatch.setattr(web.time, "strftime", lambda *_: "20261016-010000")  # mesmo segundo
    a = esperar(cliente, enviar(cliente, gravacao, pedido()).json()["id"])
    b = esperar(cliente, enviar(cliente, gravacao, pedido()).json()["id"])
    assert a["pasta"] != b["pasta"]
    assert Path(a["pasta"]).is_dir() and Path(b["pasta"]).is_dir()


def test_historico_sem_permissao_nao_impede_a_transcricao(cliente, gravacao, monkeypatch, tmp_path):
    from transcritor import historico

    def sem_permissao(termos, arquivo=None):
        raise PermissionError("somente leitura")

    monkeypatch.setattr(pipeline, "executar", falso_executar({}))
    monkeypatch.setattr(historico, "registrar", sem_permissao)
    resposta = enviar(cliente, gravacao, pedido(contexto=["Strahd"]))
    assert resposta.status_code == 200, resposta.text
    assert any("histórico" in a for a in resposta.json()["avisos"])
    assert esperar(cliente, resposta.json()["id"])["estado"] == "concluido"
