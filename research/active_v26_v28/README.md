# Active V26–V28 canonical archive

This directory fixes the previously completed V26 and V27 candidates and the new V28 exact-8h candidate in the canonical repository.

- V26: execution-aware frozen V8 foundation.
- V27: V26 plus conservative segregated cash sweep.
- V28: V26 plus a 15% causal breakout overlay, horizon-separated BTC/ETH hedge execution, exact-8h positive-funding cash-and-carry and the V27 cash sleeve.

Critical V28 runner and exact-8h engine are stored directly and are checked against their original Git blob identities. Immutable V26/V27 decisions, exact metrics, selection-proof hashes and source-artifact hashes are retained in `v26_v27_compact_evidence.json`. Large single-file transport blobs were intentionally removed after the connector was proven to truncate them at 10,000 bytes.

The public integrity-only workflow independently reconstructs the V28 engine from size-limited source fragments, verifies exact identity, compiles it and validates every frozen acceptance check.

All three versions remain frozen paper-forward candidates. This archive is not permission for live trading.
