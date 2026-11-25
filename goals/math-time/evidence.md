# Evidence: Lower math and time

`import math` aliases `java/lang/Math` for the allow-list. Calls
use the Java name and a `(D)D` or `(DD)D` descriptor. `I` and `F`
arguments widen with `i2d` or `f2d`. `fabs` is `abs(D)D`. Builtin
`abs(int)` stays `abs(I)I`. `time.py` is the packaged fallback:
`time()` is `l2f` of `currentTimeMillis` then `fdiv` by `1000.0`,
and `sleep` takes `int` seconds then `i2l` into `Thread.sleep(J)V`.

Shared docs (`ROADMAP.md`, `docs/covered.md`, `docs/not-covered.md`)
were left untouched for a later sequential edit. Phase 6 of
`plan.md` stays open. Certification is left to the parent.

## Commands and results

| Command | Result |
|---|---|
| `python3 -m unittest discover -s tests -v` | 120 tests, OK |
| `just check` | exit 0 |
| `just lint` | exit 0 |
| `sila goals claim math-time` | refused: file conflict with slice-values |

## Acceptance checks

- `math.sqrt(4.0)` emits `f2d` and `Math/sqrt(D)D`, and no `d2f`.
- `math.sqrt` of an `int` emits `i2d` and the same invoke.
- `import math as m` / `m.pow(2.0, 3.0)` emits `Math/pow(DD)D`.
- `from math import radians as rad` of a `double` emits
  `Math/toRadians(D)D` and no `i2d`, `f2d`, or `d2f`.
- `fabs` of an `int` emits `Math/abs(D)D` and `i2d`, not
  `Math/abs(I)I`. Bare `abs` of an `int` still emits `Math/abs(I)I`.
- `from math import factorial` raises
  `unsupported import math.factorial`.
- `math.factorial(1.0)` raises
  `unknown method signature java/lang/Math.factorial`.
- `import time` / `time.time()` emits `invokestatic time/time()F`.
- `from time import sleep` / `sleep(1)` emits
  `invokestatic time/sleep(I)V`.
- `Py2APK/stdlib/time.py` emits `currentTimeMillis()J`, `l2f`,
  `fdiv`, `Thread/sleep(J)V`, and `i2l`.
- `src/math.py` wins `resolve_module`. An empty project does not
  resolve `math` to a fallback `.py`.

Verdict: PASS

## Memory Writes

```json
{
  "schema": "sila/memory-writes/v1",
  "goal_slug": "math-time",
  "project": "py2apk",
  "prime": {"ran": true, "command": "prime_context"},
  "recall": [
    {
      "query": "math time lowering",
      "matches": 0
    },
    {
      "query": "math module or time module compile",
      "matches": 11
    }
  ],
  "memos": [{"id": "fact_f5c4ba3e"}],
  "lessons": [],
  "cases": [
    {
      "id": "fact_7c7c4bb1",
      "intent": "Lower math calls to java.lang.Math and time to a packaged fallback"
    }
  ],
  "facts_verify": {"exit_code": 0},
  "handoff": {
    "id": "hoff_f2d8e6d6ddd1",
    "summary": "math aliases java.lang.Math and returns double. time.time and time.sleep are the packaged fallback."
  }
}
```
