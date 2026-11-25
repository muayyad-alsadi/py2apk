# Plan: Lower literal split and join

Decisions are locked in [`facts.md`](facts.md). Implement them in
`_CallsMixin._call` (`Py2APK/compiler_j/calls.py:742`) after the
receiver is on the stack and before `_find_method`. Do not edit
`_STRING_METHOD_ALIASES`. Do not call `String.join`. Do not add
`py2apk.str_split`.

## Execution Steps

### Phase 1 — One decision helper

- [x] Add `_str_method(self, node: ast.Call) -> tuple[str, str] | None`
      on `_CallsMixin`. Return `None` unless `node.func` is
      `ast.Attribute`. Return `(attr, receiver)` when `attr` is
      `split` or `join` and `_type_of(node.func.value)` is `_STRING`.
      Otherwise return `None`. Use `_type_of` only. Do not emit here.
- [x] Add `_literal_str(self, node: ast.expr) -> str | None`. Return
      `node.value` when `node` is `ast.Constant` and the value is
      `str`. Otherwise return `None`.
- [x] Add `_string_sequence(self, descriptor: str) -> str | None`.
      Return `"array"` for `_STRING_ARRAY`. Return `"arraylist"` for
      `arraylist:Ljava/lang/String;`. Otherwise return `None`.

### Phase 2 — Literal split

- [x] In `_call`, when `_str_method` is `("split", _)` and the receiver
      descriptor from `_expression` is `_STRING`, own the call.
      Return on every shape except one non-literal argument:
      - Keywords: `self.error(node, "split keywords are unsupported")`.
      - Not exactly one positional arg: if zero,
        `"split needs a separator"`; if more,
        `"split maxsplit is unsupported"`.
      - The one arg is `Constant` `None`:
        `"split(None) is unsupported"`.
      - `_literal_str` is `""`: `"empty separator"`.
      - `_literal_str` is a non-empty `str`: quote, then
        `split(Ljava/lang/String;I)` at limit `-1`, as in
        [`facts.md`](facts.md). Pass `node` to `_reject_newer_api`
        for `quote` and for `split`. Return `_STRING_ARRAY`.
      - Any other single arg: fall through to the existing one-arg
        `invokevirtual` `String.split(Ljava/lang/String;)`. Do not
        quote it.
- [x] In `_type_of_call`, use the same guards and the same messages.
      Literal branch returns `_STRING_ARRAY` without emitting. The
      non-literal branch keeps the catalog lookup of the one-arg
      `split`.
- [x] Leave `_java_method_name` unchanged so the regex path still
      looks up `split`.

### Phase 3 — Join helper and emit

- [x] In `Py2APK/stdlib/py2apk.py`, import `StringBuilder` from
      `java.lang` next to `Exception`. Add `str_join` with the body
      in [`facts.md`](facts.md). Full annotations. Do not name it
      `join`. Do not use `+`.
- [x] `touch Py2APK/stdlib` so `ensure_stdlib_jasmin` rebuilds
      `~/.cache/py2apk/stdlib_j`.
- [x] In `_call`, when `_str_method` is `("join", _)`:
      - Keywords: `"join keywords are unsupported"`.
      - Not exactly one positional arg:
        `"join takes one positional argument"`.
      - `_type_of` the argument. `"array"`: the value is already
        evaluated by the emit step below. `"arraylist"`: after
        `_expression`, call `_emit_to_array(_STRING)`. Anything else,
        including a non-`str` array: `"join needs a list or tuple of str"`.
      - Do not evaluate the receiver again. It is already on the
        stack under `parts`.
      - Emit `invokestatic py2apk/str_join` with descriptor
        `(Ljava/lang/String;[Ljava/lang/String;)Ljava/lang/String;`.
      - Return `_STRING`.
- [x] In `_type_of_call`, the same guards, no emit, return `_STRING`.
      An empty list or tuple still raises from `_element_type`
      (`an empty list has no element type` / tuple). Do not special-case
      that message.
- [x] In `_signature_for_call`, when the call is `str.join` with one
      positional `str` sequence (`"array"` or `"arraylist"` from
      `_type_of` / `_preview_type`), return a `_MethodSignature` whose
      only parameter is `_STRING_ARRAY`. This is only so
      `_accepts_list_array` (`collections.py:793`) keeps an unappended
      list as a `String[]`. Do not `invokestatic` that object.

### Phase 4 — Tests

- [x] Extend `_alias_catalog` (`tests/_compile_helpers.py:178`) with
      `java/util/regex/Pattern` `quote`
      `(Ljava/lang/String;)Ljava/lang/String;` `invokestatic`, and
      `java/lang/String` `split`
      `(Ljava/lang/String;I)[Ljava/lang/String;` `invokevirtual`.
      Keep the existing one-arg `split` row. Do not add `String.join`
      or `py2apk/str_join`.
- [x] Update `test_lowers_fstrings_builtins_and_constant_comparisons`.
      `parts = label.split(",")` is a literal. The `run` method must
      contain `Pattern/quote`, `iconst_m1`, and
      `String/split(Ljava/lang/String;I)[Ljava/lang/String;`. It must
      not contain the one-arg
      `String/split(Ljava/lang/String;)[Ljava/lang/String;`.
- [x] Add a compile test for `label.split("|")` with the same three
      instructions, so a regex metacharacter is quoted.
- [x] Add `text.split(sep)` with `sep: str`. That method emits the
      one-arg `String/split` and does not emit `Pattern/quote`.
- [x] `assertRaises(JasminCompilerError)` for `split()`, `split(None)`,
      `split("")`, `split(",", 1)`, and `split(sep=",")`. Messages are
      the strings in Phase 2.
- [x] `",".join(["a", "b"])` and `",".join(("a", "b"))` emit
      `invokestatic py2apk/str_join` with descriptor
      `(Ljava/lang/String;[Ljava/lang/String;)Ljava/lang/String;`.
      In that method, `ldc ","` occurs before `anewarray`, and
      `anewarray` occurs before `str_join`. No `String/join`.
- [x] With `_compiled_class(..., collections=True)`, a list that is
      `append`ed and then joined emits `ArrayList/toArray` and
      `str_join`. A list that is only joined does not emit `ArrayList`.
- [x] `",".join([1, 2])` and `",".join(items)` for `items: list[int]`
      raise. `",".join([])` still raises the empty-list error.
- [x] In `tests/test_cli_compile_stdlib.py`, after
      `ensure_stdlib_jasmin()`, cached `py2apk.j` contains
      `str_join` and `StringBuilder/append`. Do not change
      `shlex.split`.

### Phase 5 — Docs

- [x] `docs/covered.md` Strings section (the sample ends near
      line 462): add a literal `split` and a `join` example. State
      the result of `split` is a `String[]`, `join` is
      `py2apk.str_join`, and a non-literal separator stays regex
      `split`.
- [x] `docs/not-covered.md:118`: replace the split/join sentence.
      Keep `` `math`, `time`, `base64`, and `re.compile` `` on line 119
      so `goals/math-time/facts.md` does not move. State the forms
      that stay out: no-arg `split`, `split(None)`, empty separator,
      `maxsplit`, keywords, non-literal regex split, empty literals,
      non-`str` sequences.
- [x] `ROADMAP.md:152`: those two bullets are no longer "left as the
      Java method". Replace them with the same number of lines so
      later anchors stay put. Point at the subset above. Do not tell
      the reader to call `String.join`.
- [x] `README.md:100`: mention literal `split` and `join` without
      claiming the full Python signatures.
- [x] Run `ripwire . --doc-drift --legend=compact`. Fix every moved
      anchor until `drift="0"`. That may be a line number inside
      another goal's facts. Change only the anchor, not that goal's
      design.

### Phase 6 — Verification

- [x] From the repo root, exit code 0:
      `python3 -m unittest discover -s tests -v`,
      `just check`, and `just lint`.
- [x] Write `goals/string-split-join/evidence.md` with a standalone
      `Verdict: PASS` line. Record the commands and the bytecode
      each new test asserts.

### Phase 7 — Attribution and registry closure

- [ ] Check every task in this file.
- [x] Commit with `(goals/string-split-join/goal.md)` or
      `[goal: string-split-join]` under `feat:`, `fix:`, `refactor:`,
      `perf:`, or `chore:`.
- [ ] Certify with
      `sila goals certify string-split-join` with
      `--evidence=goals/string-split-join/evidence.md`
      or `sila goals audit string-split-join`.
- [ ] Run `sila goals` so `goals/README.md` matches `goal.md`.
- [ ] `sila goals --json` reports `status: "implemented"`,
      `verification_status: "verified"`, `unverified_count: 0`, and
      `git status` is clean.

## Out of scope

- `maxsplit`, keywords, `split()`, `split(None)`, empty separator,
  whitespace splitting, and regex semantics for a non-literal
  separator.
- Making a split result mutable. It stays `_STRING_ARRAY`.
- `String.join`, `Pattern.compile`, and `re.split`.
- `bytes`, generators, sets, dicts, and `sep.join([])`.
- `Py2APK/stdlib/shlex.py`.
- Changing `_STRING_METHOD_ALIASES` or moving dispatch to `expr.py`.
