"""Progresso de uma execução: etapas com peso, cancelamento e tempo restante.

O `pipeline` divide o trabalho em etapas (extrair, transcrever, analisar vozes)
cujo custo depende da duração do áudio e do dispositivo. `Acompanhamento` junta
o avanço de cada uma numa fração única — mesmo com etapas correndo em paralelo
— e é o ponto em que um pedido de cancelamento vira exceção: todo aviso de
progresso passa por ele, então o cancelamento é atendido no próximo aviso.

`EstimativaTempo` fica do lado de quem mostra o progresso (CLI e web): ela só
precisa da sequência de frações no tempo para prever quanto falta.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

Relato = Callable[[str, float], None]

# Segundos de processamento por segundo de áudio, medidos com scripts/medir.py
# numa RTX 4050 e num i7 de 16 núcleos. Só a proporção entre eles importa: são
# os pesos que mantêm a barra andando num ritmo constante entre etapas.
CUSTO_EXTRACAO = 0.002
CUSTO_WHISPER = {"cuda": 0.10, "cpu": 0.80}
CUSTO_VOSK = 0.20
CUSTO_VOZES = 0.03
CUSTO_TRADUCAO = 0.005  # por idioma


class Cancelado(Exception):
    """A execução foi interrompida a pedido de quem a iniciou."""


class Acompanhamento:
    """Junta o avanço de etapas com peso numa fração única de 0 a 1.

    Args:
        relatar: recebe (mensagem, fração) a cada avanço.
        cancelado: quando sinalizado, o próximo avanço levanta `Cancelado`.
    """

    def __init__(self, relatar: Relato | None = None, cancelado: threading.Event | None = None):
        self._relatar = relatar
        self._cancelado = cancelado
        self._pesos: dict[str, float] = {}
        self._feito: dict[str, float] = {}
        self._mensagem = ""
        self._trava = threading.Lock()

    def planejar(self, pesos: dict[str, float]) -> None:
        """Define as etapas e o custo relativo de cada uma."""
        total = sum(pesos.values()) or 1.0
        with self._trava:
            self._pesos = {chave: peso / total for chave, peso in pesos.items()}
            self._feito = dict.fromkeys(pesos, 0.0)

    @property
    def fracao(self) -> float:
        with self._trava:
            soma = sum(self._pesos[c] * self._feito[c] for c in self._pesos)
        return min(1.0, max(0.0, soma))

    def verificar(self) -> None:
        """Levanta `Cancelado` se o cancelamento foi pedido."""
        if self._cancelado is not None and self._cancelado.is_set():
            raise Cancelado("Transcrição cancelada.")

    def avisar(self, mensagem: str | None = None) -> None:
        """Reporta a mensagem atual sem mudar o avanço (e confere o cancelamento)."""
        self.verificar()
        if mensagem is not None:
            self._mensagem = mensagem
        if self._relatar is not None:
            self._relatar(self._mensagem, self.fracao)

    def etapa(self, chave: str, mensagem: str | None) -> Callable[[float], None]:
        """Devolve o callback de progresso de uma etapa, que recebe a fração dela.

        Com `mensagem=None` a etapa avança a barra sem trocar o texto atual —
        é o caso da tradução, que acontece durante "Traduzindo".
        """

        def avancar(fracao: float) -> None:
            with self._trava:
                if chave in self._feito:
                    self._feito[chave] = max(self._feito[chave], min(1.0, fracao))
            self.avisar(mensagem)

        return avancar

    def concluir(self, chave: str, mensagem: str | None = None) -> None:
        with self._trava:
            if chave in self._feito:
                self._feito[chave] = 1.0
        self.avisar(mensagem)


class EstimativaTempo:
    """Prevê o tempo restante a partir do ritmo recente do progresso.

    Usa o avanço da última `janela` de segundos, e não a média desde o início:
    o carregamento do modelo consome os primeiros segundos sem quase nada de
    progresso, e contá-lo superestimaria o resto da execução inteira.
    Não arrisca palpite antes de `minimo_s` segundos ou de 2% de avanço.
    """

    def __init__(
        self, janela: float = 90.0, minimo_s: float = 10.0,
        relogio: Callable[[], float] = time.monotonic,
    ):
        self._janela = janela
        self._minimo = minimo_s
        self._relogio = relogio
        self._inicio = relogio()
        self._amostras: deque[tuple[float, float]] = deque()

    def registrar(self, fracao: float) -> None:
        agora = self._relogio()
        self._amostras.append((agora, fracao))
        # Mantém uma amostra anterior à janela, para a janela sempre ter início.
        while len(self._amostras) > 2 and self._amostras[1][0] < agora - self._janela:
            self._amostras.popleft()

    @property
    def decorrido(self) -> float:
        return self._relogio() - self._inicio

    def restante(self) -> float | None:
        """Segundos estimados até o fim, ou None enquanto não há base para estimar."""
        if len(self._amostras) < 2 or self.decorrido < self._minimo:
            return None
        (t0, f0), (t1, f1) = self._amostras[0], self._amostras[-1]
        if f1 < 0.02 or f1 >= 1.0:
            return None if f1 < 0.02 else 0.0
        ritmo = (f1 - f0) / (t1 - t0) if t1 > t0 else 0.0
        if ritmo <= 0:
            ritmo = f1 / self.decorrido  # parado agora: cai para a média geral
        if ritmo <= 0:
            return None
        return (1.0 - f1) / ritmo
