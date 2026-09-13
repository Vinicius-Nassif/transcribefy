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
