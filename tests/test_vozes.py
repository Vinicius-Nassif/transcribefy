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
