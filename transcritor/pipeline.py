"""Orquestração: arquivo do OBS -> N faixas -> transcrição -> locutores -> mesclagem."""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import diarizacao, faixas, ffmpeg_tools, mesclagem, modelos, saida, transcricao, vozes
from .fala import Fala
from .faixas import Faixa

# Peso de cada etapa no progresso total. A preparação (conferir as faixas e os
# modelos), a extração e o fim (diarização, mesclagem e escrita) têm fatia fixa;
# o resto se divide entre as faixas em unidades: transcrever uma faixa de uma
# pessoa vale 1, de várias pessoas vale 2 (mais vozes, mais fala), e extrair os
# vetores de voz dela vale 0,8. Com o arranjo de duas faixas, dá os mesmos 24%,
# 48% e 19% de antes.
_PREPARO = 0.02
_EXTRACAO = 0.02
_FINAL = 0.05
_UNIDADES = {"uma": 1.0, "varias": 2.0, "vozes": 0.8}

Relato = Callable[[str, float], None]


@dataclass(slots=True)
class Configuracao:
    """Parâmetros de uma execução."""

    video: Path
    destino: Path
    modelo_fala: str = modelos.PADRAO
    faixas: Sequence[Faixa] = field(default_factory=faixas.padrao)
    formatos: Sequence[str] = saida.FORMATOS
    idioma: str = "pt"
    traduzir_para: str | None = None
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


def contexto_da_mesa(config: Configuracao) -> str:
    """Monta a dica de contexto que o Whisper recebe antes de transcrever.

    Os nomes da mesa entram junto com o texto livre porque é neles que um
    reconhecedor mais erra: sem a dica, um nome próprio vira a palavra comum
    mais parecida. A dica é um prompt, não uma regra — ela enviesa, não obriga.
    """
    partes = []
    presentes = ", ".join(dict.fromkeys(faixas.nomes_informados(config.faixas)))
    if presentes:
        partes.append(f"Participantes: {presentes}.")
    if config.contexto.strip():
        partes.append(config.contexto.strip())
    return " ".join(partes)


def _pesos(lista: Sequence[Faixa]) -> dict[int, tuple[float, float]]:
    """Fração do progresso de cada faixa: (transcrição, vetores de voz)."""
    unidades = {
        f.numero: (
            _UNIDADES[f.tipo],
            _UNIDADES["vozes"] if f.tipo == faixas.VARIAS else 0.0,
        )
        for f in lista
    }
    total = sum(a + b for a, b in unidades.values()) or 1.0
    disponivel = 1.0 - _PREPARO - _EXTRACAO - _FINAL
    return {n: (a / total * disponivel, b / total * disponivel) for n, (a, b) in unidades.items()}


def _descricao(faixa: Faixa) -> str:
    if faixa.tipo == faixas.UMA:
        return f"{faixa.rotulo} ({faixa.nome_unico})"
    return f"{faixa.rotulo} (várias pessoas)"


def executar(config: Configuracao, relatar: Relato | None = None) -> Resultado:
    """Roda a transcrição completa e escreve os arquivos de saída.

    Os avisos de nome repetido entre faixas (`faixas.nomes_repetidos`) não
    entram no resultado: quem chama os mostra antes de começar.
    """
    video = Path(config.video)
    if not video.is_file():
        raise FileNotFoundError(f"{video} não encontrado")
    escolhidas = list(config.faixas)
    faixas.validar(escolhidas)

    def aviso(mensagem: str, fracao: float) -> None:
        if relatar is not None:
            relatar(mensagem, min(1.0, max(0.0, fracao)))

    aviso("Verificando as faixas de áudio", 0.0)
    _validar_faixas(ffmpeg_tools.listar_faixas_audio(video), config)

    aviso("Preparando os modelos", 0.01)
    modelo = modelos.resolver_modelo_fala(config.modelo_fala, config.diretorio_modelos)
    separar_vozes = any(f.tipo == faixas.VARIAS for f in escolhidas)
    caminho_locutor = modelo_vosk = None
    if separar_vozes:
        # Só baixa o modelo de locutor se alguma faixa for de várias pessoas. A
        # extração de vetores roda no Vosk mesmo quando o texto vem do Whisper;
        # o modelo compacto basta para alimentar o reconhecedor.
        caminho_locutor = modelos.resolver_modelo_locutor(config.diretorio_modelos)
        modelo_vosk = (
            modelo
            if modelo.motor == modelos.MOTOR_VOSK
            else modelos.resolver_modelo_fala("vosk-pt", config.diretorio_modelos)
        )
    contexto = contexto_da_mesa(config)

    avisos: list[str] = []
    aviso_gpu = transcricao.aviso_de_dispositivo(modelo, config.dispositivo)
    if aviso_gpu:
        avisos.append(aviso_gpu)
        aviso(aviso_gpu, 0.02)

    pesos = _pesos(escolhidas)
    falas_por_faixa: list[tuple[Faixa, list[Fala]]] = []
    concluido = _PREPARO
    with tempfile.TemporaryDirectory(prefix="transcritor-") as tmp:
        temporario = Path(tmp)

        aviso("Extraindo as faixas de áudio do vídeo", concluido)
        wavs = {
            f.numero: ffmpeg_tools.extrair_faixa(
                video, f.numero, temporario / f"faixa-{f.numero + 1}.wav",
                inicio=config.inicio, fim=config.fim,
            )
            for f in escolhidas
        }
        concluido += _EXTRACAO

        for faixa in escolhidas:
            peso_texto, peso_vozes = pesos[faixa.numero]
            varias = faixa.tipo == faixas.VARIAS

            base = concluido
            mensagem = f"Transcrevendo a {_descricao(faixa)}"
            aviso(mensagem, base)
            # O motor Vosk já devolve os vetores de voz na mesma passada; com o
            # Whisper eles vêm depois, trecho a trecho.
            falas = transcricao.transcrever(
                wavs[faixa.numero], modelo,
                faixa=faixa.numero,
                idioma=config.idioma,
                contexto=contexto,
                dispositivo=config.dispositivo,
                modelo_locutor=caminho_locutor if varias else None,
                progresso=lambda f, base=base, mensagem=mensagem, peso=peso_texto: aviso(
                    mensagem, base + f * peso
                ),
            )
            concluido = base + peso_texto

            if varias:
                if not any(f.vetor_voz for f in falas):
                    base = concluido
                    mensagem = f"Analisando as vozes da {faixa.rotulo}"
                    aviso(mensagem, base)
                    vozes.anexar_vetores(
                        wavs[faixa.numero], falas, modelo_vosk.caminho, caminho_locutor,
                        progresso=lambda f, base=base, mensagem=mensagem, peso=peso_vozes: (
                            aviso(mensagem, base + f * peso)
                        ),
                    )
                concluido += peso_vozes
            else:
                # Uma pessoa só: o nome é fixo, sem vetores de voz nem agrupamento.
                for fala in falas:
                    fala.locutor = faixa.nome_unico
            falas_por_faixa.append((faixa, falas))

    for faixa, falas in falas_por_faixa:
        if faixa.tipo == faixas.VARIAS:
            aviso(f"Separando os locutores da {faixa.rotulo} pela voz", concluido)
            diarizacao.atribuir_locutores(
                falas,
                n_locutores=faixa.n_locutores,
                nomes=list(faixa.nomes) or None,
                prefixo=faixas.prefixo_de(faixa, escolhidas),
            )

    aviso("Mesclando as faixas", concluido + 0.02)
    # Com recorte, os tempos saem relativos ao trecho; somamos o início de volta
    # para que os carimbos batam com o vídeo original.
    deslocamentos = {f.numero: config.inicio + f.deslocamento for f in escolhidas}
    falas = mesclagem.mesclar(
        *(falas for _, falas in falas_por_faixa),
        deslocamentos=deslocamentos,
        juntar=config.juntar_falas,
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
        avisos=avisos + gerados.avisos,
        locutores=sorted({f.locutor for f in falas}),
        duracao=max((f.fim for f in falas), default=0.0),
    )


def _validar_faixas(no_arquivo: Sequence[ffmpeg_tools.FaixaAudio], config: Configuracao) -> None:
    """Confere a configuração contra as faixas que o arquivo realmente tem."""
    faixas.validar(list(config.faixas), len(no_arquivo))
