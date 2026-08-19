## Context

`JSONSearchParser.add_result` (`tracking/parsers.py`) filters vendor rows by requiring the search term to appear as a contiguous, normalized substring of the row title (`search-term-relevance` capability). The same normalization helper, `_normalize_for_match`, also backs the `expected_product_line`/`expected_category` substring checks.

`_normalize_for_match` currently does `.strip().lower()` plus whitespace collapsing only. It performs no Unicode normalization, so a search term typed in plain ASCII (`"Kili the Resourceful"`) fails to match a vendor title carrying a diacritic (`"Kíli the Resourceful"`), even though the phrase content is identical.

## Goals / Non-Goals

**Goals:**
- Fold combining diacritics (accents) so ASCII-typed search terms match accented vendor titles, and vice versa.
- Keep the fix inside `_normalize_for_match` so all three call sites (term/title, product_line, category) get it automatically.
- Use only the Python standard library (`unicodedata`) — no new dependency.

**Non-Goals:**
- Handling characters that are their own distinct letters rather than "base letter + combining mark" under Unicode `NFKD` decomposition — e.g. German `ß`, `æ`, `ø`, Turkish dotless `ı`. These stay exact-match only.
- Transliteration of non-Latin scripts (e.g. Japanese/Korean vendor titles) to Latin equivalents.
- Any change to the phrase/substring matching logic itself (contiguous-substring semantics are unchanged).

## Decisions

**Decision: `unicodedata.normalize("NFKD", ...)` + strip combining marks (Unicode category `Mn`), inside `_normalize_for_match`.**

```python
import unicodedata

def _normalize_for_match(value):
    text = unicodedata.normalize("NFKD", str(value).strip().lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text)
```

NFKD decomposes precomposed accented characters (`í` = U+00ED) into a base letter plus a combining mark (`i` + U+0301 COMBINING ACUTE ACCENT). Filtering out characters where `unicodedata.combining(c) != 0` then drops the accent, leaving the base letter. This covers the large majority of Latin diacritics used in card names (á, é, í, ó, ú, ñ, ü, ç, ê, à, ō, etc.) with a few lines of stdlib code and no new dependency.

*Alternatives considered:*
- **`unidecode`/`text-unidecode` (transliteration library)**: broader coverage (handles `æ`→`ae`, `ø`→`o`, CJK→ASCII romanization attempts) but adds a dependency, and its output is a lossy best-effort transliteration rather than a principled decomposition — it will silently mangle non-Latin scripts into ASCII noise rather than leaving them alone. Rejected as disproportionate to the reported problem (a single accented Latin letter).
- **Manual character-mapping table** (`{"æ": "ae", "ø": "o", "ß": "ss", ...}`): would close the gap this design leaves open, but is unbounded in principle (new mappings needed as new cards/vendors surface new characters) and turns a filter into a maintenance burden. Deferred until a concrete, observed card name motivates it.
- **`str.casefold()` instead of/with `.lower()`**: `casefold()` is a strictly stronger normalization (e.g. maps `ß` → `ss`) but folding `ß` this way would be an isolated special case bundled into a change that's explicitly about diacritics, not case-folding semantics. Not adopted here to keep this change narrowly scoped; left as a candidate for a future change if `ß` (or similar) is reported.

**Decision: place the fold inside `_normalize_for_match`, not a separate helper.**

Both the search-term phrase check and the `expected_product_line`/`expected_category` checks already route through this one function (`add_result` in `tracking/parsers.py`). Changing it there means every existing and future caller inherits diacritic-insensitivity with zero additional wiring — consistent with how the original relevance filter was built to apply uniformly via the shared base class.

## Risks / Trade-offs

- **[Risk]** Folding diacritics could cause two *genuinely different* card names that differ only by accent to be treated as relevant to the same search. → **Mitigation**: This is the same class of trade-off already accepted for case-insensitivity in the existing filter; no such collision has been observed in the vendor data reviewed for the original `search-term-relevance` change, and the filter remains a coarse relevance gate, not an exact-identity check.
- **[Risk]** Out-of-scope characters (`ß`, `æ`, `ø`) could surprise a future maintainer who expects "diacritics are handled" to mean "all special Latin characters are handled." → **Mitigation**: explicit spec scenario and inline code comment document the boundary as a deliberate decision, not an oversight.

## Migration Plan

Pure behavior change in a pure function with no persisted state or schema involved — ship as a normal code change. No rollback complexity beyond reverting the commit if an unexpected regression in matching behavior surfaces.

## Open Questions

None outstanding; scope was explicitly narrowed to NFKD-decomposable characters per proposal.
