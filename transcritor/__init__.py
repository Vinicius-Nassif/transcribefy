"""Transcrição mesclada de gravações do OBS com duas faixas de áudio."""

from .pipeline import Configuracao, Resultado, executar, inspecionar
from .transcricao import Fala

__version__ = "0.1.0"
__all__ = ["Configuracao", "Fala", "Resultado", "executar", "inspecionar"]
