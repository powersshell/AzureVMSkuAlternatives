# Tenant migration helpers

One-off scripts for moving the app to a new Azure tenant / subscription (see the
migration plan). These are **not** part of the deployed app.

## `migrate_tables.py` — Table Storage data copy (Phase 5)

Copies `vmskus` (warm cache) and `vmskuhistory` (accrued daily price history) from the
OLD storage account to the NEW one. `cpuperf` is intentionally skipped — it self-seeds
from an in-code constant on the next refresh. AzCopy v10 has no Azure Table support, so
this uses the `azure-data-tables` SDK.

### Prerequisites
1. `pip install -r requirements.txt`
2. `az login` as an identity with **Storage Table Data Contributor** on **both** accounts.
3. The NEW account is private (public access + shared key disabled). Before running,
   either temporarily enable public network access with your client IP on the NEW
   account, **or** run from a host inside its VNet. Re-lock it afterwards.
4. Run **before** the old tenant is decommissioned (the source must be reachable).

### Run
```bash
# Preview counts only (no writes)
python migrate_tables.py --source <OLD_STORAGE_ACCT> --dest <NEW_STORAGE_ACCT> --dry-run

# Full copy
python migrate_tables.py --source <OLD_STORAGE_ACCT> --dest <NEW_STORAGE_ACCT>

# Just the price history
python migrate_tables.py --source <OLD_STORAGE_ACCT> --dest <NEW_STORAGE_ACCT> --tables vmskuhistory
```

Find the storage account names with:
`az storage account list -g rg-vmsku-alternatives --query "[].name" -o tsv`
(run against each subscription).
