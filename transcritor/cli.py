"""Interface de linha de comando."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import ffmpeg_tools, modelos, pipeline, saida, temporarios
from .progresso import EstimativaTempo


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="transcritor",
        description=(
            "Transcreve uma gravação com uma ou mais faixas de áudio (por padrão, "
            "faixa 1: GM e faixa 2: jogadores) e gera uma transcrição única com "
            "os locutores separados pela voz."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemplos:\n"
            "  transcritor faixas sessao.mp4\n"
            "  transcritor transcrever sessao.mp4 -o saida/sessao-01\n"
            "  transcritor transcrever sessao.mp4 --nomes Ana,Bruno,Caio,Duda,Edu\n"
            "  transcritor transcrever sessao.mp4 --faixa '1; nomes=GM' --faixa '2; nomes=Ana' \\\n"
            "      --faixa '3; nomes=Bruno,Caio; vozes=2'\n"
            "  transcritor transcrever sessao.mp4 --contexto 'Campanha de Ravenloft; Strahd; Barovia'\n"
            "  transcritor web --porta 8000\n"
            "  transcritor diagnostico\n"
        ),
    )
    sub = p.add_subparsers(dest="comando", required=True)

    faixas = sub.add_parser("faixas", help="lista as faixas de áudio de um arquivo")
    faixas.add_argument("video", type=Path)

    lista = sub.add_parser("modelos", help="lista e baixa os modelos de fala")
    lista.add_argument(
        "--baixar", metavar="APELIDO", help="baixa um modelo de fala antecipadamente"
    )

    idiomas = sub.add_parser("idiomas", help="lista e instala os idiomas de tradução")
    idiomas.add_argument("--origem", default="pt", help="idioma do áudio (padrão: pt)")
    idiomas.add_argument(
        "--instalar", metavar="ORIGEM-DESTINO",
        help="baixa o modelo offline de um par, ex.: pt-en ou pt-es (~70 MB cada)",
    )

    sub.add_parser(
        "diagnostico",
        help="mostra tudo que decide se uma transcrição vai funcionar",
    )

    web = sub.add_parser("web", help="sobe a interface de envio de vídeo no navegador")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--porta", type=int, default=8000)

    t = sub.add_parser("transcrever", help="transcreve o vídeo")
    t.add_argument("video", type=Path)
    t.add_argument(
        "-o", "--saida", type=Path, default=None,
        help="caminho de saída sem extensão (padrão: ./saida/<nome-do-video>)",
    )
    t.add_argument(
        "-m", "--modelo", default=modelos.PADRAO,
        help=f"modelo de fala: {', '.join(modelos.MODELOS_FALA)} ou um caminho",
    )
    t.add_argument(
        "--faixa", action="append", default=[], metavar="ESPEC",
        help=(
            "uma faixa a transcrever; repita para cada uma. Formato: "
            "'TRILHA[; nomes=A,B][; vozes=N|auto][; ajuste=S][; arquivo=CAMINHO]'. "
            "Com 'vozes' a faixa tem várias pessoas e é separada por voz; sem, é "
            "de uma pessoa só (no máximo um nome). Ex.: --faixa '1; nomes=GM' "
            "--faixa '2; nomes=Ana,Bruno; vozes=5'"
        ),
    )
    legado = t.add_argument_group(
        "atalhos para a mesa de duas faixas (GM + jogadores); não combinam com --faixa"
    )
    legado.add_argument("--faixa-gm", type=int, default=None, help="número da faixa do GM (padrão 1)")
    legado.add_argument(
        "--faixa-grupo", type=int, default=None, help="número da faixa dos jogadores (padrão 2)"
    )
    legado.add_argument("--nome-gm", default=None, help="como chamar o locutor da faixa do GM")
    legado.add_argument(
        "-n", "--jogadores", default=None,
        help="quantos locutores há na faixa dos jogadores (padrão 5); 'auto' estima",
    )
    legado.add_argument(
        "--nomes", default=None,
        help="nomes dos jogadores separados por vírgula, na ordem em que falam",
    )
    legado.add_argument(
        "--deslocamento", type=float, default=None,
        help="ajuste de sincronia da faixa dos jogadores, em segundos",
    )
    t.add_argument(
        "-f", "--formatos", default=",".join(saida.FORMATOS),
        help=f"formatos de saída ({', '.join(saida.FORMATOS)})",
    )
    t.add_argument(
        "--idioma", default="pt",
        help="idioma do áudio ('auto' detecta); é a origem da tradução",
    )
    t.add_argument(
        "--contexto", default="",
        help=(
            "nomes de personagens, lugares e jargão da mesa, em texto corrido. "
            "Reduz muito o erro em nomes próprios (só vale para os modelos Whisper)"
        ),
    )
    t.add_argument(
        "--dispositivo", default="auto", choices=("auto", "cuda", "cpu"),
        help="onde rodar o Whisper; 'auto' usa a GPU quando houver",
    )
    t.add_argument(
        "--traduzir", default=None, metavar="IDIOMAS",
        help=(
            "também gera a transcrição traduzida, separada por vírgula: 'en,es' usa "
            "os modelos offline instalados; 'google:ja' usa o Google Tradutor "
            "(online). Veja 'transcritor idiomas'"
        ),
    )
    t.add_argument(
        "--inicio", type=float, default=0.0,
        help="transcreve só a partir deste segundo (útil para testar ajustes)",
    )
    t.add_argument(
        "--fim", type=float, default=None, help="transcreve só até este segundo"
    )
    t.add_argument(
        "--sem-juntar", action="store_true",
        help="mantém cada trecho reconhecido como uma linha separada",
    )
    return p


def _cmd_faixas(args) -> int:
    faixas = ffmpeg_tools.listar_faixas_audio(args.video)
    duracao = ffmpeg_tools.duracao_segundos(args.video)
    if duracao:
        print(f"Duração: {duracao / 60:.1f} min")
    if not faixas:
        print("Nenhuma faixa de áudio encontrada.")
        return 1
    print(f"{len(faixas)} faixa(s) de áudio:")
    for faixa in faixas:
        print(f"  {faixa.rotulo}")
    return 0


def _cmd_modelos(args) -> int:
    if args.baixar:
        pronto = modelos.resolver_modelo_fala(args.baixar)
        modelos.resolver_modelo_locutor()
        print(f"Modelo '{pronto.apelido}' ({pronto.motor}) pronto em {pronto.caminho}")
        return 0
    cache = modelos.diretorio_padrao()
    print(f"Cache: {cache}\n")
    print("Modelos de fala:")
    for modelo in modelos.MODELOS_FALA.values():
        marca = "*" if modelos.baixado(modelo, cache) else " "
        padrao = " (padrão)" if modelo.apelido == modelos.PADRAO else ""
        print(
            f" {marca} {modelo.apelido:<15} ~{modelo.tamanho_mb:>5} MB  "
            f"{modelo.descricao}{padrao}"
        )
    spk = modelos.MODELO_LOCUTOR
    marca = "*" if modelos.baixado(spk, cache) else " "
    print(
        f"\nModelo de locutor (sempre usado, separa as vozes dos jogadores):\n"
        f" {marca} {spk.apelido:<15} ~{spk.tamanho_mb:>5} MB  {spk.descricao}"
    )
    print("\n(* = já baixado)")
    return 0


def _cmd_idiomas(args) -> int:
    from . import traducao

    if args.instalar:
        origem, _, destino = args.instalar.partition("-")
        if not destino:
            raise ValueError("Use --instalar ORIGEM-DESTINO, por exemplo pt-en.")
        traducao.instalar(origem, destino)
        print(f"Pronto: {traducao.nome(origem)} → {traducao.nome(destino)} offline.")
        return 0
    idiomas = traducao.disponiveis(args.origem)
    offline = [i for i in idiomas if i.servico == traducao.OFFLINE]
    online = [i for i in idiomas if i.servico == traducao.GOOGLE]
    print(f"Tradução a partir de {traducao.nome(args.origem)}:\n")
    print("Offline (modelos instalados — o texto não sai da máquina):")
    for idioma in offline:
        print(f"  {idioma.valor:<12} {idioma.nome}")
    if not offline:
        print("  nenhum. Instale com: transcritor idiomas --instalar pt-en")
    print(f"\nOnline (Google Tradutor): {len(online)} idiomas" if online else
          "\nOnline (Google Tradutor): sem conexão agora")
    if online:
        print("  use com o prefixo google:, ex.: --traduzir google:ja")
    print(f"\nModelos em {traducao.pasta()}")
    return 0


def _cmd_diagnostico(args) -> int:
    """Reúne num lugar só o que costuma explicar uma transcrição que falhou."""
    import platform

    from . import motor_whisper

    print(f"Python      {platform.python_version()} em {platform.platform()}")

    try:
        print(f"ffmpeg      {ffmpeg_tools.localizar_ffmpeg()}")
    except ffmpeg_tools.FFmpegIndisponivel as erro:
        print(f"ffmpeg      AUSENTE — {erro}")

    cuda = motor_whisper.diagnosticar_cuda()
    dispositivo, precisao = motor_whisper.escolher_dispositivo()
    print(f"Dispositivo {dispositivo} ({precisao})")
    print(f"GPU         {cuda.explicacao}")
    if cuda.pasta_bibliotecas:
        print(f"            bibliotecas em {cuda.pasta_bibliotecas}")

    cache = modelos.diretorio_padrao()
    baixados = [m.apelido for m in modelos.MODELOS_FALA.values() if modelos.baixado(m, cache)]
    print(f"Modelos     {', '.join(baixados) if baixados else 'nenhum baixado ainda'}")
    print(f"            cache em {cache}")
    if not modelos.baixado(modelos.MODELO_LOCUTOR, cache):
        print("            falta o modelo de locutor; ele é baixado na primeira transcrição")
    return 0


def _cmd_web(args) -> int:
    import uvicorn

    from .web import app

    print(f"Interface disponível em http://{args.host}:{args.porta}")
    uvicorn.run(app, host=args.host, port=args.porta, log_level="warning")
    return 0


def _numero(texto: str, campo: str) -> int:
    try:
        return int(texto)
    except ValueError:
        raise ValueError(f"'{campo}' precisa ser um número inteiro, não '{texto}'.") from None


def _trilha(numero: int | str | None, padrao: int, campo: str) -> int:
    """Número de trilha digitado (a partir de 1) -> índice (a partir de 0).

    Compara com None, e não usa `or`: um 0 digitado é um erro a apontar, não um
    "não informado" a trocar em silêncio pela trilha padrão.
    """
    if numero is None:
        return padrao - 1
    valor = numero if isinstance(numero, int) else _numero(numero, campo)
    if valor < 1:
        raise ValueError(f"'{campo}' começa em 1 (a primeira trilha), não em {valor}.")
    return valor - 1


def faixa_da_especificacao(texto: str) -> pipeline.Faixa:
    """Lê uma faixa no formato de `--faixa`: 'TRILHA; chave=valor; ...'."""
    partes = [p.strip() for p in texto.split(";") if p.strip()]
    if not partes:
        raise ValueError("--faixa vazia: informe ao menos o número da trilha.")
    faixa = pipeline.Faixa(trilha=_trilha(partes[0], 1, "trilha"))
    for parte in partes[1:]:
        chave, igual, valor = (x.strip() for x in parte.partition("="))
        if not igual:
            raise ValueError(f"Em --faixa, '{parte}' deveria ser chave=valor.")
        if chave == "nomes":
            faixa.nomes = [n.strip() for n in valor.split(",") if n.strip()]
        elif chave == "vozes":
            faixa.varias_pessoas = True
            faixa.vozes = None if valor.lower() == "auto" else _numero(valor, "vozes")
        elif chave == "ajuste":
            faixa.ajuste = float(valor)
        elif chave == "arquivo":
            faixa.arquivo = Path(valor)
        else:
            raise ValueError(
                f"Em --faixa, chave desconhecida '{chave}'. Use nomes, vozes, ajuste ou arquivo."
            )
    return faixa


def faixas_dos_argumentos(args) -> list[pipeline.Faixa]:
    """As faixas pedidas: por --faixa, ou pelos atalhos da mesa de duas faixas."""
    atalhos = [
        args.faixa_gm, args.faixa_grupo, args.nome_gm,
        args.jogadores, args.nomes, args.deslocamento,
    ]
    if args.faixa:
        if any(a is not None for a in atalhos):
            raise ValueError(
                "Use --faixa ou os atalhos (--faixa-gm, --nomes...), não os dois juntos."
            )
        return [faixa_da_especificacao(texto) for texto in args.faixa]

    jogadores = (args.jogadores or "5").strip()
    return [
        pipeline.Faixa(
            trilha=_trilha(args.faixa_gm, 1, "--faixa-gm"),
            nomes=[args.nome_gm if args.nome_gm is not None else "GM"],
        ),
        pipeline.Faixa(
            trilha=_trilha(args.faixa_grupo, 2, "--faixa-grupo"),
            nomes=[n.strip() for n in (args.nomes or "").split(",") if n.strip()],
            varias_pessoas=True,
            vozes=None if jogadores.lower() == "auto" else _numero(jogadores, "jogadores"),
            ajuste=args.deslocamento or 0.0,
        ),
    ]


def _duracao_legivel(segundos: float) -> str:
    minutos, segundos = divmod(int(segundos + 0.5), 60)
    horas, minutos = divmod(minutos, 60)
    if horas:
        return f"{horas}h{minutos:02d}"
    return f"{minutos}min{segundos:02d}s" if minutos else f"{segundos}s"


def _cmd_transcrever(args) -> int:
    temporarios.limpar_orfas()  # sobras de execuções interrompidas à força
    destino = args.saida or Path("saida") / args.video.stem

    config = pipeline.Configuracao(
        video=args.video,
        destino=destino,
        faixas=faixas_dos_argumentos(args),
        modelo_fala=args.modelo,
        formatos=[f.strip() for f in args.formatos.split(",") if f.strip()],
        idioma=args.idioma,
        traducoes=[v.strip() for v in (args.traduzir or "").split(",") if v.strip()],
        contexto=args.contexto,
        dispositivo=args.dispositivo,
        inicio=args.inicio,
        fim=args.fim,
        juntar_falas=not args.sem_juntar,
    )

    ultimo = [0.0]
    estimativa = EstimativaTempo()

    def relatar(mensagem: str, fracao: float) -> None:
        estimativa.registrar(fracao)
        agora = time.monotonic()
        if fracao < 1.0 and agora - ultimo[0] < 0.5:
            return
        ultimo[0] = agora
        largura = 30
        cheio = int(fracao * largura)
        barra = "#" * cheio + "-" * (largura - cheio)
        restante = estimativa.restante()
        falta = f"falta ~{_duracao_legivel(restante)}" if restante else ""
        print(
            f"\r[{barra}] {fracao * 100:5.1f}% {falta:<16} {mensagem[:40]:<40}",
            end="", flush=True,
        )

    resultado = pipeline.executar(config, relatar)
    print()
    print(f"\n{len(resultado.falas)} falas | {resultado.duracao / 60:.1f} min "
          f"| em {_duracao_legivel(estimativa.decorrido)}")
    print(f"Locutores: {', '.join(resultado.locutores)}")
    for aviso in resultado.avisos:
        print(f"Aviso: {aviso}")
    print("Arquivos gerados:")
    for arquivo in resultado.arquivos:
        print(f"  {arquivo}")
    return 0


def _saida_em_utf8() -> None:
    """Garante UTF-8 na saída do terminal, inclusive quando ela é redirecionada.

    No Windows o console já escreve Unicode, mas um `> arquivo.txt` cai para a
    cp1252 e quebra nos travessões das descrições dos modelos. No Linux isto não
    muda nada.
    """
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):  # fluxo capturado ou já fechado
            continue


def main(argv: list[str] | None = None) -> int:
    _saida_em_utf8()
    args = _parser().parse_args(argv)
    acoes = {
        "faixas": _cmd_faixas,
        "modelos": _cmd_modelos,
        "diagnostico": _cmd_diagnostico,
        "idiomas": _cmd_idiomas,
        "web": _cmd_web,
        "transcrever": _cmd_transcrever,
    }
    try:
        return acoes[args.comando](args)
    except (ValueError, FileNotFoundError, RuntimeError, TypeError) as erro:
        print(f"\nErro: {erro}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, pipeline.Cancelado):
        print("\nInterrompido.", file=sys.stderr)
        return 130
