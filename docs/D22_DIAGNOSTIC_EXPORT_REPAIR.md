# D22 diagnostic export repair candidate

The failed D22 run `20261001T075116822831-D22` received none of its seven declared diagnostic documents, although all seven exist at root. Reusing an accepted base does not refresh current task-specific diagnostic docs. These are historical traces, not missing sports sources or completed latency trials.

This candidate adds `export_task_diagnostics(root, workspace, task)` and `verify_task_diagnostics(root, workspace, task)` to development_inputs.py. The current root plan owns the exact task reference list; the caller cannot add references. Only single ordinary `.json`, `.txt`, `.md` or `.log` files beneath `docs/<same-task-id>/` are allowed. The exporter validates every source and destination before copying, rejects conflicts/symlinks/traversal/malformed input, caps16files/1MiB each/4MiB total, and publishes the exact SHA/byte receipt last. Copy time is explicitly separate from measurement time and approval. A later trusted check rejects changed source bytes, ownership, copied files or receipts without repairing them.

Actual-source replay: all seven files absent in the retained failed workspace; all seven exact root bodies (17,994bytes) copied and verified in an isolated replay folder. The retained failed workspace and root diagnostics remain untouched. `evidence/actual-D22-before-after.json` pins each original and copied body. This is helper-level proof; root still must wire the callsites and prove automatic worker preparation.

Validation:65focused cases pass;110total including unchanged development evidence, general-input and NHL-source export tests pass. Existing tests were not edited. Full root integration tests and independent review remain required.

Integration belongs to root. Exact suggestions are in `evidence/root-callsite-suggestions.md`; no development.py edit is part of this handoff. The root must supply the helper during both accepted-base and fresh preparations, make the exact diagnostic sources/receipt read-only in native permissions, and verify them around trusted tests/review. This does not enable a live service or schedule a coding run.

D22 still requires the two120second real concurrent-load trials, actual guarded refresh/publication, three active development lanes, three network SSE clients, endpoint samples at the unchanged thresholds, separate Tailscale RTT and immutable live-ledger comparisons. No diagnostic copy, synthetic health experiment, historical trace or passing unit test closes those requirements. Original deadlines are unchanged.

Review corrections: bounded task identities include legitimate hyphenated IDs. Nonempty tasks still require the authoritative root plan. Internal destinations are restricted to data/development/workspaces/<safe-run-id>; root source/config/docs/accepted trees are rejected. External isolated temporary workspaces remain supported. The original candidate/source/manifest/logs are retained in evidence/pre-hyphen-review.
