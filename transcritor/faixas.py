"""Quem fala em cada faixa de áudio: a configuração das N faixas.

Cada faixa do arquivo escolhida pelo usuário é de um de dois tipos:

- **uma pessoa** (`UMA`) — todas as falas recebem um nome fixo, sem separação
  por voz. É o caso do microfone do GM.
- **várias pessoas** (`VARIAS`) — as falas passam pela separação por voz
  (`diarizacao`), com uma lista opcional de nomes. É o caso do Discord.

Este módulo é o único lugar que traduz o que o usuário escreveu — na linha de
comando (`ler_especificacao`) ou no formulário web (`ler_json`) — numa lista de
`Faixa`, e o único que confere essa lista (`validar`, `nomes_repetidos`). A CLI
e a API usam as mesmas funções, então as duas aceitam e recusam as mesmas coisas.

Números de faixa: o usuário conta a partir de 1, como o `transcritor faixas`
mostra; `Faixa.numero` conta a partir de 0, como o ffmpeg.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

UMA = "uma"
VARIAS = "varias"
TIPOS = (UMA, VARIAS)

# Prefixo dos locutores sem nome quando há uma faixa só de várias pessoas: é o
# arranjo de sempre (GM + jogadores), e o resultado continua "Jogador 1, 2...".
PREFIXO_UNICO = "Jogador"


@dataclass(slots=True)
class Faixa:
    """Uma faixa de áudio do arquivo e quem fala nela."""

    numero: int                     # posição no arquivo, a partir de 0
    tipo: str = UMA
    nome: str = ""                  # UMA: rótulo das falas; vazio vira "Faixa N"
    nomes: Sequence[str] = ()       # VARIAS: na ordem da primeira fala de cada voz
    n_locutores: int | None = None  # VARIAS: quantas vozes; None estima sozinho
    prefixo: str = ""               # VARIAS: rótulo dos sem nome; vazio é automático
    deslocamento: float = 0.0       # ajuste de sincronia, em segundos

    @property
    def rotulo(self) -> str:
        return f"faixa {self.numero + 1}"

    @property
    def nome_unico(self) -> str:
        return self.nome.strip() or f"Faixa {self.numero + 1}"


def varias(
    numero: int,
    nomes: Sequence[str] = (),
    n_locutores: int | None = None,
    deslocamento: float = 0.0,
) -> Faixa:
    """Faixa de várias pessoas sem quantidade explícita: usa o número de nomes.

    Com nomes informados, a separação procura exatamente aquelas vozes; sem
    nenhum, estima a quantidade sozinha.
    """
    nomes = tuple(n.strip() for n in nomes if n.strip())
    if n_locutores is None and nomes:
        n_locutores = len(nomes)
    return Faixa(numero, VARIAS, nomes=nomes, n_locutores=n_locutores,
                 deslocamento=deslocamento)


def padrao(
    faixa_gm: int = 0,
    faixa_grupo: int = 1,
    nome_gm: str = "GM",
    n_jogadores: int | None = 5,
    nomes_jogadores: Sequence[str] = (),
    deslocamento_grupo: float = 0.0,
) -> list[Faixa]:
    """O arranjo de duas faixas de antes: o GM numa, os jogadores na outra.

    Reproduz as opções antigas exatamente — inclusive `n_jogadores=None` como
    "estimar", mesmo com nomes —, para que um arquivo de duas faixas saia igual.
    """
    return [
        Faixa(faixa_gm, UMA, nome=nome_gm.strip() or "GM"),
        Faixa(
            faixa_grupo, VARIAS,
            nomes=tuple(n.strip() for n in nomes_jogadores if n.strip()),
            n_locutores=n_jogadores,
            deslocamento=deslocamento_grupo,
        ),
    ]


def prefixo_de(faixa: Faixa, faixas: Sequence[Faixa]) -> str:
    """Prefixo dos locutores sem nome de uma faixa de várias pessoas.

    Com duas faixas de várias pessoas, as duas gerariam "Jogador 1"; nesse caso
    o nome padrão leva a faixa junto, como "Faixa 3 · Pessoa 1".
    """
    if faixa.prefixo.strip():
        return faixa.prefixo.strip()
    if sum(f.tipo == VARIAS for f in faixas) > 1:
        return f"Faixa {faixa.numero + 1} · Pessoa"
    return PREFIXO_UNICO


def nomes_informados(faixas: Sequence[Faixa]) -> list[str]:
    """Todos os nomes dados pelo usuário, na ordem das faixas."""
    nomes: list[str] = []
    for faixa in faixas:
        if faixa.tipo == UMA:
            nomes.append(faixa.nome_unico)
        else:
            nomes.extend(faixa.nomes)
    return [n for n in nomes if n.strip()]


# --- Leitura -----------------------------------------------------------------


def _numero(texto: str) -> int:
    try:
        numero = int(texto.strip())
    except ValueError:
        raise ValueError(f"'{texto}' não é um número de faixa") from None
    if numero < 1:
        raise ValueError(f"A faixa {numero} não existe: a contagem começa em 1.")
    return numero - 1


def _quantidade(texto: str) -> int | None:
    texto = str(texto).strip().lower()
    if texto in {"", "auto"}:
        return None
    try:
        quantidade = int(texto)
    except ValueError:
        raise ValueError(
            f"'{texto}' não é uma quantidade de pessoas: use um número ou 'auto'."
        ) from None
    if quantidade < 1:
        raise ValueError("A quantidade de pessoas numa faixa precisa ser ao menos 1.")
    return quantidade


def ler_especificacao(texto: str) -> Faixa:
    """Lê uma faixa na sintaxe da CLI.

    - `1=GM` — faixa 1, uma pessoa chamada GM. Tudo depois do `=` é o nome.
    - `1=` — faixa 1, uma pessoa, com o nome padrão "Faixa 1".
    - `2=varias` — faixa 2, várias pessoas, quantidade estimada.
    - `2=varias:5` — cinco pessoas, nomes padrão.
    - `2=varias:5:Ana,Bruno` — cinco pessoas, as duas primeiras com nome.
    - `2=varias::Ana,Bruno` — tantas pessoas quantos nomes.
    """
    numero, separador, resto = texto.partition("=")
    if not separador:
        raise ValueError(
            f"'{texto}' não está no formato FAIXA=NOME ou FAIXA=varias[:QTD][:NOMES]"
        )
    indice = _numero(numero)
    tipo, _, opcoes = resto.partition(":")
    if tipo.strip().lower() != VARIAS:
        return Faixa(indice, UMA, nome=resto.strip())
    quantidade, _, nomes = opcoes.partition(":")
    return varias(indice, nomes.split(","), _quantidade(quantidade))


def ler_deslocamento(texto: str) -> tuple[int | None, float]:
    """`N=SEG` ajusta a faixa N; um número sozinho é o estilo antigo (faixa do grupo)."""
    faixa, separador, segundos = texto.partition("=")
    alvo = _numero(faixa) if separador else None
    valor = segundos if separador else faixa
    try:
        return alvo, float(valor)
    except ValueError:
        raise ValueError(f"'{valor}' não é um deslocamento em segundos") from None


def ler_json(texto: str) -> list[Faixa]:
    """Lê as faixas enviadas pela interface web.

    Uma lista de objetos com `numero` (a partir de 1), `tipo` (`uma` ou
    `varias`) e, conforme o tipo, `nome` ou `nomes` e `pessoas` (número,
    `"auto"` ou ausente). `deslocamento` é opcional.
    """
    try:
        dados = json.loads(texto)
    except json.JSONDecodeError as erro:
        raise ValueError(f"As faixas enviadas não são um JSON válido: {erro}") from None
    if not isinstance(dados, list):
        raise ValueError("As faixas devem ser uma lista.")
    lidas = []
    for item in dados:
        if not isinstance(item, dict):
            raise ValueError("Cada faixa deve ser um objeto.")
        indice = _numero(str(item.get("numero", "")))
        tipo = str(item.get("tipo", UMA)).strip().lower()
        try:
            deslocamento = float(item.get("deslocamento") or 0.0)
        except (TypeError, ValueError):
            raise ValueError(f"Deslocamento inválido na faixa {indice + 1}.") from None
        if tipo == UMA:
            lidas.append(Faixa(indice, UMA, nome=str(item.get("nome") or "").strip(),
                               deslocamento=deslocamento))
        elif tipo == VARIAS:
            nomes = item.get("nomes") or []
            if not isinstance(nomes, list):
                raise ValueError(f"Os nomes da faixa {indice + 1} devem ser uma lista.")
            pessoas = item.get("pessoas")
            lidas.append(varias(
                indice, [str(n) for n in nomes],
                _quantidade("" if pessoas is None else pessoas), deslocamento,
            ))
        else:
            raise ValueError(
                f"Tipo de faixa desconhecido: '{tipo}'. Use '{UMA}' ou '{VARIAS}'."
            )
    return lidas


# --- Conferência -------------------------------------------------------------


def validar(faixas: Sequence[Faixa], no_arquivo: int | None = None) -> None:
    """Recusa o que não tem como transcrever. `no_arquivo` é quantas faixas o arquivo tem."""
    if not faixas:
        raise ValueError("Escolha ao menos uma faixa de áudio para transcrever.")
    vistas: set[int] = set()
    for faixa in faixas:
        if faixa.tipo not in TIPOS:
            raise ValueError(f"Tipo de faixa desconhecido: '{faixa.tipo}'.")
        if faixa.numero < 0:
            raise ValueError("A contagem das faixas começa em 1.")
        if faixa.numero in vistas:
            raise ValueError(f"A {faixa.rotulo} foi escolhida mais de uma vez.")
        vistas.add(faixa.numero)
        if faixa.n_locutores is not None and faixa.n_locutores < 1:
            raise ValueError(f"A {faixa.rotulo} precisa de ao menos 1 pessoa.")

    if no_arquivo is None:
        return
    if no_arquivo == 0:
        raise ValueError("O arquivo não tem nenhuma faixa de áudio.")
    for faixa in faixas:
        if faixa.numero >= no_arquivo:
            raise ValueError(
                f"A {faixa.rotulo} não existe: o arquivo tem {no_arquivo} faixa(s) "
                "de áudio. No OBS, habilite as trilhas em Configurações > Saída > "
                "Gravação; `transcritor faixas` mostra as que o arquivo tem."
            )


def repeticoes(faixas: Sequence[Faixa]) -> list[tuple[str, list[int]]]:
    """Nomes que aparecem em mais de uma faixa, com os números das faixas (a partir de 1).

    A comparação ignora maiúsculas e espaços; fica a primeira grafia.
    """
    onde: dict[str, list[int]] = {}
    grafia: dict[str, str] = {}
    for faixa in faixas:
        proprios = [faixa.nome_unico] if faixa.tipo == UMA else list(faixa.nomes)
        for chave, original in {n.strip().casefold(): n.strip() for n in proprios if n.strip()}.items():
            grafia.setdefault(chave, original)
            onde.setdefault(chave, []).append(faixa.numero + 1)
    return [(grafia[chave], numeros) for chave, numeros in onde.items() if len(numeros) > 1]


def nomes_repetidos(faixas: Sequence[Faixa]) -> list[str]:
    """Avisos para nomes que aparecem em mais de uma faixa.

    Não é erro — pode ser de propósito —, mas nas transcrições as falas das
    faixas vão parecer da mesma pessoa.
    """
    avisos = []
    for nome, numeros in repeticoes(faixas):
        lista = ", ".join(str(n) for n in numeros[:-1]) + f" e {numeros[-1]}"
        avisos.append(
            f"O nome \"{nome}\" aparece nas faixas {lista}: nas transcrições, as "
            "falas delas vão parecer da mesma pessoa."
        )
    return avisos
