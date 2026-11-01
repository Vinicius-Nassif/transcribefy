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
        re.findall(r'<(?:input|select|textarea)\b[^>]*?name="([a-z_]+)"', PAGINA)
    )
    aceitos = set(inspect.signature(web.criar_trabalho).parameters) | set(
        inspect.signature(web._formulario).parameters
    )
    assert enviados <= aceitos, enviados - aceitos


SCRIPT = PAGINA[PAGINA.index("<script>"): PAGINA.index("</script>")]


def rotas_da_api() -> list[re.Pattern]:
    """Cada rota do FastAPI vira uma expressão: `{id}` casa com `${...}` do JavaScript."""
    padroes = []
    for rota in web.app.routes:
        caminho = getattr(rota, "path", "")
        if caminho.startswith("/api/"):
            corpo = re.sub(r"\\{[^}]+\\}", r"\\$\\{[^}]+\\}", re.escape(caminho))
            padroes.append(re.compile(corpo + "$"))
    return padroes


def test_toda_chamada_da_pagina_aponta_para_uma_rota_que_existe():
    chamadas = re.findall(r"""(?:fetch\(|open\("POST",\s*)[`"](/api/[^`"?]*)""", SCRIPT)
    assert len(chamadas) >= 8
    padroes = rotas_da_api()
    sem_rota = [c for c in chamadas if not any(p.match(c) for p in padroes)]
    assert sem_rota == []


def test_a_pagina_so_envia_campos_que_a_api_aceita():
    import inspect

    enviados = set(re.findall(r'\b(?:dados|corpo)\.set\("([a-z_]+)"', SCRIPT))
    assert {"faixas", "termos", "traduzir", "video", "inicio", "fim"} <= enviados
    aceitos = set(inspect.signature(web._formulario).parameters) | {"video"}
    assert enviados <= aceitos, enviados - aceitos


def test_cada_faixa_e_montada_com_as_chaves_que_o_servidor_le():
    """O objeto de `faixaParaApi` precisa bater com o que `faixas.ler_json` procura."""
    import inspect

    from transcritor import faixas

    corpo = SCRIPT[SCRIPT.index("function faixaParaApi"):]
    corpo = corpo[: corpo.index("\n}\n")]
    enviadas = set(re.findall(r"^\s+(\w+):", corpo, re.MULTILINE))
    lidas = set(re.findall(r'item\.get\("(\w+)"', inspect.getsource(faixas.ler_json)))
    assert enviadas == lidas


def test_nada_vindo_do_servidor_vira_html():
    """Nomes, falas e mensagens entram por textContent; innerHTML abriria brecha."""
    assert "innerHTML" not in SCRIPT
    assert "insertAdjacentHTML" not in SCRIPT


def test_todo_campo_estatico_tem_ajuda():
    """Cada `<label for=...>` vem acompanhado do botão "i" com o balão de ajuda."""
    for achado in re.finditer(r'<label for="([\w-]+)"', PAGINA):
        trecho = PAGINA[achado.end(): achado.end() + 300]
        assert 'class="info"' in trecho, achado.group(1)


def test_todo_campo_das_faixas_tem_ajuda():
    rotulos = re.findall(r'el\("label", \{ for:.*? \}\),\s*(\w+)\(', SCRIPT)
    assert len(rotulos) == SCRIPT.count('el("label", { for:') == 3  # nome, pessoas, deslocamento
    assert set(rotulos) == {"dica"}


def test_o_recorte_comeca_desligado():
    """Desligado, os campos ficam desabilitados e não são enviados."""
    assert re.search(r'id="recorte-ativo"(?![^>]*checked)', PAGINA)
    for campo in ("inicio", "fim"):
        assert re.search(rf'<input id="{campo}"[^>]*disabled', PAGINA), campo
    assert 'if ($("recorte-ativo").checked)' in SCRIPT


def test_a_janela_de_nome_repetido_tem_continuar_e_revisar():
    dialogo = PAGINA[PAGINA.index('<dialog id="dialogo-repetidos"'): PAGINA.index("</dialog>")]
    assert 'id="continuar"' in dialogo and 'id="revisar"' in dialogo
    assert "/api/faixas/verificar" in SCRIPT


def test_a_pagina_comeca_com_o_arranjo_de_duas_faixas():
    assert 'adicionarFaixa({ numero: 1, tipo: "uma", nome: "GM" })' in SCRIPT
    assert 'adicionarFaixa({ numero: 2, tipo: "varias", pessoas: "5" })' in SCRIPT


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


def test_a_busca_sem_acento_usa_o_escape_e_nao_caracteres_invisiveis():
    """Os acentos são removidos por uma regex com o intervalo dos diacríticos escrito com escape.

    Os mesmos caracteres colados literalmente funcionam, mas são invisíveis no
    editor e se perdem numa cópia; uma revisão não teria como notá-los.
    """
    import unicodedata

    barra = "\\"
    assert "replace(/[" + barra + "u0300-" + barra + "u036f]/g" in SCRIPT
    assert not any(unicodedata.combining(c) for c in PAGINA)
