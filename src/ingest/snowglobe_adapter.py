"""Snow Globe history → Wargame adapter (stub).

Snow Globe (the LLM-driven wargaming tool) emits history JSON where each turn
records an agent's choice from a set of options, with the underlying LLM logits
or sampled distribution implying a probability. To build a `Wargame` from one,
this adapter would need to:

  1. Walk the history tree, identifying decision nodes.
  2. For each node, derive a `WargameBranch.question` from the option text
     and infer a `probability` from the LLM's choice distribution (or the
     observed sampled rate across N runs of the same scenario).
  3. Tag each branch with a `domain` and `horizon`. These are usually
     not in the Snow Globe output and need to be inferred from the
     scenario brief or human-tagged.

We are deferring this until we have a Snow Globe history file in hand. The
contract — `from_snowglobe(history_path) -> Wargame` — is fixed; the body
is the only thing that's empty.
"""
from __future__ import annotations

from pathlib import Path

from .types import Wargame


class SnowGlobeNotImplemented(NotImplementedError):
    pass


def from_snowglobe(history_path: str | Path) -> Wargame:
    raise SnowGlobeNotImplemented(
        "Snow Globe adapter not implemented yet. To enable: provide a Snow Globe "
        "history JSON file and implement the decision-node walk + probability "
        f"inference described in this module's docstring. (path: {history_path})"
    )
