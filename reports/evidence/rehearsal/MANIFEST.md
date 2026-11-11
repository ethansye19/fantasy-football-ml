# Live-test rehearsal evidence

Raw evidence behind reports/livetest_final_rehearsal.md. Synthetic data
only (each world builds its own synthetic stores); no real or restricted
data.

| file | run | SHA-256 of the bytes as written | SHA-256 with LF line ends (as git stores it) | notes |
| --- | --- | --- | --- | --- |
| run6_X4799913_partial.jsonl | run 6, X = 4799913, base Z = 765fb1a, two workers, 2026-11-11 17:43-20:20 UTC | 4174e4e4d7444100956198c16f892a09030e932c13b7f8bfdd966dd72cb8ccad | 0362c4868b4a15929566b60cba43c5cc0c8b4c9def10a6915048834709290ee9 | stopped by the owner's pause; 70 records, nine of ten worlds finished, B2 only started; not used for the verdict of the rehearsal, which comes from run 7 (D-130) |

The file was written with CRLF line ends (Windows text-mode append), and
git normalises it to LF (`* text=auto eol=lf`), so a checkout gives the
LF hash.
