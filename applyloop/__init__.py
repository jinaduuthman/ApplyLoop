"""applyloop — a job-search agent: find roles, score fit, tailor your CV, apply, log everything.

Command line:  `applyloop init`, then `applyloop hunt`. As a library:

    from applyloop import Agent, load_settings, filter_reason
    agent = Agent(interactive=False)              # reads ./config/applyloop.yml (or $APPLYLOOP_HOME)
    summary = agent.hunt(mode="queue")            # find + score + prepare; nothing is submitted
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

try:
    from importlib.metadata import version as _version
    __version__ = _version("applyloop")
except Exception:                                   # running from a source tree that isn't installed
    __version__ = "0.1.0"

from .config import import_legacy_env as _import_legacy_env

_import_legacy_env()

# Public API, imported on first use so `import applyloop` stays fast.
_LAZY = {
    "Agent": ".agent",
    "HuntSummary": ".agent",
    "Settings": ".settings",
    "Track": ".settings",
    "load_settings": ".settings",
    "ProjectPaths": ".config",
    "find_project_root": ".config",
    "Store": ".store",
    "filter_reason": ".filters",
    "detect_work_mode": ".filters",
    "AnswerBank": ".answers",
    "route": ".router",
    "TexCV": ".latex_cv",
    "JobPosting": ".sources",
    "init_project": ".cli_agent",
}

__all__ = sorted(_LAZY) + ["__version__"]


def __getattr__(name: str):
    if name in _LAZY:
        return getattr(import_module(_LAZY[name], __name__), name)
    raise AttributeError(f"module 'applyloop' has no attribute {name!r}")


if TYPE_CHECKING:  # pragma: no cover
    from .agent import Agent, HuntSummary
    from .answers import AnswerBank
    from .cli_agent import init_project
    from .config import ProjectPaths, find_project_root
    from .filters import detect_work_mode, filter_reason
    from .latex_cv import TexCV
    from .router import route
    from .settings import Settings, Track, load_settings
    from .sources import JobPosting
    from .store import Store
