"""Leitura das faixas a partir do relatório que o ffmpeg imprime."""

from pathlib import Path

import pytest

from transcritor import ffmpeg_tools

RELATORIO = """\
Input #0, matroska,webm, from 'sessao.mkv':
  Metadata:
    ENCODER         : Lavf60.16.100
  Duration: 03:12:45.60, start: 0.000000, bitrate: 2450 kb/s
  Stream #0:0: Video: h264 (High), yuv420p(tv), 1920x1080, 30 fps, 30 tbr, 1k tbn
    Metadata:
      title           : Video
  Stream #0:1(por): Audio: aac (LC), 48000 Hz, stereo, fltp
    Metadata:
      title           : Mic do GM
  Stream #0:2: Audio: aac (LC), 48000 Hz, stereo, fltp
    Metadata:
      title           : Discord
"""


@pytest.fixture
def relatorio_fixo(monkeypatch):
    monkeypatch.setattr(ffmpeg_tools, "_relatorio", lambda _video: RELATORIO)


def test_lista_apenas_faixas_de_audio(relatorio_fixo):
    faixas = ffmpeg_tools.listar_faixas_audio(Path("sessao.mkv"))
    assert [f.ordem for f in faixas] == [0, 1]
    assert [f.stream for f in faixas] == [1, 2]


def test_le_titulo_e_idioma_de_cada_faixa(relatorio_fixo):
    primeira, segunda = ffmpeg_tools.listar_faixas_audio(Path("sessao.mkv"))
    assert primeira.titulo == "Mic do GM"
    assert primeira.idioma == "por"
    assert segunda.titulo == "Discord"
    assert segunda.idioma is None


def test_nao_atribui_titulo_do_video_a_uma_faixa(relatorio_fixo):
    # O `title: Video` pertence ao stream de vídeo e não pode vazar para o áudio.
    faixas = ffmpeg_tools.listar_faixas_audio(Path("sessao.mkv"))
    assert all(f.titulo != "Video" for f in faixas)


def test_duracao_em_segundos(relatorio_fixo):
    assert ffmpeg_tools.duracao_segundos(Path("sessao.mkv")) == pytest.approx(11565.6)


def test_rotulo_legivel(relatorio_fixo):
    primeira, _ = ffmpeg_tools.listar_faixas_audio(Path("sessao.mkv"))
    assert primeira.rotulo == "Faixa 1 - Mic do GM - aac (LC), 48000 Hz, stereo"


def test_extrai_varias_trilhas_numa_chamada(tmp_path):
    import subprocess

    from transcritor import ffmpeg_tools

    origem = tmp_path / "duas.mka"
    tom = "sine=frequency={}:duration=2"
    subprocess.run(
        [ffmpeg_tools.localizar_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", tom.format(440), "-f", "lavfi", "-i", tom.format(660),
         "-map", "0", "-map", "1", "-c:a", "pcm_s16le", str(origem)],
        check=True,
    )
    a, b = ffmpeg_tools.extrair_faixas(
        origem, [(0, tmp_path / "a.wav"), (1, tmp_path / "b.wav")], fim=1.0
    )
    import wave

    for wav in (a, b):
        with wave.Wave_read(str(wav)) as onda:
            assert (onda.getnchannels(), onda.getframerate()) == (1, 16000)
            assert abs(onda.getnframes() / 16000 - 1.0) < 0.05
    assert a.read_bytes() != b.read_bytes()
