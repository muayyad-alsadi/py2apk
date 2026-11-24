# Facts: Reject APIs newer than minSdkVersion

## Architectural Invariants & Constraints
- Ground all implementations in official repository standards.
- Ensure deterministic verification before closing goal.

## File & Interface Contracts
- Relevant files:
  - `Py2APK/compiler_j.py`
  - `Py2APK/cli_fetch_platform.py`
  - `tests/test_cli_compile.py`
