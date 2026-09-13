"""Orquestração: MP4 do OBS -> duas faixas -> transcrição -> locutores -> mesclagem."""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import diarizacao, ffmpeg_tools, mesclagem, modelos, saida, transcricao, vozes
from .fala import Fala

# Peso de cada etapa no progresso total: as duas transcrições dominam o tempo, e
# a extração dos vetores de voz roda só na faixa dos jogadores. Os 5% restantes
# ficam para a diarização, a mesclagem e a escrita dos arquivos.
_ETAPAS = {"extracao": 0.04, "faixa_gm": 0.24, "faixa_grupo": 0.48, "vozes": 0.19}

Relato = Callable[[str, float], None]


@dataclass(slots=True)
class Configuracao:
    """Parâmetros de uma execução."""

    video: Path
    destino: Path
    modelo_fala: str = modelos.PADRAO
    faixa_gm: int = 0
    faixa_grupo: int = 1
    nome_gm: str = "GM"
    n_jogadores: int | None = 5
    nomes_jogadores: Sequence[str] = ()
    prefixo_jogador: str = "Jogador"
    formatos: Sequence[str] = saida.FORMATOS
    idioma: str = "pt"
    traduzir_para: str | None = None
    contexto: str = ""
    dispositivo: str = "auto"
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


def contexto_da_mesa(config: Configuracao) -> str:
    """Monta a dica de contexto que o Whisper recebe antes de transcrever.

    Os nomes da mesa entram junto com o texto livre porque é neles que um
    reconhecedor mais erra: sem a dica, um nome próprio vira a palavra comum
    mais parecida. A dica é um prompt, não uma regra — ela enviesa, não obriga.
    """
    partes = []
    nomes = [config.nome_gm, *config.nomes_jogadores]
    presentes = ", ".join(n for n in nomes if n.strip())
    if presentes:
        partes.append(f"Participantes: {presentes}.")
    if config.contexto.strip():
        partes.append(config.contexto.strip())
    return " ".join(partes)


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
    modelo = modelos.resolver_modelo_fala(config.modelo_fala, config.diretorio_modelos)
    caminho_locutor = modelos.resolver_modelo_locutor(config.diretorio_modelos)
    # A extração de vetores de voz roda no Vosk mesmo quando o texto vem do
    # Whisper; o modelo compacto basta para alimentar o reconhecedor.
    modelo_vosk = (
        modelo
        if modelo.motor == modelos.MOTOR_VOSK
        else modelos.resolver_modelo_fala("vosk-pt", config.diretorio_modelos)
    )
    contexto = contexto_da_mesa(config)

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

        # Faixa do GM: locutor único, logo não há por que calcular vetores de voz.
        base = concluido
        aviso(f"Transcrevendo a faixa do {config.nome_gm}", base)
        falas_gm = transcricao.transcrever(
            wav_gm, modelo,
            faixa=config.faixa_gm,
            idioma=config.idioma,
            contexto=contexto,
            dispositivo=config.dispositivo,
            progresso=lambda f: aviso(
                f"Transcrevendo a faixa do {config.nome_gm}",
                base + f * _ETAPAS["faixa_gm"],
            ),
        )
        for fala in falas_gm:
            fala.locutor = config.nome_gm
        concluido = base + _ETAPAS["faixa_gm"]

        # Faixa do grupo: várias vozes. O motor Vosk já devolve os vetores na
        # mesma passada; com o Whisper eles vêm depois, trecho a trecho.
        base = concluido
        aviso("Transcrevendo a faixa dos jogadores", base)
        falas_grupo = transcricao.transcrever(
            wav_grupo, modelo,
            faixa=config.faixa_grupo,
            idioma=config.idioma,
            contexto=contexto,
            dispositivo=config.dispositivo,
            modelo_locutor=caminho_locutor,
            progresso=lambda f: aviso(
                "Transcrevendo a faixa dos jogadores",
                base + f * _ETAPAS["faixa_grupo"],
            ),
        )
        concluido = base + _ETAPAS["faixa_grupo"]

        if not any(f.vetor_voz for f in falas_grupo):
            base = concluido
            aviso("Analisando as vozes dos jogadores", base)
            vozes.anexar_vetores(
                wav_grupo, falas_grupo, modelo_vosk.caminho, caminho_locutor,
                progresso=lambda f: aviso(
                    "Analisando as vozes dos jogadores",
                    base + f * _ETAPAS["vozes"],
                ),
            )
        concluido += _ETAPAS["vozes"]

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
