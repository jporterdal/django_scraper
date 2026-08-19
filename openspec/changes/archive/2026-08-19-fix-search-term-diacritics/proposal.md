## Why

The search-term relevance filter (`_normalize_for_match` in `tracking/parsers.py`) only lowercases and collapses whitespace before comparing the search term against a vendor title. It does not account for accented characters, so a genuine match is silently dropped whenever the vendor's title carries a diacritic the user didn't type — e.g. searching `"Kili the Resourceful"` fails to match the vendor title `"Kíli the Resourceful"`. The filter this change touches was built to *admit* genuine matches and exclude noise; right now it's excluding genuine matches too.

## What Changes

- `_normalize_for_match` additionally strips combining diacritical marks via Unicode `NFKD` decomposition, so a search term typed without accents matches a vendor title that has them (and vice versa).
- Scope is limited to characters that decompose into a base letter plus combining marks under `NFKD` (e.g. `í` → `i` + combining acute). Characters that are their own distinct letters and do **not** decompose this way (e.g. German `ß`, `æ`, `ø`) are explicitly out of scope and will continue to require an exact match — documented as a deliberate limitation, not a bug, in the spec and in a code comment.
- No new dependency: this uses only the stdlib `unicodedata` module.
- Because `_normalize_for_match` is shared by both the search-term phrase check and the `expected_product_line`/`expected_category` substring checks in `add_result`, all three checks become diacritic-insensitive together with no additional wiring.

## Capabilities

### Modified Capabilities
- `search-term-relevance`: the normalization step used by the "Parser results must contain the search term as a contiguous phrase" requirement now folds combining diacritics before comparison, with an explicit scenario documenting which characters are out of scope.

## Impact

- Code: `tracking/parsers.py` (`_normalize_for_match`).
- Tests: `tracking/tests/test_parsers.py` (new cases for accented match, and for an out-of-scope character like `ß` remaining unmatched).
- No schema, API, or dependency changes.
