"""Dump the ground-truth players of a PUBLIC reference world.

FM-Bench worlds are generated deterministically from an integer seed: the same
seed + this engine + this params.yaml always produces bit-identical players
(true abilities, ages, names, everything). Because the engine and params are
open and the reference seed (1) is public, the seed-1 world's ground truth is
already computable by anyone; this script simply writes it out directly.

This is the PUBLIC REFERENCE / practice world. It is NOT the official scored
world: official FM-Bench evaluation runs on withheld (salted) seeds whose
ground truth is not published. During normal play the engine still hides true
abilities behind noisy scout bands (see the `scout_band` field for what a
manager actually sees); the true_* fields here are the hidden answer key,
published only for this open reference seed.

Usage:
    python scripts/dump_reference_world.py                 # seed 1 -> data/reference_world_seed1.json
    python scripts/dump_reference_world.py --seed 1 --out data/reference_world_seed1.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from engine.core.params import load_params
from engine.domain.worldgen import generate_world
from engine.sim import scouting


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--club", default="mid", help="which stratum is the player club")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--out", default="data/reference_world_seed1.json")
    args = ap.parse_args()

    params = load_params()
    w = generate_world(args.seed, params, player_club=args.club, years=args.years)

    players = []
    for pid, p in sorted(w.players.items()):
        band = None
        if p.club_id is not None:
            try:
                band = scouting.ability_band(w, p.club_id, p)
            except Exception:
                band = None
        players.append({
            "pid": pid,
            "club": p.club_id,
            "name": p.name,
            "nationality": p.nationality,
            "pos": p.pos,
            "age": p.age(1),
            "true_ca": p.ca(),
            "true_phys": p.true_phys,
            "true_tech": p.true_tech,
            "true_ment": p.true_ment,
            "pa": p.pa,
            "scout_band": band,
        })

    out = {
        "note": ("Public reference world (deterministic from the seed). NOT the "
                 "official scored world; official evaluation uses withheld salted "
                 "seeds. true_* are the hidden ground truth; scout_band is what a "
                 "manager sees in play."),
        "seed": args.seed,
        "player_club_stratum": args.club,
        "n_clubs": len(w.clubs),
        "n_players": len(players),
        "players": players,
    }
    p_out = pathlib.Path(args.out)
    p_out.parent.mkdir(parents=True, exist_ok=True)
    p_out.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"seed {args.seed}: {len(players)} players across {len(w.clubs)} clubs -> {args.out}")


if __name__ == "__main__":
    main()
