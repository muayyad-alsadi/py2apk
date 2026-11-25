# Evidence: Lower set literals to HashSet

A non-empty homogeneous set literal lowers to `java.util.HashSet`.
Primitives are boxed. `len` and `if` use `HashSet.size()`. The set is
never an array.

## Commands and results

| Command | Result |
|---|---|
| `python3 -m unittest discover -s tests -v` | 98 tests, OK |
| `just check` | exit 0 (`ty check Py2APK` passed) |
| `just lint` | exit 0 (`ruff check .` clean) |
| `ripwire . --doc-drift --legend=compact` | `drift="0"`, `clean="64"` of 64 docs |
| `ripwire . --quality-delta` | `gating="0"` (4 catalog and handler acks) |
| `sila facts verify` | exit 0; 23/23 blocks; 3 pre-existing missing files |

The three missing references are `data/api-versions.xml`,
`Py2APK/compiler_j.py`, and `tests/test_cli_compile.py`. This change
did not add them.

## Acceptance checks

- `values = {"a", "b"}` emits `new java/util/HashSet` and two
  `add(Ljava/lang/Object;)Z` calls, with no `anewarray` and no
  `ArrayList`: `test_lowers_a_string_set`.
- `{1, 2}` boxes with `Integer.valueOf`: `test_boxes_int_elements`.
- `{1, "a"}` fails with `a set needs one element type`. `{None}` fails
  with `a set element needs a value type`. `{*items}` fails with
  `set unpacking is unsupported`. A list mix still says
  `a list needs one element type`.
- `len(values)` emits `HashSet.size()I`. `if not values:` emits
  `ifne`. `if values:` emits `ifeq`. `test_len_and_truth_use_size`.
- `set()` still fails with `unsupported builtin set`.
- A field annotated `HashSet` stores the literal. `Objects.requireNonNull`
  accepts it as `Object`.

## Deviations from plan

- `docs/README.md` only links the docs. It does not list the subset, so
  it was left unchanged.
- Set and `ArrayList` construction share `_fill_collection`. List error
  text stays `list element has type ...`.

## Memory Writes

`sila` MCP `prime_context` ran at session start. `sila recall` for
`set literal HashSet _element_type _array_list` returned 2 matches.

```json
{"schema":"sila/memory-writes/v1","goal_slug":"hashset-literals","prime":{"ran":true},"recall":[{"query":"set literal HashSet _element_type _array_list","matches":2}],"memos":[{"id":"fact_35df2280"},{"id":"fact_318e948f"}],"lessons":[],"cases":[{"id":"fact_d9974752"}],"facts_verify":{"exit_code":0,"violations":3,"preexisting_missing":["data/api-versions.xml","Py2APK/compiler_j.py","tests/test_cli_compile.py"]},"handoff":{"id":"hoff_56dd6f4e09e0"}}
```

Verdict: PASS
