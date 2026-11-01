"""Geração dos arquivos de transcrição."""

import json

import pytest

from transcritor import saida
from transcritor.transcricao import Fala

FALAS = [
    Fala(0.0, 4.0, "abram a porta", 0, "GM"),
    Fala(6.0, 9.5, "eu abro", 1, "Ana"),
    Fala(12.0, 30.0, "espera ai", 1, "Bruno"),
]


def test_srt_usa_o_tempo_final_real_da_fala():
    transcript = saida.construir_transcript(FALAS)
    blocos = transcript.to_srt()
    assert "00:00:00,000 --> 00:00:04,000" in blocos
    assert "00:00:06,000 --> 00:00:09,500" in blocos
    # a última fala dura 18 s: a estimativa fixa de 5 s do pytranscript não serve
    assert "00:00:12,000 --> 00:00:30,000" in blocos


def test_srt_mantem_a_fala_interrompida_inteira():
    """Cortar a legenda do GM quando alguém o interrompe apagaria a interrupção."""
    falas = [Fala(0.0, 12.0, "a", 0, "GM"), Fala(5.0, 9.0, "b", 1, "Ana")]
    srt = saida.construir_transcript(falas).to_srt()
    assert "00:00:00,000 --> 00:00:12,000" in srt
    assert "00:00:05,000 --> 00:00:09,000" in srt


def test_srt_traz_as_duas_falas_simultaneas():
    falas = [Fala(0.0, 12.0, "a", 0, "GM"), Fala(5.0, 9.0, "b", 1, "Ana")]
    srt = saida.construir_transcript(falas).to_srt()
    assert "GM: a" in srt and "Ana: b" in srt


def test_uma_resposta_de_uma_palavra_fica_tempo_suficiente_na_tela():
    falas = [Fala(3.0, 3.05, "sim", 1, "Ana")]
    srt = saida.construir_transcript(falas).to_srt()
    assert f"00:00:03,000 --> 00:00:03,{int(saida.DURACAO_MINIMA_LEGENDA * 1000):03d}" in srt


def test_cada_linha_leva_o_nome_do_locutor():
    transcript = saida.construir_transcript(FALAS)
    assert transcript.text[0] == "GM: abram a porta"
    assert transcript.text[1] == "Ana: eu abro"


def test_vtt_tem_cabecalho_e_ponto_decimal():
    vtt = saida.construir_transcript(FALAS).to_vtt()
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:04.000" in vtt


def test_markdown_traz_carimbo_de_tempo_e_locutor():
    md = saida.para_markdown(FALAS, "Sessão 1")
    assert md.startswith("# Sessão 1")
    assert "**[00:06.00] Ana:** eu abro" in md


def test_json_detalhado_preserva_faixa_e_tempo_final():
    dados = json.loads(saida.para_json_detalhado(FALAS))
    assert dados["locutores"] == ["Ana", "Bruno", "GM"]
    assert dados["total_falas"] == 3
    assert dados["duracao"] == 30.0
    assert dados["falas"][1] == {
        "inicio": 6.0, "fim": 9.5, "faixa": 2, "locutor": "Ana",
        "sobreposta": False, "texto": "eu abro",
    }


def test_escrever_gera_todos_os_formatos(tmp_path):
    gerados = saida.escrever(FALAS, tmp_path / "sessao", formatos=saida.FORMATOS)
    nomes = {c.name for c in gerados.arquivos}
    assert nomes == {
        "sessao.txt", "sessao.csv", "sessao.json", "sessao.srt",
        "sessao.vtt", "sessao.md", "sessao.detalhado.json",
    }
    assert all(c.is_file() for c in gerados.arquivos)
    assert gerados.avisos == []


def test_escrever_respeita_o_subconjunto_de_formatos(tmp_path):
    gerados = saida.escrever(FALAS, tmp_path / "s", formatos=["srt"])
    assert {c.name for c in gerados.arquivos} == {"s.srt", "s.detalhado.json"}


class MotorFalso:
    """Motor de tradução que só prefixa o idioma e falha nas linhas pedidas."""

    nome = "falso"
    descricao = "falso"
    online = False

    def __init__(self, falhar=()):
        self.falhar = set(falhar)
        self.chamadas = []

    def idiomas(self):
        return {"en": "Inglês", "es": "Espanhol"}

    def traduzir(self, linhas, origem, destino):
        self.chamadas.append((list(linhas), origem, destino))
        return [
            None if texto in self.falhar else f"{destino.upper()} {texto}"
            for texto in linhas
        ]


def test_traducao_reinsere_as_linhas_que_falharam(tmp_path):
    """Uma linha que a tradução não cobre volta no idioma original, sem sumir."""
    gerados = saida.escrever(
        FALAS, tmp_path / "s", formatos=["srt"], traducoes=["en"],
        motor_traducao=MotorFalso(falhar={"eu abro"}),
    )
    conteudo = (tmp_path / "s.en.srt").read_text(encoding="utf-8")
    assert "GM: EN abram a porta" in conteudo
    assert "Ana: eu abro" in conteudo          # preservada no original
    assert "00:00:06,000 --> 00:00:09,500" in conteudo  # e ainda alinhada no tempo
    assert gerados.avisos == [
        "1 linha(s) não puderam ser traduzidas para Inglês e ficaram no idioma original."
    ]


def test_quando_tudo_falha_o_aviso_diz_a_causa_provavel(tmp_path):
    motor = MotorFalso(falhar={f.texto for f in FALAS})
    gerados = saida.escrever(FALAS, tmp_path / "s", formatos=["txt"], traducoes=["en"],
                             motor_traducao=motor)
    (aviso,) = gerados.avisos
    assert "falhou por inteiro" in aviso and "conexão" in aviso
    assert "Ana: eu abro" in (tmp_path / "s.en.txt").read_text(encoding="utf-8")


def test_traduz_para_varios_idiomas_um_arquivo_por_idioma(tmp_path):
    motor = MotorFalso()
    gerados = saida.escrever(
        FALAS, tmp_path / "s", formatos=["srt", "txt", "md"], idioma="pt",
        traducoes=["en", "es"], motor_traducao=motor,
    )
    nomes = {c.name for c in gerados.arquivos}
    assert {"s.en.srt", "s.en.txt", "s.es.srt", "s.es.txt"} <= nomes
    assert "s.en.md" not in nomes  # o Markdown sai só no original
    assert [(origem, destino) for _, origem, destino in motor.chamadas] == [
        ("pt", "en"), ("pt", "es"),
    ]
    assert "ES espera ai" in (tmp_path / "s.es.txt").read_text(encoding="utf-8")


def test_o_nome_de_quem_fala_nao_vai_para_o_tradutor(tmp_path):
    motor = MotorFalso()
    saida.escrever(FALAS, tmp_path / "s", formatos=["txt"], traducoes=["en"],
                   motor_traducao=motor)
    (linhas, _, _), = motor.chamadas
    assert linhas == ["abram a porta", "eu abro", "espera ai"]
    assert "Bruno: EN espera ai" in (tmp_path / "s.en.txt").read_text(encoding="utf-8")


def test_sem_traducoes_o_motor_nem_e_consultado(tmp_path, monkeypatch):
    from transcritor import traducao

    def nao_devia(*_a):
        raise AssertionError("consultou o motor sem tradução pedida")

    monkeypatch.setattr(traducao, "motor", nao_devia)
    saida.escrever(FALAS, tmp_path / "s", formatos=["txt"])


def test_formato_desconhecido_e_recusado(tmp_path):
    with pytest.raises(ValueError):
        saida.escrever(FALAS, tmp_path / "s", formatos=["docx"])


def test_json_detalhado_marca_quem_falou_ao_mesmo_tempo():
    falas = [
        Fala(0.0, 12.0, "monólogo", 0, "GM"),
        Fala(5.0, 6.0, "peraí", 1, "Ana"),
        Fala(20.0, 22.0, "tá", 1, "Bruno"),
    ]
    dados = json.loads(saida.para_json_detalhado(falas))
    assert [f["sobreposta"] for f in dados["falas"]] == [True, True, False]
    assert dados["total_sobrepostas"] == 2


def test_nenhuma_fala_simultanea_some_dos_arquivos(tmp_path):
    """A garantia que importa: o que entra no `escrever` sai em todo formato."""
    falas = [
        Fala(0.0, 12.0, "monólogo do mestre", 0, "GM"),
        Fala(4.0, 5.0, "peraí", 1, "Ana"),
        Fala(4.5, 5.5, "deixa eu ver", 1, "Bruno"),
    ]
    saida.escrever(falas, tmp_path / "s", formatos=saida.FORMATOS)

    for sufixo in ("md", "txt", "csv", "srt", "vtt"):
        conteudo = (tmp_path / f"s.{sufixo}").read_text(encoding="utf-8")
        for fala in falas:
            assert fala.texto in conteudo, f"{fala.texto!r} sumiu do .{sufixo}"

    # Os JSON escapam os acentos, então a checagem é sobre o conteúdo, não o texto bruto.
    simples = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    detalhado = json.loads((tmp_path / "s.detalhado.json").read_text(encoding="utf-8"))
    for fala in falas:
        assert any(fala.texto in linha for linha in simples["text"]), fala.texto
    assert [f["texto"] for f in detalhado["falas"]] == [f.texto for f in falas]


def test_arquivos_saem_em_utf8_mesmo_com_caracteres_fora_da_cp1252(tmp_path):
    """Travessão e reticências o Whisper produz; a codificação padrão do Windows não aceita."""
    falas = [Fala(0.0, 2.0, "ele hesita — e então… abre a porta", 0, "GM")]
    saida.escrever(falas, tmp_path / "s", formatos=["txt", "srt", "vtt", "csv", "md"])
    for sufixo in ("txt", "srt", "vtt", "csv", "md"):
        conteudo = (tmp_path / f"s.{sufixo}").read_bytes().decode("utf-8")
        assert "— e então…" in conteudo, sufixo


def test_formato_desconhecido_continua_recusado_com_a_gravacao_propria(tmp_path):
    with pytest.raises(ValueError, match="Formato desconhecido"):
        saida._gravar(saida.construir_transcript(FALAS), tmp_path / "s.docx")
