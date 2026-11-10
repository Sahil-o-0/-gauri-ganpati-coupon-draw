"""A pretend Tally for testing and demos (no real Tally needed).

    python tools/mock_tally.py                 # serves tools/demo_bills.json on port 9000
    python tools/mock_tally.py --demo 400      # first makes 400 random demo bills

Edit tools/demo_bills.json while it runs to try edits, cancellations and deletions:
  - change "amount"  -> the bill is edited
  - set "cancelled": true -> the bill is cancelled
  - remove a bill   -> the bill is deleted in Tally
The LuckyDraw software then picks the change up on its next check.
"""
import argparse
import json
import random
import re
import sys
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.sax.saxutils import escape

DATA = Path(__file__).with_name("demo_bills.json")
FIRST = ["Ramesh", "Suresh", "Mahesh", "Anita", "Sunita", "Kavita", "Rajesh", "Pooja", "Vijay", "Neha",
         "Amit", "Priya", "Sanjay", "Meena", "Deepak", "Rekha", "Anil", "Seema", "Mohan", "Geeta"]
LAST = ["Sharma", "Patil", "Joshi", "Deshmukh", "Agrawal", "Gupta", "Jain", "Kulkarni", "More", "Shah"]
CITIES = ["Pune", "Nashik", "Nagpur", "Satara", "Kolhapur", "Solapur", "Sangli", "Latur", "Akola", "Jalgaon"]


def make_demo(n):
    rnd = random.Random(7)
    start = date(2026, 11, 8)
    span = max((date.today() - start).days, 1)
    people = []
    for i in range(max(n // 2, 1)):
        people.append({
            "name": f"{rnd.choice(FIRST)} {rnd.choice(LAST)}",
            "city": rnd.choice(CITIES),
            "phone": f"{rnd.choice('6789')}{rnd.randint(0, 999999999):09d}",
        })
    bills = []
    for i in range(1, n + 1):
        p = rnd.choice(people)
        amount = rnd.choice([8000, 15000, 21000, 25000, 42000, 45000, 63000, 70000, 105000])
        has_phone = rnd.random() > 0.07
        bills.append({
            "guid": f"demo-guid-{i:05d}",
            "number": f"GG/{i:04d}",
            "date": (start + timedelta(days=rnd.randint(0, span))).isoformat(),
            "type": "Sales",
            "party": f"{p['name']} {p['city']}",
            "buyer": p["name"],
            "address": [f"Shop {rnd.randint(1, 99)}, Main Road", p["city"], "Maharashtra",
                        f"4{rnd.randint(10000, 99999)}"] + ([f"Mob: {p['phone']}"] if has_phone else []),
            "amount": amount,
            "cancelled": rnd.random() < 0.02,
            "alter_id": i,
        })
    DATA.write_text(json.dumps(bills, indent=1), encoding="utf-8")
    return bills


def voucher_xml(b):
    amt = float(b["amount"])
    tax = round(amt * 18 / 118, 2)
    lines = "".join(f"<BASICBUYERADDRESS>{escape(a)}</BASICBUYERADDRESS>" for a in b.get("address", []))
    return f"""<TALLYMESSAGE xmlns:UDF="TallyUDF"><VOUCHER VCHTYPE="{b['type']}" ACTION="Create" OBJVIEW="Invoice Voucher View">
<DATE>{b['date'].replace('-', '')}</DATE><GUID>{b['guid']}</GUID><VOUCHERTYPENAME>{b['type']}</VOUCHERTYPENAME>
<VOUCHERNUMBER>{escape(b['number'])}</VOUCHERNUMBER><PARTYLEDGERNAME>{escape(b['party'])}</PARTYLEDGERNAME>
<BASICBUYERNAME>{escape(b.get('buyer', ''))}</BASICBUYERNAME>
<ISCANCELLED>{'Yes' if b.get('cancelled') else 'No'}</ISCANCELLED><ISOPTIONAL>No</ISOPTIONAL>
<ALTERID> {b.get('alter_id', 1)}</ALTERID>
<BASICBUYERADDRESS.LIST TYPE="String">{lines}</BASICBUYERADDRESS.LIST>
<NARRATION>{escape(b.get('narration', ''))}</NARRATION>
<ALLLEDGERENTRIES.LIST><LEDGERNAME>{escape(b['party'])}</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
<ISPARTYLEDGER>Yes</ISPARTYLEDGER><AMOUNT>-{amt:.2f}</AMOUNT>
<BILLALLOCATIONS.LIST><NAME>{escape(b['number'])}</NAME><AMOUNT>-{amt:.2f}</AMOUNT></BILLALLOCATIONS.LIST></ALLLEDGERENTRIES.LIST>
<ALLLEDGERENTRIES.LIST><LEDGERNAME>Sales</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><ISPARTYLEDGER>No</ISPARTYLEDGER><AMOUNT>{amt - tax:.2f}</AMOUNT></ALLLEDGERENTRIES.LIST>
<ALLLEDGERENTRIES.LIST><LEDGERNAME>Output GST</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><ISPARTYLEDGER>No</ISPARTYLEDGER><AMOUNT>{tax:.2f}</AMOUNT></ALLLEDGERENTRIES.LIST>
</VOUCHER></TALLYMESSAGE>"""


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8", "replace")
        f = re.search(r"<SVFROMDATE[^>]*>(\d{8})<", body)
        t = re.search(r"<SVTODATE[^>]*>(\d{8})<", body)
        lo = f.group(1) if f else "00000000"
        hi = t.group(1) if t else "99999999"
        bills = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else []
        picked = [b for b in bills if lo <= b["date"].replace("-", "") <= hi]
        xml = ("<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER><BODY><DATA>"
               + "".join(voucher_xml(b) for b in picked) + "</DATA></BODY></ENVELOPE>")
        data = xml.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        data = b"<RESPONSE>TallyPrime Server is Running (mock)</RESPONSE>"
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--demo", type=int, default=0, help="create this many random demo bills first")
    args = ap.parse_args()
    if args.demo or not DATA.exists():
        make_demo(args.demo or 60)
        print(f"Wrote demo bills to {DATA}")
    print(f"Mock Tally running on port {args.port}. Press Ctrl+C to stop.")
    try:
        ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
