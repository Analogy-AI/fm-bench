# Security and benchmark-integrity reporting

Contact: **support@analogyai.org**. Please report privately first and give us a
reasonable window to fix before publishing.

## Two kinds of report

**1. Ordinary software vulnerabilities.** The usual: anything that lets code
run where it shouldn't, or exposes credentials. Note that `run_web.py` is a
local development server for a single human player — it is not hardened and
should not be exposed to a network.

**2. Benchmark-integrity issues.** For a benchmark these matter as much as
memory safety, and we would rather hear about them from you than discover them
in a leaderboard run:

- **Information leaks** — any route by which an agent can recover a true player
  ability (or any hidden trait) that the observation layer is supposed to hide:
  through `engine/obs/` output, error hints, wage or price channels, negotiation
  counter-offers, timing, or the stop-packet schedule. Historic examples we have
  already fixed include inverting initial wages to solve true ability, and
  using unbudgeted offer probes as a midpoint oracle.
- **Score exploits** — a way to inflate the composite that isn't management
  skill: an arbitrage in early settlement, a cash-cycling trick, an
  unintended-but-legal action loop.
- **Replay or signing weaknesses** — anything that lets a submitted action log
  verify against a score it did not actually produce, or that makes replay
  diverge from the original run.

## What helps

A seed, a track length, and either an action log or a short script that
reproduces it. Because runs are deterministic, a seed plus an action log is
usually a complete and exact report.

## Scope note

This repository is the open Solo benchmark. The hidden official seed sets, the
world salt, and the Open Track signing secret are never in the repo; they are
read from the environment at run time. If you believe one has been exposed,
treat that as urgent and say so in the subject line.
