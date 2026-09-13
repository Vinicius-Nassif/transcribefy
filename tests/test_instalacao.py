"""O que precisa continuar valendo para a instalação funcionar nas duas plataformas.

São testes de empacotamento, não de comportamento: cada um trava uma decisão que
um `uv pip freeze` distraído ou um editor com a configuração errada desfariam em
silêncio — e que só apareceria na máquina de quem está instalando.
"""

import codecs
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
SCRIPTS = RAIZ / "scripts"
INSTALADOR = SCRIPTS / "instalar-windows.ps1"
INICIADOR = SCRIPTS / "iniciar-windows.ps1"

# Cada comando do Windows é um par: o .ps1 com a lógica e o .cmd que o chama.
COMANDOS = ("instalar-windows", "iniciar-windows")


def linhas_de(arquivo: str, comeco: str) -> list[str]:
    texto = (RAIZ / arquivo).read_text(encoding="utf-8")
    return [l for l in texto.splitlines() if l.strip().startswith(comeco)]


def test_uvloop_so_e_instalado_fora_do_windows():
    """Não há wheel de uvloop para Windows: sem o marcador, a instalação inteira falha lá."""
    (linha,) = linhas_de("requirements.txt", "uvloop")
    assert 'sys_platform != "win32"' in linha


def test_as_bibliotecas_cuda_ficam_fora_do_requirements_principal():
    """São 1,4 GB que só servem com GPU NVIDIA; quem não tem não deve baixá-las."""
    assert not linhas_de("requirements.txt", "nvidia-")
    assert len(linhas_de("requirements-gpu.txt", "nvidia-")) == 3


@pytest.mark.parametrize("comando", COMANDOS)
def test_cada_comando_do_windows_tem_script_e_atalho(comando):
    assert (SCRIPTS / f"{comando}.ps1").is_file()
    assert (SCRIPTS / f"{comando}.cmd").is_file()


@pytest.mark.parametrize("comando", COMANDOS)
def test_os_scripts_estao_em_utf8_com_bom(comando):
    """O PowerShell 5.1 do Windows só lê o arquivo como UTF-8 quando há BOM."""
    assert (SCRIPTS / f"{comando}.ps1").read_bytes().startswith(codecs.BOM_UTF8)


@pytest.mark.parametrize("comando", COMANDOS)
def test_os_atalhos_usam_fim_de_linha_do_windows(comando):
    """Um .cmd com fim de linha do Unix se comporta de forma imprevisível."""
    bruto = (SCRIPTS / f"{comando}.cmd").read_bytes()
    assert b"\r\n" in bruto
    assert b"\n" not in bruto.replace(b"\r\n", b"")


@pytest.mark.parametrize("comando", COMANDOS)
def test_os_atalhos_contornam_a_politica_de_execucao(comando):
    """Sem isto, os scripts não rodam numa instalação limpa do Windows."""
    conteudo = (SCRIPTS / f"{comando}.cmd").read_text(encoding="ascii")
    assert "-ExecutionPolicy Bypass" in conteudo
    assert f"{comando}.ps1" in conteudo


def test_o_iniciador_chama_o_instalador_pelo_nome_certo():
    """Quando o ambiente não existe, iniciar delega a instalação em vez de falhar."""
    conteudo = INICIADOR.read_text(encoding="utf-8-sig")
    assert "instalar-windows.ps1" in conteudo
    assert INSTALADOR.is_file()
