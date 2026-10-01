# Exact candidate fixture recovery

Root-reviewed manifest SHA256: `a783b349132201fa4d6b6aded7055817738745379e4a1e83b9247ce8e0df821e`. The Python payloads end in `.py.source`, so installing this documentation package cannot add runtime modules or collected production tests. Full `docs/` is already copied and readable in isolated development workspaces.

Apply only the manifest paths assigned to the current original task, after checking all SHA256 and byte counts. The corrected tests are new relative to the accepted/production base; all prior run files remain original. Keep worker existing-test protection unchanged. If any listed path was already present with different bytes in the attempt's prepared baseline, stop this particular copy and obtain a separately reviewed root baseline repair. An already-restored prior-attempt copy is not the accepted baseline.

The frozen exports unpack only into a bounded temporary directory; current research exports are untouched. Both complete original bundles are included because the shared extraction regressions verify each bundle. Economics fresh-export coverage imports only economics candidate modules; volleyball fresh-export coverage imports only volleyball candidate modules.

Root must separately attach this manifest's exact path/hash and task-specific instruction to the original task recovery context and schedule a targeted attempt with its original identity/deadline. Copying documentation alone is not scheduling work. Do not call publish_candidate until the original task's actual implementation acceptance, complete suites and independent review have succeeded. No acceptance receipt exists for the old three tests, so do not manufacture or rewrite one.
