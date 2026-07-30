"""Headless CLI.

    python -m engine.cli simulate --seed 42 --years 5
    python -m engine.cli simulate --seed 42 --years 20 --policy idle --club mid
"""

from __future__ import annotations

import argparse
import json
import time

from engine.game import Game
from engine.persist.snapshot import state_hash


def idle_policy(game: Game, packet: dict) -> None:
    """Baseline hook shape: look at packet, call tools, then the runner advances.
    Idle does nothing — pure default behavior."""


def cmd_simulate(args) -> None:
    t0 = time.time()
    if args.policy == "none":
        game = Game(seed=args.seed, years=args.years, player_club=None)
        summary = game.simulate_headless()
        for s in summary["seasons"]:
            print(f"Y{s['year']:02d}  champion={s['t1_champion']:<22} "
                  f"pts={s['t1_champion_pts']}  avg_ca={s['avg_ca']}")
        print(f"final_hash={summary['final_hash']}")
    else:
        game = Game(seed=args.seed, years=args.years, player_club=args.club)
        packet = game.start()
        stops = 0
        while not packet.get("game_over"):
            stops += 1
            idle_policy(game, packet)
            packet = game.advance()
        result = packet["result"]
        print(json.dumps(result, indent=2, sort_keys=True))
        print(f"stops={stops} final_hash={state_hash(game.world)}")
    print(f"wall_clock={time.time() - t0:.2f}s")


def main() -> None:
    ap = argparse.ArgumentParser(prog="fm-bench-engine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sim = sub.add_parser("simulate", help="run a headless simulation")
    sim.add_argument("--seed", type=int, default=42)
    sim.add_argument("--years", type=int, default=5)
    sim.add_argument("--policy", choices=["none", "idle"], default="none",
                     help="none = all-AI world sim; idle = player club with idle policy")
    sim.add_argument("--club", choices=["big", "mid", "small"], default="mid")
    sim.set_defaults(fn=cmd_simulate)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
