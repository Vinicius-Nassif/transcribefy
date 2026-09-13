"""Transcrição mesclada de gravações do OBS com duas faixas de áudio."""

from .fala import Fala
from .pipeline import Configuracao, Resultado, executar, inspecionar

__version__ = "0.1.0"
__all__ = ["Configuracao", "Fala", "Resultado", "executar", "inspecionar"]
