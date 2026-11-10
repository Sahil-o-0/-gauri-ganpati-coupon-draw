from luckydraw import db, engine, tally
from luckydraw.engine import Voucher


def V(guid, amount, phone="9876543210", name="Ramesh", city="Pune", cancelled=False, date="2026-12-01", no=None):
    return Voucher(guid=guid, voucher_no=no or guid, date=date, party_ledger=name, amount_paise=int(amount * 100),
                   cancelled=cancelled, name=name, phone=phone, city=city)


def put(v):
    with db.tx() as conn:
        return engine.upsert_voucher(conn, v)


def sale(guid):
    with db.read() as conn:
        return dict(conn.execute("SELECT * FROM sales WHERE guid=?", (guid,)).fetchone())


def coupons(sale_id=None, status=None):
    sql, p = "SELECT number, status FROM coupons WHERE 1=1", []
    if sale_id:
        sql += " AND sale_id=?"; p.append(sale_id)
    if status:
        sql += " AND status=?"; p.append(status)
    with db.read() as conn:
        return [(r["number"], r["status"]) for r in conn.execute(sql + " ORDER BY number", p)]


def test_one_coupon_per_21000_per_bill():
    cases = {20999.99: 0, 21000: 1, 42000: 2, 63000: 3, 70000: 3, 105000: 5}
    for i, (amt, n) in enumerate(cases.items()):
        put(V(f"g{i}", amt, phone=f"98765432{i:02d}"))
        assert sale(f"g{i}")["coupons_earned"] == n, amt
    assert sale("g0")["status"] == "no_coupon"


def test_bills_are_not_added_together():
    put(V("a", 15000)); put(V("b", 15000))
    assert coupons() == []


def test_no_phone_means_no_coupons_until_entered():
    put(V("a", 63000, phone=""))
    s = sale("a")
    assert s["status"] == "waiting_phone" and s["coupons_earned"] == 0 and coupons() == []
    with db.tx() as conn:
        engine.set_sale_phone(conn, s["id"], "98765 43210", "Suresh Patil", "Nashik", "admin")
    assert sale("a")["status"] == "active"
    assert [n for n, _ in coupons()] == [1, 2, 3]
    with db.read() as conn:
        c = conn.execute("SELECT * FROM customers").fetchone()
    assert (c["name"], c["city"], c["phone"]) == ("Suresh Patil", "Nashik", "9876543210")


def test_manual_phone_survives_tally_resync():
    put(V("a", 21000, phone=""))
    with db.tx() as conn:
        engine.set_sale_phone(conn, sale("a")["id"], "9123456789")
    put(V("a", 42000, phone=""))                       # edited in Tally, still no phone there
    assert sale("a")["status"] == "active" and sale("a")["coupons_earned"] == 2


def test_invalid_phone_rejected():
    put(V("a", 21000, phone=""))
    with db.tx() as conn:
        try:
            engine.set_sale_phone(conn, sale("a")["id"], "12345")
            assert False
        except ValueError:
            pass


def test_edit_up_and_down_and_numbers_never_reused():
    put(V("a", 63000))                                  # 1,2,3
    put(V("b", 21000, phone="9111111111"))              # 4
    put(V("a", 84000))                                  # bill a edited up -> new number 5
    assert [n for n, s in coupons(sale("a")["id"])] == [1, 2, 3, 5]
    put(V("a", 42000))                                  # edited down -> highest two voided (5, 3)
    assert coupons(sale("a")["id"], "valid") == [(1, "valid"), (2, "valid")]
    put(V("a", 63000))                                  # back up -> a NEW number, 3 is not reused
    assert [n for n, _ in coupons(sale("a")["id"], "valid")] == [1, 2, 6]
    assert sale("a")["coupons_earned"] == 3


def test_cancel_then_unchanged_import():
    put(V("a", 63000))
    assert put(V("a", 63000))[0] == "unchanged"
    put(V("a", 63000, cancelled=True))
    assert sale("a")["status"] == "cancelled" and coupons(status="valid") == []


def test_outside_campaign_dates():
    put(V("a", 63000, date="2026-10-01"))
    put(V("b", 63000, date="2027-07-06"))
    put(V("c", 63000, date="2027-07-05"))
    assert sale("a")["status"] == "outside_campaign" and sale("b")["status"] == "outside_campaign"
    assert sale("c")["status"] == "active"


def test_deleted_bill_detected():
    put(V("a", 63000)); put(V("b", 21000, phone="9111111111"))
    with db.tx() as conn:
        missing = engine.find_missing(conn, {"b"}, "2026-11-08", "2027-07-05")
        engine.mark_deleted(conn, missing)
    assert sale("a")["status"] == "deleted" and coupons(sale("a")["id"], "valid") == []
    assert sale("b")["status"] == "active"


def test_same_phone_joins_one_customer():
    put(V("a", 21000, name="Ramesh Sharma")); put(V("b", 42000, name="R. Sharma"))
    with db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 1


def test_changing_amount_setting_recomputes():
    put(V("a", 63000))
    with db.tx() as conn:
        db.set_setting(conn, "amount_per_coupon", "31500")
        engine.recompute_all(conn)
    assert sale("a")["coupons_earned"] == 2


def test_draw_one_prize_per_customer():
    put(V("big", 21000 * 30, phone="9000000001", name="Big Buyer"))       # 30 coupons, one customer
    for i in range(5):
        put(V(f"s{i}", 21000, phone=f"900000010{i}", name=f"Small {i}"))
    with db.tx() as conn:
        won = engine.draw_winners(conn, "admin", count=6)
    assert len(won) == 6
    assert len({w["customer_id"] for w in won}) == 6                      # nobody twice
    with db.tx() as conn:
        try:
            engine.draw_winners(conn, "admin", 1)
            assert False
        except ValueError:
            pass


def test_draw_respects_total_prizes_and_prize_quantity():
    for i in range(10):
        put(V(f"s{i}", 21000, phone=f"90000002{i:02d}"))
    with db.tx() as conn:
        db.set_setting(conn, "total_prizes", "4")
        pid = conn.execute("INSERT INTO prizes(name, quantity) VALUES ('Gold coin', 2)").lastrowid
        w = engine.draw_winners(conn, "admin", count=5, prize_id=pid)
    assert len(w) == 2 and all(x["prize_name"] == "Gold coin" for x in w)
    with db.tx() as conn:
        w2 = engine.draw_winners(conn, "admin", count=10)                  # unnamed prizes, capped by total 4
    assert len(w2) == 2


def test_cancel_winner_and_put_back():
    for i in range(3):
        put(V(f"s{i}", 21000, phone=f"90000003{i:02d}"))
    with db.tx() as conn:
        w = engine.draw_winners(conn, "admin", 1)[0]
    with db.tx() as conn:
        try:
            engine.cancel_winner(conn, w["id"], "", False, "admin"); assert False
        except ValueError:
            pass
        engine.cancel_winner(conn, w["id"], "unreachable", False, "admin")
    with db.read() as conn:
        assert engine.draw_status(conn)["eligible_customers"] == 2          # stays out
    with db.tx() as conn:
        w = engine.draw_winners(conn, "admin", 1)[0]
        engine.cancel_winner(conn, w["id"], "pressed twice", True, "admin")
    with db.read() as conn:
        assert engine.draw_status(conn)["eligible_customers"] == 2          # this one is back


def test_phone_and_city_extraction():
    assert engine.extract_phone("Mob: 98765 43210") == "9876543210"
    assert engine.extract_phone("call +91-9876543210 now") == "9876543210"
    assert engine.extract_phone("pin 411001 9876543210") == "9876543210"
    assert engine.extract_phone("GSTIN 27ABCDE1234F1Z5 bill 1234567890") == ""   # starts with 1: not a mobile
    assert engine.extract_phone("12345678901234") == ""
    assert engine.guess_city(["Shop 5, MG Road", "Pune", "Maharashtra", "411001", "Mob: 9876543210"]) == "Pune"
    assert engine.guess_city(["12 Main Road, Nashik, Maharashtra - 422001"]) == "Nashik"
    assert engine.guess_city([]) == ""


SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE><BODY><DATA>
<TALLYMESSAGE><VOUCHER VCHTYPE="Sales"><DATE>20261110</DATE><GUID>g-1</GUID><VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
<VOUCHERNUMBER>GG/1</VOUCHERNUMBER><PARTYLEDGERNAME>Ramesh Traders</PARTYLEDGERNAME><ISCANCELLED>No</ISCANCELLED><ALTERID> 55</ALTERID>
<BASICBUYERADDRESS.LIST><BASICBUYERADDRESS>Shop 4, Market</BASICBUYERADDRESS><BASICBUYERADDRESS>Satara</BASICBUYERADDRESS><BASICBUYERADDRESS>Ph 98 22 33 44 55</BASICBUYERADDRESS></BASICBUYERADDRESS.LIST>
<ALLLEDGERENTRIES.LIST><LEDGERNAME>Ramesh Traders</LEDGERNAME><ISPARTYLEDGER>Yes</ISPARTYLEDGER><AMOUNT>-66,150.50</AMOUNT><BILLALLOCATIONS.LIST><AMOUNT>-66,150.50</AMOUNT></BILLALLOCATIONS.LIST></ALLLEDGERENTRIES.LIST>
<ALLLEDGERENTRIES.LIST><LEDGERNAME>Sales</LEDGERNAME><ISPARTYLEDGER>No</ISPARTYLEDGER><AMOUNT>56058.05</AMOUNT></ALLLEDGERENTRIES.LIST></VOUCHER></TALLYMESSAGE>
<TALLYMESSAGE><VOUCHER VCHTYPE="Receipt"><DATE>20261110</DATE><GUID>g-2</GUID><VOUCHERTYPENAME>Receipt</VOUCHERTYPENAME></VOUCHER></TALLYMESSAGE>
<TALLYMESSAGE><VOUCHER VCHTYPE="Sales"><DATE>20261111</DATE><GUID>g-3</GUID><VOUCHERTYPENAME>Sales</VOUCHERTYPENAME><VOUCHERNUMBER>GG/2</VOUCHERNUMBER>
<PARTYLEDGERNAME>Cash</PARTYLEDGERNAME><ISCANCELLED>Yes</ISCANCELLED><NARRATION>Walk-in 9988776655 &#4; x</NARRATION>
<LEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME><AMOUNT>-21000</AMOUNT></LEDGERENTRIES.LIST></VOUCHER></TALLYMESSAGE>
</DATA></BODY></ENVELOPE>"""


def test_parse_tally_xml():
    vs = tally.parse_vouchers(SAMPLE, ["Sales"])
    assert [v.guid for v in vs] == ["g-1", "g-3"]                            # Receipt ignored
    a, b = vs
    assert a.amount_paise == 6615050 and a.phone == "9822334455" and a.city == "Satara" and a.alter_id == 55
    assert a.date == "2026-11-10" and a.voucher_no == "GG/1" and not a.cancelled
    assert b.cancelled and b.phone == "9988776655" and b.amount_paise == 2100000   # amount from ledger named like the party


def test_tally_error_message():
    import pytest
    with pytest.raises(tally.TallyError):
        tally.parse_vouchers(b"<ENVELOPE><LINEERROR>Could not set SVCurrentCompany</LINEERROR></ENVELOPE>")
