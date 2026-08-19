## 1. Implementation

- [x] 1.1 In `tracking/parsers.py`, update `_normalize_for_match` to apply `unicodedata.normalize("NFKD", ...)` and strip combining marks (`unicodedata.combining(c) != 0`) before the existing lower/strip/whitespace-collapse logic.
- [x] 1.2 Add an inline comment noting that non-decomposing characters (`ß`, `æ`, `ø`, etc.) are intentionally out of scope, per design.md.

## 2. Tests

- [x] 2.1 Add a test to `tracking/tests/test_parsers.py` asserting an ASCII search term matches an accented vendor title (the `Kíli`/`Kili` case from the spec).
- [x] 2.2 Add a test asserting an accented search term matches a plain-ASCII vendor title.
- [x] 2.3 Add a test asserting a non-decomposing character (e.g. `ß` vs `ss`) is NOT folded and the row is excluded, documenting the scope boundary as intentional.
- [x] 2.4 Run the existing `test_parsers.py` suite to confirm no regressions in the case-insensitivity, whitespace-tolerance, and product_line/category matching tests (all of which route through `_normalize_for_match`).

## 3. Verification

- [x] 3.1 Run the full test suite (`python manage.py test` or project's configured test runner) to confirm no unrelated breakage.
