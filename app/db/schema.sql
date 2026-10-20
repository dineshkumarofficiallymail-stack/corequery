-- CoreQuery synthetic core-banking schema (SQLite)
-- Deliberately mirrors real core-banking concepts: branches, customers, CASA/TD/Loan accounts,
-- transactions, NACH mandates & presentments, charges. All data is synthetic.

PRAGMA foreign_keys = ON;

CREATE TABLE branches (
    branch_id      INTEGER PRIMARY KEY,
    branch_code    TEXT NOT NULL UNIQUE,          -- e.g. 'MUM001'
    branch_name    TEXT NOT NULL,
    city           TEXT NOT NULL,
    state          TEXT NOT NULL,
    ifsc_code      TEXT NOT NULL UNIQUE
);

CREATE TABLE customers (
    customer_id    INTEGER PRIMARY KEY,
    cif_number     TEXT NOT NULL UNIQUE,          -- Customer Information File number
    full_name      TEXT NOT NULL,
    date_of_birth  DATE NOT NULL,
    gender         TEXT CHECK (gender IN ('M','F','O')),
    customer_type  TEXT NOT NULL CHECK (customer_type IN ('INDIVIDUAL','CORPORATE','SENIOR_CITIZEN')),
    kyc_status     TEXT NOT NULL CHECK (kyc_status IN ('VERIFIED','PENDING','EXPIRED')),
    risk_category  TEXT NOT NULL CHECK (risk_category IN ('LOW','MEDIUM','HIGH')),
    home_branch_id INTEGER NOT NULL REFERENCES branches(branch_id),
    city           TEXT NOT NULL,
    onboarded_on   DATE NOT NULL
);

CREATE TABLE accounts (
    account_id       INTEGER PRIMARY KEY,
    account_number   TEXT NOT NULL UNIQUE,
    customer_id      INTEGER NOT NULL REFERENCES customers(customer_id),
    branch_id        INTEGER NOT NULL REFERENCES branches(branch_id),
    account_type     TEXT NOT NULL CHECK (account_type IN ('SAVINGS','CURRENT','TERM_DEPOSIT','LOAN')),
    product_code     TEXT NOT NULL,               -- e.g. 'SB-REG','CA-PREM','TD-12M','LN-HOME'
    currency         TEXT NOT NULL DEFAULT 'INR',
    balance          REAL NOT NULL DEFAULT 0,     -- current ledger balance (loans: outstanding principal, negative sign not used)
    status           TEXT NOT NULL CHECK (status IN ('ACTIVE','DORMANT','FROZEN','CLOSED')),
    opened_on        DATE NOT NULL,
    closed_on        DATE,
    interest_rate    REAL,                        -- annual %, applies to SAVINGS/TD/LOAN
    maturity_date    DATE,                        -- TERM_DEPOSIT and LOAN only
    sanctioned_amount REAL                        -- LOAN only
);

CREATE TABLE transactions (
    transaction_id   INTEGER PRIMARY KEY,
    account_id       INTEGER NOT NULL REFERENCES accounts(account_id),
    txn_date         DATE NOT NULL,
    value_date       DATE NOT NULL,
    txn_type         TEXT NOT NULL CHECK (txn_type IN ('CREDIT','DEBIT')),
    channel          TEXT NOT NULL CHECK (channel IN ('BRANCH','ATM','UPI','IMPS','NEFT','RTGS','NACH','POS','INTERNET_BANKING')),
    amount           REAL NOT NULL CHECK (amount > 0),
    balance_after    REAL NOT NULL,
    narration        TEXT,
    reference_number TEXT NOT NULL UNIQUE,
    status           TEXT NOT NULL CHECK (status IN ('SUCCESS','FAILED','REVERSED','PENDING'))
);

CREATE TABLE nach_mandates (
    mandate_id        INTEGER PRIMARY KEY,
    umrn              TEXT NOT NULL UNIQUE,       -- Unique Mandate Reference Number
    account_id        INTEGER NOT NULL REFERENCES accounts(account_id),
    sponsor_bank      TEXT NOT NULL,
    corporate_name    TEXT NOT NULL,              -- utility / lender collecting via NACH
    mandate_type      TEXT NOT NULL CHECK (mandate_type IN ('DEBIT','CREDIT')),
    frequency         TEXT NOT NULL CHECK (frequency IN ('MONTHLY','QUARTERLY','HALF_YEARLY','YEARLY','AS_PRESENTED')),
    max_amount        REAL NOT NULL,
    start_date        DATE NOT NULL,
    end_date          DATE,
    status            TEXT NOT NULL CHECK (status IN ('ACTIVE','CANCELLED','EXPIRED','REJECTED','PENDING'))
);

CREATE TABLE nach_presentments (
    presentment_id    INTEGER PRIMARY KEY,
    mandate_id        INTEGER NOT NULL REFERENCES nach_mandates(mandate_id),
    presentment_date  DATE NOT NULL,
    amount            REAL NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('SUCCESS','RETURNED','PENDING')),
    return_reason_code TEXT,                      -- NPCI-style codes, e.g. '01' insufficient funds, '04' account closed
    return_reason     TEXT,
    transaction_id    INTEGER REFERENCES transactions(transaction_id)
);

CREATE TABLE charges (
    charge_id        INTEGER PRIMARY KEY,
    account_id       INTEGER NOT NULL REFERENCES accounts(account_id),
    charge_type      TEXT NOT NULL CHECK (charge_type IN ('MIN_BALANCE','ATM_EXCESS','CHEQUE_BOOK','NACH_RETURN','SMS_ALERT','TD_PREMATURE','LOAN_LATE_PAYMENT','ACCOUNT_MAINTENANCE')),
    charge_date      DATE NOT NULL,
    amount           REAL NOT NULL,
    gst_amount       REAL NOT NULL DEFAULT 0,
    status           TEXT NOT NULL CHECK (status IN ('COLLECTED','WAIVED','PENDING')),
    transaction_id   INTEGER REFERENCES transactions(transaction_id)
);

CREATE INDEX idx_accounts_customer   ON accounts(customer_id);
CREATE INDEX idx_accounts_branch     ON accounts(branch_id);
CREATE INDEX idx_txn_account_date    ON transactions(account_id, txn_date);
CREATE INDEX idx_txn_channel         ON transactions(channel);
CREATE INDEX idx_mandate_account     ON nach_mandates(account_id);
CREATE INDEX idx_presentment_mandate ON nach_presentments(mandate_id);
CREATE INDEX idx_charges_account     ON charges(account_id);
