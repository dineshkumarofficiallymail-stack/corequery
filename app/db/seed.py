"""Generate a deterministic synthetic core-banking dataset.

Run:  python -m app.db.seed            (creates data/corequery.db)
Everything is seeded with a fixed random seed so evaluation results are reproducible.
"""
from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from app.config import settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
RNG = random.Random(42)
TODAY = date(2026, 9, 30)

BRANCHES = [
    ("MUM001", "Fort Main Branch", "Mumbai", "Maharashtra"),
    ("MUM002", "Andheri West", "Mumbai", "Maharashtra"),
    ("NAG001", "Sitabuldi", "Nagpur", "Maharashtra"),
    ("PUN001", "Shivaji Nagar", "Pune", "Maharashtra"),
    ("BLR001", "MG Road", "Bengaluru", "Karnataka"),
    ("BLR002", "Whitefield", "Bengaluru", "Karnataka"),
    ("CHE001", "T Nagar", "Chennai", "Tamil Nadu"),
    ("MDU001", "Anna Nagar", "Madurai", "Tamil Nadu"),
    ("AHM001", "Navrangpura", "Ahmedabad", "Gujarat"),
    ("MEH001", "Mehsana Main", "Mehsana", "Gujarat"),
    ("HYD001", "Banjara Hills", "Hyderabad", "Telangana"),
    ("DEL001", "Connaught Place", "New Delhi", "Delhi"),
]

FIRST = ["Aarav", "Vihaan", "Aditya", "Rohan", "Karthik", "Suresh", "Ramesh", "Manoj", "Deepak", "Arjun",
         "Priya", "Ananya", "Kavya", "Meera", "Sneha", "Divya", "Lakshmi", "Pooja", "Neha", "Aishwarya",
         "Farhan", "Imran", "Zoya", "Sameer", "Joseph", "Maria", "Harpreet", "Gurpreet", "Devansh", "Ishaan"]
LAST = ["Sharma", "Verma", "Iyer", "Nair", "Reddy", "Patel", "Shah", "Mehta", "Kumar", "Singh", "Rao",
        "Pillai", "Joshi", "Desai", "Chauhan", "Khan", "Sheikh", "Fernandes", "Das", "Bose"]
CORPORATES = ["Tata Power Ltd", "Reliance Jio Infocomm", "LIC of India", "HDFC Life Insurance", "Bajaj Finance",
              "Airtel Payments", "Adani Electricity", "Maharashtra Gas", "SBI Cards", "Zerodha Broking",
              "Groww Mutual Fund", "Tata AIA", "ICICI Prudential", "Mahindra Finance"]
SPONSOR_BANKS = ["HDFC Bank", "ICICI Bank", "Axis Bank", "State Bank of India", "Kotak Mahindra Bank", "Yes Bank"]
RETURN_REASONS = [("01", "Insufficient funds"), ("04", "Account closed"), ("05", "No such account"),
                  ("13", "Mandate not registered"), ("52", "Account frozen"), ("70", "Amount exceeds mandate limit")]
NARRATIONS = {
    "UPI": ["UPI/{ref}/Payment to {name}", "UPI/{ref}/Swiggy", "UPI/{ref}/Zomato", "UPI/{ref}/BigBasket"],
    "IMPS": ["IMPS/{ref}/Transfer", "IMPS/{ref}/Rent"],
    "NEFT": ["NEFT/{ref}/Salary", "NEFT/{ref}/Vendor payment"],
    "RTGS": ["RTGS/{ref}/Property payment"],
    "ATM": ["ATM WDL {city}", "ATM WDL OFFSITE"],
    "POS": ["POS/{ref}/Amazon", "POS/{ref}/Reliance Retail", "POS/{ref}/Fuel"],
    "BRANCH": ["CASH DEPOSIT", "CASH WITHDRAWAL", "CHEQUE CLEARING"],
    "NACH": ["NACH/{corp}/EMI", "NACH/{corp}/Premium", "NACH/{corp}/Bill"],
    "INTERNET_BANKING": ["IB/{ref}/Fund transfer", "IB/{ref}/Bill payment"],
}


def rand_date(start: date, end: date) -> date:
    return start + timedelta(days=RNG.randint(0, (end - start).days))


def build(db_path: Path) -> None:
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    cur = con.cursor()

    # Branches
    for i, (code, name, city, state) in enumerate(BRANCHES, start=1):
        cur.execute("INSERT INTO branches VALUES (?,?,?,?,?,?)",
                    (i, code, name, city, state, f"CQBK0{code[-3:]}{i:03d}"))

    # Customers
    n_customers = 400
    for cid in range(1, n_customers + 1):
        dob = rand_date(date(1945, 1, 1), date(2004, 12, 31))
        age = (TODAY - dob).days // 365
        ctype = "CORPORATE" if RNG.random() < 0.08 else ("SENIOR_CITIZEN" if age >= 60 else "INDIVIDUAL")
        name = (f"{RNG.choice(FIRST)} {RNG.choice(LAST)}" if ctype != "CORPORATE"
                else f"{RNG.choice(LAST)} {RNG.choice(['Traders', 'Enterprises', 'Textiles', 'Agro', 'Logistics'])} Pvt Ltd")
        branch = RNG.randint(1, len(BRANCHES))
        cur.execute("INSERT INTO customers VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            cid, f"CIF{100000 + cid}", name, dob.isoformat(),
            RNG.choice(["M", "F", "M", "F", "O"]) if ctype != "CORPORATE" else "O",
            ctype,
            RNG.choices(["VERIFIED", "PENDING", "EXPIRED"], weights=[85, 9, 6])[0],
            RNG.choices(["LOW", "MEDIUM", "HIGH"], weights=[70, 24, 6])[0],
            branch, BRANCHES[branch - 1][2], rand_date(date(2015, 1, 1), date(2026, 8, 31)).isoformat()))

    # Accounts
    acc_id = 0
    accounts: list[tuple] = []
    for cid in range(1, n_customers + 1):
        cust_branch, onboarded = cur.execute(
            "SELECT home_branch_id, onboarded_on FROM customers WHERE customer_id=?", (cid,)).fetchone()
        onboarded_d = date.fromisoformat(onboarded)
        n_acc = RNG.choices([1, 2, 3, 4], weights=[45, 35, 15, 5])[0]
        types = ["SAVINGS"] + RNG.sample(["CURRENT", "TERM_DEPOSIT", "LOAN", "TERM_DEPOSIT"], k=n_acc - 1)
        for atype in types:
            acc_id += 1
            opened = rand_date(onboarded_d, min(TODAY, onboarded_d + timedelta(days=2000)))
            status = RNG.choices(["ACTIVE", "DORMANT", "FROZEN", "CLOSED"], weights=[84, 8, 3, 5])[0]
            closed = rand_date(opened, TODAY).isoformat() if status == "CLOSED" else None
            rate = maturity = sanctioned = None
            if atype == "SAVINGS":
                product, balance, rate = RNG.choice(["SB-REG", "SB-PREM", "SB-SAL"]), round(RNG.lognormvariate(10.5, 1.1), 2), 3.5
            elif atype == "CURRENT":
                product, balance = RNG.choice(["CA-REG", "CA-PREM"]), round(RNG.lognormvariate(12, 1.2), 2)
            elif atype == "TERM_DEPOSIT":
                months = RNG.choice([6, 12, 24, 36, 60])
                product, balance, rate = f"TD-{months}M", round(RNG.choice([50000, 100000, 200000, 500000, 1000000]) * RNG.uniform(0.8, 3), 2), {6: 6.0, 12: 6.8, 24: 7.0, 36: 7.1, 60: 7.25}[months]
                maturity = (opened + timedelta(days=30 * months)).isoformat()
            else:
                product = RNG.choice(["LN-HOME", "LN-AUTO", "LN-PERSONAL", "LN-GOLD"])
                sanctioned = round({"LN-HOME": RNG.uniform(15, 80) * 1e5, "LN-AUTO": RNG.uniform(3, 15) * 1e5,
                                    "LN-PERSONAL": RNG.uniform(1, 10) * 1e5, "LN-GOLD": RNG.uniform(0.5, 5) * 1e5}[product], 2)
                balance = round(sanctioned * RNG.uniform(0.2, 0.95), 2)
                rate = {"LN-HOME": 8.5, "LN-AUTO": 9.25, "LN-PERSONAL": 12.5, "LN-GOLD": 10.0}[product]
                maturity = (opened + timedelta(days=365 * RNG.choice([3, 5, 7, 15, 20]))).isoformat()
            if status == "CLOSED":
                balance = 0.0
            row = (acc_id, f"{cust_branch:02d}{acc_id:012d}", cid, cust_branch, atype, product, "INR", balance,
                   status, opened.isoformat(), closed, rate, maturity, sanctioned)
            cur.execute("INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            accounts.append(row)

    # Transactions (operative accounts only)
    txn_id = 0
    ref_counter = 900000
    for acc in accounts:
        aid, _, cid, bid, atype, _, _, balance, status, opened, *_ = acc
        if atype not in ("SAVINGS", "CURRENT") or status == "CLOSED":
            continue
        n_txn = RNG.randint(8, 60) if atype == "SAVINGS" else RNG.randint(20, 120)
        running = max(balance * RNG.uniform(0.5, 1.5), 1000)
        start = max(date.fromisoformat(opened), TODAY - timedelta(days=365))
        for _ in range(n_txn):
            txn_id += 1
            ref_counter += 1
            d = rand_date(start, TODAY)
            channel = RNG.choices(["UPI", "IMPS", "NEFT", "ATM", "POS", "BRANCH", "NACH", "INTERNET_BANKING", "RTGS"],
                                  weights=[35, 12, 10, 10, 12, 8, 6, 5, 2])[0]
            ttype = "CREDIT" if RNG.random() < (0.35 if channel != "ATM" else 0.0) else "DEBIT"
            amount = round({"UPI": RNG.lognormvariate(6.5, 1.0), "IMPS": RNG.lognormvariate(8.5, 0.8),
                            "NEFT": RNG.lognormvariate(9.5, 0.9), "RTGS": RNG.uniform(2e5, 2e6),
                            "ATM": RNG.choice([500, 1000, 2000, 5000, 10000]), "POS": RNG.lognormvariate(7, 0.9),
                            "BRANCH": RNG.lognormvariate(9, 1.0), "NACH": RNG.lognormvariate(8.3, 0.6),
                            "INTERNET_BANKING": RNG.lognormvariate(8.8, 0.9)}[channel], 2)
            st = RNG.choices(["SUCCESS", "FAILED", "REVERSED", "PENDING"], weights=[93, 4, 2, 1])[0]
            if st == "SUCCESS":
                running = running + amount if ttype == "CREDIT" else max(running - amount, 0)
            narr = RNG.choice(NARRATIONS[channel]).format(ref=ref_counter, name=RNG.choice(FIRST),
                                                           city=BRANCHES[bid - 1][2], corp=RNG.choice(CORPORATES))
            cur.execute("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                txn_id, aid, d.isoformat(), (d + timedelta(days=RNG.choice([0, 0, 0, 1]))).isoformat(),
                ttype, channel, amount, round(running, 2), narr, f"REF{ref_counter}", st))

    # NACH mandates + presentments
    mandate_id = pres_id = 0
    for acc in accounts:
        aid, _, cid, *_ = acc
        if acc[4] not in ("SAVINGS", "CURRENT") or RNG.random() > 0.35:
            continue
        for _ in range(RNG.choice([1, 1, 2])):
            mandate_id += 1
            start = rand_date(date(2023, 1, 1), date(2026, 6, 30))
            status = RNG.choices(["ACTIVE", "CANCELLED", "EXPIRED", "REJECTED", "PENDING"], weights=[70, 12, 8, 5, 5])[0]
            max_amt = RNG.choice([2000, 5000, 10000, 25000, 50000, 100000])
            freq = RNG.choices(["MONTHLY", "QUARTERLY", "HALF_YEARLY", "YEARLY", "AS_PRESENTED"], weights=[70, 8, 5, 7, 10])[0]
            cur.execute("INSERT INTO nach_mandates VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                mandate_id, f"UMRN{RNG.choice(SPONSOR_BANKS)[:4].upper().replace(' ', '')}{mandate_id:012d}",
                aid, RNG.choice(SPONSOR_BANKS), RNG.choice(CORPORATES), "DEBIT" if RNG.random() < 0.92 else "CREDIT",
                freq, max_amt, start.isoformat(),
                (start + timedelta(days=365 * RNG.choice([1, 3, 5, 10]))).isoformat() if RNG.random() < 0.8 else None, status))
            if status in ("ACTIVE", "CANCELLED", "EXPIRED"):
                months = min(((TODAY - start).days // 30), 18)
                for m in range(months):
                    pres_id += 1
                    pdate = start + timedelta(days=30 * (m + 1))
                    if pdate > TODAY:
                        break
                    amount = round(max_amt * RNG.uniform(0.3, 1.0), 2)
                    pst = RNG.choices(["SUCCESS", "RETURNED", "PENDING"], weights=[86, 12, 2])[0]
                    code, reason = RNG.choices(RETURN_REASONS, weights=[60, 8, 5, 10, 7, 10])[0] if pst == "RETURNED" else (None, None)
                    cur.execute("INSERT INTO nach_presentments VALUES (?,?,?,?,?,?,?,?)",
                                (pres_id, mandate_id, pdate.isoformat(), amount, pst, code, reason, None))

    # Charges
    charge_id = 0
    for acc in accounts:
        aid, _, cid, _, atype, _, _, balance, status, *_ = acc
        if status == "CLOSED":
            continue
        for _ in range(RNG.choices([0, 1, 2, 3], weights=[40, 35, 18, 7])[0]):
            charge_id += 1
            if atype == "LOAN":
                ctype, amt = "LOAN_LATE_PAYMENT", RNG.choice([500, 750, 1000])
            elif atype == "TERM_DEPOSIT":
                ctype, amt = "TD_PREMATURE", round(balance * 0.01, 2)
            else:
                ctype = RNG.choices(["MIN_BALANCE", "ATM_EXCESS", "CHEQUE_BOOK", "NACH_RETURN", "SMS_ALERT", "ACCOUNT_MAINTENANCE"],
                                    weights=[30, 20, 10, 20, 15, 5])[0]
                amt = {"MIN_BALANCE": RNG.choice([100, 250, 500]), "ATM_EXCESS": 21, "CHEQUE_BOOK": 75,
                       "NACH_RETURN": RNG.choice([250, 500]), "SMS_ALERT": 15, "ACCOUNT_MAINTENANCE": 200}[ctype]
            cur.execute("INSERT INTO charges VALUES (?,?,?,?,?,?,?,?)", (
                charge_id, aid, ctype, rand_date(TODAY - timedelta(days=365), TODAY).isoformat(), amt,
                round(amt * 0.18, 2), RNG.choices(["COLLECTED", "WAIVED", "PENDING"], weights=[80, 12, 8])[0], None))

    con.commit()
    counts = {t: cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ["branches", "customers", "accounts", "transactions", "nach_mandates", "nach_presentments", "charges"]}
    con.close()
    print(f"Database created at {db_path}")
    for t, c in counts.items():
        print(f"  {t:<20}{c:>8}")


if __name__ == "__main__":
    build(Path(settings.database_path))
