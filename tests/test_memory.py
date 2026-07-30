"""retrieval-notebook memory mode: deterministic relevance ordering."""

from runner.memory import DEFAULT_RETRIEVAL_K, retrieve_notebook

_NB = "\n".join([
    "scout watched the young keeper excellent reflexes distribution passing vision",
    "insolvency: sell striker now",
])
_PACKET = {
    "digest": {"warnings": ["insolvency_risk"], "window": "summer",
               "season_target": "avoid_relegation"},
    "notebook": _NB,   # must NOT feed the situation query (self-query bug)
    "stop_types": ["DEADLINE_SUMMER"],
}


def test_notebook_does_not_query_itself():
    # the verbose irrelevant scout note must NOT outrank the short relevant one
    out = retrieve_notebook(_NB, _PACKET)
    lines = [ln for ln in out.splitlines() if ln.startswith("- ")]
    assert "insolvency" in lines[0], out


def test_empty_notebook_unchanged():
    assert retrieve_notebook("", _PACKET) == ""
    assert retrieve_notebook("   \n  ", _PACKET).strip() == ""


def test_relevant_notes_rank_above_irrelevant():
    nb = "\n".join([
        "morale is good after the derby win",
        "#finance cut wages to avoid insolvency risk",
        "our summer window target is a new winger",
    ])
    out = retrieve_notebook(nb, _PACKET)
    lines = [ln for ln in out.splitlines() if ln.startswith("- ")]
    # both situation-relevant notes precede the irrelevant morale note
    assert lines[-1].endswith("derby win"), out
    assert any("insolvency" in ln for ln in lines[:2]), out
    assert any("summer window" in ln for ln in lines[:2]), out


def test_deterministic():
    nb = "note about wages\n#board board is unhappy\nsummer signing plan"
    assert retrieve_notebook(nb, _PACKET) == retrieve_notebook(nb, _PACKET)


def test_small_notebook_no_section_headers():
    nb = "one note about insolvency\nanother about summer"
    out = retrieve_notebook(nb, _PACKET)
    assert "## most relevant now" not in out
    assert "relevance-ordered for this stop" in out


def test_large_notebook_has_sections_and_keeps_all():
    notes = [f"note number {i} filler text" for i in range(DEFAULT_RETRIEVAL_K + 5)]
    notes.append("#finance urgent insolvency risk this summer")
    nb = "\n".join(notes)
    out = retrieve_notebook(nb, _PACKET)
    assert "## most relevant now" in out and "## other notes" in out
    # nothing dropped: every note still present
    assert out.count("- ") == len(notes)
    # the strongly-relevant note is foregrounded (before the "other notes" split)
    top_section = out.split("## other notes")[0]
    assert "insolvency risk this summer" in top_section
