"""Cópias de vídeo da interface web: o que pode e o que nunca pode ser apagado."""

import json
import time

import pytest

from transcritor import copias

DIA = 86400


def envio(raiz, identificador="abc123abc123", video="sessao.mp4", conteudo=b"video"):
    """Uma pasta de trabalho como a que o web.py cria: a cópia mais o registro."""
    pasta = raiz / identificador
    pasta.mkdir(parents=True)
    (pasta / video).write_bytes(conteudo)
    copias.registrar(pasta, video, {"modelo": "leve"})
    return pasta


def envelhecer(pasta, dias):
    caminho = pasta / copias.REGISTRO
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    dados["atualizado_em"] = time.time() - dias * DIA
    caminho.write_text(json.dumps(dados), encoding="utf-8")


def test_o_registro_guarda_o_nome_e_os_parametros(tmp_path):
    pasta = envio(tmp_path)
    registro = copias.ler(pasta)
    assert registro.video == "sessao.mp4"
    assert registro.estado == copias.AGUARDANDO
    assert registro.parametros == {"modelo": "leve"}
    assert copias.copia(pasta) == pasta / "sessao.mp4"


@pytest.mark.parametrize("nome", ["../fora.mp4", "a/b.mp4", "..", "", copias.REGISTRO])
def test_recusa_registrar_nome_que_sairia_da_pasta(tmp_path, nome):
    with pytest.raises(ValueError):
        copias.registrar(tmp_path, nome, {})


def test_concluir_apaga_so_a_copia_e_mantem_as_transcricoes(tmp_path):
    pasta = envio(tmp_path)
    (pasta / "sessao.md").write_text("transcrição", encoding="utf-8")
    copias.concluir(pasta)
    assert not (pasta / "sessao.mp4").exists()
    assert not (pasta / copias.REGISTRO).exists()
    assert (pasta / "sessao.md").read_text(encoding="utf-8") == "transcrição"


def test_a_falha_mantem_a_copia_e_registra_o_erro(tmp_path):
    pasta = envio(tmp_path)
    copias.marcar_falha(pasta, "sem memória")
    assert (pasta / "sessao.mp4").is_file()
    registro = copias.ler(pasta)
    assert registro.estado == copias.FALHOU
    assert registro.erro == "sem memória"


def test_registro_adulterado_nao_aponta_para_fora_da_pasta(tmp_path):
    """Mesmo com o trabalho.json editado à mão, nada fora da pasta é apagado."""
    original = tmp_path / "original.mp4"
    original.write_bytes(b"do usuario")
    pasta = envio(tmp_path / "trabalhos")
    dados = json.loads((pasta / copias.REGISTRO).read_text(encoding="utf-8"))
    dados["video"] = "../../original.mp4"
    (pasta / copias.REGISTRO).write_text(json.dumps(dados), encoding="utf-8")

    assert copias.copia(pasta) is None
    copias.concluir(pasta)
    assert original.read_bytes() == b"do usuario"


def test_link_simbolico_nao_e_tratado_como_copia(tmp_path):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"do usuario")
    pasta = tmp_path / "trabalhos" / "abc123abc123"
    pasta.mkdir(parents=True)
    try:
        (pasta / "sessao.mp4").symlink_to(original)
    except OSError:
        pytest.skip("o sistema não permite criar links simbólicos")
    copias.registrar(pasta, "sessao.mp4", {})
    assert copias.copia(pasta) is None
    copias.concluir(pasta)
    assert original.read_bytes() == b"do usuario"


def test_antigas_so_lista_copias_alem_do_prazo(tmp_path):
    velha = envio(tmp_path, "aaaaaaaaaaaa")
    nova = envio(tmp_path, "bbbbbbbbbbbb")
    envelhecer(velha, 8)
    envelhecer(nova, 2)
    listadas = copias.antigas(tmp_path, dias=7)
    assert [c.pasta for c in listadas] == [velha]
    assert listadas[0].tamanho == len(b"video")


def test_pasta_sem_registro_nunca_e_listada(tmp_path):
    """Envios anteriores a esta regra, ou qualquer outra coisa na pasta, ficam."""
    antiga = tmp_path / "cccccccccccc"
    antiga.mkdir()
    (antiga / "sessao.mp4").write_bytes(b"video")
    assert copias.antigas(tmp_path, dias=0) == []


def test_remover_apaga_copia_e_registro_e_preserva_transcricoes(tmp_path):
    com_saida = envio(tmp_path, "aaaaaaaaaaaa")
    (com_saida / "sessao.txt").write_text("parcial", encoding="utf-8")
    so_copia = envio(tmp_path, "bbbbbbbbbbbb")
    for pasta in (com_saida, so_copia):
        envelhecer(pasta, 30)

    removidas = copias.remover(copias.antigas(tmp_path, dias=7))

    assert len(removidas) == 2
    assert not (com_saida / "sessao.mp4").exists()
    assert (com_saida / "sessao.txt").is_file()  # a pasta fica, com a transcrição
    assert not so_copia.exists()  # vazia, a pasta vai junto


def test_remover_ignora_copia_que_mudou_desde_a_listagem(tmp_path):
    """Uma nova tentativa entre listar e remover renova a cópia: ela não é apagada."""
    pasta = envio(tmp_path)
    envelhecer(pasta, 30)
    listadas = copias.antigas(tmp_path, dias=7)
    (pasta / copias.REGISTRO).unlink()
    assert copias.remover(listadas) == []
    assert (pasta / "sessao.mp4").is_file()


def test_raiz_inexistente_nao_e_erro(tmp_path):
    assert copias.antigas(tmp_path / "nao-existe") == []
