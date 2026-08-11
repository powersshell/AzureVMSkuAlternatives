# VM SKU lifecycle data runbook

How to maintain the two hand-curated lifecycle tables that drive ranking penalties,
badges, banners, filters and MCP tool output across all three surfaces.

## The two signals are orthogonal

| | Question it answers | Source doc |
|---|---|---|
| **Retirement** (`VM_RETIREMENT_INFO`) | "Must I leave this size, and by when?" | [Retired sizes list](https://learn.microsoft.com/azure/virtual-machines/sizes/retirement/) |
| **Growth restriction** (`VM_GROWTH_RESTRICTION_INFO`) | "May I stay, but can I grow?" | [Previous-gen capacity limitations](https://learn.microsoft.com/azure/virtual-machines/migration/sizes/previous-gen-series-capacity-limitations) |

A size may carry **neither, either, or both**. This is not a hypothetical edge case —
it is the steady state. As of the last reconciliation run, **102 SKUs in eastus across
13 series carry both flags** (Av2/Amv2, B/Bs, D, Ds, Dsv2, Dv2, F, Fs, Fsv2, G, Gs, Ls, Lsv2).

Microsoft states this explicitly in the capacity-limitations FAQ:

> This restriction is about capacity growth, not retirement. Retirement follows a
> separate process with dedicated customer communications, timelines, and migration
> guidance. […] **some of the VM series affected by the capacity growth restrictions
> have retirement dates**

## Transition: a restricted series is announced for retirement

**Add a retirement entry. Do not touch the growth-restriction entry.**

There is no state machine, no migration and no backfill. The two tables are
independent inputs, and every consumer already reads them independently, so a
newly-retiring series simply starts matching both.

1. Add the entry to `VM_RETIREMENT_INFO` in `web-app/api/function_app.py`.
2. Mirror it **in the same list position** into `$script:VmRetirementInfo` in
   `powershell-script/Compare-AzureVms.ps1`.
3. **Leave `VM_GROWTH_RESTRICTION_INFO` alone.** The size still cannot get quota, and
   that stays true — often for years — before the retirement date lands.
4. Run the reconciliation check (below) and fix anything it reports.
5. Update `CHANGELOG.md` (`- **data:**`) and mirror the newest date group into the
   README "Recent Changes" section.

Nothing else needs to change. Verified behaviour once both flags are present:

- **Ranking** — penalties stack additively (`_retirement_penalty` +
  `_growth_restriction_penalty`). Retiring *and* quota-frozen is genuinely worse than
  either alone, so compounding is intended. Observed on `Standard_F16s`: 2.0 distant
  retirement + 8.0 restriction = 10.0, score 100.00 → 90.00, with
  `originalSimilarityScore` preserved. Penalties are applied *after* the
  min-similarity threshold, so they only reorder, never exclude.
- **Web UI** — `renderRetirementBadge()` and `renderGrowthRestrictionBadge()` are
  independent and both render; `retirementBanner` and `growthRestrictionBanner` can
  both show for the target SKU.
- **Filters** — `hideRetiringFilter` (default **on**) and `hideGrowthRestrictedFilter`
  (default **off**) test different fields, so a dual-flagged size is removed by the
  retirement filter alone. Correct: "must leave" outranks "cannot grow".
- **API / MCP contract** — retirement and `growthRestricted` /
  `growthRestrictionSeries` / `recommendedTargets` are separate response fields.
  Nothing is removed, so no agent consumer breaks.

### Why not drop the restriction flag on retirement?

Because it is still true and still actionable. A customer on a restricted-and-retiring
size needs both facts: they cannot scale *today*, and they must migrate *by a date*.
Dropping the restriction would silently make a constrained size look scalable during
the multi-year window before its retirement date.

### When a size actually reaches `Retired`

Flip `status` to `Retired` in `VM_RETIREMENT_INFO` (penalty jumps 2.0–10.0 → 15.0).
Still leave the growth-restriction entry in place. Azure keeps returning the size in
some regions after retirement, and the restriction remains accurate.

## Reconciliation check

```
python scripts/check_sku_lifecycle.py                    # fast, needs the live API
python scripts/check_sku_lifecycle.py --check-urls       # also verifies Learn links
python scripts/check_sku_lifecycle.py --location westeurope
```

Exits non-zero on any problem, so it can gate CI. It checks:

1. **Python ↔ PowerShell parity** — identical patterns in identical order. The two
   copies are hand-maintained and drift silently otherwise.
2. **Within-series retirement coverage** — if some sizes in a growth-restricted series
   are retirement-flagged and others are not, a regex missed a variant. This caught
   `^Standard_DS\d+_v2$` failing to match constrained variants
   (`Standard_DS11-1_v2`, `Standard_DS12-2_v2`, …) — 7 SKUs were under-flagged.
3. **Pattern reachability** — first-match-wins means a broad pattern can permanently
   shadow a narrower one placed after it. This caught `^Standard_B\d+ls$` being dead
   code behind `^Standard_B\d+[a-z]*s$` (`[a-z]*` absorbs the `l`).
4. **Overlap census** — how many SKUs are in the dual state, so the number is a
   tracked fact rather than a surprise.

### Authoring rules the check enforces

- **Narrower patterns first.** Constrained-vCPU (`-\d+`) and letter-suffixed variants
  must precede the general pattern for their series.
- **Allow `(-\d+)?` on constrained-capable series** (DS, GS, E, M). Constrained sizes
  share the fate of their parent series.
- **Keep the two files in lockstep** — same patterns, same order, same commit.

## Refreshing from Microsoft

Both source docs carry an `ms.date`. When either changes:

1. Re-read the impacted-series table and the recommended replacement targets.
2. Update the table(s), keeping the `ms.date` comment above
   `VM_GROWTH_RESTRICTION_INFO` current.
3. Run `python scripts/check_sku_lifecycle.py --check-urls`.
4. Smoke-test after deploy:
   ```
   curl https://vmsku-api-func-cus.azurewebsites.net/api/retirements
   curl https://vmsku-api-func-cus.azurewebsites.net/api/growth-restrictions
   curl "https://vmsku-api-func-cus.azurewebsites.net/api/growth-restrictions?sku=Standard_D4s_v3"
   ```

Deploying a table change only requires a push to `main` (Functions **code** job and the
MCP container). The Bicep infra job stays gated behind manual `workflow_dispatch`.
