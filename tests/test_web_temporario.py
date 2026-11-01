"""A cópia do vídeo enviada pela web: apagada no sucesso, guardada na falha.

O pipeline é substituído por um falso e a fila roda na hora, então nada aqui
precisa de modelo, de ffmpeg nem de esperar uma thread.
"""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from transcritor import cli, copias, ffmpeg_tools, pipeline, web
from transcritor.fala import Fala


class FilaImediata:
    """Executa o trabalho na mesma hora, no lugar do ThreadPoolExecutor."""

    def submit(self, funcao, *args):
        funcao(*args)


def faixa(ordem):
    return ffmpeg_tools.FaixaAudio(
        ordem=ordem, stream=ordem + 1, codec="aac", sample_rate=48000, layout="stereo"
    )


def resultado_falso(config):
    destino = Path(config.destino).with_suffix(".md")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("# Transcrição", encoding="utf-8")
    return pipeline.Resultado(
        falas=[Fala(inicio=0.0, fim=1.0, texto="oi", faixa=0, locutor="GM")],
        arquivos=[destino],
        locutores=["GM"],
        duracao=1.0,
    )


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    raiz = tmp_path / "trabalhos"
    monkeypatch.setattr(web, "RAIZ_TRABALHOS", raiz)
    monkeypatch.setattr(web, "_executor", FilaImediata())
    monkeypatch.setattr(web, "_trabalhos", {})
    monkeypatch.setattr(
        web.ffmpeg_tools, "listar_faixas_audio", lambda _video: [faixa(0), faixa(1)]
    )
    estado = {"falhar": False, "chamadas": 0}

    def executar(config, relatar=None):
        estado["chamadas"] += 1
        assert Path(config.video).is_file(), "o pipeline recebeu uma cópia inexistente"
        if estado["falhar"]:
            raise RuntimeError("a GPU ficou sem memória")
        return resultado_falso(config)

    monkeypatch.setattr(web.pipeline, "executar", executar)
    return raiz, estado


def enviar(cliente, nome="sessao.mp4"):
    resposta = cliente.post(
        "/api/trabalhos",
        files={"video": (nome, b"conteudo do video", "video/mp4")},
        data={"nomes": "Ana,Bruno", "jogadores": "2"},
    )
    assert resposta.status_code == 200, resposta.text
    return resposta.json()["id"]


def test_sucesso_apaga_a_copia_e_mantem_as_transcricoes(ambiente):
    raiz, _ = ambiente
    cliente = TestClient(web.app)
    identificador = enviar(cliente)

    pasta = raiz / identificador
    assert not (pasta / "sessao.mp4").exists()
    assert not (pasta / copias.REGISTRO).exists()
    assert (pasta / "sessao.md").is_file()

    estado = cliente.get(f"/api/trabalhos/{identificador}").json()
    assert estado["estado"] == "concluido"
    baixado = cliente.get(f"/api/trabalhos/{identificador}/arquivos/sessao.md")
    assert baixado.status_code == 200


def test_falha_mantem_a_copia_e_registra_o_erro(ambiente):
    raiz, estado = ambiente
    estado["falhar"] = True
    cliente = TestClient(web.app)
    identificador = enviar(cliente)

    pasta = raiz / identificador
    assert (pasta / "sessao.mp4").read_bytes() == b"conteudo do video"
    registro = copias.ler(pasta)
    assert registro.estado == copias.FALHOU
    assert "sem memória" in registro.erro
    assert cliente.get(f"/api/trabalhos/{identificador}").json()["estado"] == "erro"


def test_tentar_novamente_reusa_a_copia_e_a_apaga_no_sucesso(ambiente):
    raiz, estado = ambiente
    estado["falhar"] = True
    cliente = TestClient(web.app)
    identificador = enviar(cliente)

    estado["falhar"] = False
    resposta = cliente.post(f"/api/trabalhos/{identificador}/tentar-novamente")

    assert resposta.status_code == 200, resposta.text
    assert estado["chamadas"] == 2
    assert cliente.get(f"/api/trabalhos/{identificador}").json()["estado"] == "concluido"
    assert not (raiz / identificador / "sessao.mp4").exists()


def test_tentar_novamente_usa_os_mesmos_parametros_do_envio(ambiente, monkeypatch):
    _, estado = ambiente
    estado["falhar"] = True
    cliente = TestClient(web.app)
    identificador = enviar(cliente)

    recebidas = []
    monkeypatch.setattr(
        web.pipeline, "executar", lambda c, r=None: recebidas.append(c) or resultado_falso(c)
    )
    cliente.post(f"/api/trabalhos/{identificador}/tentar-novamente")
    (config,) = recebidas
    assert list(config.nomes_jogadores) == ["Ana", "Bruno"]
    assert config.n_jogadores == 2


def test_falha_de_novo_mantem_a_copia_para_outra_tentativa(ambiente):
    raiz, estado = ambiente
    estado["falhar"] = True
    cliente = TestClient(web.app)
    identificador = enviar(cliente)
    cliente.post(f"/api/trabalhos/{identificador}/tentar-novamente")

    assert (raiz / identificador / "sessao.mp4").is_file()
    assert copias.ler(raiz / identificador).estado == copias.FALHOU


def test_tentar_novamente_funciona_depois_de_reiniciar_o_servidor(ambiente, monkeypatch):
    """A memória do processo some; a cópia e o registro em disco bastam."""
    _, estado = ambiente
    estado["falhar"] = True
    cliente = TestClient(web.app)
    identificador = enviar(cliente)

    monkeypatch.setattr(web, "_trabalhos", {})
    estado["falhar"] = False
    resposta = cliente.post(f"/api/trabalhos/{identificador}/tentar-novamente")
    assert resposta.status_code == 200
    assert cliente.get(f"/api/trabalhos/{identificador}").json()["estado"] == "concluido"


def test_trabalho_concluido_nao_pode_ser_tentado_de_novo(ambiente):
    cliente = TestClient(web.app)
    identificador = enviar(cliente)
    resposta = cliente.post(f"/api/trabalhos/{identificador}/tentar-novamente")
    assert resposta.status_code == 409


def test_sem_a_copia_pede_para_enviar_de_novo(ambiente):
    raiz, estado = ambiente
    estado["falhar"] = True
    cliente = TestClient(web.app)
    identificador = enviar(cliente)
    (raiz / identificador / "sessao.mp4").unlink()
    resposta = cliente.post(f"/api/trabalhos/{identificador}/tentar-novamente")
    assert resposta.status_code == 404
    assert "Envie o arquivo de novo" in resposta.json()["detail"]


@pytest.mark.parametrize("identificador", ["..", "nao-existe", "ABCDEF123456", "a" * 13])
def test_identificador_fora_do_formato_e_recusado(ambiente, identificador):
    resposta = TestClient(web.app).post(f"/api/trabalhos/{identificador}/tentar-novamente")
    assert resposta.status_code == 404


def test_arquivo_recusado_no_envio_nao_deixa_nada_para_tras(ambiente, monkeypatch):
    """Faixas de menos não é falha de transcrição: tentar de novo não resolveria."""
    raiz, _ = ambiente
    monkeypatch.setattr(web.ffmpeg_tools, "listar_faixas_audio", lambda _v: [faixa(0)])
    resposta = TestClient(web.app).post(
        "/api/trabalhos", files={"video": ("s.mp4", b"x", "video/mp4")}
    )
    assert resposta.status_code == 400
    assert list(raiz.iterdir()) == []


def test_parametro_invalido_e_recusado_sem_deixar_copia(ambiente):
    raiz, _ = ambiente
    resposta = TestClient(web.app).post(
        "/api/trabalhos",
        files={"video": ("s.mp4", b"x", "video/mp4")},
        data={"jogadores": "cinco"},
    )
    assert resposta.status_code == 400
    assert list(raiz.iterdir()) == []


def test_a_interface_limpa_as_copias_antigas_ao_subir(ambiente):
    raiz, estado = ambiente
    estado["falhar"] = True
    cliente = TestClient(web.app)
    velho = enviar(cliente, "velho.mp4")
    recente = enviar(cliente, "recente.mp4")
    registro = raiz / velho / copias.REGISTRO
    dados = json.loads(registro.read_text(encoding="utf-8"))
    dados["atualizado_em"] = time.time() - 30 * 86400
    registro.write_text(json.dumps(dados), encoding="utf-8")

    with TestClient(web.app):  # o `with` dispara a inicialização da aplicação
        pass

    assert not (raiz / velho).exists()
    assert (raiz / recente / "recente.mp4").is_file()


# --- Linha de comando ------------------------------------------------------


def test_a_cli_nunca_apaga_o_video_passado(tmp_path, monkeypatch):
    video = tmp_path / "sessao.mp4"
    video.write_bytes(b"original")
    monkeypatch.setattr(cli.pipeline, "executar", lambda c, r=None: resultado_falso(c))
    codigo = cli.main(["transcrever", str(video), "-o", str(tmp_path / "saida" / "s")])
    assert codigo == 0
    assert video.read_bytes() == b"original"


def test_a_cli_mantem_o_video_mesmo_quando_falha(tmp_path, monkeypatch):
    video = tmp_path / "sessao.mp4"
    video.write_bytes(b"original")

    def falhar(config, relatar=None):
        raise RuntimeError("falhou")

    monkeypatch.setattr(cli.pipeline, "executar", falhar)
    assert cli.main(["transcrever", str(video)]) == 1
    assert video.read_bytes() == b"original"


def copia_velha(raiz, identificador="aaaaaaaaaaaa"):
    pasta = raiz / identificador
    pasta.mkdir(parents=True)
    (pasta / "s.mp4").write_bytes(b"video")
    copias.registrar(pasta, "s.mp4", {})
    copias.marcar_falha(pasta, "falhou")
    caminho = pasta / copias.REGISTRO
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    dados["atualizado_em"] = time.time() - 10 * 86400
    caminho.write_text(json.dumps(dados), encoding="utf-8")
    return pasta / "s.mp4"


def test_limpar_mostra_o_que_vai_remover_e_respeita_o_nao(tmp_path, monkeypatch, capsys):
    video = copia_velha(tmp_path)
    monkeypatch.setattr("builtins.input", lambda _p: "n")
    assert cli.main(["limpar", "--pasta", str(tmp_path)]) == 0
    saida = capsys.readouterr().out
    assert str(video) in saida
    assert "Nada foi removido" in saida
    assert video.is_file()


def test_limpar_remove_depois_de_confirmar(tmp_path, monkeypatch):
    video = copia_velha(tmp_path)
    monkeypatch.setattr("builtins.input", lambda _p: "s")
    assert cli.main(["limpar", "--pasta", str(tmp_path)]) == 0
    assert not video.exists()


def test_limpar_sem_terminal_nao_remove_nada(tmp_path, monkeypatch):
    """Sem quem responda (ex.: num script), a resposta padrão é não."""
    video = copia_velha(tmp_path)

    def sem_entrada(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", sem_entrada)
    cli.main(["limpar", "--pasta", str(tmp_path)])
    assert video.is_file()


def test_limpar_respeita_o_prazo(tmp_path):
    video = copia_velha(tmp_path)
    assert cli.main(["limpar", "--pasta", str(tmp_path), "--dias", "30", "--sim"]) == 0
    assert video.is_file()
    assert cli.main(["limpar", "--pasta", str(tmp_path), "--dias", "7", "--sim"]) == 0
    assert not video.exists()
