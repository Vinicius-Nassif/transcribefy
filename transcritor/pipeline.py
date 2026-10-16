"""Orquestração: arquivo(s) com N faixas -> transcrição -> locutores -> mesclagem.

Cada faixa é de um de dois tipos, e é só isso que muda o tratamento dela:

- **uma pessoa** (o microfone do GM, ou de um jogador): todas as falas ganham o
  nome da faixa, sem análise de voz;
- **várias pessoas** (um microfone de mesa, o retorno do Discord): as falas são
  separadas por voz em `diarizacao`, com os nomes informados na ordem em que
  cada pessoa fala pela primeira vez.

As faixas costumam ser trilhas de um mesmo arquivo do OBS, mas cada uma pode
apontar para um arquivo próprio — é o caso das gravações com um arquivo por
pessoa (Craig, por exemplo).
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import (
    diarizacao,
    ffmpeg_tools,
    mesclagem,
    modelos,
    saida,
    temporarios,
    traducao,
    transcricao,
    vozes,
)
from .fala import Fala
from .progresso import (
    CUSTO_EXTRACAO,
    CUSTO_TRADUCAO,
    CUSTO_VOSK,
    CUSTO_VOZES,
    CUSTO_WHISPER,
    Acompanhamento,
    Cancelado,
    Relato,
)

__all__ = [
    "MAX_FAIXAS",
    "Cancelado",
    "Configuracao",
    "Faixa",
    "Relato",
    "Resultado",
    "executar",
]

# Teto de faixas por execução. O OBS grava no máximo 6 trilhas num arquivo; a
# folga até 8 cobre as gravações com um arquivo por pessoa, que numa mesa de RPG
# raramente passam disso. O tempo cresce linearmente com o número de faixas
# (cada uma é uma passada do Whisper na GPU), a memória não — as faixas são
# transcritas uma de cada vez. Oito faixas de três horas já são ~2,5 h de GPU.
MAX_FAIXAS = 8

# Custo fixo de abrir o modelo, em "segundos de áudio" equivalentes, para a
# barra não ficar parada no zero enquanto ele carrega.
_CUSTO_MODELO = 60.0


@dataclass(slots=True)
class Faixa:
    """Uma faixa de áudio a transcrever e como tratá-la.

    Attributes:
        trilha: índice da trilha de áudio dentro do arquivo (0 é a primeira).
        nomes: o nome da pessoa (uma pessoa) ou os nomes na ordem em que falam
            pela primeira vez (várias pessoas). Pode ficar vazio.
        varias_pessoas: separa as falas por voz.
        vozes: quantas pessoas falam na faixa; None estima sozinho. Só vale com
            `varias_pessoas`.
        ajuste: segundos somados aos tempos da faixa, para acertar a sincronia.
        arquivo: arquivo de onde vem a faixa; None é o arquivo principal.
    """

    trilha: int = 0
    nomes: Sequence[str] = ()
    varias_pessoas: bool = False
    vozes: int | None = None
    ajuste: float = 0.0
    arquivo: Path | None = None


def faixas_padrao() -> list[Faixa]:
    """A mesa clássica: trilha 1 só o GM, trilha 2 os jogadores."""
    return [Faixa(0, ("GM",)), Faixa(1, (), varias_pessoas=True, vozes=5)]


@dataclass(slots=True)
class Configuracao:
    """Parâmetros de uma execução."""

    video: Path
    destino: Path
    faixas: Sequence[Faixa] = field(default_factory=faixas_padrao)
    modelo_fala: str = modelos.PADRAO
    prefixo_jogador: str = "Jogador"
    formatos: Sequence[str] = saida.FORMATOS
    idioma: str = "pt"
    traducoes: Sequence[str] = ()  # "en" (offline) ou "google:en"; vazio, sem tradução
    contexto: str = ""
    dispositivo: str = "auto"
    inicio: float = 0.0
    fim: float | None = None
    juntar_falas: bool = True
    diretorio_modelos: Path | None = None


@dataclass(slots=True)
class Resultado:
    falas: list[Fala]
    arquivos: list[Path] = field(default_factory=list)
    locutores: list[str] = field(default_factory=list)
    duracao: float = 0.0
    avisos: list[str] = field(default_factory=list)


def inspecionar(video: Path) -> list[ffmpeg_tools.FaixaAudio]:
    """Lista as faixas de áudio do vídeo, para a interface mostrar ao usuário."""
    return ffmpeg_tools.listar_faixas_audio(Path(video))


def rotulo(faixa: Faixa, posicao: int) -> str:
    """Como a faixa aparece nas mensagens: o nome, se for de uma pessoa só."""
    if not faixa.varias_pessoas and faixa.nomes:
        return faixa.nomes[0]
    return f"faixa {posicao + 1}"


def sem_repetidos(nomes: Sequence[str]) -> tuple[list[str], list[str]]:
    """Tira espaços, vazios e repetições (sem diferenciar maiúsculas).

    Devolve (nomes únicos na ordem original, nomes que estavam repetidos). Um
    nome repetido viraria dois locutores com o mesmo rótulo — e a diarização
    passaria a fundir na saída duas vozes que ela separou.
    """
    unicos: list[str] = []
    repetidos: list[str] = []
    vistos: set[str] = set()
    for nome in nomes:
        limpo = " ".join(nome.split())
        if not limpo:
            continue
        chave = limpo.casefold()
        if chave in vistos:
            if limpo not in repetidos:
                repetidos.append(limpo)
            continue
        vistos.add(chave)
        unicos.append(limpo)
    return unicos, repetidos


def contexto_da_mesa(config: Configuracao) -> str:
    """Monta a dica de contexto que o Whisper recebe antes de transcrever.

    Os nomes da mesa entram junto com o texto livre porque é neles que um
    reconhecedor mais erra: sem a dica, um nome próprio vira a palavra comum
    mais parecida. A dica é um prompt, não uma regra — ela enviesa, não obriga.
    """
    partes = []
    nomes, _ = sem_repetidos([n for faixa in config.faixas for n in faixa.nomes])
    if nomes:
        partes.append(f"Participantes: {', '.join(nomes)}.")
    if config.contexto.strip():
        partes.append(config.contexto.strip())
    return " ".join(partes)


def validar(config: Configuracao, inspecoes: dict[Path, ffmpeg_tools.Inspecao]) -> list[str]:
    """Confere a configuração contra o que os arquivos têm. Devolve avisos.

    Levanta ValueError com uma mensagem para o usuário quando não dá para seguir.
    """
    faixas = list(config.faixas)
    if not faixas:
        raise ValueError("Escolha pelo menos uma faixa para transcrever.")
    if len(faixas) > MAX_FAIXAS:
        raise ValueError(f"São no máximo {MAX_FAIXAS} faixas por transcrição.")

    avisos: list[str] = []
    usadas: dict[tuple[Path, int], int] = {}
    for posicao, faixa in enumerate(faixas):
        arquivo = Path(faixa.arquivo or config.video)
        disponiveis = inspecoes[arquivo].faixas
        if not 0 <= faixa.trilha < len(disponiveis):
            raise ValueError(
                f"A faixa {posicao + 1} pede a trilha {faixa.trilha + 1}, mas "
                f"{arquivo.name} tem {len(disponiveis)} trilha(s) de áudio."
            )
        chave = (arquivo, faixa.trilha)
        if chave in usadas:
            raise ValueError(
                f"As faixas {usadas[chave] + 1} e {posicao + 1} usam a mesma trilha "
                f"({faixa.trilha + 1}) de {arquivo.name}."
            )
        usadas[chave] = posicao

        nomes, repetidos = sem_repetidos(faixa.nomes)
        if repetidos:
            avisos.append(
                f"Faixa {posicao + 1}: nome(s) repetido(s) considerado(s) uma vez só: "
                f"{', '.join(repetidos)}."
            )
        if faixa.varias_pessoas:
            if faixa.vozes is not None and faixa.vozes < 1:
                raise ValueError(f"A faixa {posicao + 1} precisa de pelo menos 1 pessoa.")
            if faixa.vozes is not None and len(nomes) > faixa.vozes:
                avisos.append(
                    f"Faixa {posicao + 1}: {len(nomes)} nomes para {faixa.vozes} "
                    f"vozes; os últimos nomes não serão usados."
                )
        elif len(nomes) > 1:
            raise ValueError(
                f"A faixa {posicao + 1} é de uma pessoa só, mas tem {len(nomes)} nomes. "
                "Marque “mais de uma pessoa” ou deixe um nome."
            )

    if config.inicio < 0:
        raise ValueError("O início do trecho não pode ser negativo.")
    if config.fim is not None:
        if config.fim <= config.inicio:
            raise ValueError("O fim do trecho precisa ser depois do início.")
        duracoes = [i.duracao for i in inspecoes.values() if i.duracao]
        if duracoes and config.fim > max(duracoes) + 0.5:
            raise ValueError(
                f"O fim do trecho ({config.fim:.0f} s) passa da duração do áudio "
                f"({max(duracoes):.0f} s)."
            )
    return avisos


def _custos(
    config: Configuracao, modelo: modelos.ModeloPronto, dispositivo: str, duracao: float
) -> dict[str, float]:
    """Peso de cada etapa, proporcional ao tempo que ela deve levar."""
    por_segundo = (
        CUSTO_VOSK if modelo.motor == modelos.MOTOR_VOSK else CUSTO_WHISPER[dispositivo]
    )
    pesos = {"modelo": _CUSTO_MODELO * por_segundo}
    for posicao, faixa in enumerate(config.faixas):
        pesos[f"extracao-{posicao}"] = duracao * CUSTO_EXTRACAO
        pesos[f"texto-{posicao}"] = duracao * por_segundo
        if faixa.varias_pessoas:
            pesos[f"vozes-{posicao}"] = duracao * CUSTO_VOZES
    pesos["final"] = duracao * CUSTO_EXTRACAO
    pesos["traducao"] = duracao * CUSTO_TRADUCAO * len(config.traducoes)
    return pesos


def _tradutores(
    valores: Sequence[str], origem: str, diretorio: Path | None, avisos: list[str]
) -> list[tuple[str, saida.Traduzir]]:
    """Monta os tradutores pedidos. Com origem conhecida, um idioma sem modelo
    é erro já no começo; com origem detectada, vira aviso e fica de fora."""
    tradutores = []
    for valor in dict.fromkeys(valores):
        try:
            tradutores.append((traducao.codigo(valor), traducao.tradutor(valor, origem, diretorio)))
        except ValueError as erro:
            avisos.append(f"Tradução para {traducao.nome(traducao.codigo(valor))} pulada: {erro}")
    return tradutores


def executar(
    config: Configuracao,
    relatar: Relato | None = None,
    cancelado: threading.Event | None = None,
) -> Resultado:
    """Roda a transcrição completa e escreve os arquivos de saída.

    Args:
        config: o que transcrever e como.
        relatar: recebe (mensagem, fração de 0 a 1) a cada avanço.
        cancelado: quando sinalizado, a execução para no próximo avanço e
            levanta `Cancelado`. Os arquivos temporários são removidos.
    """
    video = Path(config.video)
    arquivos = {video, *(Path(f.arquivo) for f in config.faixas if f.arquivo)}
    for arquivo in arquivos:
        if not arquivo.is_file():
            raise FileNotFoundError(f"{arquivo} não encontrado")

    acompanhar = Acompanhamento(relatar, cancelado)
    acompanhar.avisar("Verificando as faixas de áudio")
    inspecoes = {arquivo: ffmpeg_tools.inspecionar(arquivo) for arquivo in arquivos}
    avisos = validar(config, inspecoes)

    duracao_total = max((i.duracao or 0.0) for i in inspecoes.values()) or 1.0
    duracao = (config.fim or duracao_total) - config.inicio

    acompanhar.avisar("Preparando os modelos")
    modelo = modelos.resolver_modelo_fala(config.modelo_fala, config.diretorio_modelos)
    dispositivo = (
        transcricao.dispositivo_efetivo(config.dispositivo)
        if modelo.motor == modelos.MOTOR_WHISPER else "cpu"
    )
    acompanhar.planejar(_custos(config, modelo, dispositivo, duracao))

    tem_grupo = any(f.varias_pessoas for f in config.faixas)
    caminho_locutor = modelos.resolver_modelo_locutor(config.diretorio_modelos) if tem_grupo else None
    # A extração de vetores de voz roda no Vosk mesmo quando o texto vem do
    # Whisper; o modelo compacto basta para alimentar o reconhecedor.
    modelo_vosk = (
        modelo if modelo.motor == modelos.MOTOR_VOSK or not tem_grupo
        else modelos.resolver_modelo_fala("vosk-pt", config.diretorio_modelos)
    )
    contexto = contexto_da_mesa(config)

    detectado = config.idioma if config.idioma not in ("", "auto") else None
    if detectado:  # origem conhecida: um idioma sem modelo falha antes de transcrever
        for valor in config.traducoes:
            traducao.tradutor(valor, detectado, config.diretorio_modelos)

    def ao_detectar(codigo: str) -> None:
        nonlocal detectado
        detectado = detectado or codigo

    aviso_gpu = transcricao.aviso_de_dispositivo(modelo, config.dispositivo)
    if aviso_gpu:
        avisos.append(aviso_gpu)
        acompanhar.avisar(aviso_gpu)
    acompanhar.avisar("Carregando o modelo de reconhecimento (até 1 min na primeira vez)")
    transcricao.carregar(modelo, config.dispositivo)
    acompanhar.concluir("modelo")

    falas_por_faixa: dict[int, list[Fala]] = {}
    with temporarios.pasta_temporaria() as temporario:
        # Uma leitura por arquivo, extraindo todas as trilhas pedidas dele de uma vez.
        wavs: dict[int, Path] = {}
        por_arquivo: dict[Path, list[int]] = {}
        for posicao, faixa in enumerate(config.faixas):
            por_arquivo.setdefault(Path(faixa.arquivo or video), []).append(posicao)
        for arquivo, posicoes in por_arquivo.items():
            acompanhar.avisar(f"Extraindo o áudio de {arquivo.name}")
            pedidos = [
                (config.faixas[p].trilha, temporario / f"faixa-{p + 1}.wav") for p in posicoes
            ]
            ffmpeg_tools.extrair_faixas(
                arquivo, pedidos, inicio=config.inicio, fim=config.fim,
                cancelado=acompanhar.verificar,
            )
            for posicao, (_, destino) in zip(posicoes, pedidos, strict=True):
                wavs[posicao] = destino
                acompanhar.concluir(f"extracao-{posicao}")

        for posicao, faixa in enumerate(config.faixas):
            nome = rotulo(faixa, posicao)
            wav = wavs[posicao]
            mensagem = f"Transcrevendo {'a ' if nome.startswith('faixa') else ''}{nome}"
            falas = transcricao.transcrever(
                wav, modelo,
                faixa=posicao,
                idioma=config.idioma,
                contexto=contexto,
                dispositivo=config.dispositivo,
                modelo_locutor=caminho_locutor if faixa.varias_pessoas else None,
                progresso=acompanhar.etapa(f"texto-{posicao}", mensagem),
                ao_detectar_idioma=ao_detectar,
            )
            acompanhar.concluir(f"texto-{posicao}")
            falas_por_faixa[posicao] = falas

            # O motor Vosk já devolve os vetores na mesma passada; com o
            # Whisper eles vêm depois, trecho a trecho.
            if faixa.varias_pessoas and not any(f.vetor_voz for f in falas):
                vozes.anexar_vetores(
                    wav, falas, modelo_vosk.caminho, caminho_locutor,
                    progresso=acompanhar.etapa(
                        f"vozes-{posicao}", f"Analisando as vozes da faixa {posicao + 1}"
                    ),
                )

    grupos: list[list[Fala]] = []
    proximo_jogador = 1
    for posicao, faixa in enumerate(config.faixas):
        falas = falas_por_faixa[posicao]
        nomes, _ = sem_repetidos(faixa.nomes)
        if not faixa.varias_pessoas:
            for fala in falas:
                fala.locutor = nomes[0] if nomes else f"Faixa {posicao + 1}"
        else:
            acompanhar.concluir(f"vozes-{posicao}", "Separando os locutores pela voz")
            diarizacao.atribuir_locutores(
                falas,
                n_locutores=faixa.vozes,
                nomes=nomes or None,
                prefixo=config.prefixo_jogador,
                primeiro=proximo_jogador,
            )
            proximo_jogador += len({f.locutor for f in falas} - set(nomes))
        grupos.append(falas)

    acompanhar.avisar("Mesclando as faixas")
    # Com recorte, os tempos saem relativos ao trecho; somamos o início de volta
    # para que os carimbos batam com o vídeo original.
    deslocamentos = {
        posicao: config.inicio + faixa.ajuste for posicao, faixa in enumerate(config.faixas)
    }
    falas = mesclagem.mesclar(*grupos, deslocamentos=deslocamentos, juntar=config.juntar_falas)

    origem = detectado or "pt"
    tradutores = _tradutores(config.traducoes, origem, config.diretorio_modelos, avisos)
    acompanhar.avisar("Gravando os arquivos")
    gerados = saida.escrever(falas, config.destino, formatos=config.formatos, idioma=origem)
    acompanhar.concluir("final")

    # Daqui em diante a transcrição já está no disco, e a tradução é um extra:
    # um tradutor que falha vira aviso, e um cancelamento para as traduções
    # restantes sem apagar o que já foi gravado.
    for feitas, (codigo, traduzir) in enumerate(tradutores):
        idioma_nome = traducao.nome(codigo)
        try:
            acompanhar.avisar(f"Traduzindo para {idioma_nome}")
        except Cancelado:
            faltaram = ", ".join(traducao.nome(c) for c, _ in tradutores[feitas:])
            avisos.append(
                f"Cancelado durante a tradução: a transcrição foi mantida, sem as "
                f"traduções para {faltaram}."
            )
            break
        try:
            traduzida = saida.escrever_traducao(
                falas, config.destino, codigo, traduzir, config.formatos
            )
        except Exception as erro:  # noqa: BLE001 - a tradução nunca derruba a transcrição
            avisos.append(f"A tradução para {idioma_nome} falhou e foi pulada: {erro}")
        else:
            gerados.arquivos.extend(traduzida.arquivos)
            gerados.avisos.extend(traduzida.avisos)
        acompanhar.concluir_parcial("traducao", (feitas + 1) / len(tradutores))
    acompanhar.concluir_parcial("traducao", 1.0)
    acompanhar.finalizar("Concluído")
    return Resultado(
        falas=falas,
        arquivos=gerados.arquivos,
        avisos=avisos + gerados.avisos,
        locutores=sorted({f.locutor for f in falas}),
        duracao=max((f.fim for f in falas), default=0.0),
    )
