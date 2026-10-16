"""O laço do pipeline sobre N faixas, com ffmpeg e motores substituídos.

Nada aqui abre modelo nem processa áudio: o que está sob teste é a costura —
quem vira locutor de quê, os ajustes por faixa, o cancelamento e a limpeza.
"""

import threading
from pathlib import Path

import pytest

from transcritor import ffmpeg_tools, modelos, pipeline, temporarios, transcricao, vozes
from transcritor.fala import Fala


@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    """Substitui tudo o que é externo e registra o que o pipeline pediu."""
    video = tmp_path / "sessao.mp4"
    video.write_bytes(b"x")
    registro = {"extraidas": [], "pastas": [], "transcritas": [], "leituras": 0}

    def inspecionar(arquivo):
        return ffmpeg_tools.Inspecao(
            [ffmpeg_tools.FaixaAudio(i, i + 1, "aac", 48000, "stereo") for i in range(4)], 600.0
        )

    def extrair_faixas(arquivo, pedidos, inicio=0.0, fim=None, cancelado=None):
        registro["leituras"] += 1
        for ordem, destino in pedidos:
            registro["extraidas"].append((Path(arquivo).name, ordem))
            registro["pastas"].append(destino.parent)
            destino.write_bytes(b"wav")
        if cancelado:
            cancelado()
        return [destino for _, destino in pedidos]

    def transcrever(wav, modelo, faixa=0, progresso=None, **_):
        registro["transcritas"].append(faixa)
        if progresso:
            progresso(0.5)
        # Duas falas por faixa, com vozes distintas dentro da faixa.
        return [
            Fala(10.0 + faixa, 12.0 + faixa, f"f{faixa}a", faixa),
            Fala(40.0 + faixa, 42.0 + faixa, f"f{faixa}b", faixa),
        ]

    def anexar_vetores(wav, falas, *_args, progresso=None):
        for i, fala in enumerate(falas):
            fala.vetor_voz = [1.0, 0.0] if i == 0 else [0.0, 1.0]
            fala.frames_voz = 100
        return falas

    pronto = modelos.ModeloPronto("preciso", modelos.MOTOR_WHISPER, tmp_path)
    monkeypatch.setattr(ffmpeg_tools, "inspecionar", inspecionar)
    monkeypatch.setattr(ffmpeg_tools, "extrair_faixas", extrair_faixas)
    monkeypatch.setattr(modelos, "resolver_modelo_fala", lambda *a, **k: pronto)
    monkeypatch.setattr(modelos, "resolver_modelo_locutor", lambda *a, **k: tmp_path)
    monkeypatch.setattr(transcricao, "transcrever", transcrever)
    monkeypatch.setattr(transcricao, "dispositivo_efetivo", lambda *_: "cuda")
    monkeypatch.setattr(transcricao, "carregar", lambda *_: None)
    monkeypatch.setattr(transcricao, "aviso_de_dispositivo", lambda *_: "")
    monkeypatch.setattr(vozes, "anexar_vetores", anexar_vetores)
    return video, tmp_path / "saida" / "sessao", registro


def test_tres_faixas_com_uma_pessoa_e_um_grupo(ambiente):
    video, destino, registro = ambiente
    faixas = [
        pipeline.Faixa(0, ("Mestre",)),
        pipeline.Faixa(2, ("Ana",)),
        pipeline.Faixa(1, ("Bruno",), varias_pessoas=True, vozes=2),
    ]
    resultado = pipeline.executar(
        pipeline.Configuracao(video=video, destino=destino, faixas=faixas, formatos=["txt"],
                              juntar_falas=False)
    )
    assert registro["extraidas"] == [("sessao.mp4", 0), ("sessao.mp4", 2), ("sessao.mp4", 1)]
    assert registro["leituras"] == 1  # as três trilhas numa leitura só do arquivo
    assert sorted(resultado.locutores) == ["Ana", "Bruno", "Jogador 2", "Mestre"]
    # `faixa` na fala é a posição na configuração, não o número da trilha.
    assert {f.faixa for f in resultado.falas if f.locutor == "Ana"} == {1}
    assert destino.with_suffix(".txt").is_file()


def test_faixa_de_uma_pessoa_sem_nome_vira_faixa_n(ambiente):
    video, destino, _ = ambiente
    resultado = pipeline.executar(
        pipeline.Configuracao(video=video, destino=destino, faixas=[pipeline.Faixa(3)],
                              formatos=["txt"])
    )
    assert resultado.locutores == ["Faixa 1"]


def test_duas_faixas_de_grupo_nao_repetem_rotulos(ambiente):
    video, destino, _ = ambiente
    faixas = [
        pipeline.Faixa(0, varias_pessoas=True, vozes=2),
        pipeline.Faixa(1, varias_pessoas=True, vozes=2),
    ]
    resultado = pipeline.executar(
        pipeline.Configuracao(video=video, destino=destino, faixas=faixas, formatos=["txt"],
                              juntar_falas=False)
    )
    assert resultado.locutores == ["Jogador 1", "Jogador 2", "Jogador 3", "Jogador 4"]


def test_ajuste_e_inicio_sao_somados_por_faixa(ambiente):
    video, destino, _ = ambiente
    faixas = [pipeline.Faixa(0, ("GM",)), pipeline.Faixa(1, ("Ana",), ajuste=0.5)]
    resultado = pipeline.executar(
        pipeline.Configuracao(video=video, destino=destino, faixas=faixas, formatos=["txt"],
                              juntar_falas=False, inicio=100.0)
    )
    inicios = {(f.locutor, f.inicio) for f in resultado.falas}
    assert ("GM", 110.0) in inicios
    assert ("Ana", 111.5) in inicios


def test_o_progresso_vai_de_zero_a_um_sem_voltar(ambiente):
    video, destino, _ = ambiente
    fracoes = []
    pipeline.executar(
        pipeline.Configuracao(video=video, destino=destino, formatos=["txt"]),
        lambda mensagem, fracao: fracoes.append(fracao),
    )
    assert fracoes[-1] == 1.0
    assert fracoes == sorted(fracoes)


def test_cancelar_interrompe_e_limpa_a_pasta_temporaria(ambiente):
    video, destino, registro = ambiente
    cancelado = threading.Event()

    def relatar(mensagem, fracao):
        if registro["transcritas"]:
            cancelado.set()

    with pytest.raises(pipeline.Cancelado):
        pipeline.executar(
            pipeline.Configuracao(video=video, destino=destino, formatos=["txt"]),
            relatar, cancelado,
        )
    assert registro["transcritas"] == [0]  # parou antes da segunda faixa
    assert registro["pastas"] and not registro["pastas"][0].exists()
    assert not destino.with_suffix(".txt").exists()


def test_erro_no_meio_tambem_limpa_a_pasta_temporaria(ambiente, monkeypatch):
    video, destino, registro = ambiente

    def falha(*_a, **_k):
        raise RuntimeError("motor caiu")

    monkeypatch.setattr(transcricao, "transcrever", falha)
    with pytest.raises(RuntimeError):
        pipeline.executar(pipeline.Configuracao(video=video, destino=destino))
    assert not registro["pastas"][0].exists()
    assert registro["pastas"][0].name.startswith(temporarios.PREFIXO)


def test_uma_leitura_por_arquivo(ambiente, tmp_path):
    video, destino, registro = ambiente
    outro = tmp_path / "ana.flac"
    outro.write_bytes(b"x")
    faixas = [pipeline.Faixa(0, ("GM",)), pipeline.Faixa(0, ("Ana",), arquivo=outro),
              pipeline.Faixa(1, ("Bruno",))]
    pipeline.executar(
        pipeline.Configuracao(video=video, destino=destino, faixas=faixas, formatos=["txt"])
    )
    assert registro["leituras"] == 2
    assert sorted(registro["extraidas"]) == [("ana.flac", 0), ("sessao.mp4", 0), ("sessao.mp4", 1)]
