"""A unidade que todos os módulos trocam entre si: um trecho contínuo de fala.

Fica num módulo próprio, sem dependências, para que os motores de
reconhecimento (`motor_whisper`, `motor_vosk`) e quem os consome possam
importá-la sem ciclos de importação.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Fala:
    """Um trecho contínuo de fala reconhecido em uma das faixas.

    Os campos de voz (`vetor_voz` e `frames_voz`) são preenchidos depois do
    reconhecimento, por `transcritor.vozes`, e alimentam a diarização.
    """

    inicio: float
    fim: float
    texto: str
    faixa: int
    locutor: str = ""
    vetor_voz: list[float] | None = None
    frames_voz: int = 0
    palavras: list[dict] = field(default_factory=list)

    @property
    def duracao(self) -> float:
        return max(0.0, self.fim - self.inicio)
