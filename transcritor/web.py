"""Interface web: envio do MP4, acompanhamento do progresso e download."""

from __future__ import annotations

import re
import shutil
import threading
import traceback
import uuid
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import copias, ffmpeg_tools, modelos, pipeline, progresso, saida
from . import faixas as arranjo  # `faixas` é o nome do campo do formulário

RAIZ_TRABALHOS = copias.RAIZ_PADRAO
PAGINA = Path(__file__).parent / "web_static" / "index.html"

# Uma transcrição por vez: o modelo de fala já satura a GPU (ou os núcleos).
_executor = ThreadPoolExecutor(max_workers=1)
_trava = threading.Lock()


@dataclass
class Trabalho:
    id: str
    nome_video: str
    estado: str = "na fila"       # na fila | processando | concluido | erro
    mensagem: str = "Aguardando"
    progresso: float = 0.0
    erro: str | None = None
    arquivos: list[str] = field(default_factory=list)
    locutores: list[str] = field(default_factory=list)
    total_falas: int = 0
    duracao: float = 0.0
    avisos: list[str] = field(default_factory=list)
    previa: list[dict] = field(default_factory=list)
    pasta: Path | None = field(default=None, repr=False)
    estimativa: progresso.EstimativaTempo | None = field(default=None, repr=False)

    def tempo_restante(self) -> float | None:
        """Segundos até o fim, calculados aqui; None enquanto não há base."""
        if self.estado == "concluido":
            return 0.0
        if self.estado != "processando" or self.estimativa is None:
            return None
        return self.estimativa.restante()

    def como_dict(self) -> dict:
        restante = self.tempo_restante()
        return {
            "id": self.id,
            "nome_video": self.nome_video,
            "estado": self.estado,
            "mensagem": self.mensagem,
            "progresso": round(self.progresso, 4),
            # A página só exibe: o cálculo e o texto saem prontos daqui.
            "restante": None if restante is None else round(restante),
            "restante_texto": (
                progresso.texto_restante(restante)
                if self.estado == "processando" else ""
            ),
            "decorrido": (
                round(self.estimativa.decorrido) if self.estimativa is not None else 0
            ),
            "erro": self.erro,
            "arquivos": self.arquivos,
            "locutores": self.locutores,
            "total_falas": self.total_falas,
            "duracao": self.duracao,
            "avisos": self.avisos,
            "previa": self.previa,
        }


_trabalhos: dict[str, Trabalho] = {}

# Os identificadores são gerados aqui (`uuid4().hex[:12]`); qualquer outra coisa
# vinda da URL é recusada antes de virar caminho de pasta.
_ID_VALIDO = re.compile(r"[0-9a-f]{12}")


def limpar_copias_antigas(dias: float = copias.DIAS_PADRAO) -> list[Path]:
    """Apaga as cópias de envios que falharam há mais de `dias` dias."""
    removidas = copias.remover(copias.antigas(RAIZ_TRABALHOS, dias))
    for video in removidas:
        print(f"Cópia de envio antigo removida: {video}")
    return removidas


@asynccontextmanager
async def _ciclo_de_vida(_app: FastAPI) -> AsyncIterator[None]:
    # Uma limpeza que falhe não pode impedir a interface de subir.
    try:
        limpar_copias_antigas()
    except OSError as erro:
        print(f"Aviso: a limpeza das cópias antigas falhou: {erro}")
    yield


app = FastAPI(
    title="Transcritor de sessões", docs_url="/api/docs", lifespan=_ciclo_de_vida
)


@app.get("/", response_class=HTMLResponse)
def pagina_inicial() -> HTMLResponse:
    return HTMLResponse(PAGINA.read_text(encoding="utf-8"))


@app.get("/api/modelos")
def listar_modelos() -> dict:
    cache = modelos.diretorio_padrao()
    return {
        "modelos": [
            {
                "apelido": m.apelido,
                "motor": m.motor,
                "descricao": m.descricao,
                "tamanho_mb": m.tamanho_mb,
                "baixado": modelos.baixado(m, cache),
            }
            for m in modelos.MODELOS_FALA.values()
        ],
        "modelo_padrao": modelos.PADRAO,
        "formatos": list(saida.FORMATOS),
        "ffmpeg": _ffmpeg_disponivel(),
    }


def _ffmpeg_disponivel() -> str | None:
    try:
        return ffmpeg_tools.localizar_ffmpeg()
    except ffmpeg_tools.FFmpegIndisponivel:
        return None


@app.post("/api/trabalhos")
async def criar_trabalho(
    video: UploadFile,
    modelo: str = Form(modelos.PADRAO),
    faixas: str = Form(""),
    faixa_gm: int = Form(1),
    faixa_grupo: int = Form(2),
    nome_gm: str = Form("GM"),
    jogadores: str = Form("5"),
    nomes: str = Form(""),
    formatos: str = Form(",".join(saida.FORMATOS)),
    idioma: str = Form("pt"),
    traduzir: str = Form(""),
    contexto: str = Form(""),
    deslocamento: float = Form(0.0),
    inicio: float = Form(0.0),
    fim: str = Form(""),
    juntar: bool = Form(True),
) -> JSONResponse:
    """Recebe o vídeo e enfileira a transcrição.

    As faixas vêm em `faixas`, um JSON no formato de `faixas.ler_json`. Sem ele,
    valem os campos do arranjo de duas faixas (`faixa_gm`, `faixa_grupo`,
    `nome_gm`, `jogadores`, `nomes`, `deslocamento`), com o comportamento de
    antes. A resposta traz em `avisos` os nomes repetidos entre faixas.
    """
    parametros = {
        "modelo": modelo, "faixas": faixas, "faixa_gm": faixa_gm, "faixa_grupo": faixa_grupo,
        "nome_gm": nome_gm, "jogadores": jogadores, "nomes": nomes,
        "formatos": formatos, "idioma": idioma, "traduzir": traduzir,
        "contexto": contexto, "deslocamento": deslocamento, "inicio": inicio,
        "fim": fim, "juntar": juntar,
    }
    identificador = uuid.uuid4().hex[:12]
    pasta = RAIZ_TRABALHOS / identificador
    pasta.mkdir(parents=True, exist_ok=True)

    nome = Path(video.filename or "video.mp4").name
    caminho_video = pasta / nome
    with caminho_video.open("wb") as destino:
        shutil.copyfileobj(video.file, destino, length=4 << 20)
    await video.close()

    # Um envio recusado aqui não é falha de transcrição: tentar de novo com o
    # mesmo arquivo e os mesmos campos não resolveria, então nada fica guardado.
    try:
        no_arquivo = ffmpeg_tools.listar_faixas_audio(caminho_video)
        config = _configuracao(parametros, caminho_video, pasta)
        arranjo.validar(list(config.faixas), len(no_arquivo))
    except (ValueError, ffmpeg_tools.FFmpegIndisponivel) as erro:
        shutil.rmtree(pasta, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(erro)) from erro
    copias.registrar(pasta, nome, parametros)

    avisos = arranjo.nomes_repetidos(config.faixas)
    trabalho = Trabalho(id=identificador, nome_video=nome, pasta=pasta, avisos=list(avisos))
    with _trava:
        _trabalhos[identificador] = trabalho
    _executor.submit(_processar, trabalho, config)

    return JSONResponse({
        "id": identificador,
        "faixas": [f.rotulo for f in no_arquivo],
        "avisos": avisos,
    })


def _configuracao(parametros: dict, video: Path, pasta: Path) -> pipeline.Configuracao:
    """Monta a configuração a partir dos campos do formulário.

    É a mesma função no envio e na nova tentativa, que relê os parâmetros
    gravados no registro da cópia: as duas não têm como divergir. Registros
    gravados antes do campo `faixas` caem no arranjo de duas faixas.
    """
    p = parametros
    fim = str(p["fim"]).strip()
    return pipeline.Configuracao(
        video=video,
        destino=pasta / Path(video.name).stem,
        modelo_fala=p["modelo"],
        faixas=_faixas_do_formulario(p),
        formatos=[f.strip() for f in p["formatos"].split(",") if f.strip()],
        idioma=p["idioma"],
        traduzir_para=p["traduzir"].strip() or None,
        contexto=p["contexto"],
        inicio=float(p["inicio"]),
        fim=float(fim) if fim else None,
        juntar_falas=bool(p["juntar"]),
    )


def _faixas_do_formulario(p: dict) -> list[arranjo.Faixa]:
    if str(p.get("faixas") or "").strip():
        return arranjo.ler_json(p["faixas"])
    jogadores = str(p["jogadores"]).strip()
    return arranjo.padrao(
        faixa_gm=int(p["faixa_gm"]) - 1,
        faixa_grupo=int(p["faixa_grupo"]) - 1,
        nome_gm=p["nome_gm"],
        n_jogadores=None if jogadores.lower() == "auto" else int(jogadores),
        nomes_jogadores=p["nomes"].split(","),
        deslocamento_grupo=float(p["deslocamento"]),
    )


@app.post("/api/trabalhos/{identificador}/tentar-novamente")
def tentar_novamente(identificador: str) -> JSONResponse:
    """Reprocessa um trabalho que falhou, com a cópia do vídeo guardada no envio.

    Também serve para um trabalho interrompido por uma queda do servidor: o que
    vale é a cópia e o registro em disco, não a memória do processo.
    """
    if not _ID_VALIDO.fullmatch(identificador):
        raise HTTPException(status_code=404, detail="Trabalho não encontrado")
    pasta = RAIZ_TRABALHOS / identificador
    with _trava:
        atual = _trabalhos.get(identificador)
        if atual is not None and atual.estado != "erro":
            raise HTTPException(
                status_code=409,
                detail=f"O trabalho está {atual.estado}; só um trabalho que falhou "
                "pode ser tentado de novo.",
            )
        registro = copias.ler(pasta)
        video = copias.copia(pasta)
        if registro is None or video is None:
            raise HTTPException(
                status_code=404,
                detail="A cópia do vídeo deste trabalho não existe mais. "
                "Envie o arquivo de novo.",
            )
        config = _configuracao(registro.parametros, video, pasta)
        copias.marcar(pasta, copias.AGUARDANDO)
        trabalho = Trabalho(
            id=identificador, nome_video=registro.video, pasta=pasta,
            avisos=arranjo.nomes_repetidos(config.faixas),
        )
        _trabalhos[identificador] = trabalho
    _executor.submit(_processar, trabalho, config)
    return JSONResponse({"id": identificador})


def _processar(trabalho: Trabalho, config: pipeline.Configuracao) -> None:
    trabalho.estimativa = progresso.EstimativaTempo()

    def relatar(mensagem: str, fracao: float) -> None:
        trabalho.estimativa.registrar(fracao)
        trabalho.estado = "processando"
        trabalho.mensagem = mensagem
        trabalho.progresso = fracao

    try:
        resultado = pipeline.executar(config, relatar)
    except Exception as erro:  # noqa: BLE001 - o erro vai para a interface
        trabalho.estado = "erro"
        trabalho.mensagem = "Falhou"
        trabalho.erro = str(erro) or erro.__class__.__name__
        traceback.print_exc()
        if trabalho.pasta is not None:
            # A cópia fica, para que o trabalho possa ser tentado de novo.
            copias.marcar_falha(trabalho.pasta, trabalho.erro)
        return

    if trabalho.pasta is not None:
        try:
            copias.concluir(trabalho.pasta)
        except OSError as erro:
            resultado.avisos.append(
                f"A transcrição terminou, mas a cópia do vídeo não pôde ser apagada: {erro}"
            )

    trabalho.estado = "concluido"
    trabalho.mensagem = "Concluído"
    trabalho.progresso = 1.0
    trabalho.arquivos = [a.name for a in resultado.arquivos]
    trabalho.locutores = resultado.locutores
    trabalho.total_falas = len(resultado.falas)
    trabalho.avisos = trabalho.avisos + resultado.avisos
    trabalho.duracao = resultado.duracao
    trabalho.previa = [
        {
            "inicio": round(f.inicio, 2),
            "locutor": f.locutor,
            "texto": f.texto,
            "faixa": f.faixa + 1,
        }
        for f in resultado.falas[:80]
    ]


@app.get("/api/trabalhos/{identificador}")
def estado_trabalho(identificador: str) -> dict:
    trabalho = _trabalhos.get(identificador)
    if trabalho is None:
        raise HTTPException(status_code=404, detail="Trabalho não encontrado")
    return trabalho.como_dict()


@app.get("/api/trabalhos/{identificador}/arquivos/{nome}")
def baixar(identificador: str, nome: str) -> FileResponse:
    if identificador not in _trabalhos:
        raise HTTPException(status_code=404, detail="Trabalho não encontrado")
    caminho = (RAIZ_TRABALHOS / identificador / Path(nome).name).resolve()
    pasta = (RAIZ_TRABALHOS / identificador).resolve()
    if not caminho.is_file() or pasta not in caminho.parents:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    return FileResponse(caminho, filename=caminho.name)
