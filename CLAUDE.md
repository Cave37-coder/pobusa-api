# Project rules

## pokemart-api is read-only reference data — never a stock/data dependency

pokemart-api (PokeBulk SA's own webstore backend) is a **completely
standalone system**. PoBuSA is only ever allowed to pull Pokemon *card
info* from it (name, set, image, market price) for browsing/search — the
same read-only pattern already established in `services.py`'s
`search_cards()` and `catalog_browse_views.py`'s Pokemon singles proxy.

Michael, 2026-08-19: "pokemart-api is stand alone, you only pulling card
info from there! (Set in rules now!!!!) pobusa pos will hold it's only
stock in a seperate db for customers, never touching anything involved
with pokemart-api ever!!!!!!!!!!!!!!!!!!!!!!"

Concretely, this means:

- **Never read or filter on pokemart-api's own stock fields** (`stock`,
  `in_stock`) for any PoBuSA business logic — that's PokeBulk's own
  webstore inventory, not a PoBuSA Client's physical stock. Using it for
  a PoBuSA store's in-stock enforcement would show the wrong numbers
  entirely (a different business's stock, not this store's).
- **Never write to pokemart-api** — no POST/PATCH/PUT of any kind, ever.
  PoBuSA has never done this and must never start.
- **All real stock/inventory for every product PoBuSA sells — including
  Pokemon singles — lives in PoBuSA's own database**, in tables scoped to
  the Client/store, completely separate from pokemart-api's tables. This
  applies even though Pokemon *catalog* data (name/set/image/price) is
  proxied live rather than synced locally — the catalog lookup and the
  stock ledger are two different concerns, and only the catalog lookup
  is allowed to touch pokemart-api at all.
- If a feature needs "is this in stock" for a Pokemon product, the
  answer must come from a PoBuSA-owned table keyed by the pokemart SKU/id
  as a plain reference string (same pattern `CardStockLine.card_id`
  already uses) — never from a live call into pokemart-api's own stock
  field.
