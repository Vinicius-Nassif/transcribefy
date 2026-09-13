"""Catálogo de modelos e resolução de apelidos."""

import pytest

from transcritor import modelos


def test_o_padrao_existe_no_catalogo_e_usa_whisper():
    """O motor padrão precisa ser o que leva o contexto da frase em conta."""
    padrao = modelos.MODELOS_FALA[modelos.PADRAO]
    assert padrao.motor == modelos.MOTOR_WHISPER


def test_todo_apelido_bate_com_a_chave_do_catalogo():
    for chave, modelo in modelos.MODELOS_FALA.items():
        assert modelo.apelido == chave


def test_todo_modelo_declara_um_motor_conhecido():
    conhecidos = {modelos.MOTOR_WHISPER, modelos.MOTOR_VOSK}
    for modelo in (*modelos.MODELOS_FALA.values(), modelos.MODELO_LOCUTOR):
        assert modelo.motor in conhecidos


def test_os_apelidos_antigos_apontam_para_modelos_que_existem():
    """Comandos e scripts escritos antes do Whisper continuam valendo."""
    for antigo, atual in modelos.APELIDOS_ANTIGOS.items():
        assert atual in modelos.MODELOS_FALA, antigo


def test_so_o_vosk_e_baixado_por_url():
    assert modelos.MODELOS_FALA["vosk-pt"].url.endswith(".zip")
    with pytest.raises(ValueError, match="não é baixado por URL"):
        _ = modelos.MODELOS_FALA[modelos.PADRAO].url


def test_o_diretorio_sai_da_variavel_de_ambiente(monkeypatch, tmp_path):
    monkeypatch.setenv("TRANSCRITOR_MODELOS", str(tmp_path / "m"))
    assert modelos.diretorio_padrao() == tmp_path / "m"


def test_reconhece_um_modelo_whisper_pelo_conteudo_da_pasta(tmp_path):
    (tmp_path / "model.bin").write_bytes(b"")
    assert modelos._motor_do_caminho(tmp_path) == modelos.MOTOR_WHISPER


def test_reconhece_um_modelo_vosk_pelo_conteudo_da_pasta(tmp_path):
    (tmp_path / "am").mkdir()
    assert modelos._motor_do_caminho(tmp_path) == modelos.MOTOR_VOSK


def test_recusa_uma_pasta_que_nao_e_modelo(tmp_path):
    with pytest.raises(ValueError, match="não parece um modelo"):
        modelos._motor_do_caminho(tmp_path)


def test_um_caminho_em_disco_dispensa_download(tmp_path):
    (tmp_path / "model.bin").write_bytes(b"")
    pronto = modelos.resolver_modelo_fala(str(tmp_path))
    assert (pronto.motor, pronto.caminho) == (modelos.MOTOR_WHISPER, tmp_path)


def test_nome_desconhecido_lista_as_opcoes(tmp_path):
    with pytest.raises(ValueError, match="não encontrado"):
        modelos.resolver_modelo_fala("inexistente-123", tmp_path)


def test_o_vosk_e_considerado_baixado_quando_a_pasta_existe(tmp_path):
    modelo = modelos.MODELOS_FALA["vosk-pt"]
    assert not modelos.baixado(modelo, tmp_path)
    (tmp_path / modelo.referencia).mkdir(parents=True)
    assert modelos.baixado(modelo, tmp_path)
