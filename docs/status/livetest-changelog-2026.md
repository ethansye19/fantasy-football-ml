# Live-test change log (companion to docs/status/livetest-preregistration-2026.md)

Every change to the repository that a build, a data refresh or the scorer
uses during the chain_v2 live test (2026 weeks 11-18 and 2027 weeks 1-10, D-127)
is listed here before its first use. Allowed paths and the verification each
change needs are defined in the pre-registration ("Frozen code policy").
Any path not listed as allowed in the pre-registration is frozen.
A change on a frozen path is not logged here; it invalidates the test
unless the owner rules otherwise, and the ruling is recorded in
docs/DECISIONS.md.

One entry per commit, newest last:

| Date (UTC) | Commit | Paths | Reason | Verification | Verified by | First week that uses it |
| --- | --- | --- | --- | --- | --- | --- |
| | | | | | | |

Verification for `ffml/data/` entries: the recomputed feature rows
(`player_week`, `team_week`, `dst_week`) for every already-committed week
are byte-identical to the stored ones; record the snapshot ids compared, the
row counts and the result. Entries under `scripts/`, `api/`, `web/` and
docs record "no effect on commitments".
