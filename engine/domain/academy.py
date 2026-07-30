"""Youth intake generation (pure, stream-driven; lazy generation is replay-safe)."""

from __future__ import annotations

import math

from engine.core.params import Params
from engine.core.rng import Stream
from engine.domain import names
from engine.domain.player import POSITIONS, Player


def generate_intake(params: Params, stream: Stream, club, year: int,
                    ids, reverse_factor: float = 0.0) -> list[Player]:
    """Season youth intake for one club. PA formula per DESIGN §5.1 with
    diminishing academy returns and reputation/tier ceiling (X7)."""
    ac = params.academy
    n = ac.intake_base + club.academy_level
    rep_factor = 0.75 + 0.35 * (club.reputation / 100.0)
    if club.division == 2:
        rep_factor *= 0.85
    mu = ac.pa_mu_base + ac.pa_mu_per_sqrt_level * math.sqrt(club.academy_level) * rep_factor
    # anti-hollowing floor (opt-in): a reverse-order "draft" — worse league rank
    # (reverse_factor -> 1) raises intake potential. reverse_pa_mu=0 (default)
    # leaves mu byte-identical, so goldens are unaffected until turned on.
    mu += ac.get("reverse_pa_mu", 0) * reverse_factor
    lo, hi = ac.pa_clip
    out = []
    for _ in range(n):
        nationality = stream.choice(names.NATIONALITIES)
        pa_display = stream.truncgauss(mu, ac.pa_sigma, lo, hi)
        pa = int(round(pa_display * 10))
        ca = int(round(pa * stream.uniform(ac.ca_ratio[0], ac.ca_ratio[1])))
        pos = stream.choice(POSITIONS)
        spread = stream.uniform(0.9, 1.1)
        p = Player(
            pid=ids.next("P"),
            name=names.player_name(stream, nationality),
            nationality=nationality,
            pos=pos,
            birth_year=year - stream.randint(16, 17),
            true_phys=max(10, int(ca * stream.uniform(0.9, 1.1))),
            true_tech=max(10, int(ca * spread)),
            true_ment=max(10, int(ca * stream.uniform(0.85, 1.05))),
            pa=pa,
            injury_proneness=round(0.5 + 2.5 * stream.uniform(0.0, 1.0) ** 2, 3),
            professionalism=round(stream.uniform(0.5, 1.5), 3),
            consistency=round(stream.uniform(0.7, 1.3), 3),
            aging_offset=round(stream.uniform(-1.0, 2.0), 2),
            style_pref=stream.choice(params.tactics.styles),
            rating_bias=round(stream.gauss(0.0, params.match.rating_persistent_bias_sigma), 4),
            club_id=club.cid,
            is_youth=True,
            wage_cents=0,
            contract_end_year=year + 1,  # academy terms; real contract on promotion
            signed_year=year,
        )
        out.append(p)
    return out
