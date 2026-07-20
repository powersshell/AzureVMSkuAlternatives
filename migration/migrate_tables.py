#!/usr/bin/env python3
"""
One-off Table Storage migration for the tenant move (Phase 5 of the migration plan).

Copies the SKU cache and price-history tables from the OLD storage account to the NEW
one. AzCopy v10 dropped Azure Table support, so this uses the same `azure-data-tables`
SDK the Function App already depends on.

Tables:
  - vmskus        warm SKU cache (also self-repopulates from the daily timer)
  - vmskuhistory  accrued daily price history (FORWARD-ONLY -> worth preserving)
  - cpuperf       NOT copied: it self-seeds from an in-code constant (seed_cpu_performance_table)

Auth: Azure AD (DefaultAzureCredential). Grant yourself **Storage Table Data Contributor**
on BOTH accounts. Run `az login` (or set a service principal) first.

Networking gotcha: the NEW storage account is private (public network access disabled,
shared-key disabled). Before running, either:
  (a) temporarily set the NEW account's public network access to "Enabled from selected
      networks" and add your client IP, then re-disable it afterwards; or
  (b) run this script from a host inside the account's VNet.
The OLD account must still be reachable (run BEFORE decommissioning the old tenant).

Usage:
  python migrate_tables.py --source <oldacct> --dest <newacct>
  python migrate_tables.py --source <oldacct> --dest <newacct> --tables vmskuhistory
  python migrate_tables.py --source <oldacct> --dest <newacct> --dry-run

Examples:
  python migrate_tables.py --source vmskunapiabc123 --dest vmskunapixyz789
"""
import argparse
import sys
import time

from azure.core.exceptions import HttpResponseError, ResourceExistsError
from azure.data.tables import TableServiceClient, UpdateMode
from azure.identity import DefaultAzureCredential

DEFAULT_TABLES = ["vmskus", "vmskuhistory"]
SYSTEM_KEYS = {"PartitionKey", "RowKey", "Timestamp", "etag"}


def _endpoint(account: str) -> str:
    return f"https://{account}.table.core.windows.net"


def _service_client(account: str, credential) -> TableServiceClient:
    return TableServiceClient(endpoint=_endpoint(account), credential=credential)


def _clean_entity(entity: dict) -> dict:
    """Strip service-managed metadata so the entity can be upserted cleanly."""
    return {k: v for k, v in entity.items() if k not in SYSTEM_KEYS}


def migrate_table(src_svc: TableServiceClient, dst_svc: TableServiceClient,
                  table_name: str, dry_run: bool) -> tuple:
    """Copy every entity in one table from source to dest. Returns (read, written)."""
    src = src_svc.get_table_client(table_name)
    dst = dst_svc.get_table_client(table_name)

    if not dry_run:
        try:
            dst_svc.create_table(table_name)
            print(f"  created table '{table_name}' on dest")
        except ResourceExistsError:
            print(f"  table '{table_name}' already exists on dest")

    read = written = 0
    t0 = time.time()
    for entity in src.list_entities():
        read += 1
        if not dry_run:
            dst.upsert_entity(entity=_clean_entity(entity), mode=UpdateMode.REPLACE)
            written += 1
        if read % 500 == 0:
            print(f"  {table_name}: {read} read, {written} written "
                  f"({read / max(time.time() - t0, 0.001):.0f}/s)")
    return read, written


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate Azure Tables between storage accounts.")
    parser.add_argument("--source", required=True, help="OLD storage account name")
    parser.add_argument("--dest", required=True, help="NEW storage account name")
    parser.add_argument("--tables", nargs="*", default=DEFAULT_TABLES,
                        help=f"Tables to copy (default: {' '.join(DEFAULT_TABLES)})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count source rows without writing to dest")
    args = parser.parse_args()

    if args.source == args.dest:
        print("ERROR: source and dest must differ", file=sys.stderr)
        return 2

    credential = DefaultAzureCredential()
    src_svc = _service_client(args.source, credential)
    dst_svc = _service_client(args.dest, credential)

    mode = "DRY RUN" if args.dry_run else "MIGRATE"
    print(f"[{mode}] {args.source} -> {args.dest}  tables={args.tables}\n")

    grand_read = grand_written = 0
    for table_name in args.tables:
        print(f"Table '{table_name}':")
        try:
            read, written = migrate_table(src_svc, dst_svc, table_name, args.dry_run)
        except HttpResponseError as e:
            print(f"  ERROR on '{table_name}': {e.message or e}", file=sys.stderr)
            return 1
        grand_read += read
        grand_written += written
        print(f"  done: {read} read, {written} written\n")

    print(f"TOTAL: {grand_read} read, {grand_written} written")
    if args.dry_run:
        print("(dry run — nothing was written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
