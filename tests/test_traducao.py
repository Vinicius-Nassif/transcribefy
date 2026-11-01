"""Motores de tradução: registro, idiomas e tradução em lotes conferidos."""

import pytest

from transcritor import cli, traducao


class TradutorFalso:
    """Faz o papel do Google: recebe um texto e devolve a tradução."""

    def __init__(self, juntar_em=None, falhar_lotes=0, falhar_linhas=()):
        self.juntar_em = juntar_em  # lote que volta com duas linhas fundidas
        self.falhar_lotes = falhar_lotes
        self.falhar_linhas = set(falhar_linhas)
        self.recebidos: list[str] = []

    def __call__(self, texto):
        self.recebidos.append(texto)
        if self.falhar_lotes:
            self.falhar_lotes -= 1
            raise ConnectionError("sem rede")
        if texto in self.falhar_linhas:
            raise RuntimeError("falhou")
        linhas = [f"T({linha})" for linha in texto.split("\n")]
        if self.juntar_em is not None and len(self.recebidos) - 1 == self.juntar_em:
            linhas[0:2] = [linhas[0] + " " + linhas[1]]  # o tradutor fundiu duas linhas
        return "\n".join(linhas)


def test_lotes_respeitam_o_limite_de_caracteres():
    linhas = ["a" * 40] * 10  # 41 caracteres cada, com a quebra de linha
    lotes = traducao._lotes(linhas, limite=100)
    assert [len(lote) for lote in lotes] == [2, 2, 2, 2, 2]
    assert sum(lotes, []) == list(range(10))


def test_uma_linha_maior_que_o_limite_vai_sozinha():
    assert traducao._lotes(["x" * 300, "y"], limite=100) == [[0], [1]]


def test_traduz_em_poucas_requisicoes_e_na_ordem():
    tradutor = TradutorFalso()
    linhas = [f"fala {i}" for i in range(50)]
    resultado = traducao.traduzir_em_lotes(linhas, tradutor, limite=100)
    assert resultado == [f"T(fala {i})" for i in range(50)]
    assert len(tradutor.recebidos) < 10  # e não 50, uma por linha


def test_lote_com_quantidade_diferente_e_refeito_linha_por_linha():
    """Se o tradutor funde duas linhas, não dá para saber qual tradução é de quem."""
    tradutor = TradutorFalso(juntar_em=1)  # o segundo lote volta com uma linha a menos
    linhas = [f"fala {i}" for i in range(8)]
    resultado = traducao.traduzir_em_lotes(linhas, tradutor, limite=30)

    assert resultado == [f"T(fala {i})" for i in range(8)]  # cada fala no seu lugar
    segundo_lote = tradutor.recebidos[1].split("\n")
    refeitas = tradutor.recebidos[2 : 2 + len(segundo_lote)]
    assert refeitas == segundo_lote  # uma requisição por linha daquele lote


def test_lote_que_falha_marca_as_linhas_sem_traducao():
    tradutor = TradutorFalso(falhar_lotes=1)
    linhas = [f"fala {i}" for i in range(6)]
    resultado = traducao.traduzir_em_lotes(linhas, tradutor, limite=30)
    primeiro = len(tradutor.recebidos[0].split("\n"))
    assert resultado[:primeiro] == [None] * primeiro
    assert all(r is not None for r in resultado[primeiro:])


def test_desiste_depois_de_falhas_seguidas():
    """Sem rede, insistir lote a lote só multiplicaria a espera."""
    tradutor = TradutorFalso(falhar_lotes=99)
    linhas = [f"fala {i}" for i in range(40)]
    resultado = traducao.traduzir_em_lotes(linhas, tradutor, limite=30)
    assert resultado == [None] * 40
    assert len(tradutor.recebidos) == traducao.FALHAS_SEGUIDAS


def test_linha_que_falha_no_refazer_fica_sem_traducao():
    tradutor = TradutorFalso(juntar_em=0, falhar_linhas={"b"})
    assert traducao.traduzir_em_lotes(["a", "b", "c"], tradutor) == ["T(a)", None, "T(c)"]


def test_linhas_vazias_nao_vao_ao_tradutor_e_quebras_viram_espaco():
    tradutor = TradutorFalso()
    resultado = traducao.traduzir_em_lotes(["a", "", "  ", "b\nc"], tradutor)
    assert resultado == ["T(a)", "", "  ", "T(b c)"]
    assert tradutor.recebidos == ["a\nb c"]


# --- Registro e idiomas ------------------------------------------------------


def test_o_motor_ativo_e_o_google():
    assert traducao.motor().nome == "google"
    assert traducao.motor("google") is traducao.MOTORES["google"]


def test_motor_desconhecido_e_recusado():
    with pytest.raises(ValueError, match="Disponíveis: google"):
        traducao.motor("babel")


def test_idiomas_do_google_tem_nome_em_portugues():
    idiomas = traducao.motor().idiomas()
    assert len(idiomas) > 100
    assert idiomas["en"] == "Inglês"
    assert idiomas["zh-CN"] == "Chinês (simplificado)"
    sem_nome = [c for c in idiomas if c not in traducao.NOMES]
    assert sem_nome == [], f"faltam nomes em português para {sem_nome}"


def test_validar_destinos_normaliza_e_recusa_o_desconhecido():
    assert traducao.validar_destinos([" en", "es", "en", ""]) == ["en", "es"]
    assert traducao.validar_destinos([]) == []
    with pytest.raises(ValueError, match="xx"):
        traducao.validar_destinos(["en", "xx"])


def test_um_motor_novo_so_precisa_ser_registrado(monkeypatch):
    class Offline:
        nome = "offline"
        descricao = "teste"
        online = False

        def idiomas(self):
            return {"eo": "Esperanto"}

        def traduzir(self, linhas, origem, destino):
            return list(linhas)

    monkeypatch.setitem(traducao.MOTORES, "offline", Offline())
    monkeypatch.setattr(traducao, "PADRAO", "offline")
    assert traducao.motor().nome == "offline"
    assert traducao.validar_destinos(["eo"]) == ["eo"]
    with pytest.raises(ValueError):
        traducao.validar_destinos(["en"])  # o Google tem, o ativo não


def test_google_usa_o_deep_translator_em_lotes(monkeypatch):
    recebidos = []

    class GoogleFalso:
        def __init__(self, source, target):
            self.par = (source, target)

        def translate(self, texto):
            recebidos.append((self.par, texto))
            return texto.upper()

    import deep_translator

    monkeypatch.setattr(deep_translator, "GoogleTranslator", GoogleFalso)
    assert traducao.Google().traduzir(["oi", "tchau"], "pt", "en") == ["OI", "TCHAU"]
    assert recebidos == [(("pt", "en"), "oi\ntchau")]


# --- CLI ---------------------------------------------------------------------


def test_cli_aceita_varios_idiomas(monkeypatch, tmp_path):
    from transcritor import pipeline

    recebidas = []
    monkeypatch.setattr(
        cli.pipeline, "executar",
        lambda c, r=None: recebidas.append(c) or pipeline.Resultado(falas=[]),
    )
    cli.main(["transcrever", "s.mp4", "-o", str(tmp_path / "s"), "--traduzir", "en, es"])
    assert list(recebidas[0].traducoes) == ["en", "es"]


def test_cli_recusa_idioma_que_o_motor_nao_tem(monkeypatch, capsys):
    monkeypatch.setattr(cli.pipeline, "executar", lambda *_a: pytest.fail("não devia rodar"))
    assert cli.main(["transcrever", "s.mp4", "--traduzir", "klingon"]) == 1
    assert "klingon" in capsys.readouterr().err


def test_cli_lista_os_idiomas(capsys):
    assert cli.main(["idiomas"]) == 0
    saida = capsys.readouterr().out
    assert "Google" in saida
    assert "en        Inglês" in saida
