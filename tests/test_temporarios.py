"""Pastas temporárias: removidas ao fim e, se órfãs, na próxima inicialização."""

import os
import subprocess
import sys
import time

import pytest

from transcritor import temporarios


def test_a_pasta_some_ao_sair_mesmo_com_erro(tmp_path):
    with pytest.raises(RuntimeError), temporarios.pasta_temporaria(tmp_path) as pasta:
        (pasta / "faixa.wav").write_bytes(b"x")
        raise RuntimeError
    assert not pasta.exists()


def test_nao_apaga_a_pasta_de_quem_esta_vivo(tmp_path):
    viva = temporarios.PastaTemporaria(tmp_path)
    try:
        assert temporarios.limpar_orfas(tmp_path) == []
        assert viva.caminho.is_dir()
    finally:
        viva.remover()


def test_apaga_a_pasta_de_um_processo_que_morreu(tmp_path):
    """O processo filho cria a pasta e morre sem limpar, como num kill -9."""
    codigo = (
        "import os, sys; from pathlib import Path; from transcritor import temporarios; "
        f"p = temporarios.PastaTemporaria(Path(r'{tmp_path}')); print(p.caminho); "
        "sys.stdout.flush(); os._exit(1)"
    )
    saida = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True)
    orfa = tmp_path / os.path.basename(saida.stdout.strip())
    assert orfa.is_dir()
    assert temporarios.limpar_orfas(tmp_path) == [orfa]
    assert not orfa.exists()


def test_pasta_sem_trava_so_e_apagada_se_for_antiga(tmp_path):
    nova = tmp_path / f"{temporarios.PREFIXO}nova"
    antiga = tmp_path / f"{temporarios.PREFIXO}antiga"
    nova.mkdir()
    antiga.mkdir()
    velho = time.time() - temporarios.IDADE_SEM_TRAVA - 60
    os.utime(antiga, (velho, velho))
    assert temporarios.limpar_orfas(tmp_path) == [antiga]
    assert nova.is_dir()


def test_nao_mexe_no_que_nao_e_do_transcritor(tmp_path):
    alheia = tmp_path / "outra-coisa"
    alheia.mkdir()
    temporarios.limpar_orfas(tmp_path)
    assert alheia.is_dir()
