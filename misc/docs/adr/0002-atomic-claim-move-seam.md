# ADR 0002: Claiming a held report is an atomic move, behind a `claim_move` seam

Status: accepted (2026-11-23)

## Context

`release_held` claims a held report before sending it, so a crash never loses it. Claiming was a copy into the claims store followed by a delete from holding, on a plain `MutableMapping`. Issue [#24](https://github.com/i2mint/heed/issues/24) showed an interleaving of two releases of the same report in which the loser keeps a claim on a report the winner already sent; the next `recover_claimed()` puts it back and the next release sends it a second time. A moderation UI that can double-click "release" hits it.

The issue named two fixes, both changing the store contract: an atomic-move seam, or a "released" tombstone that every listing of holding must skip and `recover_claimed` must clean up.

## Decision

The atomic-move seam. `release_held` takes `claim_move=`, a `heed.storage.ClaimMove`: `move(holding_store, report_id, claims_store, claim_key)` takes the report out of holding and puts it under the claim key as one step; of concurrent moves of one report exactly one succeeds, the others raise `KeyError` and change nothing; a crash leaves the report in at least one of the two stores (`recover_claimed` already handles a report in both).

- `make_holding_store` stores carry their own move (`heed_claim_move`, beside `heed_claims`): `rename_claim_move` (`os.rename`) on disk, `pop_claim_move` (`dict.pop`) in memory. `release_held` uses it when `claim_move` is not given and the claims store is the holding store's own, so the stores heed recommends are safe with no change at the call site. Only the store's own attribute counts: a `dol` wrapper forwards attribute lookups but maps keys differently, so a wrapped holding store claims by copy, and `rename_claim_move` refuses any store whose keys do not resolve to absolute paths under its `rootdir`, and symlinked reports.
- Any other holding store keeps the copy-then-delete claim and its documented limitation: serialise releases, or pass `claim_move`.

The tombstone was not chosen: it adds a record type that every reader of holding must filter, and cleanup logic in recovery, to emulate what the backends already provide atomically.

## Seams

| Seam | v1 default | Replacement already in view |
|---|---|---|
| claim move | `os.rename` (files) / `dict.pop` (memory), carried by `make_holding_store`; copy-then-delete for other stores | Redis `RENAMENX` (same database; "no such key" maps to `KeyError`). Not S3 conditional writes on the claim key: claim keys are unique per release, so the exclusion would have to be a conditional delete of the holding object, which cannot be one step |

## Consequences

- Releases from a `make_holding_store` store are safe against concurrent releases of the same report, across threads (memory) and processes on one filesystem (disk).
- `os.rename` needs the holding directory and `<rootdir>.claims` on one filesystem, which `make_holding_store` arranges (sibling directories).
- On Windows, `os.rename` of a file another process has open raises `PermissionError`: the release fails and the report stays held, nothing is lost.
- Reversible: removing the carried move restores the old behaviour; `claim_move` is a new keyword, so no caller changes.
