"""Narration parser: a raw bank/UPI description  ->  structured fields (mode, reference, UPI address, payee, remark ...).

WHY ANCHOR-BASED?  Banks do not share one narration format - the field order and the delimiter differ from bank to bank (we
modelled five layouts from public examples; NOT verified against real files). A parser that says "field 3 is the
name" breaks the day a new bank shows up. So instead we look for things that are recognisable *wherever they are* (the "anchors"):

    12-digit reference (RRN/UTR)    UPI address (VPA)  something@handle     IFSC  ABCD0123456
    markers   UPI IMPS NEFT ATM POS ...,  DR / CR,  P2M / P2A / P2P / P2V
    boiler-plate  "Payment from PhonePe", "TO TRANSFER", "SUCCESS" ...

Everything that is not an anchor is a candidate for "the payee's name" or "the payer's remark". We score each candidate
(is it next to the reference / the address / a "paid to"?  mostly letters?  looks like a shop or a person?  looks like a
typed remark such as "rent"?) and keep the best. It never raises: garbage in -> low-confidence "unknown" out.

Pipeline position:  ingest -> **parse** -> normalise -> kind (shop/person) -> category.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .. import seeds
from ..textutil import mask_vpa, norm_ws, tokens_of, vpa_parts

# --------------------------------------------------------------------------------------------------------------------
# anchors
# --------------------------------------------------------------------------------------------------------------------
# local part may contain '-' (e.g. gpay-123456@okbizaxis) only when we already split by a different delimiter
_VPA_PERMISSIVE = re.compile(r"(?<![\w.@])([A-Za-z0-9][A-Za-z0-9._\-]{0,63})@([A-Za-z][A-Za-z0-9]{1,23})(?![A-Za-z0-9])(?!\.[A-Za-z])")
_VPA_STRICT = re.compile(r"(?<![\w.@\-])([A-Za-z0-9][A-Za-z0-9._]{0,63})@([A-Za-z][A-Za-z0-9]{1,23})(?![A-Za-z0-9])(?!\.[A-Za-z])")
_RRN = re.compile(r"^\d{9,16}$")
_PHONE = re.compile(r"^(?:\+?91)?[6-9]\d{9}$")
_IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_GSTIN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_UTR = re.compile(r"^[A-Z]{4}[A-Z0-9]?\d{9,22}$")                 # NEFT/RTGS UTR such as HDFC2268012345678
_TXN_ID = re.compile(r"^(?:T\d{12,}[A-Z0-9]*|OD\d{8,}|[A-Z]{1,3}\d{9,}[A-Z0-9]*)$")  # PhonePe T24101..., order ids
_MASKED = re.compile(r"^(?:[X\*]{2,}\d{2,6}|\d{2,6}[X\*]{2,}\d{0,6}|[\dX\*]{8,})$")
_DATE_LIKE = re.compile(r"^(?:\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{6,10})$")
_AMOUNT_LIKE = re.compile(r"^(?:RS|INR)?\.?\d[\d,]*\.\d{1,2}$")
_HAS_DIGIT = re.compile(r"\d")

MARKERS = {"P2M", "P2A", "P2P", "P2V"}
MODE_WORDS = {
    "UPI": "UPI", "BHIM": "UPI", "IMPS": "IMPS", "MMT": "IMPS", "NEFT": "NEFT", "RTGS": "RTGS",
    "ATM": "ATM", "ATW": "ATM", "NWD": "ATM", "EAW": "ATM", "NFS": "ATM", "POS": "POS", "PCD": "POS", "ECOM": "POS", "ACH": "ACH", "NACH": "ACH", "ECS": "ACH",
    "CHQ": "CHEQUE", "CLG": "CHEQUE", "CHEQUE": "CHEQUE", "INB": "NETBANKING", "BIL": "NETBANKING",
}
DIR_DEBIT = {"DR", "DEBIT", "DEBITED", "PAID", "SENT", "WDL", "WITHDRAWAL"}
DIR_CREDIT = {"CR", "CREDIT", "CREDITED", "RECEIVED", "DEPOSIT", "DEP"}
STATUS_WORDS = {"SUCCESS", "SUCCESSFUL", "COMPLETED", "FAILED", "PENDING", "DONE", "APPROVED", "SETTLED"}

BANK_CODES = {   # 4-letter IFSC prefixes / short codes commonly printed in narrations -> bank
    "YESB": "Yes Bank", "HDFC": "HDFC Bank", "ICIC": "ICICI Bank", "SBIN": "SBI", "UTIB": "Axis Bank",
    "KKBK": "Kotak", "PUNB": "PNB", "BARB": "Bank of Baroda", "CNRB": "Canara Bank", "IDIB": "Indian Bank",
    "UBIN": "Union Bank", "BKID": "Bank of India", "MAHB": "Bank of Maharashtra", "INDB": "IndusInd",
    "FDRL": "Federal Bank", "IDFB": "IDFC First", "AIRP": "Airtel Payments Bank", "PYTM": "Paytm Payments Bank",
    "KARB": "Karnataka Bank", "SIBL": "South Indian Bank", "RATN": "RBL Bank", "ESFB": "Equitas SFB",
    "JSFB": "Jana SFB", "CBIN": "Central Bank", "IOBA": "Indian Overseas Bank", "UCBA": "UCO Bank",
    "SRCB": "Saraswat Bank", "JAKA": "J&K Bank", "DLXB": "Dhanlaxmi Bank", "TMBL": "Tamilnad Mercantile",
}
BANK_WORDS = {"YES BANK", "HDFC BANK", "ICICI BANK", "STATE BANK OF INDIA", "AXIS BANK", "KOTAK MAHINDRA BANK",
              "KOTAK BANK", "PUNJAB NATIONAL BANK", "BANK OF BARODA", "CANARA BANK", "UNION BANK OF INDIA",
              "INDIAN BANK", "BANK OF INDIA", "FEDERAL BANK", "IDFC FIRST BANK", "INDUSIND BANK", "RBL BANK",
              "PAYTM PAYMENTS BANK", "AIRTEL PAYMENTS BANK", "INDIA POST PAYMENTS BANK", "BANK OF MAHARASHTRA",
              "CENTRAL BANK OF INDIA", "INDIAN OVERSEAS BANK", "UCO BANK", "KARNATAKA BANK", "SOUTH INDIAN BANK",
              "JAMMU AND KASHMIR BANK", "BANDHAN BANK", "DCB BANK", "CSB BANK", "IDBI BANK", "AU SMALL FINANCE BANK",
              "EQUITAS SMALL FINANCE BANK", "UJJIVAN SMALL FINANCE BANK", "SARASWAT BANK", "TAMILNAD MERCANTILE BANK",
              "SBI", "PNB", "BOB", "BOI", "KOTAK MAHINDRA", "KOTAK", "PUNJAB NATIONAL", "STATE BANK", "HDFC", "ICICI",
              "AXIS", "CANARA", "INDUSIND", "FEDERAL", "UNION BANK", "STATE BANK OF"}


def is_bank_text(piece_upper: str) -> bool:
    """'Kotak Mahindra', 'State Bank Of I' (truncated by the bank), 'Bandhan Bank' ... -> True."""
    t = re.sub(r"[^A-Z& ]", " ", piece_upper.replace("&", " AND "))
    t = " ".join(t.split())
    if not t:
        return False
    if t in BANK_WORDS:
        return True
    if len(t) >= 6 and any(b.startswith(t) for b in BANK_WORDS if len(b) > 6):
        return True
    return t.endswith(" BANK") and len(t.split()) <= 4

# handle -> app. Only handles confirmed in public PSP documentation are listed; anything else is just "bank/other app".
HANDLE_APP = {
    "ybl": "PhonePe", "ibl": "PhonePe", "axl": "PhonePe",
    "okhdfcbank": "Google Pay", "okicici": "Google Pay", "oksbi": "Google Pay", "okaxis": "Google Pay",
    "paytm": "Paytm", "ptsbi": "Paytm", "ptyes": "Paytm", "pthdfc": "Paytm", "ptaxis": "Paytm",
    "apl": "Amazon Pay", "ikwik": "MobiKwik", "freecharge": "Freecharge",
}

# tokens that carry no identity information (boiler-plate of the payment rails)
BOILER_TOKENS = {
    "UPI", "PAYMENT", "PAYMENTS", "PAID", "PAY", "PAYING", "TO", "FROM", "VIA", "USING", "BY", "SENT", "RECEIVED", "TRANSFER",
    "TRANSFERRED", "TRF", "TXN", "TRANSACTION", "REF", "REFNO", "NO", "ID", "DEBIT", "CREDIT", "DR", "CR", "DEBITED",
    "CREDITED", "SUCCESS", "SUCCESSFUL", "FAILED", "COMPLETED", "PHONEPE", "PHONE", "PE", "GPAY", "GOOGLE", "PAYTM", "BHIM",
    "MOBILE", "BANK", "AC", "ACCOUNT", "MR", "MRS", "MS", "THE", "FOR", "OF", "ON", "AT", "IN", "MERCHANT", "COLLECT",
    "REQUEST", "AUTOPAY", "MANDATE", "STATUS", "REMARK", "REMARKS", "NA", "NIL", "NULL", "NONE", "QR", "SCAN", "SCANNED",
    "MONEY", "AMOUNT", "ONLINE", "INTERNET", "CUSTOMER", "SELF", "FR", "FRO", "PH", "PHO", "PHON", "PHONEP", "PAYMEN",
    "PAYME", "ORDER", "INV", "INVOICE", "BILL", "RRN", "UTR", "IFSC", "MOB", "P2M", "P2A", "P2P", "P2V",
}
# short words people type as the UPI remark - a remark is not a payee name
REMARK_WORDS = {
    "RENT", "FOOD", "LUNCH", "DINNER", "BREAKFAST", "THANKS", "THANK", "YOU", "GIFT", "HELP", "LOAN", "RETURN", "SPLIT",
    "TRIP", "MOVIE", "MESS", "FEES", "FEE", "EMI", "SALARY", "REFUND", "BIRTHDAY", "PARTY", "UDHAR", "KHANA", "CHAI", "OK",
    "HI", "HII", "HELLO", "BRO", "BHAI", "SIR", "BILL", "RECHARGE", "SNACKS", "CASH", "BALANCE", "ADVANCE", "TICKET",
    "TICKETS", "PIZZA", "MILK", "GROCERY", "MEDICINE", "BOOKS", "PAYMENT", "SORRY", "BIRTHDAY", "DINNER", "FUEL", "PETROL",
    "SHARE", "CONTRIBUTION", "PARTY", "TREAT", "FOR", "FROM",
}
_PREP_TO_FROM = {"TO", "FROM"}
_REF_LABELS = {"INV", "INVOICE", "BILL", "ORDER", "REF", "NO", "ID", "TXN", "NUMBER", "NUM", "RECEIPT", "OD", "UTR"}
_LABEL_WORDS = {"MOBILE", "MOB", "PHONE", "AC", "ACCOUNT", "NO", "NUMBER", "ACCT", "CARD"}
_TRAILING_LABELS = {"INV", "INVOICE", "RECEIPT", "TXN", "REF"}
# Only THESE split a run of words. Other BOILER_TOKENS (MOBILE, PAYMENT, PAY, BANK, ORDER, ONLINE, MONEY ...) also occur inside real
# shop/company names (HIRALAL MOBILE POINT, AMAZON PAY INDIA, ABC PAYMENTS LTD), so they stay part of the name; a run made ONLY of such
# words (e.g. "Payment fr", "Google Pay") is boiler-plate as a whole.
HARD_BOILER = {"UPI", "TO", "FROM", "VIA", "USING", "BY", "PAID", "SENT", "RECEIVED", "TRANSFER", "TRANSFERRED", "TRF", "TXN", "TRANSACTION",
               "REF", "REFNO", "ID", "DEBIT", "CREDIT", "DR", "CR", "DEBITED", "CREDITED", "SUCCESS", "SUCCESSFUL", "FAILED", "COMPLETED",
               "PHONEPE", "GPAY", "BHIM", "STATUS", "REMARK", "REMARKS", "NA", "NIL", "NULL", "NONE", "RRN", "UTR", "IFSC", "COLLECT", "REQUEST",
               "AUTOPAY", "MANDATE", "P2M", "P2A", "P2P", "P2V"}
# words that name the QR/payment PROVIDER, not the shop (bharatpe.6319495099@fbpe is *a* BharatPe QR, not "BharatPe")
QR_PROVIDER_WORDS = {"BHARATPE", "PAYTM", "PAYTMQR", "PHONEPE", "GPAY", "GOOGLEPAY", "RAZORPAY", "RZP", "PAYU", "CASHFREE",
                     "PINELABS", "INSTAMOJO", "EZETAP", "UPI", "PAY", "MERCHANT", "BHARATQR", "BQR", "QR", "POS", "MSWIPE",
                     "SBIPAY", "PAYZAPP", "MOBIKWIK", "AMAZONPAY", "FREECHARGE", "YESBANK", "STORE", "SHOP", "VYAPAR"}

_RE_MODE_ATM = re.compile(r"\bATM\b|\bATW\b|\bNWD\b|\bEAW\b|CASH\s*WDL|CASH\s*WITHDRAW|\bNFS\b")
_RE_MODE_POS = re.compile(r"\bPOS\b|\bPCD\b|\bE-?COM\b|DEBIT CARD|\bVISA\b|\bMASTERCARD\b|\bRUPAY\b")
_RE_ACH = re.compile(r"\bACH\b|\bNACH\b|\bECS\b|\bAUTO\s?PAY\b|\bMANDATE\b|\bSTANDING INSTRUCTION\b")
_RE_INTEREST = re.compile(r"\bINT\.?\s?(?:PD|PAID|CREDIT)\b|CREDIT INTEREST|\bINTEREST\b|\bINT\.PD\b|\bSB INT\b")
_RE_CHARGES = re.compile(r"\bCHG\b|\bCHGS\b|CHARGES|\bGST\b|SMS ALERT|ANNUAL FEE|PROCESSING FEE|MIN(?:IMUM)? BAL|PENALTY|\bAMC\b|STAMP DUTY|DEBIT CARD ANNUAL|SERVICE TAX|\bCGST\b|\bSGST\b")
_RE_REFUND = re.compile(r"REFUND|REVERSAL|\bREV\b|\bRVSL\b|CASH\s?BACK|REVERSED|CHARGEBACK")
_RE_WALLET = re.compile(r"ADD MONEY|WALLET\s*(?:LOAD|TOP\s?UP|RECHARGE)|LOAD MONEY|ADDED TO WALLET|UPI LITE|ADD TO WALLET")
_RE_CARD_BILL = re.compile(r"CREDIT CARD|\bCC PAYMENT\b|CARD PAYMENT|\bCCARD\b|BILLDESK.*\bCC\b|CC BILL")
_RE_SELF = re.compile(r"\bSELF\b|OWN ACCOUNT|TO SELF|SELF TRANSFER|OWN A/C")
_RE_SALARY = re.compile(r"\bSALARY\b|\bSTIPEND\b|SCHOLARSHIP|\bPAYROLL\b|\bDBT\b")
_RE_EMI = re.compile(r"\bEMI\b|\bLOAN\b")


@dataclass
class Parsed:
    raw: str
    mode: str = "OTHER"                 # UPI IMPS NEFT RTGS ATM POS ACH CHEQUE NETBANKING OTHER
    marker: str | None = None           # P2M / P2A / P2P / P2V  (only some banks print it)
    dir_hint: str | None = None         # 'debit' | 'credit' | None  - a hint; the amount column is authoritative
    rrn: str | None = None
    vpa: str | None = None
    vpas: list[str] = field(default_factory=list)
    handle: str | None = None
    app: str | None = None              # PhonePe / Google Pay / Paytm ... (from the handle, only where confirmed)
    phone: str | None = None            # a bare 10-digit mobile number found in the narration
    ifsc: str | None = None
    bank_hint: str | None = None
    payee_raw: str = ""
    remarks: str = ""
    payee_source: str = "none"          # text (read from the narration) | vpa (guessed from the UPI address) | none
    name_score: float = 0.0             # how sure we are the payee text really is a name (>= ~2.2 = trusted for merging)
    template: str = "free"              # slash | dash | pipe | star | free
    flags: list[str] = field(default_factory=list)
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self, mask: bool = True) -> dict:
        d = asdict(self)
        if mask:
            d["vpa"] = mask_vpa(self.vpa) if self.vpa else None
            d["vpas"] = [mask_vpa(v) for v in self.vpas]
            d["phone"] = (self.phone[:2] + "X" * (len(self.phone) - 4) + self.phone[-2:]) if self.phone else None
            d["notes"] = [re.sub(r"\b\d{10,}\b", lambda m: m.group(0)[:2] + "X" * (len(m.group(0)) - 4) + m.group(0)[-2:], n) for n in self.notes]
        return d


@dataclass
class _Item:
    kind: str                # rrn phone ifsc vpa mode dir marker status bank id mask date amount boiler gstin text
    text: str = ""
    value: str = ""


# --------------------------------------------------------------------------------------------------------------------
def _split(s: str) -> tuple[str, list[str]]:
    """Pick the dominant delimiter. Returns (style, pieces)."""
    u = s.upper()
    if s.count("/") >= 2:
        return "slash", s.split("/")
    if s.count("|") >= 2:
        return "pipe", s.split("|")
    if s.count("*") >= 2:
        return "star", s.split("*")
    if s.count("-") >= 3 and re.match(r"^(UPI|IMPS|NEFT|RTGS|ACH|POS|ATM)\b", u) and ("@" in s or re.search(r"\d{9,}", s)):
        return "dash", s.split("-")
    if s.count(":") >= 2 and re.match(r"^(UPI|IMPS|NEFT)", u):
        return "colon", s.split(":")
    return "free", [s]


def _classify_token(tok: str, style: str) -> tuple[str, str]:
    """Return (kind, value) for one whitespace/hyphen-free token. kind == 'word' for ordinary words."""
    up = tok.upper().strip(".,;:()[]{}\"'")
    if not up:
        return "boiler", ""
    if up in MARKERS:
        return "marker", up
    if up in MODE_WORDS:
        return "mode", MODE_WORDS[up]
    if up in STATUS_WORDS:
        return "status", up
    if up in DIR_DEBIT and up in {"DR", "DEBIT", "DEBITED", "WDL"}:
        return "dir", "debit"
    if up in DIR_CREDIT and up in {"CR", "CREDIT", "CREDITED", "DEP", "DEPOSIT"}:
        return "dir", "credit"
    if up in BANK_CODES:
        return "bank", up
    if _IFSC.match(up):
        return "ifsc", up
    if _GSTIN.match(up):
        return "gstin", up
    if _PHONE.match(up):
        return "phone", up[-10:]
    if _RRN.match(up):
        return "rrn", up
    if _UTR.match(up):
        return "rrn", up
    if _MASKED.match(up):
        return "mask", up
    if _AMOUNT_LIKE.match(up):
        return "amount", up
    if _DATE_LIKE.match(up):
        return "date", up
    if _TXN_ID.match(up) and _HAS_DIGIT.search(up) and len(up) >= 10:
        return "id", up
    if up in BOILER_TOKENS:
        return "boiler", up
    if len(up) >= 7 and _HAS_DIGIT.search(up) and sum(ch.isdigit() for ch in up) >= 6:
        return "id", up
    return "word", up


def _items_from_piece(piece: str, style: str, allow_hyphen_vpa: bool) -> list[_Item]:
    items: list[_Item] = []
    piece = norm_ws(piece)
    if not piece:
        return items
    # whole-piece shortcuts that must survive multi-word text: bank names
    if is_bank_text(piece.upper()):
        return [_Item("bank", piece, piece.upper())]
    vpa_re = _VPA_PERMISSIVE if allow_hyphen_vpa else _VPA_STRICT
    # pull VPAs out first so '@' tokens are not split by hyphens/dots
    pos = 0
    chunks: list[tuple[str, str]] = []   # ('vpa'|'text', value)
    for m in vpa_re.finditer(piece):
        if m.start() > pos:
            chunks.append(("text", piece[pos:m.start()]))
        chunks.append(("vpa", f"{m.group(1)}@{m.group(2)}".lower()))
        pos = m.end()
    if pos < len(piece):
        chunks.append(("text", piece[pos:]))

    for ctype, val in chunks:
        if ctype == "vpa":
            items.append(_Item("vpa", val, val))
            continue
        # split on whitespace and hyphens (hyphen only matters for words like INV-001), keep other punctuation off the ends
        toks = [t for t in re.split(r"[\s\-,;:]+", val) if t]
        run: list[str] = []

        def flush():
            if not run:
                return
            # a reference label followed by digits is not part of the name: "ABC CORP INV-2024-001" -> "ABC CORP"
            while run and run[-1].isdigit():
                j = len(run)
                while j > 0 and run[j - 1].isdigit():
                    j -= 1
                if j > 0 and run[j - 1].upper() in _REF_LABELS:
                    del run[j - 1:]
                    while run and run[-1].upper() in _REF_LABELS:
                        run.pop()
                else:
                    break
            if not run:
                return
            up = [r.upper() for r in run]
            if all(u in BOILER_TOKENS or u.isdigit() for u in up):   # "Payment fr", "Google Pay", "ORDER-891" -> pure boiler-plate
                items.append(_Item("boiler", " ".join(run), up[-1]))
            else:
                items.append(_Item("text", " ".join(run), ""))
            run.clear()

        for t in toks:
            kind, v = _classify_token(t, style)
            soft = kind == "boiler" and v not in HARD_BOILER
            if kind == "word" or soft:
                run.append(t.strip(".,;:()[]{}\"'"))
            else:
                if kind in ("phone", "mask", "rrn", "id", "ifsc") and run and run[-1].upper() in _LABEL_WORDS:
                    run.pop()                                  # "JOHN DOE MOBILE 98XXXX" : MOBILE labels the number, it is not part of the name
                flush()
                if items and kind == "boiler" and items[-1].kind == "boiler":
                    items[-1].text += " " + (v or t)
                    items[-1].value = v or t           # remember the LAST boiler word (TO / FROM matter)
                else:
                    items.append(_Item(kind, t, v))
        flush()
    return items


def _person_likeness(tokens: list[str]) -> float:
    first, sur = seeds.person_tokens()
    hits = sum(1 for t in tokens if t in first or t in sur)
    return hits / max(1, len(tokens))


def _score_text(item: _Item, prev: _Item | None, nxt: _Item | None) -> float:
    text = item.text
    toks = tokens_of(text)
    if not toks:
        return -9.0
    letters = sum(ch.isalpha() for ch in text)
    total = len(text.replace(" ", "")) or 1
    alpha = letters / total
    s = 2.0 * alpha
    if letters < 2:
        s -= 3.0
    if all(t in REMARK_WORDS or t in BOILER_TOKENS for t in toks):
        s -= 2.6
    elif any(t in REMARK_WORDS for t in toks) and len(toks) == 1:
        s -= 1.5
    if any(t in seeds.business_words() for t in toks):
        s += 0.8
    pl = _person_likeness(toks)
    if pl > 0 and 1 <= len(toks) <= 4:
        s += 0.4 + 0.8 * pl
    if len(toks) >= 2:
        s += 0.3
    if any(ch.isdigit() for ch in text):
        s -= 0.5 + 0.2 * sum(ch.isdigit() for ch in text) / max(1, len(text))
    s += min(len(text), 20) / 40.0
    prev_is_prep = prev is not None and prev.kind == "boiler" and prev.value in _PREP_TO_FROM
    if text.isupper():
        s += 0.8
    elif text.islower():
        s -= 3.5                      # typed free text ("bday treat") - banks print payee names in capitals / title case
    elif not prev_is_prep:
        s -= 0.6                      # mixed case ("Add money") after something other than "paid to": more likely a typed remark
    # context: what sits right before / after
    if prev is not None:
        pk = prev.kind
        if pk == "rrn":
            s += 1.6
        elif pk == "marker":
            s += 1.1
        elif pk == "vpa":
            s += 1.0
        elif pk == "phone":
            s += 0.6
        elif pk == "mode" or pk == "dir":
            s += 0.4
        elif pk == "boiler" and prev.value in _PREP_TO_FROM:
            s += 1.9
    if nxt is not None:
        nk = nxt.kind
        if nk == "vpa":
            s += 0.6
        elif nk == "bank":
            s += 0.4
        elif nk == "phone":
            s += 0.3
    return s


# On a structured UPI payment (it has a reference number or a UPI address) words like FEE / LOAN / EMI belong to the payer's
# typed remark ("mess fee", "gym fee", "loan return"), not to the bank - so only these flags are read from such lines.
_FLAGS_FOR_STRUCTURED = {"refund", "autopay", "wallet_topup", "card_bill", "self"}


def _detect_flags(u: str, structured: bool) -> list[str]:
    flags: list[str] = []
    if _RE_INTEREST.search(u):
        flags.append("interest")
    if _RE_CHARGES.search(u):
        flags.append("bank_charge")
    if _RE_REFUND.search(u):
        flags.append("refund")
    if _RE_WALLET.search(u):
        flags.append("wallet_topup")
    if _RE_CARD_BILL.search(u):
        flags.append("card_bill")
    if _RE_SELF.search(u):
        flags.append("self")
    if _RE_SALARY.search(u):
        flags.append("income_hint")
    if _RE_EMI.search(u):
        flags.append("emi")
    if _RE_ACH.search(u):
        flags.append("autopay")
    if structured:
        flags = [f for f in flags if f in _FLAGS_FOR_STRUCTURED]
    return flags


def parse(raw: object) -> Parsed:
    """Parse one narration. Never raises."""
    try:
        return _parse(raw)
    except Exception as e:  # pragma: no cover - defensive: a weird row must not kill an import
        p = Parsed(raw=str(raw)[:300])
        p.notes.append(f"Parser fell back to 'unknown' ({type(e).__name__}).")
        return p


def _parse(raw: object) -> Parsed:
    s = norm_ws("" if raw is None else str(raw))
    p = Parsed(raw=s)
    if not s or s.lower() in ("nan", "none", "null", "-"):
        p.notes.append("Empty narration.")
        return p
    u = s.upper()
    style, pieces = _split(s)
    p.template = style
    allow_hy = style not in ("dash",)
    items: list[_Item] = []
    for piece in pieces:
        items.extend(_items_from_piece(piece, style, allow_hy))

    # ---- anchors -----------------------------------------------------------------------------------------------
    for it in items:
        if it.kind == "vpa" and it.value not in p.vpas:
            p.vpas.append(it.value)
        elif it.kind == "rrn" and not p.rrn and (len(it.value) == 12 or p.rrn is None):
            p.rrn = it.value
        elif it.kind == "marker" and not p.marker:
            p.marker = it.value
        elif it.kind == "ifsc" and not p.ifsc:
            p.ifsc = it.value
        elif it.kind == "phone" and not p.phone:
            p.phone = it.value
        elif it.kind == "bank" and not p.bank_hint:
            code = it.value
            p.bank_hint = BANK_CODES.get(code, code.title())
    # prefer a 12-digit RRN over other long numbers
    twelve = [it.value for it in items if it.kind == "rrn" and len(it.value) == 12 and it.value.isdigit()]
    if twelve:
        p.rrn = twelve[0]
    if p.ifsc and not p.bank_hint:
        p.bank_hint = BANK_CODES.get(p.ifsc[:4])
    if p.vpas:
        p.vpa = p.vpas[0]
        local, handle = vpa_parts(p.vpa)
        p.handle = handle
        p.app = HANDLE_APP.get(handle.lower())

    # ---- direction hint ------------------------------------------------------------------------------------------
    words = set(tokens_of(s))
    deb = bool(words & {"DR", "DEBIT", "DEBITED", "PAID", "SENT", "WDL"}) or "TO TRANSFER" in u
    cre = bool(words & {"CR", "CREDIT", "CREDITED", "RECEIVED", "DEPOSIT"}) or "BY TRANSFER" in u
    if deb and not cre:
        p.dir_hint = "debit"
    elif cre and not deb:
        p.dir_hint = "credit"

    # ---- mode ----------------------------------------------------------------------------------------------------
    modes = [it.value for it in items if it.kind == "mode"]
    if _RE_MODE_ATM.search(u) and "UPI" not in modes:
        p.mode = "ATM"
    elif "UPI" in modes or p.vpa:
        p.mode = "UPI"
    elif modes:
        p.mode = modes[0]
    elif _RE_MODE_POS.search(u):
        p.mode = "POS"
    elif _RE_ACH.search(u):
        p.mode = "ACH"
    structured = bool(p.rrn or p.vpa) and p.mode == "UPI"
    p.flags = _detect_flags(u, structured)
    if p.mode == "ATM" and "atm" not in p.flags:
        p.flags.append("atm")

    # ---- choose the payee ----------------------------------------------------------------------------------------
    cands: list[tuple[float, int]] = []
    for i, it in enumerate(items):
        if it.kind != "text":
            continue
        prev = items[i - 1] if i > 0 else None
        nxt = items[i + 1] if i + 1 < len(items) else None
        cands.append((_score_text(it, prev, nxt), i))
    cands.sort(reverse=True)
    best_score = cands[0][0] if cands else -9.0
    threshold = 1.7
    if p.mode in ("ATM",) or ("bank_charge" in p.flags and not p.vpa) or ("interest" in p.flags and not p.vpa):
        threshold = 99.0                      # these lines have no counterparty
    name_idx = None
    if cands and best_score >= threshold:
        name_idx = cands[0][1]
        p.payee_raw = items[name_idx].text.strip()
        p.payee_source, p.name_score = "text", round(best_score, 2)
        words = p.payee_raw.split()
        while len(words) >= 2 and words[-1].upper() in _TRAILING_LABELS:          # "ABC CORP INV" + "2024" + "001": a dash split the label from its number
            words.pop()
        p.payee_raw = " ".join(words)
        prev = items[name_idx - 1] if name_idx > 0 else None
        if prev is not None and prev.kind == "rrn":
            p.notes.append("Payee name = the text right after the reference number.")
        elif prev is not None and prev.kind == "boiler" and prev.value in _PREP_TO_FROM:
            p.notes.append(f"Payee name = the text after '{prev.value.lower()}'.")
        elif prev is not None and prev.kind in ("vpa", "marker"):
            p.notes.append("Payee name = the text next to the payment address / P2M-P2A marker.")
        else:
            p.notes.append("Payee name = the most name-like text in the narration.")
    if p.mode == "ACH" and re.match(r"^[DC] ", p.payee_raw):            # "ACH D- NETFLIX": D = debit marker
        p.dir_hint = p.dir_hint or ("debit" if p.payee_raw[0] == "D" else "credit")
        p.payee_raw = p.payee_raw[2:].strip()
    if not structured and set(p.flags) & {"wallet_topup", "bank_charge", "interest", "atm"}:
        p.payee_raw = ""                                                 # these lines have no real counterparty
        name_idx = None
    # remarks: the other text items that are not boiler-plate
    rem = []
    for sc, i in cands:
        if i == name_idx:
            continue
        t = items[i].text.strip()
        if sc > -1.5 and len(t) >= 2:
            rem.append(t)
    p.remarks = " / ".join(rem[:3])

    # fallback identity: the UPI address itself ("swiggy@icici" -> "swiggy")
    if not p.payee_raw and p.vpa:
        local, _h = vpa_parts(p.vpa)
        if not re.fullmatch(r"\d{8,}", local):
            guess = re.sub(r"[._\-]+", " ", re.sub(r"\d{4,}", " ", local)).strip()
            gtoks = set(tokens_of(guess))
            if len(guess) >= 3 and not guess.lower().startswith(("paytmqr", "paytm")) and not (gtoks and gtoks <= QR_PROVIDER_WORDS):
                p.payee_raw = guess.upper()
                p.payee_source = "vpa"
                p.notes.append("No readable name in the narration - using the UPI address as the identity.")
    if p.payee_raw == "" and p.mode in ("ATM",):
        p.notes.append("ATM cash withdrawal - no counterparty.")

    # ---- notes + confidence --------------------------------------------------------------------------------------
    conf = 0.25
    if p.rrn:
        conf += 0.2
        p.notes.insert(0, f"Reference number {p.rrn[:3]}…{p.rrn[-3:]} found." if len(p.rrn) >= 9 else "Reference found.")
    if p.vpa:
        conf += 0.2
        app = f" ({p.app})" if p.app else ""
        p.notes.append(f"UPI address {mask_vpa(p.vpa)}{app}.")
    if p.marker:
        conf += 0.1
        meaning = {"P2M": "a payment to a merchant", "P2A": "a payment to a bank account (person-to-account)",
                   "P2P": "a person-to-person transfer", "P2V": "a payment to a UPI address"}[p.marker]
        p.notes.append(f"Marker {p.marker}: {meaning}.")
    if p.payee_raw:
        conf += 0.25 if name_idx is not None else 0.1
    if p.mode != "OTHER":
        conf += 0.05
    p.confidence = round(min(conf, 1.0), 2)
    if not p.notes:
        p.notes.append("Nothing recognisable in this narration - treating it as free text.")
    return p


def parse_many(descs) -> list[Parsed]:
    return [parse(d) for d in descs]
