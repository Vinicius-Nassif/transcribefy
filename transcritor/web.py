"""Interface web: envio do MP4, acompanhamento do progresso e download."""

from __future__ import annotations

import shutil
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import ffmpeg_tools, modelos, pipeline, saida

RAIZ_TRABALHOS = Path("dados/trabalhos")
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

    def como_dict(self) -> dict:
        return {
            "id": self.id,
            "nome_video": self.nome_video,
            "estado": self.estado,
            "mensagem": self.mensagem,
            "progresso": round(self.progresso, 4),
            "erro": self.erro,
            "arquivos": self.arquivos,
            "locutores": self.locutores,
            "total_falas": self.total_falas,
            "duracao": self.duracao,
            "avisos": self.avisos,
            "previa": self.previa,
        }


_trabalhos: dict[str, Trabalho] = {}

app = FastAPI(title="Transcritor de sessões", docs_url="/api/docs")


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
    """Recebe o vídeo e enfileira a transcrição."""
    identificador = uuid.uuid4().hex[:12]
    pasta = RAIZ_TRABALHOS / identificador
    pasta.mkdir(parents=True, exist_ok=True)

    nome = Path(video.filename or "video.mp4").name
    caminho_video = pasta / nome
    with caminho_video.open("wb") as destino:
        shutil.copyfileobj(video.file, destino, length=4 << 20)
    await video.close()

    try:
        faixas = ffmpeg_tools.listar_faixas_audio(caminho_video)
    except (ValueError, ffmpeg_tools.FFmpegIndisponivel) as erro:
        shutil.rmtree(pasta, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(erro)) from erro
    if len(faixas) < 2:
        shutil.rmtree(pasta, ignore_errors=True)
        raise HTTPException(
            status_code=400,
            detail=(
                f"O arquivo enviado tem {len(faixas)} faixa(s) de áudio. "
                "São necessárias 2 faixas separadas (grave no OBS com as "
                "trilhas 1 e 2 habilitadas em Configurações > Saída > Gravação)."
            ),
        )

    config = pipeline.Configuracao(
        video=caminho_video,
        destino=pasta / Path(nome).stem,
        modelo_fala=modelo,
        faixa_gm=faixa_gm - 1,
        faixa_grupo=faixa_grupo - 1,
        nome_gm=nome_gm.strip() or "GM",
        n_jogadores=None if jogadores.strip().lower() == "auto" else int(jogadores),
        nomes_jogadores=[n.strip() for n in nomes.split(",") if n.strip()],
        formatos=[f.strip() for f in formatos.split(",") if f.strip()],
        idioma=idioma,
        traduzir_para=traduzir.strip() or None,
        contexto=contexto,
        deslocamento_grupo=deslocamento,
        inicio=inicio,
        fim=float(fim) if fim.strip() else None,
        juntar_falas=juntar,
    )

    trabalho = Trabalho(id=identificador, nome_video=nome)
    with _trava:
        _trabalhos[identificador] = trabalho
    _executor.submit(_processar, trabalho, config)

    return JSONResponse({"id": identificador, "faixas": [f.rotulo for f in faixas]})


def _processar(trabalho: Trabalho, config: pipeline.Configuracao) -> None:
    def relatar(mensagem: str, fracao: float) -> None:
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
        return

    trabalho.estado = "concluido"
    trabalho.mensagem = "Concluído"
    trabalho.progresso = 1.0
    trabalho.arquivos = [a.name for a in resultado.arquivos]
    trabalho.locutores = resultado.locutores
    trabalho.total_falas = len(resultado.falas)
    trabalho.avisos = resultado.avisos
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
