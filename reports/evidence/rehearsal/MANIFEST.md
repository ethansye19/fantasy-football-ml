# Live-test rehearsal evidence

Raw evidence behind reports/livetest_final_rehearsal.md. Synthetic data
only (each world builds its own synthetic stores); no real or restricted
data.

| file | run | SHA-256 of the bytes as written | SHA-256 with LF line ends (as git stores it) | notes |
| --- | --- | --- | --- | --- |
| run6_X4799913_partial.jsonl | run 6, X = 4799913, base Z = 765fb1a, two workers, 2026-11-11 17:43-20:20 UTC | 4174e4e4d7444100956198c16f892a09030e932c13b7f8bfdd966dd72cb8ccad | 0362c4868b4a15929566b60cba43c5cc0c8b4c9def10a6915048834709290ee9 | stopped by the owner's pause; 70 records, nine of ten worlds finished, B2 only started; not used for the verdict of the rehearsal, which comes from run 7 (D-130) |
| run7_X4799913.json | run 7, X = 4799913, base Z = 765fb1a, two workers, 2026-11-11 22:16 to 2026-11-12 00:23 UTC | b6ddb854849308bb432a10a6147bda3e582ef5215376be13cd0bead16e413327 | 5a4d0df6e3e5de0f538e5611e08863afc2315002d389cac0f485f00e689c4717 | the full evidence of all ten worlds; 43 passed, 1 failed (a test assertion defect, D-131), 1 skipped (the optional 5,000-draw run) |
| run7_X4799913_partial.jsonl | run 7, streamed as it ran | b52b1bcd2cd41943218340b1f28e9561cbaf7c0035c23d1204445ea5639f4d26 | 37629735c3a7ee7801bc6e72437500fe8b96c9b5def0fb571c2f454ca15a13a0 | all 29 final and refusal records of run 6 recur with identical verdicts and lines |
| run7_X4799913_pytest.txt | run 7, pytest output (`-v -rA`) | 645ce4f7e0326b0199f9e30f538e2874eafd6afe876856b27d6e9a7ecbd6bf39 | 6b46fb8d9501f0e1e1cd9fc815915624a75f18780dc6adfced180fbf3f52d524 | the run log (renamed from .log, which git ignores) |
| run8_X4799913_partial.jsonl | run 8, X = 4799913, base Z = 34a6644, two workers, 2026-11-12 00:26 to about 01:26 UTC | 72ba117af89c0876be68f6c36bfb2a8adf899a8397bc1665abaca2f335472dc5 | eae5bd8f50d98797f3100e46137940f64f2f2f61eca79625b502e17fcd36d999 | stopped by Claude Code's low-memory reaper after 2 of 10 worlds (G, A; no errors); its 20 final and refusal records equal run 7's once commit shas are masked (the base differs) (D-132) |
| run9_X4799913.json | run 9, X = 4799913, base Z = cfcfcd6, one worker, 2026-11-12 14:53 to 19:48 UTC | 1a405e8761aaf979126668b94b0f751593fcb0f538b727eb87add362ee7ee50d | 8b01bac360938bb131ea4545d5e7d8695ee6a05e16ce4f34becd105d4e8232bb | the confirmation run at the fixed test code (D-131, D-133): 44 passed, 1 skipped (the optional 5,000-draw run); all 30 final and refusal records equal run 7's in verdict, exit code, line values and check statuses once commit shas are masked; check texts that count the commits and allowed paths after X differ because the base differs |
| run9_X4799913_partial.jsonl | run 9, streamed as it ran | 5345a9da8413089210517e03319a1bd35c7fa462d12522c77b3b23cae90ad155 | b2d0c0467369fadf23b67b5ad3d877d2e5fbb8eef8eafc424e92665f9090caf0 | 72 records |
| run9_X4799913_pytest.txt | run 9, pytest output (`-v -rA`) | ada5162cf8503d9cc8cb52e76f5680e32a3a6dbc53bd9bbd4158788ab35aac3f | c13445c2dad8c9c55b4ac4f00135b6267722b34d64f0d0a374cd54607580a949 | the run log (renamed from .log, which git ignores) |

The files were written with CRLF line ends (Windows text-mode append), and
git normalises it to LF (`* text=auto eol=lf`), so a checkout gives the
LF hash. The evidence names the scorer's rules (for example L13 and L15 against the
consensus), but every metric in it is computed on synthetic data.
