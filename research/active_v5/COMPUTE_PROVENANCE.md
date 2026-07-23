# Active V5 compute provenance

The canonical research design, configuration, market-data parser and metrics code are stored in this directory. The integrity-checked full source payload is also committed in the public compute branch because GitHub Actions in the private repository did not start reliably.

- compute repository: `balkhaev/trader`;
- branch: `agent/active-v5-compute`;
- draft PR: https://github.com/balkhaev/trader/pull/8;
- workflow name: `Active research v5 compute`;
- workflow run: `30034631906`;
- encoded payload SHA-256: `5123fdb1b2ef8c8bd81c8e5dac5a9c4c2e7a77446278896598c9f627b5eeb619`;
- compressed source archive SHA-256: `99b3170be8f2c20e25ded9f8ca83a3916e6c68571d619ecdbf03454aa5bd5f22`.

The result artifact and exact executed `strategy.py` / `run_research.py` will be copied back into this directory after the workflow completes and their hashes are verified.

Neither the compute PR nor a successful historical run constitutes live-trading approval.
