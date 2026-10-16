"""Média dos vetores de voz de um trecho."""

import json

from transcritor import vozes


class ReconhecedorFalso:
    """Imita o KaldiRecognizer: entrega um resultado a cada N blocos aceitos."""

    def __init__(self, parciais: list[dict], final: dict):
        self.parciais = list(parciais)
        self.final = final
        self.entregues: list[dict] = []
        self.reiniciou = False

    def AcceptWaveform(self, dados: bytes) -> bool:  # API do vosk
        if self.parciais:
            self.entregues.append(self.parciais.pop(0))
            return True
        return False

    def Result(self) -> str:  # API do vosk
        return json.dumps(self.entregues[-1])

    def FinalResult(self) -> str:  # API do vosk
        return json.dumps(self.final)

    def Reset(self) -> None:  # API do vosk
        self.reiniciou = True


def test_acumula_pesando_pelos_frames():
    soma, total = vozes._acumular(json.dumps({"spk": [1.0, 0.0], "spk_frames": 10}), None, 0)
    soma, total = vozes._acumular(json.dumps({"spk": [0.0, 1.0], "spk_frames": 30}), soma, total)
    assert total == 40
    assert list(soma / total) == [0.25, 0.75]


def test_ignora_resultado_sem_vetor():
    soma, total = vozes._acumular(json.dumps({"text": "oi"}), None, 0)
    assert (soma, total) == (None, 0)


def test_ignora_vetor_sem_frames():
    """Sem frames o vetor não tem peso e distorceria a média."""
    soma, total = vozes._acumular(json.dumps({"spk": [1.0, 0.0], "spk_frames": 0}), None, 0)
    assert (soma, total) == (None, 0)


def test_junta_os_parciais_e_o_final_num_vetor_so():
    reconhecedor = ReconhecedorFalso(
        parciais=[{"spk": [1.0, 0.0], "spk_frames": 10}],
        final={"spk": [0.0, 1.0], "spk_frames": 10},
    )
    vetor, frames = vozes._vetor_do_trecho(reconhecedor, b"\x00" * 64_000, largura=2)
    assert frames == 20
    assert vetor == [0.5, 0.5]


def test_reinicia_o_reconhecedor_para_o_proximo_trecho():
    """Sem o reset, o trecho seguinte herdaria o áudio deste."""
    reconhecedor = ReconhecedorFalso([], {"spk": [1.0], "spk_frames": 5})
    vozes._vetor_do_trecho(reconhecedor, b"\x00" * 32, largura=2)
    assert reconhecedor.reiniciou


def test_trecho_curto_demais_nao_produz_vetor():
    reconhecedor = ReconhecedorFalso([], {"text": ""})
    assert vozes._vetor_do_trecho(reconhecedor, b"\x00" * 32, largura=2) == (None, 0)


def test_lista_vazia_nao_abre_modelo_nenhum(tmp_path):
    assert vozes.anexar_vetores(tmp_path / "x.wav", [], tmp_path, tmp_path) == []


# --- Extração em paralelo ------------------------------------------------------

import sys
import types
import wave

import pytest

from transcritor.fala import Fala


@pytest.fixture
def vosk_falso(monkeypatch):
    """Um módulo `vosk` cujo vetor de voz é o próprio primeiro byte do trecho."""
    criados = []

    class Reconhecedor:
        def __init__(self, modelo, taxa, locutor):
            criados.append(self)
            self.dados = b""

        def AcceptWaveform(self, dados):
            self.dados += dados
            return False

        def Result(self):
            return "{}"

        def FinalResult(self):
            return json.dumps({"spk": [float(self.dados[0]), 1.0], "spk_frames": 50})

        def Reset(self):
            self.dados = b""

    modulo = types.SimpleNamespace(
        SetLogLevel=lambda *_: None, Model=lambda *_: object(),
        SpkModel=lambda *_: object(), KaldiRecognizer=Reconhecedor,
    )
    monkeypatch.setitem(sys.modules, "vosk", modulo)
    return criados


def wav_com_marcas(caminho, segundos: int):
    """Cada segundo do WAV tem um valor de amostra igual ao número do segundo."""
    with wave.Wave_write(str(caminho)) as onda:
        onda.setnchannels(1)
        onda.setsampwidth(2)
        onda.setframerate(100)
        onda.writeframes(b"".join(bytes([s, 0]) * 100 for s in range(segundos)))


def test_cada_fala_recebe_o_vetor_do_proprio_trecho(tmp_path, vosk_falso):
    wav_com_marcas(tmp_path / "a.wav", 40)
    falas = [Fala(float(s), s + 0.5, "x", 0) for s in range(40)]
    vozes.anexar_vetores(tmp_path / "a.wav", falas, tmp_path, tmp_path, paralelismo=4)
    assert [f.vetor_voz[0] for f in falas] == [float(s) for s in range(40)]
    assert len(vosk_falso) == 4  # um reconhecedor por parte do trabalho


def test_progresso_chega_a_um_e_nao_recua(tmp_path, vosk_falso):
    wav_com_marcas(tmp_path / "a.wav", 20)
    fracoes = []
    falas = [Fala(float(s), s + 0.5, "x", 0) for s in range(20)]
    vozes.anexar_vetores(
        tmp_path / "a.wav", falas, tmp_path, tmp_path, progresso=fracoes.append, paralelismo=3
    )
    assert fracoes == sorted(fracoes) and fracoes[-1] == 1.0


def test_cancelamento_no_progresso_para_todas_as_threads(tmp_path, vosk_falso):
    wav_com_marcas(tmp_path / "a.wav", 60)
    falas = [Fala(float(s), s + 0.5, "x", 0) for s in range(60)]

    def cancela(fracao):
        if fracao > 0.2:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        vozes.anexar_vetores(
            tmp_path / "a.wav", falas, tmp_path, tmp_path, progresso=cancela, paralelismo=4
        )
    assert sum(f.vetor_voz is not None for f in falas) < 60


def test_erro_numa_thread_para_as_outras(tmp_path, vosk_falso, monkeypatch):
    """Um trecho que quebra o Kaldi não pode deixar as outras threads rodando a faixa toda."""
    wav_com_marcas(tmp_path / "a.wav", 200)
    falas = [Fala(float(s), s + 0.5, "x", 0) for s in range(200)]
    original = vozes._vetor_do_trecho

    def quebra_no_trecho_7(reconhecedor, dados, largura):
        if dados[0] == 7:
            raise RuntimeError("erro do Kaldi")
        return original(reconhecedor, dados, largura)

    monkeypatch.setattr(vozes, "_vetor_do_trecho", quebra_no_trecho_7)
    with pytest.raises(RuntimeError, match="Kaldi"):
        vozes.anexar_vetores(tmp_path / "a.wav", falas, tmp_path, tmp_path, paralelismo=4)
    assert sum(f.vetor_voz is not None for f in falas) < 100


def test_o_modelo_de_locutor_e_carregado_uma_vez(tmp_path, vosk_falso, monkeypatch):
    import sys

    cargas = []
    monkeypatch.setattr(sys.modules["vosk"], "SpkModel", lambda *_: cargas.append(1) or object())
    wav_com_marcas(tmp_path / "a.wav", 20)
    falas = [Fala(float(s), s + 0.5, "x", 0) for s in range(20)]
    vozes.anexar_vetores(tmp_path / "a.wav", falas, tmp_path, tmp_path, paralelismo=4)
    assert len(cargas) == 1
