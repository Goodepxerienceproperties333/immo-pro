"""CODA file parser for Belgian bank statements (CODA 2.x format)"""

from datetime import datetime


def parse_coda_file(content: str) -> dict:
    """Parse a CODA file and return structured data."""
    lines = content.strip().split('\n')
    result = {
        "header": {},
        "old_balance": {},
        "movements": [],
        "new_balance": {},
        "summary": {}
    }
    current_movement = None

    for line in lines:
        if len(line) < 2:
            continue
        record_type = line[0]

        if record_type == '0':
            result["header"] = parse_header(line)
        elif record_type == '1':
            result["old_balance"] = parse_old_balance(line)
        elif record_type == '2':
            sub_type = line[1] if len(line) > 1 else '1'
            if sub_type == '1':
                if current_movement:
                    result["movements"].append(current_movement)
                current_movement = parse_movement_21(line)
            elif sub_type == '2' and current_movement:
                parse_movement_22(line, current_movement)
            elif sub_type == '3' and current_movement:
                parse_movement_23(line, current_movement)
        elif record_type == '3':
            pass  # Information records - skip for now
        elif record_type == '8':
            result["new_balance"] = parse_new_balance(line)
        elif record_type == '9':
            result["summary"] = parse_trailer(line)

    if current_movement:
        result["movements"].append(current_movement)

    return result


def safe_slice(line, start, end):
    """Safely slice a string, returning empty string if out of bounds."""
    return line[start:end].strip() if len(line) >= end else line[start:].strip() if len(line) > start else ""


def parse_date(date_str):
    """Parse CODA date format DDMMYY."""
    date_str = date_str.strip()
    if len(date_str) == 6 and date_str.isdigit():
        day = int(date_str[0:2])
        month = int(date_str[2:4])
        year = int(date_str[4:6])
        year = year + 2000 if year < 50 else year + 1900
        try:
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


def parse_amount(sign_and_amount):
    """Parse CODA amount: first char is sign (0=credit, 1=debit), rest is amount in cents."""
    if not sign_and_amount or len(sign_and_amount) < 2:
        return 0.0
    sign = sign_and_amount[0]
    amount_str = sign_and_amount[1:].strip()
    if not amount_str.isdigit():
        return 0.0
    amount = int(amount_str) / 100.0
    return -amount if sign == '1' else amount


def parse_header(line):
    """Record type 0: Header."""
    return {
        "creation_date": parse_date(safe_slice(line, 5, 11)),
        "bank_id": safe_slice(line, 11, 14),
        "application_code": safe_slice(line, 14, 15),
        "duplicate": safe_slice(line, 16, 17) == 'D',
        "reference": safe_slice(line, 24, 34),
        "addressee": safe_slice(line, 34, 60),
        "bic": safe_slice(line, 60, 71),
        "account_holder": safe_slice(line, 71, 97),
    }


def parse_old_balance(line):
    """Record type 1: Old balance."""
    account_raw = safe_slice(line, 5, 42)
    # Try to extract IBAN or BE account
    account = account_raw.replace(" ", "")
    return {
        "account_number": account,
        "statement_number": safe_slice(line, 2, 5),
        "sign": "credit" if safe_slice(line, 42, 43) == '0' else "debit",
        "balance": parse_amount(safe_slice(line, 42, 58)),
        "date": parse_date(safe_slice(line, 58, 64)),
        "currency": safe_slice(line, 97, 100) or "EUR",
    }


def parse_movement_21(line):
    """Record type 21: Movement part 1."""
    sequence = safe_slice(line, 2, 6)
    detail = safe_slice(line, 6, 10)
    ref = safe_slice(line, 10, 31)
    amount = parse_amount(safe_slice(line, 31, 47))
    value_date = parse_date(safe_slice(line, 47, 53))
    transaction_code = safe_slice(line, 53, 61)
    communication = safe_slice(line, 61, 115)
    entry_date = parse_date(safe_slice(line, 115, 121))

    return {
        "sequence": sequence,
        "detail": detail,
        "reference": ref,
        "amount": amount,
        "value_date": value_date,
        "entry_date": entry_date,
        "transaction_code": transaction_code,
        "communication": communication,
        "counterparty_name": "",
        "counterparty_account": "",
        "type": "credit" if amount >= 0 else "debit",
    }


def parse_movement_22(line, movement):
    """Record type 22: Movement part 2 - communication continuation."""
    comm = safe_slice(line, 10, 63)
    if comm:
        movement["communication"] = (movement.get("communication", "") + " " + comm).strip()


def parse_movement_23(line, movement):
    """Record type 23: Movement part 3 - counterparty info."""
    movement["counterparty_account"] = safe_slice(line, 10, 47).replace(" ", "")
    movement["counterparty_name"] = safe_slice(line, 47, 82)
    address = safe_slice(line, 82, 125)
    if address:
        movement["counterparty_address"] = address


def parse_new_balance(line):
    """Record type 8: New balance."""
    return {
        "sign": "credit" if safe_slice(line, 41, 42) == '0' else "debit",
        "balance": parse_amount(safe_slice(line, 41, 57)),
        "date": parse_date(safe_slice(line, 57, 63)),
    }


def parse_trailer(line):
    """Record type 9: Trailer."""
    return {
        "num_records": safe_slice(line, 1, 7),
        "debit_total": parse_amount("1" + safe_slice(line, 22, 37)),
        "credit_total": parse_amount("0" + safe_slice(line, 37, 52)),
    }
