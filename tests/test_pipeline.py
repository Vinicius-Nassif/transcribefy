"""Validações feitas antes de começar a transcrever."""

from pathlib import Path

import pytest

from transcritor import ffmpeg_tools, pipeline


VIDEO = Path("s.mp4")


def trilha(ordem: int) -> ffmpeg_tools.FaixaAudio:
    return ffmpeg_tools.FaixaAudio(
        ordem=ordem, stream=ordem + 1, codec="aac", sample_rate=48000, layout="stereo"
    )


def inspecao(trilhas: int = 2, duracao: float | None = 600.0) -> dict:
    return {VIDEO: ffmpeg_tools.Inspecao([trilha(i) for i in range(trilhas)], duracao)}


def config(**extra) -> pipeline.Configuracao:
    return pipeline.Configuracao(video=VIDEO, destino=Path("s"), **extra)


def test_aceita_a_configuracao_padrao():
    assert pipeline.validar(config(), inspecao()) == []


def test_a_configuracao_padrao_e_a_mesa_de_duas_faixas():
    gm, grupo = config().faixas
    assert (gm.trilha, list(gm.nomes), gm.varias_pessoas) == (0, ["GM"], False)
    assert (grupo.trilha, grupo.varias_pessoas, grupo.vozes) == (1, True, 5)


def test_uma_faixa_so_e_aceita():
    """Mesa presencial com um microfone: uma trilha, várias pessoas."""
    so_uma = [pipeline.Faixa(0, varias_pessoas=True)]
    assert pipeline.validar(config(faixas=so_uma), inspecao(trilhas=1)) == []


def test_exige_ao_menos_uma_faixa():
    with pytest.raises(ValueError, match="pelo menos uma"):
        pipeline.validar(config(faixas=[]), inspecao())


def test_recusa_faixas_demais():
    faixas = [pipeline.Faixa(i) for i in range(pipeline.MAX_FAIXAS + 1)]
    with pytest.raises(ValueError, match="no máximo"):
        pipeline.validar(config(faixas=faixas), inspecao(trilhas=20))


def test_aceita_ate_o_maximo_de_faixas():
    faixas = [pipeline.Faixa(i) for i in range(pipeline.MAX_FAIXAS)]
    pipeline.validar(config(faixas=faixas), inspecao(trilhas=pipeline.MAX_FAIXAS))


def test_recusa_trilha_inexistente():
    with pytest.raises(ValueError, match="tem 2 trilha"):
        pipeline.validar(config(faixas=[pipeline.Faixa(4)]), inspecao())


def test_recusa_a_mesma_trilha_em_duas_faixas():
    faixas = [pipeline.Faixa(1), pipeline.Faixa(1, varias_pessoas=True)]
    with pytest.raises(ValueError, match="mesma trilha"):
        pipeline.validar(config(faixas=faixas), inspecao())


def test_a_mesma_trilha_de_arquivos_diferentes_e_permitida():
    outro = Path("ana.flac")
    inspecoes = {**inspecao(), outro: ffmpeg_tools.Inspecao([trilha(0)], 600.0)}
    faixas = [pipeline.Faixa(0), pipeline.Faixa(0, arquivo=outro)]
    assert pipeline.validar(config(faixas=faixas), inspecoes) == []


def test_faixa_de_uma_pessoa_nao_aceita_varios_nomes():
    faixas = [pipeline.Faixa(0, ("Ana", "Bruno"))]
    with pytest.raises(ValueError, match="uma pessoa só"):
        pipeline.validar(config(faixas=faixas), inspecao())


def test_lista_de_nomes_vazia_nao_e_erro():
    faixas = [pipeline.Faixa(0), pipeline.Faixa(1, (), varias_pessoas=True)]
    assert pipeline.validar(config(faixas=faixas), inspecao()) == []


def test_nomes_repetidos_viram_aviso_e_nao_erro():
    faixas = [pipeline.Faixa(0, ("Ana", "Bruno", "ana "), varias_pessoas=True)]
    avisos = pipeline.validar(config(faixas=faixas), inspecao())
    assert len(avisos) == 1 and "ana" in avisos[0]


def test_sem_repetidos_mantem_a_ordem_e_a_primeira_grafia():
    assert pipeline.sem_repetidos([" Ana ", "Bruno", "ANA", "", "Caio  Lima"]) == (
        ["Ana", "Bruno", "Caio Lima"], ["ANA"],
    )


def test_mais_nomes_que_vozes_vira_aviso():
    faixas = [pipeline.Faixa(0, ("Ana", "Bruno", "Caio"), varias_pessoas=True, vozes=2)]
    assert "não serão usados" in pipeline.validar(config(faixas=faixas), inspecao())[0]


def test_recusa_zero_vozes():
    faixas = [pipeline.Faixa(0, varias_pessoas=True, vozes=0)]
    with pytest.raises(ValueError, match="pelo menos 1"):
        pipeline.validar(config(faixas=faixas), inspecao())


@pytest.mark.parametrize(
    ("inicio", "fim", "mensagem"),
    [(-1, None, "negativo"), (300, 300, "depois do início"), (300, 100, "depois do início"),
     (0, 900, "passa da duração")],
)
def test_valida_o_intervalo(inicio, fim, mensagem):
    with pytest.raises(ValueError, match=mensagem):
        pipeline.validar(config(inicio=inicio, fim=fim), inspecao(duracao=600.0))


def test_intervalo_dentro_da_duracao_e_aceito():
    assert pipeline.validar(config(inicio=60, fim=600), inspecao(duracao=600.0)) == []


def test_sem_duracao_conhecida_o_fim_nao_e_barrado():
    assert pipeline.validar(config(fim=99999), inspecao(duracao=None)) == []


def test_video_inexistente():
    with pytest.raises(FileNotFoundError):
        pipeline.executar(config())


def gm_e_jogadores(gm: str, *jogadores: str) -> list[pipeline.Faixa]:
    return [pipeline.Faixa(0, (gm,)), pipeline.Faixa(1, jogadores, varias_pessoas=True)]


def test_o_contexto_comeca_pelos_nomes_da_mesa():
    montado = pipeline.contexto_da_mesa(config(faixas=gm_e_jogadores("Vini", "Ana", "Bruno")))
    assert montado == "Participantes: Vini, Ana, Bruno."


def test_o_contexto_livre_entra_depois_dos_nomes():
    montado = pipeline.contexto_da_mesa(
        config(faixas=gm_e_jogadores("Vini", "Ana"), contexto="Barovia, Strahd.")
    )
    assert montado == "Participantes: Vini, Ana. Barovia, Strahd."


def test_o_contexto_junta_os_nomes_de_todas_as_faixas_sem_repetir():
    faixas = [*gm_e_jogadores("Vini", "Ana"), pipeline.Faixa(2, ("ana",))]
    assert pipeline.contexto_da_mesa(config(faixas=faixas)) == "Participantes: Vini, Ana."


def test_sem_nomes_nem_texto_o_contexto_fica_vazio():
    assert pipeline.contexto_da_mesa(config(faixas=gm_e_jogadores(" "))) == ""


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


def test_o_mesmo_nome_em_duas_faixas_e_recusado():
    faixas = [pipeline.Faixa(0, ("Ana",)), pipeline.Faixa(1, ("ana", "Bruno"), varias_pessoas=True)]
    with pytest.raises(ValueError, match="faixas 1 e 2"):
        pipeline.validar(config(faixas=faixas), inspecao())


def test_formato_desconhecido_e_recusado_antes_de_transcrever():
    with pytest.raises(ValueError, match="Formato desconhecido: docx"):
        pipeline.validar(config(formatos=["txt", "docx"]), inspecao())


def test_sem_formato_nenhum_e_recusado():
    with pytest.raises(ValueError, match="ao menos um formato"):
        pipeline.validar(config(formatos=[]), inspecao())


def test_inicio_alem_da_duracao_e_recusado():
    with pytest.raises(ValueError, match="início do trecho .* passa da duração"):
        pipeline.validar(config(inicio=20000), inspecao(duracao=600.0))
