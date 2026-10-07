"""Generate the prototype's sample knowledge PDFs (fictional 'Demo Bank' content).

Usage: python -m scripts.generate_sample_docs [output_dir]
Uses a tiny built-in PDF writer (Helvetica, WinAnsi) so no extra dependency is needed.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

DOCS: dict[str, tuple[str, str]] = {
    "home_loan_policy.pdf": ("Demo Bank Home Loan Policy", """
Demo Bank Home Loan Policy
Version 3.2 - Effective 1 April 2026. Applies to all retail home loans in India.

1. Purpose and Scope
This policy sets out the terms on which Demo Bank offers home loans for purchase, construction, extension and renovation of residential property to resident and non-resident Indian individuals.

2. Interest Rates
Floating-rate home loans are linked to the Demo Bank Repo Linked Lending Rate (RLLR). Current rates range from 8.40% to 9.65% per annum depending on credit score, loan amount and loan-to-value ratio. Women borrowers receive a concession of 0.05% per annum. Fixed-rate home loans are offered at 9.75% to 10.50% per annum for the first 5 years, after which they convert to floating rates. Interest is reset quarterly on floating-rate loans when the RLLR changes.

3. Foreclosure and Prepayment Charges
No foreclosure charges or part-prepayment penalties are levied on floating-rate home loans availed by individual borrowers, whether the loan is closed from own funds or by balance transfer to another lender, in line with RBI directions. For fixed-rate home loans, foreclosure within 3 years of the first disbursement attracts a charge of 2% of the principal outstanding plus GST. Foreclosure after 3 years attracts a charge of 1% of the principal outstanding plus GST. No charge applies to fixed-rate loans foreclosed from the borrower's own sources after 5 years. Part-prepayment of up to 25% of the principal outstanding in a financial year is free of charge on fixed-rate loans.

4. Processing Fee
A processing fee of 0.50% of the loan amount, subject to a minimum of Rs. 3,000 and a maximum of Rs. 10,000, plus GST, is payable at the time of application. The fee is non-refundable once the loan is sanctioned.

5. Eligibility
Salaried applicants must be aged 21 to 60 years with at least 2 years of total work experience. Self-employed applicants must be aged 25 to 65 years with at least 3 years of business continuity. The maximum loan tenure is 30 years or until the borrower turns 70, whichever is earlier. The loan-to-value ratio is capped at 90% for loans up to Rs. 30 lakh, 80% for loans between Rs. 30 lakh and Rs. 75 lakh, and 75% above Rs. 75 lakh.

6. Documents Required
KYC documents (PAN and Aadhaar or passport), last 3 months salary slips or 2 years income tax returns for self-employed, 6 months bank statements, and property documents including the sale agreement and approved building plan.

7. EMI and Late Payment
EMIs are debited on the 5th of every month by NACH mandate. A late payment charge of 2% per month on the overdue EMI amount applies, subject to a maximum of Rs. 5,000 per instance. A NACH bounce charge of Rs. 500 plus GST applies per dishonoured mandate.
"""),
    "credit_card_terms.pdf": ("Demo Bank Credit Card Terms and Conditions", """
Demo Bank Credit Card Terms and Conditions
Most Important Terms and Conditions (MITC). Effective 1 January 2026.

1. Interest-Free Period
Cardholders get an interest-free period of 20 to 50 days on retail purchases, provided the total amount due is paid in full by the payment due date. Cash withdrawals do not enjoy an interest-free period.

2. Finance Charges
If the total amount due is not paid in full by the due date, finance charges of 3.75% per month (45% per annum) apply on the outstanding balance from the date of each transaction. Cash advances attract finance charges from the date of withdrawal.

3. Minimum Amount Due
The minimum amount due is 5% of the total outstanding or Rs. 200, whichever is higher, plus any EMI instalments, overlimit amount and past dues. Paying only the minimum amount due keeps the account regular but finance charges continue to apply.

4. Late Payment Charges
Late payment charges are levied when the minimum amount due is not paid by the payment due date: nil for total amount due up to Rs. 500; Rs. 500 for Rs. 501 to Rs. 5,000; Rs. 750 for Rs. 5,001 to Rs. 10,000; and Rs. 1,200 above Rs. 10,000. GST applies.

5. Lost or Stolen Cards and Card Blocking
Report a lost or stolen card immediately through the mobile app, the 24x7 helpline or the virtual assistant. Once a card is blocked it cannot be unblocked and a replacement card will be issued. The cardholder has zero liability for fraudulent transactions reported within 3 working days of receiving the transaction alert, in line with RBI guidelines on limiting customer liability.

6. Annual Fee
The joining fee and annual fee for the Demo Bank Platinum card is Rs. 999 plus GST. The annual fee is waived if annual spends exceed Rs. 1,50,000.

7. Disputes
Transaction disputes must be raised within 60 days of the statement date. A temporary credit may be provided while the dispute is investigated.
"""),
    "fees.pdf": ("Demo Bank Schedule of Charges", """
Demo Bank Schedule of Charges
Effective 1 April 2026. All charges are exclusive of GST at 18%.

1. Savings Account Charges
Non-maintenance of average monthly balance in metro and urban branches: 6% of the shortfall, subject to a maximum of Rs. 500 per month. Semi-urban and rural branches: 6% of the shortfall, maximum Rs. 250 per month. Cheque book: first 25 leaves free per year, then Rs. 4 per leaf. Duplicate statement at branch: Rs. 100.

2. ATM Transactions
Five free transactions per month at Demo Bank ATMs. Three free transactions per month at other bank ATMs in the six metro cities and five elsewhere. Beyond the free limit: Rs. 23 per financial transaction and Rs. 10 per non-financial transaction.

3. Fund Transfers
NEFT and RTGS through net banking and the mobile app are free. IMPS: free up to Rs. 1,000; Rs. 5 for Rs. 1,001 to Rs. 1,00,000; Rs. 15 for Rs. 1,00,001 to Rs. 5,00,000. UPI transfers are free.

4. Debit Card
Annual fee for the RuPay Classic debit card is Rs. 150. Card replacement fee is Rs. 200. Declined transaction at another bank's ATM due to insufficient balance: Rs. 25.

5. Loans
Home loan processing fee: 0.50% of the loan amount, minimum Rs. 3,000, maximum Rs. 10,000. Personal loan processing fee: up to 2% of the loan amount. Loan statement or no-dues certificate: free once a year, Rs. 200 thereafter.
"""),
    "savings_account_faq.pdf": ("Demo Bank Savings Account FAQ", """
Demo Bank Savings Account - Frequently Asked Questions
Updated 1 March 2026.

1. What is the minimum balance requirement?
Regular savings accounts require an average monthly balance of Rs. 10,000 in metro and urban branches, Rs. 5,000 in semi-urban branches and Rs. 2,500 in rural branches. Basic Savings Bank Deposit Accounts have no minimum balance requirement.

2. What interest rate is paid on savings accounts?
Balances up to Rs. 1 lakh earn 2.70% per annum. Balances above Rs. 1 lakh earn 3.00% per annum. Interest is calculated on the daily closing balance and credited quarterly.

3. How do I update my KYC?
You can complete re-KYC through the mobile app using Aadhaar-based e-KYC, by visiting any branch with your original officially valid documents, or by video KYC. Re-KYC is required every 2 years for high-risk accounts, 8 years for medium-risk and 10 years for low-risk accounts.

4. How do I add a beneficiary?
Beneficiaries can be added in the mobile app or net banking under Transfers. For your security, a newly added beneficiary is activated after a cooling period of 30 minutes, and transfers to a new beneficiary are limited to Rs. 50,000 in the first 24 hours.

5. What should I do if I notice an unauthorised transaction?
Block your card immediately through the app or the virtual assistant and report the transaction. Under RBI rules, your liability is zero if you report within 3 working days. Never share your OTP, PIN, CVV or passwords with anyone, including bank staff.

6. Can I get a debit card with my account?
Yes. A RuPay Classic debit card is issued free with every savings account. Premium cards are available for a fee.
"""),
}


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(path: Path, title: str, text: str) -> None:
    lines: list[tuple[str, bool]] = []
    for para in text.strip().split("\n"):
        para = para.strip()
        if not para:
            continue
        is_heading = len(para) < 80 and (para[:2].rstrip(".").isdigit() or para == title or para.endswith("?"))
        for ln in textwrap.wrap(para, 92) or [""]:
            lines.append((ln, is_heading))
        lines.append(("", False))
    pages, per_page = [], 52
    for i in range(0, len(lines), per_page):
        pages.append(lines[i : i + per_page])
    objs: list[bytes] = []

    def add(b: bytes) -> int:
        objs.append(b)
        return len(objs)

    catalog = add(b"")
    pages_obj = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    bold = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    kids = []
    for page in pages:
        ops = ["BT", "50 790 Td", "14 TL"]
        for ln, heading in page:
            ops.append(f"/{'F2' if heading else 'F1'} 10.5 Tf ({_esc(ln)}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1", errors="replace")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        kids.append(add(f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 595 842] /Contents {content} 0 R "
                        f"/Resources << /Font << /F1 {font} 0 R /F2 {bold} 0 R >> >> >>".encode()))
    objs[catalog - 1] = f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode()
    objs[pages_obj - 1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    info = add(f"<< /Title ({_esc(title)}) /Producer (bfsi-agent-platform) >>".encode())
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


def main(out_dir: str = "data/sample_documents") -> None:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    for name, (title, text) in DOCS.items():
        write_pdf(d / name, title, text)
        print(f"wrote {d / name}")


if __name__ == "__main__":
    main(*sys.argv[1:])
