"""Interface de linha de comando."""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from . import copias, faixas, ffmpeg_tools, modelos, pipeline, progresso, saida


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="transcritor",
        description=(
            "Transcreve uma gravação do OBS com várias faixas de áudio e gera uma "
            "transcrição única, com cada fala atribuída a quem a disse. Cada faixa "
            "é de uma pessoa (nome fixo) ou de várias (separadas pela voz). Sem "
            "--faixa, vale o arranjo de sempre: faixa 1 do GM, faixa 2 dos jogadores."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemplos:\n"
            "  transcritor faixas sessao.mp4\n"
            "  transcritor transcrever sessao.mp4 -o saida/sessao-01\n"
            "  transcritor transcrever sessao.mp4 --nomes Ana,Bruno,Caio,Duda,Edu\n"
            "  transcritor transcrever sessao.mp4 --faixa 1=Mestre \\\n"
            "      --faixa 2=varias:3:Ana,Bruno,Caio --faixa 3=Duda\n"
            "  transcritor transcrever sessao.mp4 --contexto 'Campanha de Ravenloft; Strahd; Barovia'\n"
            "  transcritor web --porta 8000\n"
            "  transcritor diagnostico\n"
            "  transcritor limpar --dias 7\n"
        ),
    )
    sub = p.add_subparsers(dest="comando", required=True)

    listar_faixas = sub.add_parser("faixas", help="lista as faixas de áudio de um arquivo")
    listar_faixas.add_argument("video", type=Path)

    lista = sub.add_parser("modelos", help="lista e baixa os modelos de fala")
    lista.add_argument(
        "--baixar", metavar="APELIDO", help="baixa um modelo de fala antecipadamente"
    )

    sub.add_parser(
        "diagnostico",
        help="mostra tudo que decide se uma transcrição vai funcionar",
    )

    limpar = sub.add_parser(
        "limpar",
        help="apaga as cópias de vídeo de envios pela web que falharam há tempo",
        description=(
            "Apaga as cópias de vídeo que a interface web guarda quando uma "
            "transcrição falha. Só toca nas cópias recebidas pela página, nunca "
            "nos arquivos originais nem nos passados pela linha de comando, e "
            "mantém as transcrições geradas."
        ),
    )
    limpar.add_argument(
        "--dias", type=float, default=copias.DIAS_PADRAO,
        help=f"idade mínima da cópia, em dias (padrão: {copias.DIAS_PADRAO})",
    )
    limpar.add_argument(
        "--pasta", type=Path, default=copias.RAIZ_PADRAO,
        help=f"pasta dos trabalhos da interface web (padrão: {copias.RAIZ_PADRAO})",
    )
    limpar.add_argument(
        "-s", "--sim", action="store_true", help="remove sem pedir confirmação"
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
        "--faixa", action="append", type=_tipo(faixas.ler_especificacao),
        dest="faixas", metavar="ESPEC",
        help=(
            "uma faixa a transcrever; repita para cada uma. 'N=NOME' para uma "
            "pessoa (ex.: 1=GM); 'N=varias[:QTD][:NOMES]' para várias, separadas "
            "pela voz (ex.: 2=varias:5:Ana,Bruno). Sem QTD, usa o número de nomes, "
            "ou estima se não houver nomes"
        ),
    )
    antigas = t.add_argument_group(
        "arranjo de duas faixas (estilo antigo, não combina com --faixa)"
    )
    antigas.add_argument(
        "--faixa-gm", type=int, default=None, help="número da faixa do GM (padrão: 1)"
    )
    antigas.add_argument(
        "--faixa-grupo", type=int, default=None,
        help="número da faixa dos jogadores (padrão: 2)",
    )
    antigas.add_argument(
        "--nome-gm", default=None, help="como chamar o locutor da faixa do GM (padrão: GM)"
    )
    antigas.add_argument(
        "-n", "--jogadores", default=None,
        help="quantos locutores há na faixa dos jogadores; 'auto' estima (padrão: 5)",
    )
    antigas.add_argument(
        "--nomes", default=None,
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
        "--deslocamento", action="append", type=_tipo(faixas.ler_deslocamento),
        dest="deslocamentos", metavar="[N=]SEG", default=[],
        help=(
            "ajuste de sincronia em segundos; repita para cada faixa. 'N=SEG' "
            "ajusta a faixa N (ex.: 2=0.4); um número sozinho ajusta a faixa dos "
            "jogadores e é do estilo antigo"
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


def _tipo(leitor):
    """Adapta um leitor de `faixas` para o argparse, que espera ArgumentTypeError."""
    def ler(texto: str):
        try:
            return leitor(texto)
        except ValueError as erro:
            raise argparse.ArgumentTypeError(str(erro)) from None
    ler.__name__ = leitor.__name__
    return ler


_OPCOES_ANTIGAS = {
    "faixa_gm": "--faixa-gm", "faixa_grupo": "--faixa-grupo",
    "nome_gm": "--nome-gm", "jogadores": "--jogadores", "nomes": "--nomes",
}


def _faixas_dos_argumentos(args) -> list[faixas.Faixa]:
    """Monta as faixas pelo estilo novo (--faixa) ou pelo antigo, nunca pelos dois."""
    antigas = [opcao for campo, opcao in _OPCOES_ANTIGAS.items()
               if getattr(args, campo) is not None]
    sem_faixa = [s for alvo, s in args.deslocamentos if alvo is None]
    if sem_faixa:
        antigas.append("--deslocamento SEG")

    if args.faixas:
        if antigas:
            raise ValueError(
                f"{', '.join(antigas)} não combina(m) com --faixa. Use só --faixa "
                "(e --deslocamento N=SEG para a sincronia), ou só as opções antigas."
            )
        escolhidas = list(args.faixas)
    else:
        if len(sem_faixa) > 1:
            raise ValueError("Informe --deslocamento SEG uma vez só, ou use N=SEG.")
        jogadores = (args.jogadores or "5").strip().lower()
        escolhidas = faixas.padrao(
            faixa_gm=(1 if args.faixa_gm is None else args.faixa_gm) - 1,
            faixa_grupo=(2 if args.faixa_grupo is None else args.faixa_grupo) - 1,
            nome_gm=args.nome_gm if args.nome_gm is not None else "GM",
            n_jogadores=None if jogadores == "auto" else int(jogadores),
            nomes_jogadores=[n for n in (args.nomes or "").split(",")],
            deslocamento_grupo=sem_faixa[0] if sem_faixa else 0.0,
        )

    por_numero = {f.numero: f for f in escolhidas}
    for alvo, segundos in args.deslocamentos:
        if alvo is None:
            continue
        if alvo not in por_numero:
            raise ValueError(
                f"--deslocamento {alvo + 1}=...: a faixa {alvo + 1} não está entre "
                "as escolhidas para transcrever."
            )
        por_numero[alvo].deslocamento = segundos
    faixas.validar(escolhidas)
    return escolhidas


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


def _cmd_limpar(args) -> int:
    """Mostra as cópias que serão removidas e só remove depois de confirmar."""
    candidatas = copias.antigas(args.pasta, args.dias)
    if not candidatas:
        print(f"Nenhuma cópia com mais de {args.dias:g} dia(s) em {args.pasta}.")
        return 0

    print(f"Cópias de envios pela web sem mudança há mais de {args.dias:g} dia(s):\n")
    for item in candidatas:
        motivo = f" — {item.erro}" if item.erro else ""
        print(
            f"  {item.video}  ({item.tamanho / 1e6:,.1f} MB, "
            f"{item.dias:.0f} dia(s)){motivo[:80]}"
        )
    total = sum(item.tamanho for item in candidatas) / 1e6
    print(f"\n{len(candidatas)} cópia(s), {total:,.1f} MB. As transcrições ficam.")

    if not args.sim:
        try:
            resposta = input("Remover? [s/N] ")
        except EOFError:
            resposta = ""
        if resposta.strip().lower() not in {"s", "sim"}:
            print("Nada foi removido.")
            return 0

    removidas = copias.remover(candidatas)
    print(f"{len(removidas)} cópia(s) removida(s).")
    return 0


def _cmd_web(args) -> int:
    import uvicorn

    from .web import app

    print(f"Interface disponível em http://{args.host}:{args.porta}")
    uvicorn.run(app, host=args.host, port=args.porta, log_level="warning")
    return 0


def _cmd_transcrever(args) -> int:
    destino = args.saida or Path("saida") / args.video.stem
    escolhidas = _faixas_dos_argumentos(args)
    for aviso in faixas.nomes_repetidos(escolhidas):
        print(f"Aviso: {aviso}")

    config = pipeline.Configuracao(
        video=args.video,
        destino=destino,
        modelo_fala=args.modelo,
        faixas=escolhidas,
        formatos=[f.strip() for f in args.formatos.split(",") if f.strip()],
        idioma=args.idioma,
        traduzir_para=args.traduzir,
        contexto=args.contexto,
        dispositivo=args.dispositivo,
        inicio=args.inicio,
        fim=args.fim,
        juntar_falas=not args.sem_juntar,
    )

    ultimo = [0.0]
    estimativa = progresso.EstimativaTempo()

    def relatar(mensagem: str, fracao: float) -> None:
        estimativa.registrar(fracao)
        agora = time.monotonic()
        if fracao < 1.0 and agora - ultimo[0] < 0.5:
            return
        ultimo[0] = agora
        print(f"\r{_linha_de_progresso(mensagem, fracao, estimativa.restante())}",
              end="", flush=True)

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


_LARGURA_MENSAGEM = 52
_POSICAO = re.compile(r",?\s*\d+ de \d+$")
_DESCRICAO = re.compile(r"\s*\([^)]*\)")


def _encurtar(mensagem: str, limite: int = _LARGURA_MENSAGEM) -> str:
    """Encaixa a etapa no espaço da linha sem perder o "N de M" do final.

    Primeiro saem as partes descritivas entre parênteses — "(várias pessoas)",
    "(Caio)" —, que o resto da mensagem dispensa. Só se ainda faltar espaço o
    começo é cortado, numa fronteira de palavra, com reticências.
    """
    if len(mensagem) <= limite:
        return mensagem
    curta = _DESCRICAO.sub("", mensagem)
    if len(curta) <= limite:
        return curta
    achado = _POSICAO.search(curta)
    posicao = achado.group(0) if achado else ""
    corpo = curta[: achado.start()] if achado else curta
    espaco = limite - len(posicao) - 1
    corte = corpo[:espaco].rsplit(" ", 1)[0] if " " in corpo[:espaco] else corpo[:espaco]
    return f"{corte.rstrip(' ,')}…{posicao}"


def _linha_de_progresso(mensagem: str, fracao: float, restante: float | None) -> str:
    """Uma linha de largura fixa, reescrita no lugar a cada aviso (`\\r`)."""
    largura = 24
    cheio = int(fracao * largura)
    barra = "#" * cheio + "-" * (largura - cheio)
    tempo = "" if fracao >= 1.0 else progresso.texto_restante(restante)
    mensagem = _encurtar(mensagem)
    return f"[{barra}] {fracao * 100:5.1f}%  {tempo:<18} {mensagem:<{_LARGURA_MENSAGEM}}"


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
        "limpar": _cmd_limpar,
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
