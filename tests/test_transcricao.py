"""Porta de entrada do reconhecimento: validação do WAV e escolha do motor.

Os motores são substituídos por falsos que só registram o que receberam, então
nada aqui carrega modelo. Os WAVs são gerados com o módulo `wave` da biblioteca
padrão.
"""

import wave
from pathlib import Path

import pytest

from transcritor import modelos, motor_vosk, motor_whisper, transcricao
from transcritor.fala import Fala
from transcritor.modelos import ModeloPronto


def gerar_wav(caminho: Path, canais=1, taxa=16000, largura=2, segundos=0.1) -> Path:
    with wave.open(str(caminho), "wb") as arquivo:
        arquivo.setnchannels(canais)
        arquivo.setsampwidth(largura)
        arquivo.setframerate(taxa)
        arquivo.writeframes(b"\x00" * int(taxa * segundos) * canais * largura)
    return caminho


@pytest.fixture
def wav(tmp_path):
    return gerar_wav(tmp_path / "faixa.wav")


@pytest.fixture
def motores(monkeypatch):
    """Substitui os dois motores e devolve o que cada um recebeu."""
    chamadas = {}

    def falso(nome):
        def transcrever(wav, caminho, **opcoes):
            chamadas[nome] = {"wav": wav, "caminho": caminho, **opcoes}
            return [Fala(0.0, 1.0, nome, opcoes.get("faixa", 0))]
        return transcrever

    monkeypatch.setattr(motor_vosk, "transcrever", falso("vosk"))
    monkeypatch.setattr(motor_whisper, "transcrever", falso("whisper"))
    return chamadas


def whisper():
    return ModeloPronto("preciso", modelos.MOTOR_WHISPER, Path("modelos/whisper"))


def vosk():
    return ModeloPronto("vosk-pt", modelos.MOTOR_VOSK, Path("modelos/vosk"))


# --- wav_valido --------------------------------------------------------------


def test_wav_mono_16_bits_e_valido(wav):
    assert transcricao.wav_valido(wav)


@pytest.mark.parametrize(
    "formato", [{"canais": 2}, {"largura": 1}], ids=["estereo", "8-bits"]
)
def test_wav_fora_do_formato_nao_e_valido(tmp_path, formato):
    assert not transcricao.wav_valido(gerar_wav(tmp_path / "x.wav", **formato))


@pytest.mark.parametrize(
    "conteudo",
    [b"isto nao e um wav", b"", b"RIFF\x00\x00"],
    ids=["outro-formato", "vazio", "truncado"],
)
def test_arquivo_que_nao_e_wav_nao_e_valido(tmp_path, conteudo):
    """Antes lançava wave.Error, herdado do pytranscript, em vez de responder False."""
    falso = tmp_path / "x.wav"
    falso.write_bytes(conteudo)
    assert not transcricao.wav_valido(falso)


def test_a_verificacao_nao_deixa_o_arquivo_aberto(wav):
    """No Windows, um arquivo aberto não pode ser apagado nem substituído."""
    transcricao.wav_valido(wav)
    wav.unlink()  # falharia no Windows com o arquivo ainda aberto
    assert not wav.exists()


def test_preparar_converte_um_arquivo_que_nao_e_wav(tmp_path, monkeypatch):
    """Era o caso que o defeito quebrava: um mp3 ou mp4 nem chegava a ser convertido."""
    origem = tmp_path / "sessao.mp3"
    origem.write_bytes(b"ID3 mp3 qualquer")
    monkeypatch.setattr(transcricao.pytranscript, "to_valid_wav", lambda o, d: d)
    assert transcricao.preparar_wav(origem, tmp_path / "s.wav") == tmp_path / "s.wav"


def test_arquivo_que_nao_e_wav_e_recusado_com_o_erro_documentado(tmp_path, motores):
    falso = tmp_path / "x.wav"
    falso.write_bytes(b"isto nao e um wav")
    with pytest.raises(TypeError, match="WAV mono"):
        transcricao.transcrever(falso, whisper())
    assert motores == {}


# --- preparar_wav ------------------------------------------------------------


def test_preparar_devolve_o_proprio_arquivo_quando_ja_e_valido(wav, monkeypatch):
    def nao_devia_converter(*_args):
        raise AssertionError("converteu um WAV que já era válido")

    monkeypatch.setattr(transcricao.pytranscript, "to_valid_wav", nao_devia_converter)
    assert transcricao.preparar_wav(wav) == wav


def test_preparar_converte_o_que_nao_e_valido(tmp_path, monkeypatch):
    origem = gerar_wav(tmp_path / "estereo.wav", canais=2)
    destino = tmp_path / "saida.wav"
    recebidos = []

    def converter(o, d):
        recebidos.append((o, d))
        return d

    monkeypatch.setattr(transcricao.pytranscript, "to_valid_wav", converter)
    assert transcricao.preparar_wav(origem, destino) == destino
    assert recebidos == [(origem, destino)]


# --- transcrever: validações -------------------------------------------------


def test_wav_inexistente(tmp_path, motores):
    with pytest.raises(FileNotFoundError):
        transcricao.transcrever(tmp_path / "nao-existe.wav", whisper())
    assert motores == {}


def test_wav_invalido_e_recusado_antes_do_motor(tmp_path, motores):
    estereo = gerar_wav(tmp_path / "estereo.wav", canais=2)
    with pytest.raises(TypeError, match="WAV mono"):
        transcricao.transcrever(estereo, whisper())
    assert motores == {}


# --- transcrever: escolha do motor -------------------------------------------


def test_modelo_whisper_vai_para_o_motor_whisper(wav, motores):
    falas = transcricao.transcrever(wav, whisper())
    assert list(motores) == ["whisper"]
    assert [f.texto for f in falas] == ["whisper"]


def test_modelo_vosk_vai_para_o_motor_vosk(wav, motores):
    falas = transcricao.transcrever(wav, vosk())
    assert list(motores) == ["vosk"]
    assert [f.texto for f in falas] == ["vosk"]


def test_o_whisper_recebe_contexto_idioma_e_dispositivo(wav, motores):
    def progresso(_fracao):
        pass

    transcricao.transcrever(
        wav, whisper(), faixa=2, idioma="en", contexto="Strahd, Barovia",
        dispositivo="cpu", progresso=progresso,
    )
    recebido = motores["whisper"]
    assert recebido["wav"] == wav
    assert recebido["caminho"] == Path("modelos/whisper")
    assert recebido["faixa"] == 2
    assert recebido["idioma"] == "en"
    assert recebido["contexto"] == "Strahd, Barovia"
    assert recebido["dispositivo"] == "cpu"
    assert recebido["progresso"] is progresso


def test_o_vosk_recebe_o_modelo_de_locutor_e_nao_o_contexto(wav, motores):
    """Contexto, idioma e dispositivo são do Whisper; o Vosk não os aceita."""
    transcricao.transcrever(
        wav, vosk(), faixa=1, contexto="ignorado", modelo_locutor=Path("spk"),
    )
    recebido = motores["vosk"]
    assert recebido["caminho"] == Path("modelos/vosk")
    assert recebido["faixa"] == 1
    assert recebido["modelo_locutor"] == Path("spk")
    assert not {"contexto", "idioma", "dispositivo"} & set(recebido)


def test_o_whisper_nao_recebe_o_modelo_de_locutor(wav, motores):
    """Com o Whisper, os vetores de voz vêm depois, de `vozes.py`."""
    transcricao.transcrever(wav, whisper(), modelo_locutor=Path("spk"))
    assert "modelo_locutor" not in motores["whisper"]


def test_aceita_o_caminho_como_texto(wav, motores):
    transcricao.transcrever(str(wav), whisper())
    assert motores["whisper"]["wav"] == wav


def test_a_fala_continua_exportada_por_transcricao():
    """Módulos e testes antigos importam `Fala` daqui; o reexporte precisa ficar."""
    assert transcricao.Fala is Fala
    assert "Fala" in transcricao.__all__
