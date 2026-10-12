"""Render a synthetic World into statement files that LOOK like what banks / UPI apps export.

IMPORTANT HONESTY NOTE.  These layouts are *styles inspired by public examples*, not verified copies of any bank's real
format (banks do not share one format and change them over time).  Their job is to exercise the importer and the parser
with the structural variety that exists: separate debit/credit columns vs one signed column vs "120.00(Dr)" suffixes,
date-only vs date+time, day-first dates in different spellings, newest-first ordering, a preamble above the header, a
footer below the table, failed transactions inside an app export, and narrations with / without the payee's name.

The keys (sbi, hdfc ...) are only mnemonics for the *structure*.
"""
from __future__ import annotations

import csv
import random
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..textutil import fmt_inr
from . import pools
from .world import Entity, Rec, World


@dataclass
class Layout:
    key: str
    columns: list[str]
    upi_only: bool = False           # app exports contain only UPI payments
    newest_first: bool = False
    has_time: bool = False
    describes: str = ""


LAYOUTS: dict[str, Layout] = {
    "sbi": Layout("sbi", ["Txn Date", "Value Date", "Description", "Ref No./Cheque No.", "Debit", "Credit", "Balance"],
                  describes="slash narration with DR/CR, truncated names, '12 Oct 2026' dates, thousands separators"),
    "hdfc": Layout("hdfc", ["Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"],
                   describes="dash-delimited narration UPI-NAME-VPA-IFSC-RRN-remark, dd/mm/yy dates"),
    "icici": Layout("icici", ["S No.", "Value Date", "Transaction Date", "Cheque Number", "Transaction Remarks",
                              "Withdrawal Amount (INR)", "Deposit Amount (INR)", "Balance (INR)"],
                    describes="'address-only' narration: no payee name, only RRN / remark / UPI address / bank (hardest)"),
    "axis": Layout("axis", ["SRL NO", "Tran Date", "CHQNO", "PARTICULARS", "DR", "CR", "BAL", "SOL"],
                   describes="UPI/P2M|P2A/RRN/NAME/remark with explicit merchant/person markers"),
    "kotak": Layout("kotak", ["Date", "Description", "Chq/Ref No", "Withdrawal (Dr)/Deposit (Cr)", "Balance"],
                    describes="ONE amount column with a '(Dr)' / '(Cr)' suffix"),
    "phonepe": Layout("phonepe", ["Date", "Time", "Transaction Details", "Type", "Amount", "UTR No."], upi_only=True,
                      newest_first=True, has_time=True,
                      describes="UPI-app export: 'Paid to NAME', Type=DEBIT/CREDIT, positive amounts with the rupee sign, newest first"),
    "paytm": Layout("paytm", ["Date", "Time", "Transaction Details", "Other Transaction Details", "Your Account", "Amount", "Status"],
                    upi_only=True, newest_first=True, has_time=True,
                    describes="UPI-app export: signed '- Rs.120.00' amounts, UPI address in a second text column, FAILED rows inside"),
    "signed": Layout("signed", ["Date", "Description", "Amount"], upi_only=True,
                     describes="budget-app export: ISO dates, one signed amount (negative = spent)"),
    "demo": Layout("demo", ["Date", "Time", "Narration", "Ref No", "Debit", "Credit", "Balance"], has_time=True,
                   describes="the layout of the built-in demo file (HDFC-like narration + a Time column)"),
}


# --------------------------------------------------------------------------------------------------------------------
def _dmy(dt: datetime) -> str:
    """'2 Jun 2026' - portable (strftime's %-d is not available on Windows)."""
    return f"{dt.day} {dt:%b} {dt.year}"


def _fit(s: str, n: int) -> str:
    return s[:n] if n else s


def _money(x: float, fmt: str = "plain") -> str:
    if fmt == "grouped":
        return fmt_inr(x, 2, symbol=False)
    return f"{x:.2f}"


def _atm(rng, style: str) -> str:
    card = f"{rng.choice(['512345', '652161', '438812'])}XXXXXX{rng.randint(1000, 9999)}"
    loc = rng.choice(["CAMPUS ROAD", "COLLEGE GATE", "CITY MAIN RD", "RIVER ROAD"])
    return {"sbi": f"ATM WDL-ATM CASH {rng.randint(1000, 9999)} {loc}", "hdfc": f"ATW-{card}-S1ACMS01-{loc}",
            "icici": f"ATM/CASH WDL/{card}/{loc}", "axis": f"ATM-CASH/{card}/{loc}", "kotak": f"ATM WITHDRAWAL {card} {loc}",
            "demo": f"ATW-{card}-S1ACMS01-{loc}"}.get(style, f"ATM CASH WITHDRAWAL {loc}")


def _other_narr(rec: Rec, ent: Entity, style: str, rng: random.Random) -> str:
    ifsc = rng.choice(pools.BANK_CODES4) + "0" + str(rng.randint(100000, 999999))
    utr = rng.choice(pools.BANK_CODES4) + str(rng.randint(10**12, 10**13 - 1))
    nm = ent.printed[0] if ent.printed else ent.name.upper()
    if rec.mode == "ATM":
        return _atm(rng, style)
    if rec.mode == "NEFT":
        if rec.direction == "credit":
            return {"sbi": f"BY TRANSFER-NEFT*{ifsc}*{utr}*{nm}--", "hdfc": f"NEFT CR-{ifsc}-{nm}-STUDENT-{utr}",
                    "icici": f"NEFT-{utr}-{nm}-", "axis": f"NEFT/{utr}/{nm}", "kotak": f"NEFT {utr} {nm}"}.get(style, f"NEFT CR-{ifsc}-{nm}-{utr}")
        return {"sbi": f"TO TRANSFER-NEFT*{ifsc}*{utr}*{nm}--", "hdfc": f"NEFT DR-{ifsc}-{nm}-{utr}",
                "icici": f"NEFT-{utr}-{nm}-", "axis": f"NEFT/{utr}/{nm}", "kotak": f"NEFT {utr} {nm}"}.get(style, f"NEFT DR-{ifsc}-{nm}-{utr}")
    if rec.mode == "CHARGE":
        if "ANNUAL" in nm:
            return "DEBIT CARD ANNUAL FEE 2026-27 + GST"
        return "SMS ALERT CHARGES 01/04/26 TO 30/06/26"
    if rec.mode == "INTEREST":
        return {"sbi": "CREDIT INTEREST", "hdfc": "INT.PD:01-07-2026 TO 30-09-2026", "icici": "INT.PD 01-07-26 TO 30-09-26"}.get(style, "CREDIT INTEREST CAPITALISED")
    return nm


def _upi_narr(rec: Rec, ent: Entity, style: str, rng: random.Random) -> str:
    nm = rec.printed.upper() if rec.printed else ent.name.upper()
    d = "DR" if rec.direction == "debit" else "CR"
    remark = rec.remark
    b4, bshort = rng.choice(pools.BANK_CODES4), rng.choice(pools.BANK_SHORT)
    ifsc = b4 + "0" + str(rng.randint(100000, 999999))
    if style == "sbi":
        pre = "TO TRANSFER-" if rec.direction == "debit" else "BY TRANSFER-"
        return f"{pre}UPI/{d}/{rec.rrn}/{_fit(nm, 16)}/{b4}/{rec.vpa}/{_fit(remark or 'UPI', 10)}"
    if style in ("hdfc", "demo"):
        return f"UPI-{_fit(nm, 28)}-{(rec.vpa or '').upper()}-{ifsc}-{rec.rrn}-{remark or 'UPI'}"
    if style == "icici":
        return f"UPI/{rec.rrn}/{_fit(remark or 'UPI', 16)}/{rec.vpa}/{_fit(bshort, 15)}"
    if style == "axis":
        marker = ent.marker or ("P2M" if ent.kind == "merchant" else "P2A")
        return f"UPI/{marker}/{rec.rrn}/{_fit(nm, 22)}/{_fit(remark or 'UPI', 18)}"
    if style == "kotak":
        return f"UPI/{_fit(nm, 24)}/{rec.rrn}/{remark or 'UPI'}"
    return f"UPI-{nm}"


def _narration(rec: Rec, ent: Entity, style: str, rng: random.Random) -> str:
    if rec.mode == "UPI":
        return _upi_narr(rec, ent, style, rng)
    return _other_narr(rec, ent, style, rng)


def _app_details(rec: Rec, ent: Entity) -> str:
    nm = (rec.printed or ent.name).title() if rec.printed.isupper() else (rec.printed or ent.name)
    return f"Paid to {nm}" if rec.direction == "debit" else f"Received from {nm}"


# --------------------------------------------------------------------------------------------------------------------
def render(world: World, layout_key: str, seed: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame, Layout]:
    """Return (table, truth, layout). `table` has exactly the layout's columns (strings, as exported);
    `truth.src_row` is the 0-based position of each *real* transaction among the accepted rows of the file."""
    lay = LAYOUTS[layout_key]
    rng = random.Random((seed if seed is not None else world.seed) * 1009 + zlib.crc32(layout_key.encode()) % 100000)
    recs = [r for r in world.recs if (not lay.upi_only or r.mode in ("UPI", "WALLET"))]
    ents = world.ents
    rows: list[dict] = []
    truth: list[dict] = []
    ordered = list(reversed(recs)) if lay.newest_first else list(recs)
    n_fail_inserted = 0
    for i, r in enumerate(ordered):
        e = ents[r.ent]
        dt = r.dt
        amt = r.amount
        deb = r.direction == "debit"
        row: dict = {}
        if layout_key == "sbi":
            row = {"Txn Date": _dmy(dt), "Value Date": _dmy(dt),
                   "Description": _narration(r, e, "sbi", rng), "Ref No./Cheque No.": r.rrn if r.mode == "UPI" else "",
                   "Debit": _money(amt, "grouped") if deb else "", "Credit": "" if deb else _money(amt, "grouped"),
                   "Balance": _money(r.balance, "grouped")}
        elif layout_key == "hdfc":
            d8 = dt.strftime("%d/%m/%y")
            row = {"Date": d8, "Narration": _narration(r, e, "hdfc", rng), "Chq./Ref.No.": r.rrn if r.mode == "UPI" else "",
                   "Value Dt": d8, "Withdrawal Amt.": _money(amt) if deb else "", "Deposit Amt.": "" if deb else _money(amt),
                   "Closing Balance": _money(r.balance)}
        elif layout_key == "icici":
            row = {"S No.": str(i + 1), "Value Date": dt.strftime("%d/%m/%Y"), "Transaction Date": dt.strftime("%d/%m/%Y"),
                   "Cheque Number": "", "Transaction Remarks": _narration(r, e, "icici", rng),
                   "Withdrawal Amount (INR)": _money(amt) if deb else "0.00", "Deposit Amount (INR)": "0.00" if deb else _money(amt),
                   "Balance (INR)": _money(r.balance)}
        elif layout_key == "axis":
            row = {"SRL NO": str(i + 1), "Tran Date": dt.strftime("%d-%m-%Y"), "CHQNO": "", "PARTICULARS": _narration(r, e, "axis", rng),
                   "DR": _money(amt) if deb else "", "CR": "" if deb else _money(amt), "BAL": _money(r.balance), "SOL": "1234"}
        elif layout_key == "kotak":
            row = {"Date": dt.strftime("%d-%m-%Y"), "Description": _narration(r, e, "kotak", rng),
                   "Chq/Ref No": "UPI-" + r.rrn[-6:] if r.mode == "UPI" else "",
                   "Withdrawal (Dr)/Deposit (Cr)": f"{_money(amt, 'grouped')}({'Dr' if deb else 'Cr'})",
                   "Balance": f"{_money(r.balance, 'grouped')}(Cr)"}
        elif layout_key == "phonepe":
            row = {"Date": dt.strftime("%b %d, %Y"), "Time": dt.strftime("%I:%M %p"), "Transaction Details": _app_details(r, e),
                   "Type": "DEBIT" if deb else "CREDIT", "Amount": "₹" + fmt_inr(amt, 0 if amt == int(amt) else 2, symbol=False),
                   "UTR No.": r.rrn}
        elif layout_key == "paytm":
            row = {"Date": _dmy(dt), "Time": dt.strftime("%H:%M:%S"), "Transaction Details": _app_details(r, e),
                   "Other Transaction Details": r.vpa or "", "Your Account": "HDFC Bank - XXXX1234",
                   "Amount": f"{'-' if deb else '+'} Rs.{_money(amt, 'grouped')}", "Status": "SUCCESS"}
        elif layout_key == "signed":
            row = {"Date": dt.strftime("%Y-%m-%d"), "Description": f"UPI-{(r.printed or e.name).upper()}",
                   "Amount": f"{'-' if deb else ''}{_money(amt)}"}
        elif layout_key == "demo":
            row = {"Date": dt.strftime("%d-%m-%Y"), "Time": dt.strftime("%H:%M"), "Narration": _narration(r, e, "demo", rng),
                   "Ref No": r.rrn if r.mode == "UPI" else "", "Debit": _money(amt) if deb else "", "Credit": "" if deb else _money(amt),
                   "Balance": _money(r.balance)}
        rows.append(row)
        truth.append({"src_row": len(rows) - 1 - n_fail_inserted * 0, "rec_idx": world.recs.index(r), "ent_id": e.id, "ent_name": e.name,
                      "kind": e.kind, "category": world.truth_category(r), "direction": r.direction, "amount": r.amount,
                      "is_brand": e.is_brand, "is_local": e.is_local, "personal_vpa": e.personal_vpa, "note": e.note,
                      "anomaly": r.anomaly or "", "series": r.series or "", "date": dt.date().isoformat(),
                      "mode": r.mode, "rrn": r.rrn})
        # the Paytm-style export also lists payments that FAILED: money never moved, they must be ignored
        if layout_key == "paytm" and deb and rng.random() < 0.02:
            ghost = dict(row)
            ghost["Status"] = "FAILED"
            rows.append(ghost)
            n_fail_inserted += 1
    # fix src_row so it counts ACCEPTED rows only (failed ghost rows are rejected by the importer)
    k = 0
    acc = 0
    fixed: list[int] = []
    for row in rows:
        if row.get("Status") == "FAILED":
            continue
        fixed.append(acc)
        acc += 1
    for t, s in zip(truth, fixed):
        t["src_row"] = s
    return pd.DataFrame(rows, columns=lay.columns), pd.DataFrame(truth), lay


# --------------------------------------------------------------------------------------------------------------------
_PREAMBLE = [
    ["Account Name :", "MR. STUDENT SAMPLE"],
    ["Account Number :", "XXXXXXXX1234"],
    ["Branch :", "MAIN BRANCH"],
    ["Statement of account for the period :", "{period}"],
    [],
]
_FOOTER = [
    [],
    ["Closing balance", "{closing}"],
    ["This is a computer generated statement and does not require a signature."],
]


def write_csv(path: Path, df: pd.DataFrame, lay: Layout, world: World, preamble: bool = True, bom: bool = False) -> None:
    period = f"{world.start:%d-%m-%Y} to {world.end:%d-%m-%Y}"
    with open(path, "w", newline="", encoding="utf-8-sig" if bom else "utf-8") as f:
        w = csv.writer(f)
        if preamble and not lay.upi_only:
            for line in _PREAMBLE:
                w.writerow([c.format(period=period) for c in line])
        w.writerow(list(df.columns))
        for row in df.itertuples(index=False):
            w.writerow(list(row))
        if preamble and not lay.upi_only:
            for line in _FOOTER:
                w.writerow([c.format(closing=world.recs[-1].balance) for c in line])


def write_xlsx(path: Path, df: pd.DataFrame, lay: Layout, world: World, preamble: bool = True) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Statement"
    period = f"{world.start:%d-%m-%Y} to {world.end:%d-%m-%Y}"
    if preamble and not lay.upi_only:
        for line in _PREAMBLE:
            ws.append([c.format(period=period) for c in line])
    ws.append(list(df.columns))
    for row in df.itertuples(index=False):
        ws.append(list(row))
    if preamble and not lay.upi_only:
        for line in _FOOTER:
            ws.append([c.format(closing=world.recs[-1].balance) for c in line])
    wb.save(path)


def export(world: World, layout_key: str, out_dir: Path, fmt: str = "csv", preamble: bool = True) -> tuple[Path, Path]:
    """Write <layout>.csv|xlsx and <layout>.truth.csv; return both paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    df, truth, lay = render(world, layout_key)
    path = out_dir / f"statement_{layout_key}.{fmt}"
    if fmt == "csv":
        write_csv(path, df, lay, world, preamble=preamble, bom=(layout_key in ("hdfc", "kotak")))
    else:
        write_xlsx(path, df, lay, world, preamble=preamble)
    tpath = out_dir / f"statement_{layout_key}.truth.csv"
    truth.to_csv(tpath, index=False)
    return path, tpath
