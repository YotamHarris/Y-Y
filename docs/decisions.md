# Decisions

The game's decision log: one file per category under [decisions/](decisions/), entries
D1, D2, ..., append-only. A later entry may reverse an earlier one; the **Status:** line
under each heading says which holds. `python studio/fe_docs.py category` lists the
categories, `show D12` prints one entry, `new CATEGORY "Title"` appends the next. The
studio's own rules are its log, `studio/docs/decisions/` (S entries).
