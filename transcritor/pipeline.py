"""Orquestração: MP4 do OBS -> duas faixas -> transcrição -> locutores -> mesclagem."""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import diarizacao, ffmpeg_tools, mesclagem, modelos, saida, transcricao
from .transcricao import Fala

# Peso de cada etapa no progresso total: as duas transcrições dominam o tempo.
# Os 5% restantes ficam para a diarização, a mesclagem e a escrita dos arquivos.
_ETAPAS = {"extracao": 0.05, "faixa_gm": 0.30, "faixa_grupo": 0.60}

Relato = Callable[[str, float], None]


@dataclass(slots=True)
class Configuracao:
    """Parâmetros de uma execução."""

    video: Path
    destino: Path
    modelo_fala: str = "pt-pequeno"
    faixa_gm: int = 0
    faixa_grupo: int = 1
    nome_gm: str = "GM"
    n_jogadores: int | None = 5
    nomes_jogadores: Sequence[str] = ()
    prefixo_jogador: str = "Jogador"
    formatos: Sequence[str] = saida.FORMATOS
    idioma: str = "pt"
    traduzir_para: str | None = None
    deslocamento_grupo: float = 0.0
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


def executar(config: Configuracao, relatar: Relato | None = None) -> Resultado:
    """Roda a transcrição completa e escreve os arquivos de saída."""
    video = Path(config.video)
    if not video.is_file():
        raise FileNotFoundError(f"{video} não encontrado")

    def aviso(mensagem: str, fracao: float) -> None:
        if relatar is not None:
            relatar(mensagem, min(1.0, max(0.0, fracao)))

    aviso("Verificando as faixas de áudio", 0.0)
    faixas = ffmpeg_tools.listar_faixas_audio(video)
    _validar_faixas(faixas, config)

    aviso("Preparando os modelos", 0.01)
    caminho_fala = modelos.resolver_modelo_fala(
        config.modelo_fala, config.diretorio_modelos
    )
    caminho_locutor = modelos.resolver_modelo_locutor(config.diretorio_modelos)

    concluido = 0.0
    with tempfile.TemporaryDirectory(prefix="transcritor-") as tmp:
        temporario = Path(tmp)

        aviso("Extraindo as faixas de áudio do vídeo", concluido)
        wav_gm = ffmpeg_tools.extrair_faixa(
            video, config.faixa_gm, temporario / "faixa-gm.wav",
            inicio=config.inicio, fim=config.fim,
        )
        wav_grupo = ffmpeg_tools.extrair_faixa(
            video, config.faixa_grupo, temporario / "faixa-grupo.wav",
            inicio=config.inicio, fim=config.fim,
        )
        concluido += _ETAPAS["extracao"]

        # Faixa do GM: locutor único, logo não há por que calcular x-vectors.
        base = concluido
        aviso(f"Transcrevendo a faixa do {config.nome_gm}", base)
        falas_gm = transcricao.transcrever(
            wav_gm,
            caminho_fala,
            modelo_locutor=None,
            faixa=config.faixa_gm,
            progresso=lambda f: aviso(
                f"Transcrevendo a faixa do {config.nome_gm}",
                base + f * _ETAPAS["faixa_gm"],
            ),
        )
        for fala in falas_gm:
            fala.locutor = config.nome_gm
        concluido = base + _ETAPAS["faixa_gm"]

        # Faixa do grupo: várias vozes, precisa dos x-vectors para a diarização.
        base = concluido
        aviso("Transcrevendo a faixa dos jogadores", base)
        falas_grupo = transcricao.transcrever(
            wav_grupo,
            caminho_fala,
            modelo_locutor=caminho_locutor,
            faixa=config.faixa_grupo,
            progresso=lambda f: aviso(
                "Transcrevendo a faixa dos jogadores",
                base + f * _ETAPAS["faixa_grupo"],
            ),
        )
        concluido = base + _ETAPAS["faixa_grupo"]

    aviso("Separando os locutores pela voz", concluido)
    diarizacao.atribuir_locutores(
        falas_grupo,
        n_locutores=config.n_jogadores,
        nomes=list(config.nomes_jogadores) or None,
        prefixo=config.prefixo_jogador,
    )

    aviso("Mesclando as duas faixas", concluido + 0.02)
    # Com recorte, os tempos saem relativos ao trecho; somamos o início de volta
    # para que os carimbos batam com o vídeo original.
    deslocamentos = {
        config.faixa_gm: config.inicio,
        config.faixa_grupo: config.inicio + config.deslocamento_grupo,
    }
    falas = mesclagem.mesclar(
        falas_gm, falas_grupo, deslocamentos=deslocamentos, juntar=config.juntar_falas
    )

    aviso("Gravando os arquivos", concluido + 0.03)
    gerados = saida.escrever(
        falas,
        config.destino,
        formatos=config.formatos,
        idioma=config.idioma,
        traduzir_para=config.traduzir_para,
    )

    aviso("Concluído", 1.0)
    return Resultado(
        falas=falas,
        arquivos=gerados.arquivos,
        avisos=gerados.avisos,
        locutores=sorted({f.locutor for f in falas}),
        duracao=max((f.fim for f in falas), default=0.0),
    )


def _validar_faixas(faixas: Sequence[ffmpeg_tools.FaixaAudio], config: Configuracao) -> None:
    if len(faixas) < 2:
        raise ValueError(
            f"O vídeo tem {len(faixas)} faixa(s) de áudio; são necessárias 2. "
            "No OBS, grave com as trilhas 1 e 2 habilitadas em "
            "Configurações > Saída > Gravação."
        )
    for numero, rotulo in ((config.faixa_gm, "GM"), (config.faixa_grupo, "jogadores")):
        if not 0 <= numero < len(faixas):
            raise ValueError(
                f"A faixa {numero + 1} ({rotulo}) não existe: o vídeo tem "
                f"{len(faixas)} faixas de áudio."
            )
    if config.faixa_gm == config.faixa_grupo:
        raise ValueError("As faixas do GM e dos jogadores precisam ser diferentes.")
