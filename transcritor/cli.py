"""Interface de linha de comando."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import ffmpeg_tools, modelos, pipeline, saida


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="transcritor",
        description=(
            "Transcreve um MP4 do OBS com duas faixas de áudio (faixa 1: GM, "
            "faixa 2: jogadores) e gera uma transcrição única com os locutores "
            "separados pela voz."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemplos:\n"
            "  transcritor faixas sessao.mp4\n"
            "  transcritor transcrever sessao.mp4 -o saida/sessao-01\n"
            "  transcritor transcrever sessao.mp4 --nomes Ana,Bruno,Caio,Duda,Edu\n"
            "  transcritor transcrever sessao.mp4 --contexto 'Campanha de Ravenloft; Strahd; Barovia'\n"
            "  transcritor web --porta 8000\n"
        ),
    )
    sub = p.add_subparsers(dest="comando", required=True)

    faixas = sub.add_parser("faixas", help="lista as faixas de áudio de um arquivo")
    faixas.add_argument("video", type=Path)

    lista = sub.add_parser("modelos", help="lista e baixa os modelos de fala")
    lista.add_argument(
        "--baixar", metavar="APELIDO", help="baixa um modelo de fala antecipadamente"
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
    t.add_argument("--faixa-gm", type=int, default=1, help="número da faixa do GM (1-based)")
    t.add_argument(
        "--faixa-grupo", type=int, default=2, help="número da faixa dos jogadores (1-based)"
    )
    t.add_argument("--nome-gm", default="GM", help="como chamar o locutor da faixa 1")
    t.add_argument(
        "-n", "--jogadores", default="5",
        help="quantos locutores há na faixa 2; use 'auto' para estimar",
    )
    t.add_argument(
        "--nomes", default="",
        help="nomes dos jogadores separados por vírgula, na ordem em que falam",
    )
    t.add_argument(
        "-f", "--formatos", default=",".join(saida.FORMATOS),
        help=f"formatos de saída ({', '.join(saida.FORMATOS)})",
    )
    t.add_argument(
        "--idioma", default="pt",
        help="idioma do áudio ('auto' detecta); também define o alvo da tradução",
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
        "--traduzir", default=None, metavar="IDIOMA",
        help="também gera a transcrição traduzida (ex.: en, es)",
    )
    t.add_argument(
        "--deslocamento", type=float, default=0.0,
        help="ajuste de sincronia da faixa 2, em segundos",
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


def _cmd_web(args) -> int:
    import uvicorn

    from .web import app

    print(f"Interface disponível em http://{args.host}:{args.porta}")
    uvicorn.run(app, host=args.host, port=args.porta, log_level="warning")
    return 0


def _cmd_transcrever(args) -> int:
    destino = args.saida or Path("saida") / args.video.stem
    jogadores = None if args.jogadores.strip().lower() == "auto" else int(args.jogadores)
    nomes = [n.strip() for n in args.nomes.split(",") if n.strip()]

    config = pipeline.Configuracao(
        video=args.video,
        destino=destino,
        modelo_fala=args.modelo,
        faixa_gm=args.faixa_gm - 1,
        faixa_grupo=args.faixa_grupo - 1,
        nome_gm=args.nome_gm,
        n_jogadores=jogadores,
        nomes_jogadores=nomes,
        formatos=[f.strip() for f in args.formatos.split(",") if f.strip()],
        idioma=args.idioma,
        traduzir_para=args.traduzir,
        contexto=args.contexto,
        dispositivo=args.dispositivo,
        deslocamento_grupo=args.deslocamento,
        inicio=args.inicio,
        fim=args.fim,
        juntar_falas=not args.sem_juntar,
    )

    ultimo = [0.0]

    def relatar(mensagem: str, fracao: float) -> None:
        agora = time.monotonic()
        if fracao < 1.0 and agora - ultimo[0] < 0.5:
            return
        ultimo[0] = agora
        largura = 30
        cheio = int(fracao * largura)
        barra = "#" * cheio + "-" * (largura - cheio)
        print(f"\r[{barra}] {fracao * 100:5.1f}%  {mensagem[:45]:<45}", end="", flush=True)

    resultado = pipeline.executar(config, relatar)
    print()
    print(f"\n{len(resultado.falas)} falas | {resultado.duracao / 60:.1f} min")
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
        "web": _cmd_web,
        "transcrever": _cmd_transcrever,
    }
    try:
        return acoes[args.comando](args)
    except (ValueError, FileNotFoundError, RuntimeError, TypeError) as erro:
        print(f"\nErro: {erro}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrompido.", file=sys.stderr)
        return 130
