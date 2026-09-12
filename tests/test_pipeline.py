"""Validações feitas antes de começar a transcrever."""

from pathlib import Path

import pytest

from transcritor import ffmpeg_tools, pipeline


def faixa(ordem: int) -> ffmpeg_tools.FaixaAudio:
    return ffmpeg_tools.FaixaAudio(
        ordem=ordem, stream=ordem + 1, codec="aac", sample_rate=48000, layout="stereo"
    )


def config(**extra) -> pipeline.Configuracao:
    return pipeline.Configuracao(video=Path("s.mp4"), destino=Path("s"), **extra)


def test_exige_duas_faixas():
    with pytest.raises(ValueError, match="necessárias 2"):
        pipeline._validar_faixas([faixa(0)], config())


def test_recusa_faixa_inexistente():
    with pytest.raises(ValueError, match="não existe"):
        pipeline._validar_faixas([faixa(0), faixa(1)], config(faixa_grupo=4))


def test_recusa_a_mesma_faixa_para_gm_e_jogadores():
    with pytest.raises(ValueError, match="precisam ser diferentes"):
        pipeline._validar_faixas([faixa(0), faixa(1)], config(faixa_gm=1, faixa_grupo=1))


def test_aceita_a_configuracao_padrao():
    pipeline._validar_faixas([faixa(0), faixa(1)], config())


def test_video_inexistente():
    with pytest.raises(FileNotFoundError):
        pipeline.executar(config())
