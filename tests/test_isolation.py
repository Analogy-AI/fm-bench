"""I3 invariant: AI decision code (market_ai.py) must never touch true
attributes or hidden traits. AST-level static check, not just grep."""

import ast
import pathlib

FORBIDDEN_ATTRS = {
    "aging_offset", "ca", "consistency", "injury_proneness", "pa",
    "professionalism", "rating_bias", "style_pref", "true_ment", "true_phys",
    "true_tech",
}
# names market_ai legitimately touches via other modules are fine — the check
# is on ATTRIBUTE ACCESS expressions inside market_ai.py itself.
ALLOWED = {"ca"} & set()  # none allowed


# every module containing AI-club decision logic (M2: the invariant covers
# all AI decision paths, not one file). lineup.py is the belief-only helper
# split out of match.py; tick.py hosts AI youth/facility decisions.
AI_DECISION_MODULES = (
    "engine/sim/market_ai.py",
    "engine/sim/lineup.py",
    "engine/sim/board.py",
    "engine/sim/tick.py",
    "engine/sim/draft.py",   # v0.3: bot draft policies are AI decision code
)

# tick.py runs truth-side phases (development, injuries) alongside AI club
# decisions; only its AI functions are scanned.
TICK_AI_FUNCTIONS = {"_ai_youth_management", "_ai_facility_investment"}


def _truth_violations(tree, path):
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRS:
            violations.append(f"{path} line {node.lineno}: .{node.attr}")
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == "ca":
                violations.append(f"{path} line {node.lineno}: .ca() call")
    return violations


def test_ai_decision_modules_never_read_truth():
    violations = []
    for path in AI_DECISION_MODULES:
        src = pathlib.Path(path).read_text(encoding="utf-8")
        tree = ast.parse(src)
        if path.endswith("tick.py"):
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))                         and node.name in TICK_AI_FUNCTIONS:
                    violations += _truth_violations(node, path)
        else:
            violations += _truth_violations(tree, path)
    assert not violations, f"AI decision code touches truth: {violations}"


def test_tick_ai_functions_exist():
    """Guard against silently renaming an AI function out of the scan."""
    src = pathlib.Path("engine/sim/tick.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    names = {n.name for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    missing = TICK_AI_FUNCTIONS - names
    assert not missing, f"scanned AI functions renamed/removed: {missing}"


def test_market_ai_imports_are_clean():
    src = pathlib.Path("engine/sim/market_ai.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "engine.domain.finance", \
                "market_ai must not import truth-side finance helpers"


def test_engine_never_imports_baselines():
    """The oracle baseline is intentionally privileged (reads truth). The
    privilege must never flow back: no engine module (sim/obs/actions/...)
    may import anything from baselines/."""
    for path in sorted(pathlib.Path("engine").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {a.name.split(".")[0] for a in node.names}
                assert "baselines" not in roots, f"{path} imports baselines"
            if isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                assert root != "baselines", f"{path} imports baselines"


def test_observation_no_point_estimates():
    """Ability leaves obs only as band dicts (ca_low/ca_high/confidence)."""
    from engine.game import Game

    game = Game(seed=5, years=1, player_club="mid")
    game.start()
    squad = game.call_tool("get_squad", {})["data"]
    for p in squad:
        assert set(p["ability"].keys()) == {"ca_high", "ca_low", "confidence"}
        assert p["ability"]["ca_high"] > p["ability"]["ca_low"]
        assert "pa" not in p and "true_phys" not in p
