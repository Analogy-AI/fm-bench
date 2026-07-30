"""Ablation switches (paper-prep): each switch measurably changes what the
agent sees/can do, is recorded in results, and is a strict no-op when off.
"""

from __future__ import annotations

from engine.game import Game
from engine.persist.snapshot import state_hash
from runner.llm_agent import LLMAgent
from runner.providers import LLMResponse, ToolCall, Usage


def _drive(game, stops=6):
    packet = game.advance() if game.started else game.start()
    for _ in range(stops):
        if packet.get("game_over"):
            break
        packet = game.advance()
    return packet


def test_ablations_off_is_bit_identical():
    g1 = Game(seed=7, years=1, player_club="mid")
    g2 = Game(seed=7, years=1, player_club="mid", ablations=[])
    g1.start()
    g2.start()
    for _ in range(8):
        g1.advance()
        g2.advance()
    assert state_hash(g1.world) == state_hash(g2.world)


class _CaptureAdapter:
    """Records what the runner sends; optionally issues one probe tool call
    on the first stop, then always advances."""

    def __init__(self, probe_tool: str | None = None):
        self.probe_tool = probe_tool
        self.seen_tools = None
        self.first_packet_text = None
        self.probed = False

    def complete(self, *, system, messages, tools, max_tokens,
                 temperature=0.0):
        usage = Usage(input_tokens=5, output_tokens=2)
        if self.seen_tools is None:
            self.seen_tools = [t["name"] for t in tools]
            self.first_packet_text = messages[0]["text"]
            self.system = system
        if self.probe_tool and not self.probed:
            self.probed = True
            return LLMResponse(text=None, tool_calls=[
                ToolCall(id="p1", name=self.probe_tool, args={"text": "x"}
                         if self.probe_tool == "append_note" else {})],
                usage=usage, raw=None)
        return LLMResponse(text=None, tool_calls=[
            ToolCall(id="a1", name="advance", args={})], usage=usage, raw=None)


def _play_stops(adapter, ablations, stops=2, seed=11):
    game = Game(seed=seed, years=1, player_club="mid")
    agent = LLMAgent(model="mock", api_key="unused", adapter=adapter,
                     verbose=False, ablations=ablations)
    packet = game.start()
    for _ in range(stops):
        if packet.get("game_over"):
            break
        agent.handle_stop(game, packet)
        packet = game.advance()
    return agent


def test_no_notebook_hides_tools_packet_and_blocks_calls():
    adapter = _CaptureAdapter(probe_tool="append_note")
    agent = _play_stops(adapter, ["no-notebook"])
    assert "append_note" not in adapter.seen_tools
    assert "rewrite_notes" not in adapter.seen_tools
    assert '"notebook"' not in adapter.first_packet_text
    assert agent.error_codes.get("tool_disabled_by_ablation", 0) == 1
    assert "Ablation notice" in adapter.system
    # control: default shows the notebook and the tools
    control = _CaptureAdapter()
    _play_stops(control, None)
    assert "append_note" in control.seen_tools
    assert '"notebook"' in control.first_packet_text


def test_no_history_blocks_the_archive():
    adapter = _CaptureAdapter(probe_tool="get_history")
    agent = _play_stops(adapter, ["no-history"])
    assert "get_history" not in adapter.seen_tools
    assert agent.error_codes.get("tool_disabled_by_ablation", 0) == 1


def _force_insolvent(ablations):
    from engine.core.params import M
    game = Game(seed=5, years=2, player_club="mid", ablations=ablations)
    game.start()
    game.world.clubs[game.world.player_club_id].cash = -M(500)
    return game


def test_no_insolvency_warnings_suppresses_warnings_not_administration():
    game = _force_insolvent(["no-insolvency-warnings"])
    packet = None
    admin_seen = False
    for _ in range(200):
        packet = game.advance()
        for it in packet.get("inbox", []):
            assert it["type"] != "insolvency_warning"
            if it["type"] == "administration":
                admin_seen = True
        if packet.get("game_over"):
            break
    etypes = [e["type"] for e in game.world.event_log.entries]
    assert "insolvency_warning" not in etypes
    assert admin_seen or "administration" in etypes


def test_results_record_ablations():
    from run_benchmark import run_benchmark
    r = run_benchmark(agent="idle", seeds=[1], years=1, verbose=False,
                      run_label="test_ablation_record",
                      ablations=["no-insolvency-warnings"])
    assert r["ablations"] == ["no-insolvency-warnings"]
    assert r["per_seed"][0]["ablations"] == ["no-insolvency-warnings"]
