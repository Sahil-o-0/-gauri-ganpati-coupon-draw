"""Run this ON THE TALLY PC (Tally open, company loaded, server connection on).

    python tools/probe_tally.py                       # last 7 days, localhost:9000
    python tools/probe_tally.py --host 192.168.1.10 --port 9000 --days 30 --company "My Company"

It saves Tally's raw reply to tally_probe_output.xml and shows how the software reads the Sales bills
(name, phone, city, amount). Send that XML file back if the phone or city is not picked up correctly.
"""
import argparse
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from luckydraw import tally  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--company", default="")
    ap.add_argument("--types", default="Sales")
    ap.add_argument("--extra-tags", default="")
    a = ap.parse_args()

    today = date.today()
    body = tally.build_request((today - timedelta(days=a.days)).isoformat(), today.isoformat(), a.company)
    try:
        data = tally.fetch(a.host, a.port, body)
    except tally.TallyError as e:
        print("FAILED:", e)
        return 1
    out = Path("tally_probe_output.xml")
    out.write_bytes(data)
    print(f"Saved Tally's reply ({len(data):,} bytes) to {out.resolve()}")

    all_v = tally.parse_vouchers(data, allowed_types=())
    print("Voucher types found:", dict(Counter(v.voucher_type for v in all_v)) or "none")
    sales = tally.parse_vouchers(data, a.types.split(","), [t for t in a.extra_tags.split(",") if t])
    print(f"\nSales bills read: {len(sales)}")
    no_phone = sum(1 for v in sales if not v.phone)
    print(f"  with a phone number: {len(sales) - no_phone}   without: {no_phone}")
    for v in sales[:10]:
        print(f"  {v.date} {v.voucher_no:<12} Rs {v.amount_paise / 100:>11,.2f}  {v.name[:22]:<22} "
              f"phone={v.phone or '-':<10} city={v.city or '-'}")
    if not sales:
        print("\nNo Sales bills found. Check the voucher type name (--types) and the company name (--company).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
