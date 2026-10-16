"""Contrato entre a API e a página que a consome."""

import re
from pathlib import Path

from transcritor import web

PAGINA = (Path(web.__file__).parent / "web_static" / "index.html").read_text(
    encoding="utf-8"
)


def campos_lidos_pela_pagina() -> set[str]:
    """Campos do estado do trabalho que o JavaScript acessa (`t.algumacoisa`)."""
    return set(re.findall(r"\bt\.([a-z_]+)", PAGINA))


def test_a_pagina_so_le_campos_que_a_api_devolve():
    disponiveis = set(web.Trabalho(id="x", nome_video="v.mp4").como_dict())
    assert campos_lidos_pela_pagina() <= disponiveis


def test_o_payload_nao_usa_acentos_nas_chaves():
    """Chave acentuada quebra o acesso por ponto no JavaScript."""
    for chave in web.Trabalho(id="x", nome_video="v.mp4").como_dict():
        assert chave.isascii(), chave


def test_estado_inicial_de_um_trabalho():
    trabalho = web.Trabalho(id="abc", nome_video="sessao.mp4").como_dict()
    assert trabalho["estado"] == "na fila"
    assert trabalho["progresso"] == 0.0
    assert trabalho["arquivos"] == []
    assert trabalho["avisos"] == []


def test_o_pedido_montado_pela_pagina_so_tem_campos_que_a_api_aceita():
    """Toda chave do objeto de `montarPedido()` existe no modelo do pedido.

    O Pydantic ignora chaves desconhecidas em silêncio: um campo renomeado de um
    lado só deixaria de chegar ao pipeline sem erro nenhum.
    """
    corpo = PAGINA[PAGINA.index("function montarPedido()"):]
    corpo = corpo[: corpo.index("\n}\n")]
    enviadas = set(re.findall(r"^\s*([a-z_]+):", corpo, flags=re.MULTILINE))
    aceitas = set(web.Pedido.model_fields) | set(web.FaixaPedida.model_fields) | set(
        web.IntervaloPedido.model_fields
    )
    assert enviadas, "não achei as chaves do pedido na página"
    assert enviadas <= aceitas, enviadas - aceitas


def test_a_pagina_so_chama_rotas_que_existem():
    chamadas = {
        re.sub(r"\$\{[^}]+\}", "{x}", c).split("?")[0]
        for c in re.findall(r"""[`"](/api/[^`"]+)[`"]""", PAGINA)
    }
    rotas = {re.sub(r"\{[^}]+\}", "{x}", r.path) for r in web.app.routes if hasattr(r, "path")}
    assert chamadas, "não achei chamadas à API na página"
    assert chamadas <= rotas, chamadas - rotas


def test_o_script_de_inicio_do_windows_confere_um_campo_que_a_api_devolve():
    """O script só abre o navegador quando reconhece este campo na resposta.

    É o mesmo tipo de contrato que os testes acima cobrem entre a API e a
    página: renomear a chave no `web.py` deixaria o `iniciar-windows.ps1`
    esperando para sempre, sem erro nenhum que apontasse o motivo.
    """
    script = (
        Path(web.__file__).parent.parent / "scripts" / "iniciar-windows.ps1"
    ).read_text(encoding="utf-8-sig")
    procurados = set(re.findall(r"""Content -match '"(\w+)"'""", script))
    assert procurados, "o script não confere campo nenhum da resposta"
    assert procurados <= set(web.listar_modelos())
