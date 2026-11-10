"""Talks to Tally Prime over its built-in XML-over-HTTP connection (read only).

We ask Tally for the Day Book (all vouchers) for a date range and read the Sales vouchers out of it.
The parser is deliberately forgiving because the exact XML differs a little between Tally versions.
Run tools/probe_tally.py on the Tally PC to save a real response if anything looks wrong.
"""
import re
import urllib.request
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from . import engine


class TallyError(Exception):
    pass


def build_request(date_from, date_to, company=""):
    """XML request for the Day Book. Dates are YYYY-MM-DD."""
    f, t = date_from.replace("-", ""), date_to.replace("-", "")
    comp = f"<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>" if company else ""
    return (
        "<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST>"
        "<TYPE>Data</TYPE><ID>Day Book</ID></HEADER><BODY><DESC><STATICVARIABLES>"
        "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
        f"<SVFROMDATE>{f}</SVFROMDATE><SVTODATE>{t}</SVTODATE>{comp}"
        "</STATICVARIABLES></DESC></BODY></ENVELOPE>"
    ).encode("utf-8")


def fetch(host, port, body, timeout=90):
    url = f"http://{host}:{port}"
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "text/xml; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except OSError as e:  # connection refused, timeout, DNS ...
        raise TallyError(
            f"Cannot reach Tally at {host}:{port}. Is Tally open, the company loaded and the server connection on? ({e})"
        ) from e


def _decode(data):
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _clean(text):
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
    # Tally sometimes writes characters XML does not allow, e.g. &#4;
    def bad_ref(m):
        n = int(m.group(1))
        return "" if (n < 32 and n not in (9, 10, 13)) else m.group(0)
    text = re.sub(r"&#(\d+);", bad_ref, text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)


def _txt(el, tag):
    child = el.find(tag)
    return (child.text or "").strip() if child is not None and child.text else ""


def _lines(el, list_tag, item_tag):
    out = []
    for lst in el.findall(list_tag):
        items = lst.findall(item_tag)
        out.extend((i.text or "").strip() for i in items if (i.text or "").strip())
    return [x for x in out if x]


def _amount_paise(text):
    text = (text or "").replace(",", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if not m:
        return 0
    return int(round(abs(float(m.group(0))) * 100))


def _iso_date(text):
    text = (text or "").strip()
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", text)
    return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" if m else text


def parse_vouchers(data, allowed_types=("Sales",), extra_tags=()):
    """Turn Tally XML into a list of engine.Voucher (only the allowed voucher types)."""
    text = _clean(_decode(data))
    if "<LINEERROR>" in text:
        msg = re.search(r"<LINEERROR>(.*?)</LINEERROR>", text, re.S)
        raise TallyError("Tally said: " + (msg.group(1).strip() if msg else "error"))
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise TallyError(f"Tally sent something that is not valid XML ({e}).") from e

    allowed = {t.strip().lower() for t in allowed_types if t.strip()}
    out = []
    for el in root.iter("VOUCHER"):
        vtype = _txt(el, "VOUCHERTYPENAME") or el.get("VCHTYPE", "")
        if allowed and vtype.strip().lower() not in allowed:
            continue
        guid = _txt(el, "GUID") or el.get("REMOTEID", "")
        if not guid:
            continue
        party = _txt(el, "PARTYLEDGERNAME") or _txt(el, "PARTYNAME")

        # invoice total = the party ledger line (the customer's debit)
        total = 0
        entries = el.findall("ALLLEDGERENTRIES.LIST") + el.findall("LEDGERENTRIES.LIST")
        party_entries = [e for e in entries if _txt(e, "ISPARTYLEDGER").lower() == "yes"]
        if not party_entries:
            party_entries = [e for e in entries if _txt(e, "LEDGERNAME") == party]
        for e in party_entries:
            total += _amount_paise(_txt(e, "AMOUNT"))
        if not total:
            total = _amount_paise(_txt(el, "AMOUNT"))

        address = _lines(el, "BASICBUYERADDRESS.LIST", "BASICBUYERADDRESS")
        address += _lines(el, "ADDRESS.LIST", "ADDRESS")
        buyer = _txt(el, "BASICBUYERNAME")
        mailing = _txt(el, "PARTYMAILINGNAME")
        narration = _txt(el, "NARRATION")
        extras = {t: _txt(el, t) for t in extra_tags if t}
        extra_lines = [v for v in extras.values() if v]

        # the customer's number is typed somewhere in the bill: look in the usual places, in order
        phone = engine.extract_phone(*address, *extra_lines, mailing, buyer, narration, party)
        name = buyer or mailing or party
        city = engine.guess_city(address)
        cancelled = _txt(el, "ISCANCELLED").lower() == "yes" or _txt(el, "ISOPTIONAL").lower() == "yes"
        alter = re.sub(r"\D", "", _txt(el, "ALTERID"))
        out.append(engine.Voucher(
            guid=guid, voucher_no=_txt(el, "VOUCHERNUMBER"), date=_iso_date(_txt(el, "DATE")),
            voucher_type=vtype, party_ledger=party, amount_paise=total, cancelled=cancelled,
            alter_id=int(alter) if alter else None, name=name, phone=phone, city=city,
            address=", ".join(address), extras=extras))
    return out
