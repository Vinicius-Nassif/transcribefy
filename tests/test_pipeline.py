"""Validações feitas antes de começar a transcrever."""

from pathlib import Path

import pytest

from transcritor import faixas, ffmpeg_tools, pipeline
from transcritor.faixas import Faixa


def faixa(ordem: int) -> ffmpeg_tools.FaixaAudio:
    return ffmpeg_tools.FaixaAudio(
        ordem=ordem, stream=ordem + 1, codec="aac", sample_rate=48000, layout="stereo"
    )


def config(**extra) -> pipeline.Configuracao:
    return pipeline.Configuracao(video=Path("s.mp4"), destino=Path("s"), **extra)


def test_arquivo_sem_a_faixa_pedida_e_recusado():
    """O arranjo padrão usa as faixas 1 e 2; um arquivo de uma faixa não serve."""
    with pytest.raises(ValueError, match="faixa 2 não existe"):
        pipeline._validar_faixas([faixa(0)], config())


def test_arquivo_de_uma_faixa_serve_quando_so_ela_e_pedida():
    pipeline._validar_faixas([faixa(0)], config(faixas=[Faixa(0, faixas.UMA, "GM")]))


def test_recusa_faixa_inexistente():
    with pytest.raises(ValueError, match="não existe"):
        pipeline._validar_faixas(
            [faixa(0), faixa(1)], config(faixas=faixas.padrao(faixa_grupo=4))
        )


def test_recusa_a_mesma_faixa_duas_vezes():
    with pytest.raises(ValueError, match="mais de uma vez"):
        pipeline._validar_faixas(
            [faixa(0), faixa(1)], config(faixas=faixas.padrao(faixa_gm=1, faixa_grupo=1))
        )


def test_aceita_a_configuracao_padrao():
    pipeline._validar_faixas([faixa(0), faixa(1)], config())


def test_a_configuracao_padrao_e_o_arranjo_de_duas_faixas():
    """Compatibilidade: sem dizer nada, faixa 1 é o GM e a 2, cinco jogadores."""
    gm, grupo = config().faixas
    assert (gm.numero, gm.tipo, gm.nome_unico) == (0, faixas.UMA, "GM")
    assert (grupo.numero, grupo.tipo, grupo.n_locutores) == (1, faixas.VARIAS, 5)
    assert faixas.prefixo_de(grupo, config().faixas) == "Jogador"


def test_video_inexistente():
    with pytest.raises(FileNotFoundError):
        pipeline.executar(config())


def test_o_contexto_comeca_pelos_nomes_da_mesa():
    montado = pipeline.contexto_da_mesa(
        config(faixas=faixas.padrao(nome_gm="Vini", nomes_jogadores=["Ana", "Bruno"]))
    )
    assert montado == "Participantes: Vini, Ana, Bruno."


def test_o_contexto_livre_entra_depois_dos_nomes():
    montado = pipeline.contexto_da_mesa(
        config(
            faixas=faixas.padrao(nome_gm="Vini", nomes_jogadores=["Ana"]),
            contexto="Barovia, Strahd.",
        )
    )
    assert montado == "Participantes: Vini, Ana. Barovia, Strahd."


def test_o_contexto_junta_os_nomes_de_todas_as_faixas_sem_repetir():
    montado = pipeline.contexto_da_mesa(config(faixas=[
        Faixa(0, faixas.UMA, "Vini"),
        faixas.varias(1, ["Ana", "Bruno"]),
        Faixa(2, faixas.UMA, "Caio"),
        faixas.varias(3, ["Ana"]),
    ]))
    assert montado == "Participantes: Vini, Ana, Bruno, Caio."


def test_faixa_de_uma_pessoa_sem_nome_entra_com_o_nome_padrao():
    montado = pipeline.contexto_da_mesa(config(faixas=[Faixa(2, faixas.UMA, " ")]))
    assert montado == "Participantes: Faixa 3."


def test_os_termos_da_lista_viram_uma_frase_antes_do_texto_livre():
    montado = pipeline.contexto_da_mesa(config(
        faixas=faixas.padrao(nome_gm="Vini", nomes_jogadores=["Ana"]),
        termos=["Strahd", "teste de resistência", "Strahd"],
        contexto="Campanha de Ravenloft.",
    ))
    assert montado == (
        "Participantes: Vini, Ana. Termos da sessão: Strahd, teste de resistência. "
        "Campanha de Ravenloft."
    )


def test_os_rotulos_seguem_o_idioma_do_audio():
    """Um rótulo em português puxaria a transcrição de um áudio em inglês."""
    montado = pipeline.contexto_da_mesa(config(
        idioma="en", faixas=[Faixa(0, faixas.UMA, "Vini")], termos=["Strahd"],
    ))
    assert montado == "Participants: Vini. Session terms: Strahd."


@pytest.mark.parametrize("idioma", ["auto", "", "ja"])
def test_sem_idioma_conhecido_as_listas_vao_sem_rotulo(idioma):
    """Com detecção automática não se sabe o idioma: só os nomes, que não têm idioma."""
    montado = pipeline.contexto_da_mesa(config(
        idioma=idioma, faixas=[Faixa(0, faixas.UMA, "Vini")], termos=["Strahd", "Barovia"],
        contexto="Texto livre.",
    ))
    assert montado == "Vini. Strahd, Barovia. Texto livre."


def test_o_prompt_padrao_em_portugues_nao_mudou():
    """Compatibilidade: sem termos, o prompt é o mesmo de antes desta mudança."""
    assert pipeline.contexto_da_mesa(config()) == "Participantes: GM."


def test_sem_nomes_nem_texto_o_contexto_fica_vazio():
    assert pipeline.contexto_da_mesa(config(faixas=[faixas.varias(1)])) == ""


def modelo_whisper():
    from transcritor.modelos import MOTOR_WHISPER, ModeloPronto

    return ModeloPronto("preciso", MOTOR_WHISPER, Path("m"))


def test_avisa_quando_ha_gpu_mas_a_transcricao_vai_para_a_cpu(monkeypatch):
    from transcritor import motor_whisper, transcricao

    monkeypatch.setattr(
        motor_whisper, "_diagnostico_cuda",
        motor_whisper.DiagnosticoCuda(1, None, ("cublas64_12.dll",)),
    )
    aviso = transcricao.aviso_de_dispositivo(modelo_whisper(), "auto")
    assert "CPU" in aviso and "requirements-gpu.txt" in aviso


def test_nao_avisa_nada_quando_a_gpu_esta_em_ordem(monkeypatch):
    from transcritor import motor_whisper, transcricao

    monkeypatch.setattr(
        motor_whisper, "_diagnostico_cuda", motor_whisper.DiagnosticoCuda(1, None, ())
    )
    assert transcricao.aviso_de_dispositivo(modelo_whisper(), "auto") == ""


def test_nao_avisa_nada_em_maquina_sem_placa(monkeypatch):
    from transcritor import motor_whisper, transcricao

    monkeypatch.setattr(
        motor_whisper, "_diagnostico_cuda", motor_whisper.DiagnosticoCuda(0, None, ())
    )
    assert transcricao.aviso_de_dispositivo(modelo_whisper(), "auto") == ""


def test_quem_pediu_cpu_nao_recebe_aviso_de_gpu(monkeypatch):
    from transcritor import motor_whisper, transcricao

    monkeypatch.setattr(
        motor_whisper, "_diagnostico_cuda",
        motor_whisper.DiagnosticoCuda(1, None, ("cublas64_12.dll",)),
    )
    assert transcricao.aviso_de_dispositivo(modelo_whisper(), "cpu") == ""


def test_o_comando_de_diagnostico_esta_registrado():
    from transcritor import cli

    assert cli._parser().parse_args(["diagnostico"]).comando == "diagnostico"
