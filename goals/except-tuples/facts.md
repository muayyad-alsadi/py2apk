# Facts: Catch more than one exception type

## Architectural Invariants & Constraints
- Ground all implementations in official repository standards.
- Ensure deterministic verification before closing goal.

## File & Interface Contracts
- Relevant files:
  - `Py2APK/compiler_j.py`
  - `tests/test_cli_compile.py`
- An `except` tuple emits one `.catch` per distinct type and one handler.
- One type stores that class. Different types store `java/lang/Throwable`.
- An empty tuple and a nested or starred entry are rejected.
