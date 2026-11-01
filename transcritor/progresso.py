"""Custo de cada etapa e estimativa do tempo restante.

**Custos.** O `pipeline` dá a cada etapa de cada faixa um peso proporcional ao
tempo que ela costuma levar no dispositivo em uso. Assim a porcentagem avança em
proporção ao tempo, e não ao número de etapas: a transcrição de uma faixa no
Whisper em CPU pesa dezenas de vezes mais que a extração dos vetores de voz.

**Estimativa.** Com a porcentagem andando no ritmo do relógio, o ritmo recente
dela é um bom estimador do que falta, mesmo na passagem de uma etapa lenta para
uma rápida. `EstimativaTempo` fica do lado de quem mostra o progresso (a CLI e o
`web.py`), alimentada com as frações que o pipeline relata.

As constantes e a estimativa vieram do branch `evolucao-faixas-e-desempenho`,
onde foram medidas com o `scripts/medir.py` de lá.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

# Segundos de processamento por segundo de áudio, medidos numa RTX 4050 e num i7
# de 16 núcleos. Só a proporção entre eles importa: são os pesos que mantêm a
# barra andando num ritmo constante entre as etapas.
CUSTO_WHISPER = {"cuda": 0.10, "cpu": 0.80}
CUSTO_VOSK = 0.20
CUSTO_VOZES = 0.03


def custo_transcricao(motor: str, dispositivo: str) -> float:
    """Custo por segundo de áudio de transcrever uma faixa."""
    if motor == "vosk":
        return CUSTO_VOSK
    return CUSTO_WHISPER.get(dispositivo, CUSTO_WHISPER["cpu"])


class EstimativaTempo:
    """Prevê o tempo restante a partir do ritmo recente do progresso.

    Usa o avanço da última `janela` de segundos, e não a média desde o início:
    o carregamento do modelo consome os primeiros segundos sem quase nada de
    progresso, e contá-lo superestimaria o resto da execução inteira.
    Não arrisca palpite antes de `minimo_s` segundos ou de 2% de avanço: nesse
    período, `restante` devolve None ("calculando...").

    É segura entre threads: o pipeline registra numa e a interface lê noutra.
    """

    MINIMO_FRACAO = 0.02

    def __init__(
        self, janela: float = 90.0, minimo_s: float = 10.0,
        relogio: Callable[[], float] = time.monotonic,
    ):
        self._janela = janela
        self._minimo = minimo_s
        self._relogio = relogio
        self._inicio = relogio()
        self._amostras: deque[tuple[float, float]] = deque()
        self._trava = threading.Lock()

    def registrar(self, fracao: float) -> None:
        agora = self._relogio()
        with self._trava:
            self._amostras.append((agora, fracao))
            # Mantém uma amostra anterior à janela, para a janela sempre ter início.
            while len(self._amostras) > 2 and self._amostras[1][0] < agora - self._janela:
                self._amostras.popleft()

    @property
    def decorrido(self) -> float:
        return self._relogio() - self._inicio

    def restante(self) -> float | None:
        """Segundos estimados até o fim, ou None enquanto não há base para estimar."""
        with self._trava:
            if len(self._amostras) < 2:
                return None
            (t0, f0), (t1, f1) = self._amostras[0], self._amostras[-1]
        if f1 >= 1.0:
            return 0.0
        if self.decorrido < self._minimo or f1 < self.MINIMO_FRACAO:
            return None
        ritmo = (f1 - f0) / (t1 - t0) if t1 > t0 else 0.0
        if ritmo <= 0:
            ritmo = f1 / self.decorrido  # parado agora: cai para a média geral
        if ritmo <= 0:
            return None
        return (1.0 - f1) / ritmo


def texto_restante(segundos: float | None) -> str:
    """Como o tempo restante aparece para o usuário, na CLI e na página."""
    if segundos is None:
        return "calculando..."
    if segundos < 1:
        return "terminando"
    if segundos < 60:
        return f"faltam ~{max(5, round(segundos / 5) * 5)} s"
    minutos = round(segundos / 60)
    if minutos < 60:
        return f"faltam ~{minutos} min"
    return f"faltam ~{minutos // 60} h {minutos % 60:02d} min"
