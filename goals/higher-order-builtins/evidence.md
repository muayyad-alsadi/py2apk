# Evidence: Lower map, filter, and sorted with key

`map`, `filter`, and `sorted` on one array or `ArrayList` lower to a
fresh `ArrayList`. A keyed or reversed sort emits `Owner$cmp$N`.

## Commands and results

| Command | Result |
|---|---|
| `python3 -m unittest discover -s tests -q` | 157 tests, OK, exit 0 |
| `just check` | exit 0 |
| `just lint` | exit 0 |
| `ripwire . --quality-delta --legend=compact` | exit 0, gating=0 |
| `ripwire . --doc-drift --legend=compact` | exit 0, drift=0 |
| `PYTHONPATH=. py2apk build` in `/tmp/ho-app` | exit 0 |
| `java -cp .py2apk/classes Drive` | exit 0, `PASS` |

## Runtime output

`javap -v` on `Host$cmp$6` shows `getfield reverse`, then
`aload_1` / `aload_2` / `astore_1` / `astore_2` when reverse is set,
then `checkcast` before `call` or `intValue`.

```text
mapped=14
positives=4
ascending=123
descending=321
flagTrue=321
flagFalse=123
byLen=98
tie=98
untouched=3120
PASS
```

`mapped` is `1*1+2*2+3*3`. `positives` keeps `1` and `3`.
`ascending` is `123` and `descending` / `flag=true` is `321`.
`flag=false` stays `123`. `byLen` and `tie` return `98` (`"bb"`
first, including equal lengths). `untouched` stays `3120`, so the
input `ArrayList` is not sorted in place.

## Acceptance checks

- `map(square, xs)`, a capturing lambda, and `list[double]` emit
  `call` plus `iaload` or `daload` and `new java/util/ArrayList`.
- `filter` emits `ifeq`. A `double` predicate stores with `dstore`.
- Plain `sorted` and `key=None, reverse=False` call
  `sort(Ljava/util/List;)V` and emit no `$cmp$` class.
- `reverse=True`, a runtime flag, and `key=lambda s: len(s)` emit
  a comparator and `sort(List,Comparator)`.
- A local `Callable` named `map`, `filter`, or `sorted` still calls
  the local.
- Multi-iterable, builtin `str`, void, list-returning, keyword,
  non-final, `filter(None)`, non-bool, unknown keyword, `**kwargs`,
  non-bool `reverse`, and non-orderable elements are rejected.

Verdict: PASS

## Memory Writes

```json
{
  "schema": "sila/memory-writes/v1",
  "goal_slug": "higher-order-builtins",
  "project": "py2apk",
  "prime": {
    "ran": true,
    "command": "sila prime",
    "at": "2026-11-26T07:23:14Z"
  },
  "recall": [
    {"query": "higher order builtin", "matches": 2},
    {"query": "Comparator", "matches": 2}
  ],
  "memos": [],
  "lessons": [
    {"id": "fact_5beebf15"},
    {"id": "fact_2269473f"}
  ],
  "cases": [
    {"id": "fact_c6c20625"}
  ],
  "facts_verify": {
    "exit_code": 0,
    "violation_delta": 0,
    "verified_blocks": 3
  },
  "handoff": {"id": "hoff_9e364d01b2a2"}
}
```
