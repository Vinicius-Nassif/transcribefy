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


def test_a_pagina_envia_todos_os_campos_do_formulario():
    """Todo `name=` do formulário precisa existir como parâmetro do endpoint."""
    import inspect

    enviados = set(
        re.findall(r'<(?:input|select)\b[^>]*?name="([a-z_]+)"', PAGINA)
    )
    aceitos = set(inspect.signature(web.criar_trabalho).parameters)
    assert enviados <= aceitos, enviados - aceitos
