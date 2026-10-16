"""Interface web: envio da gravação, acompanhamento do progresso e download.

Ciclo de vida de um trabalho:

1. O envio é gravado direto numa pasta temporária do sistema, em blocos e fora
   do laço de eventos — o servidor continua respondendo durante um upload de
   vários GB, e o arquivo é escrito no disco uma vez só.
2. A configuração é validada contra as faixas que o arquivo tem antes de entrar
   na fila, para que um erro de preenchimento apareça na hora.
3. O resultado vai para `saida/<gravação>-<data>/`, que sobrevive ao servidor;
   a pasta temporária é removida no fim, com sucesso, erro ou cancelamento.
"""

from __future__ import annotations

import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError
from python_multipart import create_form_parser

from . import ffmpeg_tools, historico, modelos, pipeline, saida, temporarios, traducao
from .progresso import EstimativaTempo

PAGINA = Path(__file__).parent / "web_static" / "index.html"

# Bytes acumulados antes de entregar ao parser numa thread: blocos grandes
# diluem o custo de ir e voltar da thread, e 4 MB não pesam na memória.
BLOCO_ENVIO = 4 << 20

# Uma transcrição por vez: o modelo de fala já satura a GPU (ou os núcleos).
_executor = ThreadPoolExecutor(max_workers=1)
_trava = threading.Lock()


def pasta_saida() -> Path:
    """Onde ficam as transcrições geradas pela interface (TRANSCRITOR_SAIDA)."""
    return Path(os.environ.get("TRANSCRITOR_SAIDA", "saida"))


# --- Contrato com a página ---------------------------------------------------


class FaixaPedida(BaseModel):
    trilha: int = Field(ge=1, description="número da trilha no arquivo, a partir de 1")
    nomes: list[str] = []
    varias_pessoas: bool = False
    vozes: int | None = Field(None, ge=1, description="None estima sozinho")
    ajuste: float = 0.0
    arquivo: int | None = Field(
        None, ge=0, description="índice em `arquivos`; None é o arquivo principal"
    )


class IntervaloPedido(BaseModel):
    ativo: bool = False
    inicio: float = Field(0.0, ge=0)
    fim: float | None = None


class Pedido(BaseModel):
    faixas: list[FaixaPedida] = Field(min_length=1, max_length=pipeline.MAX_FAIXAS)
    modelo: str = modelos.PADRAO
    idioma: str = "pt"
    traducoes: list[str] = []
    contexto: list[str] = []
    intervalo: IntervaloPedido = IntervaloPedido()
    formatos: list[str] = list(saida.FORMATOS)
    juntar: bool = True


def montar_configuracao(
    pedido: Pedido, video: Path, extras: list[Path], destino: Path
) -> pipeline.Configuracao:
    """Traduz o pedido da página na configuração do pipeline.

    É aqui que o toggle do intervalo é respeitado: desligado, os valores que
    vierem nos campos são ignorados e o arquivo inteiro é transcrito.
    """
    faixas = []
    for posicao, faixa in enumerate(pedido.faixas):
        if faixa.arquivo is not None and faixa.arquivo >= len(extras):
            raise ValueError(f"A faixa {posicao + 1} aponta para um arquivo que não foi enviado.")
        faixas.append(pipeline.Faixa(
            trilha=faixa.trilha - 1,
            nomes=faixa.nomes,
            varias_pessoas=faixa.varias_pessoas,
            vozes=faixa.vozes,
            ajuste=faixa.ajuste,
            arquivo=None if faixa.arquivo is None else extras[faixa.arquivo],
        ))
    termos, _ = pipeline.sem_repetidos(pedido.contexto)
    intervalo = pedido.intervalo
    return pipeline.Configuracao(
        video=video,
        destino=destino,
        faixas=faixas,
        modelo_fala=pedido.modelo,
        formatos=pedido.formatos,
        idioma=pedido.idioma,
        traducoes=pedido.traducoes,
        contexto=f"{', '.join(termos)}." if termos else "",
        inicio=intervalo.inicio if intervalo.ativo else 0.0,
        fim=intervalo.fim if intervalo.ativo else None,
        juntar_falas=pedido.juntar,
    )


# --- Trabalhos ---------------------------------------------------------------


@dataclass
class Trabalho:
    id: str
    nome_video: str
    estado: str = "na fila"  # na fila | processando | cancelando | cancelado | concluido | erro
    mensagem: str = "Aguardando"
    progresso: float = 0.0
    erro: str | None = None
    arquivos: list[str] = field(default_factory=list)
    locutores: list[str] = field(default_factory=list)
    total_falas: int = 0
    duracao: float = 0.0
    avisos: list[str] = field(default_factory=list)
    previa: list[dict] = field(default_factory=list)
    pasta: str = ""
    cancelado: threading.Event = field(default_factory=threading.Event, repr=False)
    estimativa: EstimativaTempo | None = field(default=None, repr=False)
    temporaria: temporarios.PastaTemporaria | None = field(default=None, repr=False)
    destino: Path | None = field(default=None, repr=False)

    def como_dict(self) -> dict:
        restante = self.estimativa.restante() if self.estimativa else None
        return {
            "id": self.id,
            "nome_video": self.nome_video,
            "estado": self.estado,
            "mensagem": self.mensagem,
            "progresso": round(self.progresso, 4),
            "decorrido_s": round(self.estimativa.decorrido) if self.estimativa else 0,
            "restante_s": None if restante is None else round(restante),
            "erro": self.erro,
            "arquivos": self.arquivos,
            "locutores": self.locutores,
            "total_falas": self.total_falas,
            "duracao": self.duracao,
            "avisos": self.avisos,
            "previa": self.previa,
            "pasta": self.pasta,
        }


_trabalhos: dict[str, Trabalho] = {}


@asynccontextmanager
async def _ciclo_de_vida(_app: FastAPI):
    # Pastas de execuções que morreram sem limpar (queda, kill -9).
    temporarios.limpar_orfas()
    # Importar as bibliotecas dos motores segura o GIL por quase um segundo. Feito
    # aqui, no fundo, isso não coincide com o primeiro trabalho — quando a página
    # está consultando o progresso e o servidor pararia de responder.
    threading.Thread(target=_preimportar, daemon=True).start()
    yield


def _preimportar() -> None:
    import faster_whisper  # noqa: F401
    import vosk  # noqa: F401


app = FastAPI(title="Transcritor de sessões", docs_url="/api/docs", lifespan=_ciclo_de_vida)


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
        "max_faixas": pipeline.MAX_FAIXAS,
        "ffmpeg": _ffmpeg_disponivel(),
    }


@app.get("/api/idiomas")
def listar_idiomas(origem: str = "pt") -> dict:
    """Idiomas para os quais dá para traduzir agora (ver `traducao.disponiveis`)."""
    idiomas = traducao.disponiveis(origem if origem != "auto" else "pt")
    return {
        "idiomas": [{"valor": i.valor, "nome": i.nome, "servico": i.servico} for i in idiomas],
        "instalar": "transcritor idiomas --instalar pt-es",
    }


@app.get("/api/historico/contexto")
def historico_contexto() -> dict:
    return {"termos": historico.listar()}


@app.delete("/api/historico/contexto")
def esquecer_termo(termo: str) -> dict:
    historico.remover(termo)
    return {"termos": historico.listar()}


def _ffmpeg_disponivel() -> str | None:
    try:
        return ffmpeg_tools.localizar_ffmpeg()
    except ffmpeg_tools.FFmpegIndisponivel:
        return None


def _nome_seguro(bruto: bytes | str | None, padrao: str) -> str:
    if isinstance(bruto, bytes):
        bruto = bruto.decode("utf-8", errors="replace")
    nome = Path((bruto or "").replace("\\", "/")).name.strip()
    return nome or padrao


async def _receber(request: Request, pasta: Path) -> tuple[dict[str, str], list[tuple[str, str, Path]]]:
    """Grava os arquivos do envio direto em `pasta`, em blocos e fora do laço.

    Devolve (campos de texto, [(campo, nome original, caminho no disco)]).
    """
    campos: dict[str, str] = {}
    arquivos = []

    def ao_campo(campo) -> None:
        nome = campo.field_name.decode() if isinstance(campo.field_name, bytes) else campo.field_name
        valor = campo.value or b""
        campos[nome] = valor.decode("utf-8") if isinstance(valor, bytes) else valor

    def ao_arquivo(arquivo) -> None:
        arquivos.append(arquivo)

    tipo = request.headers.get("content-type", "")
    if not tipo.startswith("multipart/form-data"):
        raise HTTPException(status_code=400, detail="Envie o formulário como multipart/form-data.")
    parser = create_form_parser(
        {"Content-Type": tipo.encode()}, ao_campo, ao_arquivo,
        config={"UPLOAD_DIR": str(pasta), "UPLOAD_DELETE_TMP": False,
                "MAX_MEMORY_FILE_SIZE": 1 << 20},
    )
    acumulado = bytearray()
    async for bloco in request.stream():
        acumulado += bloco
        if len(acumulado) >= BLOCO_ENVIO:
            await run_in_threadpool(parser.write, bytes(acumulado))
            acumulado.clear()
    if acumulado:
        await run_in_threadpool(parser.write, bytes(acumulado))
    await run_in_threadpool(parser.finalize)

    gravados = []
    for posicao, arquivo in enumerate(arquivos):
        campo = arquivo.field_name.decode() if isinstance(arquivo.field_name, bytes) else arquivo.field_name
        original = _nome_seguro(arquivo.file_name, f"arquivo-{posicao}")
        arquivo.flush_to_disk()
        arquivo.close()
        destino = pasta / f"{posicao}-{original}"
        Path(arquivo.actual_file_name.decode() if isinstance(arquivo.actual_file_name, bytes)
             else arquivo.actual_file_name).replace(destino)
        gravados.append((campo, original, destino))
    return campos, gravados


@app.post("/api/trabalhos")
async def criar_trabalho(request: Request) -> JSONResponse:
    """Recebe a gravação e a configuração (campo `config`, JSON) e enfileira."""
    identificador = uuid.uuid4().hex[:12]
    temporaria = temporarios.PastaTemporaria()
    try:
        campos, enviados = await _receber(request, temporaria.caminho)
        try:
            pedido = Pedido.model_validate_json(campos.get("config") or "{}")
        except ValidationError as erro:
            raise HTTPException(status_code=422, detail=_explicar(erro)) from erro

        principais = [(nome, c) for campo, nome, c in enviados if campo == "video"]
        if not principais:
            raise HTTPException(status_code=400, detail="Escolha o arquivo da gravação.")
        nome, video = principais[0]
        extras = [c for campo, _, c in enviados if campo == "arquivos"]

        # O código do trabalho entra no nome: dois envios do mesmo arquivo no
        # mesmo segundo (um clique duplo, duas abas) não podem dividir a pasta.
        carimbo = time.strftime("%Y%m%d-%H%M%S")
        destino_pasta = pasta_saida() / f"{Path(nome).stem}-{carimbo}-{identificador[:6]}"
        try:
            config = montar_configuracao(pedido, video, extras, destino_pasta / Path(nome).stem)
            inspecoes = await run_in_threadpool(
                lambda: {a: ffmpeg_tools.inspecionar(a) for a in {video, *extras}}
            )
            avisos = pipeline.validar(config, inspecoes)
        except (ValueError, ffmpeg_tools.FFmpegIndisponivel) as erro:
            raise HTTPException(status_code=400, detail=str(erro)) from erro
    except BaseException:
        temporaria.remover()
        raise

    avisos.extend(await run_in_threadpool(_registrar_historico, pedido.contexto))
    trabalho = Trabalho(
        id=identificador, nome_video=nome, temporaria=temporaria, destino=destino_pasta
    )
    with _trava:
        _trabalhos[identificador] = trabalho
    _executor.submit(_processar, trabalho, config)
    return JSONResponse({
        "id": identificador,
        "faixas": [f.rotulo for f in inspecoes[video].faixas],
        "avisos": avisos,
    })


def _registrar_historico(termos: list[str]) -> list[str]:
    """Guarda os termos para sugerir depois. Devolve avisos; nunca levanta.

    As sugestões são um extra: uma pasta de dados sem permissão de escrita não
    pode impedir uma transcrição de começar.
    """
    try:
        historico.registrar(termos)
    except Exception as erro:  # noqa: BLE001
        traceback.print_exc()
        return [f"Os termos de contexto não foram salvos no histórico: {erro}"]
    return []


def _explicar(erro: ValidationError) -> str:
    """Mensagem curta a partir dos erros do Pydantic, para mostrar na página."""
    partes = []
    for item in erro.errors():
        local = ".".join(str(p) for p in item["loc"])
        partes.append(f"{local}: {item['msg']}" if local else item["msg"])
    return "Configuração inválida — " + "; ".join(partes)


def _processar(trabalho: Trabalho, config: pipeline.Configuracao) -> None:
    trabalho.estimativa = EstimativaTempo()

    def relatar(mensagem: str, fracao: float) -> None:
        if trabalho.estado == "na fila":
            trabalho.estado = "processando"
        trabalho.mensagem = mensagem
        trabalho.progresso = fracao
        trabalho.estimativa.registrar(fracao)

    try:
        if trabalho.cancelado.is_set():
            raise pipeline.Cancelado()
        resultado = pipeline.executar(config, relatar, trabalho.cancelado)
    except pipeline.Cancelado:
        trabalho.estado = "cancelado"
        trabalho.mensagem = "Cancelado"
        _remover_saida_incompleta(trabalho)
        return
    except Exception as erro:  # noqa: BLE001 - o erro vai para a interface
        trabalho.estado = "erro"
        trabalho.mensagem = "Falhou"
        trabalho.erro = str(erro) or erro.__class__.__name__
        traceback.print_exc()
        _remover_saida_incompleta(trabalho)
        return
    finally:
        if trabalho.temporaria is not None:
            trabalho.temporaria.remover()

    trabalho.estado = "concluido"
    trabalho.mensagem = "Concluído"
    trabalho.progresso = 1.0
    trabalho.arquivos = [a.name for a in resultado.arquivos]
    trabalho.pasta = str(Path(trabalho.destino).resolve())
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


def _remover_saida_incompleta(trabalho: Trabalho) -> None:
    """Uma execução que não terminou não deixa resultado pela metade."""
    destino = trabalho.destino
    if destino is not None and destino.is_dir():
        for arquivo in destino.iterdir():
            arquivo.unlink(missing_ok=True)
        destino.rmdir()


@app.get("/api/trabalhos/{identificador}")
def estado_trabalho(identificador: str) -> dict:
    trabalho = _trabalhos.get(identificador)
    if trabalho is None:
        raise HTTPException(status_code=404, detail="Trabalho não encontrado")
    return trabalho.como_dict()


@app.post("/api/trabalhos/{identificador}/cancelar")
def cancelar(identificador: str) -> dict:
    """Pede o cancelamento; ele é atendido no próximo aviso de progresso."""
    trabalho = _trabalhos.get(identificador)
    if trabalho is None:
        raise HTTPException(status_code=404, detail="Trabalho não encontrado")
    if trabalho.estado in ("na fila", "processando"):
        trabalho.cancelado.set()
        trabalho.estado = "cancelando"
        trabalho.mensagem = "Cancelando…"
    return trabalho.como_dict()


@app.get("/api/trabalhos/{identificador}/arquivos/{nome}")
def baixar(identificador: str, nome: str) -> FileResponse:
    trabalho = _trabalhos.get(identificador)
    if trabalho is None or trabalho.destino is None:
        raise HTTPException(status_code=404, detail="Trabalho não encontrado")
    pasta = Path(trabalho.destino).resolve()
    caminho = (pasta / Path(nome).name).resolve()
    if not caminho.is_file() or pasta not in caminho.parents:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    return FileResponse(caminho, filename=caminho.name)
