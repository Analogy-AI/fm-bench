"""Deterministic relevance-retrieval over the agent's own notebook.

The `retrieval` memory mode borrows ONE idea from the analogyai skill library —
*retrieve the relevant subset instead of dumping the whole store* — in its
lightest possible form:

  - no embeddings, no vector DB, no external knowledge — the notebook is still
    written by the agent itself and holds only what the agent chose to remember;
  - retrieval is pure whole-word/tag matching against a query derived from the
    current decision-stop packet, so it is fully DETERMINISTIC (same notebook +
    same packet => same ordering). That matters: fm-bench's audit rests on
    `seed + action log = bit-identical replay`, and an embedding-ANN retriever
    would inject non-determinism. This does not.

The engine is untouched: `world.notebook` stays a plain string, and this only
transforms the VIEW of it the agent is shown at each stop (runner-side). So it
is a measurable harness variant (`memory_mode=retrieval`) to compare against the
flat notebook, not a change to the frozen default harness.

Note format: one note per line. A note may carry inline `#tags` (e.g.
`#finance #board sold striker to balance wages`); tags are matched against the
situation query just like ordinary words, but the `#` is stripped for display.
"""

from __future__ import annotations

import re

#: how many notes to foreground under "most relevant now"; the rest still shown.
DEFAULT_RETRIEVAL_K = 8

# split on underscore too, so snake_case situation tokens ('insolvency_risk',
# 'avoid_relegation') align with the same words written free-text in a note.
_WORD = re.compile(r"[a-z0-9]+")
# situation words that carry no discriminating signal (present every stop).
_STOPWORDS = frozenset((
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "is", "are",
    "you", "your", "id", "true", "false", "none", "null", "day", "month",
    "year", "stop", "type", "types", "action", "required", "date",
))


def _tokens(text: str) -> set[str]:
    """Whole-word tokens of a string: lowercased [a-z0-9_] words, minus
    stopwords and pure-digit noise (numbers rarely match meaningfully)."""
    out = set()
    for w in _WORD.findall(text.lower()):
        if w in _STOPWORDS or w.isdigit():
            continue
        out.add(w)
    return out


def _situation_tokens(packet: dict) -> set[str]:
    """Bag of words describing the current stop, walked from the packet.

    Schema-robust: recurses the packet collecting dict KEYS and string VALUES
    (skipping the notebook itself and pure ids), so warnings like
    'insolvency_risk', window 'summer', targets 'avoid_relegation', etc. all
    become query tokens without hard-coding the digest shape."""
    toks: set[str] = set()

    def walk(node, key: str | None = None) -> None:
        if key and key not in ("notebook", "stop_id"):
            toks.update(_tokens(key))
        if isinstance(node, dict):
            for k, v in node.items():
                # skip the notebook VALUE too, not just its key — otherwise the
                # notebook queries itself, every note fully matches, and the
                # ranking degenerates to note verbosity instead of relevance.
                if k in ("notebook", "stop_id"):
                    continue
                walk(v, k)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v, key)
        elif isinstance(node, str):
            toks.update(_tokens(node))
        # ints/floats/bools contribute nothing (numbers are noise here)

    walk(packet)
    return toks


def _split_notes(notebook: str) -> list[str]:
    """One note per non-empty line (leading '- '/'* ' bullets stripped)."""
    notes = []
    for line in notebook.splitlines():
        s = line.strip().lstrip("-*").strip()
        if s:
            notes.append(s)
    return notes


def _score(note: str, situation: set[str]) -> int:
    """Whole-word overlap between a note and the situation. `#tags` count too
    (the '#' is ignored for matching); an explicit tag hit is weighted double
    since a tag is a deliberate retrieval handle."""
    plain = note.replace("#", " ")
    words = _tokens(plain)
    tags = _tokens(" ".join(re.findall(r"#([a-z0-9_]+)", note.lower())))
    return len(words & situation) + len(tags & situation)


def retrieve_notebook(notebook: str, packet: dict,
                      k: int = DEFAULT_RETRIEVAL_K) -> str:
    """Return a relevance-ordered VIEW of `notebook` for the current `packet`.

    Notes most relevant to the current situation come first (under a header);
    the rest are kept below so nothing is lost. Ordering is a stable sort by
    (match-score desc, original position asc) — deterministic. Small notebooks
    (<= k notes) are returned relevance-ordered without section headers.
    """
    notes = _split_notes(notebook)
    if not notes:
        return notebook  # empty / whitespace-only: unchanged
    situation = _situation_tokens(packet)
    scored = sorted(
        ((_score(n, situation), i, n) for i, n in enumerate(notes)),
        key=lambda t: (-t[0], t[1]),
    )
    ordered = [n for _, _, n in scored]

    if len(ordered) <= k:
        body = "\n".join(f"- {n}" for n in ordered)
        return f"[{len(ordered)} notes, relevance-ordered for this stop]\n{body}"

    top = ordered[:k]
    rest = ordered[k:]
    lines = [f"[{len(notes)} notes total; the {len(top)} most relevant to this "
             "stop are shown first]",
             "## most relevant now"]
    lines += [f"- {n}" for n in top]
    lines += ["## other notes"]
    lines += [f"- {n}" for n in rest]
    return "\n".join(lines)
