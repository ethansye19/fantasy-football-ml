# livetest-2026: pre-kickoff commitments for the chain_v2 live test

This orphan branch holds only commitments for the live test of chain_v2 over
2026 weeks 10-18 (D-098, D-099). It shares no history with `main`. The test
design and the frozen code commit are pre-registered on `main`.

- `<season>w<week>/commitment.json`: written by `ffml livetest build-week`
  before the week's first kickoff. It lists the output files with SHA-256,
  the model and calibrator hashes, the snapshot ids, the code commit,
  `created_at`, the first kickoff and a top-level digest.
- `<season>w<week>/player_summary.parquet`: a copy of the projection summary
  when it is under 5 MB. Its SHA-256 is in the commitment.
- `<season>w<week>/MISSED.json`: written after kickoff when no commitment was
  pushed in time. How a missed week counts is decided by the test design.
- `rehearsal/...`: rehearsal commitments. They are not part of the test.

The commit timestamps are set by the pushing machine. The external timestamp
is GitHub's push record (`gh api repos/<owner>/<repo>/activity?ref=livetest-2026`).
Outputs are labeled "not approved" and are never shown in the app (D-099).
Pushed by `scripts/livetest_week.py`; never force-pushed.
