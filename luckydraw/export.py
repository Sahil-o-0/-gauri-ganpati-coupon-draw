"""Excel exports (openpyxl)."""
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import engine


def _sheet(wb, title, headers, rows, first=False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E79")
        cell.alignment = Alignment(vertical="center")
    for r in rows:
        ws.append(list(r))
    ws.freeze_panes = "A2"
    for i, h in enumerate(headers, 1):
        width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows[:300] if r[i - 1] is not None] + [8])
        ws.column_dimensions[get_column_letter(i)].width = min(width + 3, 45)
    return ws


def _label(n, digits):
    return str(n).zfill(digits)


def build_workbook(conn, only_winners=False):
    c = engine.cfg(conn)
    d = c["digits"]
    wb = Workbook()
    winners = engine.winner_rows(conn)
    _sheet(wb, "Winners", ["No.", "Prize", "Coupon no.", "Customer", "City", "Phone", "Bill no.", "Drawn at", "Status", "Reason"],
           [[w["seq"], w["prize_name"] or "(prize to be added)", _label(w["coupon_number"], d), w["name"], w["city"],
             str(w["phone"]), w["voucher_no"], w["drawn_at"], w["status"], w["cancel_reason"]] for w in winners],
           first=True)
    if not only_winners:
        cust = conn.execute("""
            SELECT cu.name, cu.phone, cu.city,
              (SELECT COUNT(*) FROM sales s WHERE s.customer_id=cu.id AND s.status IN ('active','no_coupon')),
              (SELECT COALESCE(SUM(amount_paise),0) FROM sales s WHERE s.customer_id=cu.id AND s.status IN ('active','no_coupon')),
              (SELECT COUNT(*) FROM coupons k WHERE k.customer_id=cu.id AND k.status IN ('valid','won'))
            FROM customers cu ORDER BY cu.name COLLATE NOCASE""").fetchall()
        _sheet(wb, "Customers", ["Name", "Phone", "City", "Bills", "Total amount (Rs)", "Coupons"],
               [[r[0], str(r[1]), r[2], r[3], r[4] / 100, r[5]] for r in cust])
        coup = conn.execute("""
            SELECT k.number, k.status, k.void_reason, cu.name, cu.phone, s.voucher_no, s.voucher_date
            FROM coupons k JOIN customers cu ON cu.id=k.customer_id JOIN sales s ON s.id=k.sale_id
            ORDER BY k.number""").fetchall()
        _sheet(wb, "Coupons", ["Coupon no.", "Status", "Void reason", "Customer", "Phone", "Bill no.", "Bill date"],
               [[_label(r[0], d), r[1], r[2], r[3], str(r[4]), r[5], r[6]] for r in coup])
        sales = conn.execute("""
            SELECT s.id, s.voucher_no, s.voucher_date, s.party_ledger, s.amount_paise, s.status, s.status_note,
                   COALESCE(cu.name, s.raw_name), COALESCE(cu.phone, s.phone), s.coupons_earned
            FROM sales s LEFT JOIN customers cu ON cu.id=s.customer_id ORDER BY s.id""").fetchall()
        _sheet(wb, "Bills", ["Entry no.", "Bill no.", "Date", "Party (Tally)", "Amount (Rs)", "Status", "Note", "Customer", "Phone", "Coupons"],
               [[r[0], r[1], r[2], r[3], r[4] / 100, r[5], r[6], r[7], str(r[8] or ""), r[9]] for r in sales])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
