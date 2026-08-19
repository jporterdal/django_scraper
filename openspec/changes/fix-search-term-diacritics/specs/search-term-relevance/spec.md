## MODIFIED Requirements

### Requirement: Parser results must contain the search term as a contiguous phrase
`JSONSearchParser` SHALL reject a candidate row — omit it from `self.results` — unless the parser's search term, normalized (case-folded, whitespace-collapsed and stripped, with combining diacritical marks removed via Unicode `NFKD` decomposition), appears as a contiguous substring of the row's title, normalized the same way. Requiring the term's words to be present and adjacent in order — not merely present anywhere in the title — is deliberate: it is what distinguishes a genuine item match from a title that happens to share words with the term. This check SHALL run inside the shared `add_result` method so every subclass (including future ones) inherits it without additional wiring.

Diacritic folding is limited to characters that decompose into a base letter plus a combining mark under `NFKD` (e.g. `í` → `i` + combining acute accent). Characters that are their own distinct letters rather than an accented variant of a base letter — for example German `ß`, `æ`, or `ø` — are NOT folded and continue to require an exact character match. This is a deliberate scope boundary, not an oversight: it keeps normalization to a well-defined, stdlib-only operation rather than growing into a general transliteration engine.

#### Scenario: Row title contains the search term as a contiguous phrase
- **WHEN** a parser with `term="Fire Dragon"` adds a row titled `"Fire Dragon (POR)"`
- **THEN** the row is included in `self.results`

#### Scenario: Row title contains only one search-term word
- **WHEN** a parser with `term="Lightning Bolt"` adds a row titled `"Lightning Greaves (Foil)"` (contains "Lightning" but not "Bolt")
- **THEN** the row is excluded from `self.results`

#### Scenario: Row title contains none of the search-term words
- **WHEN** a parser with `term="Lightning Bolt"` adds a row titled `"Counterspell (Masters 25)"`
- **THEN** the row is excluded from `self.results`

#### Scenario: Row title contains every search-term word, but not as a contiguous phrase
- **WHEN** a parser with `term="Fire Dragon"` adds a row titled `"Dragon Fire (0130)"` (a real, different card from a different game — Disney Lorcana — that shares both words in reversed order; confirmed via live `wt` vendor search, which also returned `"Deck Protectors - Dragon Shield Matte Dual Fire Horse 100ct"`, an unrelated accessory, under the same reversed/split-word pattern)
- **THEN** the row is excluded from `self.results`

#### Scenario: Phrase matching is case-insensitive
- **WHEN** a parser with `term="lightning bolt"` adds a row titled `"LIGHTNING BOLT (Revised Edition)"`
- **THEN** the row is included in `self.results`

#### Scenario: Phrase matching tolerates incidental whitespace in the term
- **WHEN** a parser with `term="The Unbeatable Squirrel Girl "` (trailing space) adds a row titled `"The Unbeatable Squirrel Girl (MSH) - Foil"`
- **THEN** the row is included in `self.results`

#### Scenario: Blank search term disables the check
- **WHEN** a parser is constructed with `term=""` and adds a row with any title
- **THEN** the row is included in `self.results` (nothing to require, nothing to reject against)

#### Scenario: Phrase matching folds combining diacritics
- **WHEN** a parser with `term="Kili the Resourceful"` (no accent) adds a row titled `"Kíli the Resourceful"` (accented í)
- **THEN** the row is included in `self.results`

#### Scenario: Phrase matching also folds diacritics in the search term itself
- **WHEN** a parser with `term="Kíli the Resourceful"` (accented í) adds a row titled `"Kili the Resourceful"` (no accent)
- **THEN** the row is included in `self.results`

#### Scenario: Non-decomposing special characters are not folded
- **WHEN** a parser with `term="Straße"` (German eszett) adds a row titled `"Strasse"` (plain "ss")
- **THEN** the row is excluded from `self.results`, since `ß` does not decompose into a base letter plus a combining mark under `NFKD` and is therefore out of scope for this normalization
