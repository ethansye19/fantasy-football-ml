# livetest-2026: pre-kickoff commitments for the chain_v2 live test

This orphan branch holds only commitments for the live test of chain_v2 over
the pre-registered window (2026 weeks 11-18 and 2027 weeks 1-10, D-127; D-098,
D-099, D-105 to D-107). It shares no history with `main`. The test design and the frozen code commit are pre-registered on `main`.

Layout:

- `<season>w<week>/commitment.json`: written by `ffml livetest build-week`
  before the week's first kickoff. It lists the output files with SHA-256,
  the model and calibrator hashes, the snapshot ids, the code commit,
  `created_at`, the first kickoff and a top-level digest.
- `<season>w<week>/player_summary.parquet`: a copy of the projection summary
  when it is under 5 MB. Its SHA-256 is in the commitment.
- `<season>w<week>/MISSED.json`: the week was missed: no commitment was on
  this branch at the first kickoff. It carries the reason
  (`technical_failure` with the last error, or `machine_offline` when no
  runner log exists in the 25 hours before kickoff), the runner log excerpt,
  the miss number, the allowed count and the test state.
- `<season>w<week>/late/commitment.json`: a commitment built before kickoff
  but pushed after it. Projections that exist are never withheld; a late
  commitment is not part of the test.
- `<season>w<week>/sealed_hashes.json`: the SHA-256 of the week's sealed
  metric parts, published after the week is scored. The parts themselves
  stay local and sealed until the final evaluation.
- `STATUS.md`: the per-week state, regenerated at every push.
- `rehearsal/...`: rehearsal commitments. They are not part of the test.

Missed weeks (D-106, D-111): up to the pre-registered number of missed weeks are excluded from
scoring without extending the window; one more makes the test INVALID. The
allowed count comes from the pre-registration (`missed_weeks.allowed`). The
deadline is the official first kickoff of the week, including Thanksgiving
and other early games.

Dry runs go to a separate branch (`livetest-dryrun`), never here.

The commit timestamps are set by the pushing machine. The external timestamp
is GitHub's push record (`gh api repos/<owner>/<repo>/activity?ref=livetest-2026`).
Outputs are labeled "not approved" and are never shown in the app (D-099).
Pushed by `scripts/livetest_week.py`; never force-pushed. Only `README.md`
and `STATUS.md` are ever rewritten; every other file is immutable.
