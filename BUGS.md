# Identified Bugs & Technical Debt Log

This document records the defects, edge cases, and security boundary gaps discovered during rigorous stress, adversarial, and regression testing of **MIS Plugin Forge**.

---

## 1. 🐛 Bug: Data Review Fallback Question Generation Mismatch
- **Severity**: Medium (Unit test failure & logic ambiguity)
- **Files**:
  - Implementation: [`packages/forge-core/src/forge_core/profiling/quality.py`](file:///d:/claude-plugin-poc/packages/forge-core/src/forge_core/profiling/quality.py#L362-L400)
  - Failing Test: [`packages/forge-core/tests/test_profiling_quality.py`](file:///d:/claude-plugin-poc/packages/forge-core/tests/test_profiling_quality.py#L195-L208)
- **Observed Behavior**:
  Running `test_build_data_review_end_to_end_with_no_provider` fails:
  ```text
  FAILED packages/forge-core/tests/test_profiling_quality.py::test_build_data_review_end_to_end_with_no_provider - AssertionError: assert [DataQuestion(...), ...] == []
  ```
- **Root Cause**:
  `build_data_review()` unconditionally calls `generate_questions(findings, provider, hints)`. Even when `provider=None` (meaning no LLM provider is passed), `generate_questions()` falls back to deterministic template questions (`_fallback_question`) for every finding, plus the `GENERAL_NOTES_ID` question. The test `test_build_data_review_end_to_end_with_no_provider` asserts that `review.questions == []` when `provider=None`.
- **Reproduction**:
  ```bash
  uv run pytest packages/forge-core/tests/test_profiling_quality.py::test_build_data_review_end_to_end_with_no_provider
  ```
- **Recommended Fix**:
  Either:
  1. Add an explicit parameter `include_questions: bool = True` to `build_data_review()`, or
  2. Clarify whether `provider=None` should produce deterministic fallback questions or an empty list, and align the test assertion or `build_data_review()` logic accordingly.

---

## 2. 🛡️ Security Gap: PII Scanner Regex Missing Standard Formatting Shapes
- **Status**: RESOLVED BY REMOVAL — the PII scanner (`validation/pii.py`), the
  `is_likely_pii` profiling heuristic, and the semantic-profile redaction
  boundary were removed in the improvement-plan Phase 0. The validation
  harness is now 7 checks. Denied-column enforcement (for pack-declared role
  categories like `free_text`) is unchanged.
- **Severity**: High (PII Leakage in static artifacts/dashboards)
- **Files**:
  - Implementation: [`packages/forge-core/src/forge_core/validation/pii.py`](file:///d:/claude-plugin-poc/packages/forge-core/src/forge_core/validation/pii.py#L20-L26)
- **Observed Behavior**:
  The regex patterns for phone numbers and Aadhaar IDs in `_VALUE_PATTERNS` fail to match common formatted variations:
  - Phone pattern: `r"\b\+?\d{1,3}[-.\s]?\d{10}\b"`
    - ❌ Misses: `(555) 123-4567`, `555-123-4567`, `+1-555-123-4567`, `1-800-555-0199`
  - Aadhaar pattern: `r"\b\d{4}\s\d{4}\s\d{4}\b"`
    - ❌ Misses: `1234-5678-9012` (hyphenated), `123456789012` (unspaced)
- **Impact**:
  Sensitive client phone numbers and national ID numbers formatted with standard hyphens, dots, or parentheses can bypass Check 4 (`pii_scan`) of the validation harness and appear in generated markdown/HTML artifacts.
- **Recommended Fix**:
  Update `_VALUE_PATTERNS` in `packages/forge-core/src/forge_core/validation/pii.py`:
  ```python
  _VALUE_PATTERNS = {
      "email": re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+"),
      "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
      "aadhaar": re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"),
      "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
      "phone": re.compile(
          r"\b(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}|\d{10})\b"
      ),
  }
  ```

---

## 3. ⚠️ Edge Case: Ingestion of 0-Byte Empty Files Produces Phantom `column0`
- **Severity**: Low (User experience / error clarity)
- **Files**:
  - Implementation: [`packages/forge-core/src/forge_core/ingestion/files.py`](file:///d:/claude-plugin-poc/packages/forge-core/src/forge_core/ingestion/files.py#L85-L120)
- **Observed Behavior**:
  When a 0-byte file (e.g. `empty.csv`) is provided as a data source, DuckDB's `read_csv_auto` defaults to creating a single synthetic column named `column0` of type `VARCHAR` with 0 rows. The ingestion stage succeeds and passes the empty dataset downstream, where it eventually fails cryptically during schema fact-checking.
- **Recommended Fix**:
  Add an explicit check in `FileAdapter.ingest` before executing DuckDB commands:
  ```python
  for file_path in files:
      if file_path.is_file() and file_path.stat().st_size == 0:
          raise ValueError(f"Source file {file_path.name!r} is empty (0 bytes).")
  ```

---

## 4. 📝 Test Suite Artifacts
- A comprehensive stress, adversarial, and concurrency test suite has been saved to:
  [`tests/test_hardcore_stress_and_adversarial.py`](file:///d:/claude-plugin-poc/tests/test_hardcore_stress_and_adversarial.py)
- To run this suite:
  ```bash
  uv run pytest tests/test_hardcore_stress_and_adversarial.py -v
  ```
