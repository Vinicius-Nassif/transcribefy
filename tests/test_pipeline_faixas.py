"""O pipeline de ponta a ponta com N faixas, sem modelo nem ffmpeg.

Só o que exige modelo ou ffmpeg é substituído: listar e extrair as faixas,
resolver os modelos, reconhecer a fala e extrair os vetores de voz. A separação
por voz (`diarizacao`), a mesclagem e a escrita dos arquivos rodam de verdade,
com vetores sintéticos — um eixo por pessoa.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from transcritor import faixas, ffmpeg_tools, modelos, pipeline, transcricao, vozes
from transcritor.fala import Fala
from transcritor.faixas import UMA, Faixa

DIMENSOES = 128


def voz(pessoa: int) -> list[float]:
    """Vetor de voz sintético: cada pessoa num eixo, com um pouco de ruído."""
    gerador = np.random.default_rng(pessoa)
    vetor = gerador.normal(0, 0.05, DIMENSOES)
    vetor[pessoa] += 1.0
    return vetor.tolist()


# O que cada faixa "diz": (início, fim, texto, pessoa que fala).
ROTEIRO = {
    0: [(0.0, 4.0, "vocês chegam à cripta", 0), (10.0, 13.0, "rolem iniciativa", 0)],
    1: [(1.0, 3.0, "eu abro a porta", 1), (5.0, 7.0, "espera", 2),
        (8.0, 9.5, "cuidado", 1), (11.0, 12.0, "ok", 2)],
    2: [(2.0, 3.5, "eu vigio o corredor", 3), (12.0, 14.0, "dezoito", 3)],
    3: [(0.5, 2.5, "oi", 4), (6.0, 7.5, "tchau", 5)],
}


class Registro:
    def __init__(self):
        self.transcritas: dict[int, dict] = {}
        self.com_vetores: list[int] = []
        self.locutor_resolvido = False
        self.mensagens: list[tuple[str, float]] = []


@pytest.fixture
def falso(tmp_path, monkeypatch):
    registro = Registro()
    video = tmp_path / "sessao.mkv"
    video.write_bytes(b"video")

    def listar(_video):
        return [
            ffmpeg_tools.FaixaAudio(ordem=i, stream=i + 1, codec="aac",
                                    sample_rate=48000, layout="stereo")
            for i in range(len(ROTEIRO))
        ]

    def extrair(_video, ordem, destino, inicio=0.0, fim=None):
        destino.write_bytes(b"")
        return destino

    def transcrever(wav, modelo, faixa=0, modelo_locutor=None, **_opcoes):
        registro.transcritas[faixa] = {"modelo_locutor": modelo_locutor, **_opcoes}
        return [Fala(inicio, fim, texto, faixa) for inicio, fim, texto, _ in ROTEIRO[faixa]]

    def anexar_vetores(_wav, falas, _modelo, _locutor, progresso=None):
        (faixa,) = {f.faixa for f in falas}
        registro.com_vetores.append(faixa)
        for fala, (*_, pessoa) in zip(falas, ROTEIRO[faixa], strict=True):
            fala.vetor_voz = voz(pessoa)
            fala.frames_voz = 100
        return falas

    def resolver_locutor(_diretorio=None):
        registro.locutor_resolvido = True
        return Path("spk")

    monkeypatch.setattr(ffmpeg_tools, "listar_faixas_audio", listar)
    monkeypatch.setattr(ffmpeg_tools, "extrair_faixa", extrair)
    monkeypatch.setattr(
        modelos, "resolver_modelo_fala",
        lambda apelido, _d=None: modelos.ModeloPronto(apelido, modelos.MOTOR_WHISPER, Path("m")),
    )
    monkeypatch.setattr(modelos, "resolver_modelo_locutor", resolver_locutor)
    monkeypatch.setattr(transcricao, "transcrever", transcrever)
    monkeypatch.setattr(transcricao, "aviso_de_dispositivo", lambda *_a: "")
    monkeypatch.setattr(vozes, "anexar_vetores", anexar_vetores)

    def executar(lista, **extra):
        config = pipeline.Configuracao(
            video=video, destino=tmp_path / "saida" / "sessao", faixas=lista,
            formatos=["md"], **extra,
        )
        return pipeline.executar(config, lambda m, f: registro.mensagens.append((m, f)))

    return registro, executar


def locutores_por_faixa(resultado):
    por_faixa: dict[int, set[str]] = {}
    for fala in resultado.falas:
        por_faixa.setdefault(fala.faixa, set()).add(fala.locutor)
    return por_faixa


def test_duas_faixas_como_antes(falso):
    """Compatibilidade: GM com nome fixo e jogadores numerados pela ordem da voz."""
    registro, executar = falso
    resultado = executar(faixas.padrao(n_jogadores=2))

    assert locutores_por_faixa(resultado) == {0: {"GM"}, 1: {"Jogador 1", "Jogador 2"}}
    primeiras = [f for f in resultado.falas if f.faixa == 1]
    assert primeiras[0].locutor == "Jogador 1"  # quem falou primeiro na faixa
    assert registro.com_vetores == [1]
    assert registro.transcritas[0]["modelo_locutor"] is None


def test_tres_faixas_misturando_os_tipos(falso):
    registro, executar = falso
    resultado = executar([
        Faixa(0, UMA, "Mestre"),
        faixas.varias(1, ["Ana", "Bruno"]),
        Faixa(2, UMA, "Caio"),
    ])
    assert locutores_por_faixa(resultado) == {
        0: {"Mestre"}, 1: {"Ana", "Bruno"}, 2: {"Caio"},
    }
    # Só a faixa de várias pessoas passa pelos vetores de voz.
    assert registro.com_vetores == [1]
    assert registro.transcritas[2]["modelo_locutor"] is None
    assert registro.transcritas[1]["modelo_locutor"] == Path("spk")
    assert 3 not in registro.transcritas  # faixa do arquivo que não foi escolhida


def test_duas_faixas_de_varias_pessoas_tem_nomes_padrao_distintos(falso):
    _, executar = falso
    resultado = executar([faixas.varias(1, n_locutores=2), faixas.varias(3, n_locutores=2)])
    assert locutores_por_faixa(resultado) == {
        1: {"Faixa 2 · Pessoa 1", "Faixa 2 · Pessoa 2"},
        3: {"Faixa 4 · Pessoa 1", "Faixa 4 · Pessoa 2"},
    }


def test_so_faixas_de_uma_pessoa_dispensam_o_modelo_de_locutor(falso):
    registro, executar = falso
    resultado = executar([Faixa(0, UMA, "GM"), Faixa(2, UMA, "Caio")])
    assert not registro.locutor_resolvido
    assert registro.com_vetores == []
    assert locutores_por_faixa(resultado) == {0: {"GM"}, 2: {"Caio"}}


def test_falas_simultaneas_de_todas_as_faixas_sao_mantidas(falso, tmp_path):
    _, executar = falso
    lista = [Faixa(0, UMA, "GM"), faixas.varias(1, n_locutores=2), Faixa(2, UMA, "Caio"),
             Faixa(3, UMA, "Duda")]
    resultado = executar(lista, juntar_falas=False)

    total = sum(len(v) for v in ROTEIRO.values())
    assert len(resultado.falas) == total
    inicios = [f.inicio for f in resultado.falas]
    assert inicios == sorted(inicios)

    detalhado = json.loads(
        (tmp_path / "saida" / "sessao.detalhado.json").read_text(encoding="utf-8")
    )
    # 0,0–4,0 (GM) cobre 0,5 (Duda), 1,0 (faixa 2) e 2,0 (Caio): as quatro faixas
    # falam ao mesmo tempo, e todas ficam marcadas.
    marcadas = {f["faixa"] for f in detalhado["falas"] if f["sobreposta"]}
    assert marcadas == {1, 2, 3, 4}


def test_deslocamento_de_cada_faixa_e_o_inicio_do_recorte(falso):
    _, executar = falso
    resultado = executar(
        [Faixa(0, UMA, "GM", deslocamento=0.0), Faixa(2, UMA, "Caio", deslocamento=-1.0)],
        inicio=100.0,
    )
    gm = [f.inicio for f in resultado.falas if f.faixa == 0]
    caio = [f.inicio for f in resultado.falas if f.faixa == 2]
    assert gm == [100.0, 110.0]
    assert caio == [101.0, 111.0]


def test_o_contexto_vai_para_todas_as_faixas(falso):
    registro, executar = falso
    executar([Faixa(0, UMA, "Mestre"), faixas.varias(1, ["Ana"])], contexto="Barovia.")
    for opcoes in registro.transcritas.values():
        assert opcoes["contexto"] == "Participantes: Mestre, Ana. Barovia."


def test_o_progresso_so_avanca_e_termina_em_um(falso):
    registro, executar = falso
    executar([Faixa(0, UMA, "GM"), faixas.varias(1, n_locutores=2), Faixa(2, UMA, "C")])
    fracoes = [f for _, f in registro.mensagens]
    assert fracoes == sorted(fracoes)
    assert fracoes[-1] == 1.0


def test_os_pesos_de_duas_faixas_sao_os_de_antes():
    pesos = pipeline._pesos(faixas.padrao())
    assert pesos[0] == pytest.approx((0.24, 0.0), abs=0.005)
    assert pesos[1] == pytest.approx((0.48, 0.19), abs=0.005)


def test_faixa_pedida_que_o_arquivo_nao_tem(falso):
    _, executar = falso
    with pytest.raises(ValueError, match="faixa 9 não existe"):
        executar([Faixa(8, UMA, "X")])
