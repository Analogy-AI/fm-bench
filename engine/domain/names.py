"""Procedural fictional names: 3 nationality syllable pools (v0), city + suffix clubs."""

from __future__ import annotations

from engine.core.rng import Stream

NATIONALITIES = ["Valdorian", "Kestrelian", "Noremic"]

_SYLLABLES = {
    "Valdorian": {
        "first": ["Ra", "Mi", "Del", "San", "Tor", "Lu", "Es", "Va", "Go", "Ri"],
        "mid": ["ma", "ri", "lo", "va", "de", "ta", "ni", "co"],
        "last": ["res", "dano", "vez", "tiago", "mol", "ras", "dinho", "lez"],
    },
    "Kestrelian": {
        "first": ["Jor", "Ka", "Bren", "Al", "Wil", "Har", "Dun", "Fen", "Os", "Cal"],
        "mid": ["ter", "ken", "bur", "wal", "der", "mor", "lan", "ric"],
        "last": ["son", "field", "worth", "brook", "ley", "ford", "shaw", "wick"],
    },
    "Noremic": {
        "first": ["Ei", "Sor", "Mag", "Lau", "Vik", "Jan", "Ny", "Ru", "Kje", "As"],
        "mid": ["nar", "ke", "vald", "lin", "ger", "sten", "mun", "bjo"],
        "last": ["sen", "berg", "strom", "gaard", "vik", "dal", "holm", "lund"],
    },
}

_CITY_SYLLABLES = ["Bel", "Cor", "Dra", "Fal", "Gran", "Hol", "Kar", "Lin", "Mor",
                   "Nor", "Pol", "Quin", "Ros", "Sil", "Tor", "Vel", "West", "Zan"]
_CITY_TAILS = ["mont", "haven", "port", "field", "grad", "ford", "mere", "ton",
               "wick", "burg", "dale", "shore"]
_CLUB_SUFFIXES = ["FC", "United", "City", "Athletic", "Rovers", "Wanderers"]


def player_name(stream: Stream, nationality: str) -> str:
    pool = _SYLLABLES[nationality]
    first = stream.choice(pool["first"])
    if stream.chance(0.5):
        first += stream.choice(pool["mid"])
    last = stream.choice(pool["first"]).lower().capitalize() + stream.choice(pool["last"])
    return f"{first} {last}"


def club_name(stream: Stream) -> str:
    city = stream.choice(_CITY_SYLLABLES) + stream.choice(_CITY_TAILS)
    return f"{city} {stream.choice(_CLUB_SUFFIXES)}"
