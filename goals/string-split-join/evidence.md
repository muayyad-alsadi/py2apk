# Evidence: string-split-join

Verdict: PASS

Commands from `/tmp/py2apk-string-split-join`, each exit 0:

- `just check`
- `just lint`
- `python3 -m unittest discover -s tests -v` (115 tests)

Bytecode the new tests assert:

- Literal `split(",")` and `split("|")` emit `Pattern/quote`,
  `iconst_m1`, and
  `String/split(Ljava/lang/String;I)[Ljava/lang/String;`.
  The one-arg `String/split` is absent.
- `text.split(sep)` emits the one-arg `String/split` and does not
  emit `Pattern/quote`.
- `",".join` of a `str` list or tuple emits
  `invokestatic py2apk/str_join(Ljava/lang/String;[Ljava/lang/String;)Ljava/lang/String;`.
  `ldc ","` is before `anewarray`, which is before `str_join`.
  No `String/join`.
- An appended list emits `ArrayList/toArray` and `str_join`.
  A list that is only joined does not emit `ArrayList`.
- Cached `py2apk.j` contains `str_join` and `StringBuilder/append`.

Shared docs, certification, and `goals/README.md` are left for the
parent. Phase 5 and the certify steps in `plan.md` stay open.
