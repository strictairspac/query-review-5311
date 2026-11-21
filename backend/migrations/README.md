Schema version 2: adds ticketless reconciliation.

- `batches.mode` (`exact` | `ticketless`, default `exact`): chosen when a batch is created. `exact` keeps the original multiset comparison only.
- `claims`: one row per accepted ticketless claim. Stores the actor, the idempotency key (unique), the claimed pairs (`pairs`, JSON text), and the conclusion computed by merging all accepted claims (`status`, `conclusion`, JSON text). Basis and conclusion take effect together in the same row and commit.
- `batches.status` gains `pending_claim`; a claim may set it to `passed` (unique full matching) or keep `pending_claim`.
- `evidence.result.ticketless` holds the executed original/candidate rows, the initial status, and the matching evidence for a ticketless batch.

Existing databases: `seed_demo` runs `ALTER TABLE batches ADD COLUMN IF NOT EXISTS mode VARCHAR(20) NOT NULL DEFAULT 'exact'`; `create_all` creates `claims`. Existing batches keep their comparisons and are treated as `exact`. SQLAlchemy metadata still creates users/samples/batches/evidence/audit; `seed_demo` initializes the separate invoices fixture table and NOLOGIN `query_reader`. `invoices` is the only permitted SQL source. Run `python -m scripts.seed_demo` for repeatable initialization; natural keys preserve user edits.
