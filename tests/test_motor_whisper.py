"""Conversão dos segmentos do Whisper em falas."""

from dataclasses import dataclass, field

import pytest

from transcritor import motor_whisper


@dataclass
class Palavra:
    start: float
    end: float
    word: str
    probability: float = 0.9


@dataclass
class Segmento:
    start: float
    end: float
    text: str
    words: list[Palavra] = field(default_factory=list)


def palavras(*trios: tuple[float, float, str]) -> list[Palavra]:
    return [Palavra(inicio, fim, texto) for inicio, fim, texto in trios]


def test_um_segmento_sem_pausa_vira_uma_fala_so():
    segmento = Segmento(0.0, 2.0, " Bom dia a todos.", palavras(
        (0.1, 0.5, " Bom"), (0.5, 0.9, " dia"), (1.0, 1.2, " a"), (1.2, 1.8, " todos."),
    ))
    falas = list(motor_whisper._quebrar(segmento, faixa=1))
    assert len(falas) == 1
    assert falas[0].texto == "Bom dia a todos."
    assert falas[0].faixa == 1


def test_os_tempos_vem_das_palavras_e_nao_das_bordas_do_segmento():
    """A borda do segmento se estende até o próximo trecho com voz."""
    segmento = Segmento(0.0, 40.0, " Beleza.", palavras((0.9, 1.4, " Beleza.")))
    (fala,) = list(motor_whisper._quebrar(segmento, faixa=0))
    assert (round(fala.inicio, 2), round(fala.fim, 2)) == (0.9, 1.4)


def test_pausa_longa_dentro_do_segmento_separa_duas_falas():
    pausa = motor_whisper.PAUSA_INTERNA + 1.0
    segmento = Segmento(0.0, 30.0, " Beleza. Bota fé.", palavras(
        (0.5, 1.0, " Beleza."),
        (1.0 + pausa, 1.4 + pausa, " Bota"),
        (1.4 + pausa, 1.9 + pausa, " fé."),
    ))
    falas = list(motor_whisper._quebrar(segmento, faixa=0))
    assert [f.texto for f in falas] == ["Beleza.", "Bota fé."]
    assert falas[1].inicio > falas[0].fim


def test_pausa_curta_nao_separa():
    quase = motor_whisper.PAUSA_INTERNA - 0.2
    segmento = Segmento(0.0, 5.0, " Beleza. Bota fé.", palavras(
        (0.5, 1.0, " Beleza."), (1.0 + quase, 1.5 + quase, " Bota fé."),
    ))
    assert len(list(motor_whisper._quebrar(segmento, faixa=0))) == 1


def test_segmento_sem_palavras_ainda_vira_fala():
    segmento = Segmento(3.0, 4.5, " Oi.", [])
    (fala,) = list(motor_whisper._quebrar(segmento, faixa=2))
    assert (fala.texto, fala.inicio, fala.fim) == ("Oi.", 3.0, 4.5)


def test_descarta_o_bordao_de_legenda_que_o_whisper_inventa_no_silencio():
    segmento = Segmento(0.0, 3.0, " Legendas pela comunidade Amara.org",
                        palavras((0.1, 2.9, " Legendas pela comunidade Amara.org")))
    assert list(motor_whisper._quebrar(segmento, faixa=0)) == []


def test_nao_descarta_a_frase_que_apenas_contem_o_bordao():
    texto = " Obrigado por assistir a cena toda, agora role iniciativa."
    segmento = Segmento(0.0, 3.0, texto, palavras((0.1, 2.9, texto)))
    assert len(list(motor_whisper._quebrar(segmento, faixa=0))) == 1


def test_a_cpu_dispensa_qualquer_checagem_de_gpu():
    assert motor_whisper.escolher_dispositivo("cpu") == ("cpu", "int8")


def test_mantem_a_interjeicao_curta_que_parece_bordao():
    """"Obrigado" e "tchau" são coisas que alguém realmente diz — e costumam
    ser ditas por cima de outra pessoa. Descartá-las custaria uma fala real."""
    for texto in (" Obrigado.", " Tchau.", " Até a próxima.", " Valeu!"):
        segmento = Segmento(0.0, 1.0, texto, palavras((0.1, 0.9, texto)))
        assert len(list(motor_whisper._quebrar(segmento, faixa=1))) == 1, texto


def test_a_lista_de_alucinacoes_so_tem_credito_de_legenda():
    """Qualquer frase plausível numa mesa de RPG aqui vira fala perdida."""
    for frase in motor_whisper.ALUCINACOES:
        assert "amara" in frase, frase


def diagnostico(gpus=1, pasta=None, faltando=()):
    return motor_whisper.DiagnosticoCuda(gpus, pasta, faltando)


def test_ter_placa_nao_basta_para_a_gpu_ser_utilizavel():
    """Foi o que faltava: o CTranslate2 via a GPU e falhava só ao carregar cuBLAS."""
    assert diagnostico(gpus=1, faltando=()).utilizavel
    assert not diagnostico(gpus=1, faltando=("cublas64_12.dll",)).utilizavel
    assert not diagnostico(gpus=0).utilizavel


def test_a_explicacao_manda_instalar_quando_os_pacotes_nem_existem():
    texto = diagnostico(gpus=1, pasta=None, faltando=("cublas64_12.dll",)).explicacao
    assert "requirements-gpu.txt" in texto


def test_a_explicacao_aponta_o_driver_quando_os_pacotes_estao_no_lugar(tmp_path):
    texto = diagnostico(gpus=1, pasta=tmp_path, faltando=("cublas64_12.dll",)).explicacao
    assert "driver" in texto.lower()
    assert str(tmp_path) in texto


def test_a_explicacao_de_maquina_sem_placa_nao_manda_instalar_nada():
    assert "requirements-gpu" not in diagnostico(gpus=0).explicacao


def test_cai_para_cpu_quando_a_gpu_existe_mas_as_bibliotecas_nao_carregam(monkeypatch):
    """A queda acontece antes de abrir o modelo, não no meio da transcrição."""
    monkeypatch.setattr(
        motor_whisper, "_diagnostico_cuda", diagnostico(gpus=1, faltando=("cublas64_12.dll",))
    )
    assert motor_whisper.escolher_dispositivo("auto") == ("cpu", "int8")


def test_quem_pediu_cuda_recebe_o_motivo_em_vez_da_queda_silenciosa(monkeypatch):
    monkeypatch.setattr(
        motor_whisper, "_diagnostico_cuda", diagnostico(gpus=1, faltando=("cublas64_12.dll",))
    )
    with pytest.raises(RuntimeError, match="cublas64_12.dll"):
        motor_whisper.escolher_dispositivo("cuda")


def test_pedir_cpu_nem_consulta_a_gpu(monkeypatch):
    def explodir():
        raise AssertionError("não deveria olhar a GPU")

    monkeypatch.setattr(motor_whisper, "diagnosticar_cuda", explodir)
    assert motor_whisper.escolher_dispositivo("cpu") == ("cpu", "int8")


# --- Transcrição em blocos ---------------------------------------------------

import wave as _wave

import numpy as np

from transcritor import motor_whisper as _mw


def gravar_wav(caminho, amostras: np.ndarray, taxa: int = 1000):
    with _wave.Wave_write(str(caminho)) as onda:
        onda.setnchannels(1)
        onda.setsampwidth(2)
        onda.setframerate(taxa)
        onda.writeframes((amostras * 32767).astype(np.int16).tobytes())


def test_os_blocos_cobrem_o_audio_inteiro_sem_sobra(tmp_path, monkeypatch):
    monkeypatch.setattr(_mw, "BLOCO_S", 2.0)
    monkeypatch.setattr(_mw, "MARGEM_CORTE_S", 0.5)
    sinal = np.sin(np.arange(7300) / 3).astype(np.float32) * 0.5
    gravar_wav(tmp_path / "a.wav", sinal)
    partes = list(_mw.blocos(tmp_path / "a.wav"))
    assert sum(len(a) for _, a in partes) == len(sinal)
    inicios = [inicio for inicio, _ in partes]
    assert inicios == sorted(inicios) and inicios[0] == 0.0
    for (inicio, audio), (seguinte, _) in zip(partes, partes[1:], strict=False):
        assert inicio + len(audio) / 1000 == pytest.approx(seguinte)


def test_o_corte_cai_no_silencio(tmp_path, monkeypatch):
    monkeypatch.setattr(_mw, "BLOCO_S", 2.0)
    monkeypatch.setattr(_mw, "MARGEM_CORTE_S", 0.5)
    sinal = np.sin(np.arange(5000) / 3).astype(np.float32) * 0.5
    sinal[2200:2500] = 0.0  # silêncio entre 2,2 s e 2,5 s, dentro da margem
    gravar_wav(tmp_path / "a.wav", sinal)
    _, segundo = list(_mw.blocos(tmp_path / "a.wav"))[:2]
    assert 2.2 <= segundo[0] <= 2.5


class SegmentoFalso:
    def __init__(self, inicio, fim, texto):
        self.start, self.end, self.text = inicio, fim, texto
        self.words = [PalavraFalsa(inicio, fim, texto)]


class PalavraFalsa:
    def __init__(self, inicio, fim, texto):
        self.start, self.end, self.word, self.probability = inicio, fim, texto, 0.9


class ModeloFalso:
    def __init__(self):
        self.prompts, self.linguas = [], []

    def transcribe(self, audio, language=None, initial_prompt=None, **_):
        self.prompts.append(initial_prompt)
        self.linguas.append(language)
        n = len(self.prompts)
        info = type("Info", (), {"language": "pt"})()
        return iter([SegmentoFalso(0.5, 1.0, f"bloco {n}")]), info


def test_tempos_somam_o_inicio_do_bloco_e_todo_bloco_recebe_o_contexto(tmp_path, monkeypatch):
    monkeypatch.setattr(_mw, "BLOCO_S", 2.0)
    monkeypatch.setattr(_mw, "MARGEM_CORTE_S", 0.5)
    modelo = ModeloFalso()
    monkeypatch.setattr(_mw, "_abrir_modelo", lambda *a: modelo)
    monkeypatch.setattr(_mw, "escolher_dispositivo", lambda *_: ("cpu", "int8"))
    gravar_wav(tmp_path / "a.wav", np.zeros(16000 * 5, dtype=np.float32), taxa=16000)

    fracoes = []
    falas = _mw.transcrever(
        tmp_path / "a.wav", tmp_path, idioma="auto", contexto="Strahd.",
        progresso=fracoes.append,
    )
    assert [f.texto for f in falas] == ["bloco 1", "bloco 2", "bloco 3"]
    assert falas[0].inicio == pytest.approx(0.5)
    assert falas[1].inicio > 2.0  # deslocado pelo início do segundo bloco
    assert modelo.prompts == ["Strahd."] * 3
    assert modelo.linguas == [None, "pt", "pt"]  # detecta no primeiro e mantém
    assert fracoes == sorted(fracoes) and fracoes[-1] == 1.0
