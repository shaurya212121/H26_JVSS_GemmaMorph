"""
GemmaMorph: Autonomous Dependency Resilience & Exit Capsule Engine
Developed by Team JVSS for Hacktoberfest Hack Day Mumbai 2026.
"""

__version__ = "1.0.0"
__team__ = "JVSS"
__track__ = "Track 1: Gemma 4 Dependency Resilience"

try:
    from depexit.usage_finder import find_usages
    from depexit.llm import ask_llm, parse_json_from_llm
    from depexit.gemma_propose import propose_migrations
    from depexit.rehearsal import run_rehearsal
    from depexit.capsule import generate_capsule

    __all__ = [
        "find_usages",
        "ask_llm",
        "parse_json_from_llm",
        "propose_migrations",
        "run_rehearsal",
        "generate_capsule",
    ]
except ImportError:
    pass
