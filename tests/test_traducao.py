"""Escolha dos idiomas de tradução e os dois tradutores, sem rede."""

import json
from pathlib import Path

import pytest

from transcritor import traducao


def instalar_falso(base: Path, origem: str, destino: str) -> None:
    pasta = traducao.pasta(base) / f"translate-{origem}_{destino}"
    pasta.mkdir(parents=True)
    (pasta / "metadata.json").write_text(
        json.dumps({"from_code": origem, "to_code": destino}), encoding="utf-8"
    )


def test_sem_pacotes_nao_ha_idioma_offline(tmp_path):
    assert traducao.disponiveis("pt", online=False, diretorio=tmp_path) == []


def test_idiomas_diretos_e_pelo_ingles(tmp_path):
    instalar_falso(tmp_path, "pt", "en")
    instalar_falso(tmp_path, "en", "es")
    instalar_falso(tmp_path, "en", "pt")
    idiomas = traducao.disponiveis("pt", online=False, diretorio=tmp_path)
    assert [(i.codigo, i.servico) for i in idiomas] == [("es", "offline"), ("en", "offline")]
    assert idiomas[0].nome == "Espanhol"


def test_sem_o_primeiro_passo_o_pivo_nao_vale(tmp_path):
    instalar_falso(tmp_path, "en", "es")
    assert traducao.disponiveis("pt", online=False, diretorio=tmp_path) == []


def test_online_acrescenta_os_do_google_sem_repetir_os_offline(tmp_path):
    instalar_falso(tmp_path, "pt", "en")
    idiomas = traducao.disponiveis("pt", online=True, diretorio=tmp_path)
    assert idiomas[0].valor == "en"
    google = [i for i in idiomas if i.servico == "google"]
    assert google and all(i.valor.startswith("google:") for i in google)
    assert "en" not in {i.codigo for i in google}
    assert "pt" not in {i.codigo for i in google}


def test_tradutor_sem_pacote_explica_como_instalar(tmp_path):
    with pytest.raises(ValueError, match="transcritor idiomas --instalar pt-fr"):
        traducao.tradutor("fr", "pt", diretorio=tmp_path)


def test_codigo_do_valor():
    assert traducao.codigo("google:ja") == "ja"
    assert traducao.codigo("en") == "en"


def test_lotes_respeitam_o_limite(monkeypatch):
    monkeypatch.setattr(traducao, "LOTE_GOOGLE_CARACTERES", 10)
    assert traducao._lotes(["abcd", "efgh", "ijkl", "m"]) == [[0, 1], [2, 3]]


class GoogleFalso:
    chamadas = []
    falhar_em = None

    def __init__(self, source, target):
        pass

    def translate(self, texto):
        GoogleFalso.chamadas.append(texto)
        if GoogleFalso.falhar_em and GoogleFalso.falhar_em in texto:
            if "\n" in texto:
                return "lote desalinhado"
            raise RuntimeError("limite")
        return texto.upper()


def test_google_traduz_em_lotes_e_isola_a_linha_que_falha(monkeypatch):
    import deep_translator

    GoogleFalso.chamadas = []
    GoogleFalso.falhar_em = "ruim"
    monkeypatch.setattr(deep_translator, "GoogleTranslator", GoogleFalso)
    traduzir = traducao.tradutor_google("pt", "en")
    assert traduzir(["boa", "outra"]) == ["BOA", "OUTRA"]
    assert len(GoogleFalso.chamadas) == 1  # um lote, não uma requisição por linha
    assert traduzir(["boa", "ruim"]) == ["BOA", None]


PT_EN = any((p.origem, p.destino) == ("pt", "en") for p in traducao.instalados())


@pytest.mark.skipif(not PT_EN, reason="pacote pt→en não instalado")
def test_traducao_offline_de_verdade():
    traduzir = traducao.tradutor("en", "pt")
    (frase,) = traduzir(["eu empurro a porta devagar"])
    assert "door" in frase.lower() and "▁" not in frase
