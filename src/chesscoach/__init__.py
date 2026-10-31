"""
chesscoach — an offline chess coach.

Stockfish does the analysis, Maia predicts what a human would play,
python-chess turns positions into verified facts, and a local LLM
(via Ollama) only narrates those facts in a coach's voice.
"""

__version__ = "0.20.1"
