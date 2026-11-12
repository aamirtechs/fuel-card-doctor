"""Fuel card transactions CSV audit CLI.

Reads a fuel card transactions CSV export (EFS, Comdata, WEX,
and similar providers all export this shape) and audits it
for the fraud and billing patterns that drain a fleet's fuel
budget: fills over tank capacity, duplicate fills minutes
apart, odometer readings that go backwards, implausible MPG
between consecutive fills, fills with no miles driven, fills
outside operating hours, non-fuel purchases on a fuel card,
prices above the state median, amounts that do not match
gallons times price, one card used on two units in the same
day, missing odometers, duplicate transaction IDs, and bad
numbers or dates.

Fuel theft costs US trucking about $2.5 billion a year, 73%
of commercial fleets report theft incidents, and 3 to 5% of
total fleet fuel spend is lost to fraud. The usual detection
is a paid telematics platform or a manual spreadsheet audit
that takes 2 to 4 days for a 25 to 50 truck fleet. This tool
is the offline audit of the exported transactions: it reads
the file, flags every problem with a rule name and a
severity, and never touches the card provider.

Each problem is a finding with a transaction ID, a rule name,
a severity (error or warning), a message, and a short
suggested action, so a fleet manager who is not a fraud
analyst knows the next step for each flagged transaction.
Errors are the ones that are a live fraud signal or break
the audit; warnings are transactions worth following up.
--check exits 1 when any error is found, and --strict also
fails on warnings.

Zero dependencies, Python 3.10 or newer, standard library only.

Usage:
    python3 fuel_card_doctor.py examples/transactions.csv --as-of 2026-11-11
    python3 fuel_card_doctor.py transactions.csv --json --check
    python3 fuel_card_doctor.py transactions.csv --tank-capacity 240 --min-gap 60
"""

import argparse
import csv
import datetime
import json
import re
import sys
from decimal import Decimal, InvalidOperation

__version__ = "1.0.0"

FUEL_PRODUCTS = frozenset((
    "diesel", "diesel fuel", "diesel #2", "ulsd",
    "ultra low sulfur diesel", "gasoline", "gas", "petrol",
    "def", "diesel exhaust fluid", "diesel exhaust fluid (def)",
    "oil", "engine oil",
))

NON_FUEL_PRODUCTS = frozenset((
    "merchandise", "food", "beverage", "beverages", "snacks",
    "snack", "tobacco", "cigarettes", "lottery", "cash advance",
    "cash_advance", "atm", "atm withdrawal", "gift card",
))

DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
    "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y",
)

TIME_FORMATS = ("%H:%M:%S", "%H:%M")

# Header aliases: the export column names this tool accepts.
COLUMN_ALIASES = {
    "transaction_id": ("transaction_id", "transaction", "id",
                       "txn_id"),
    "date": ("date", "transaction_date", "trans_date"),
    "time": ("time", "transaction_time", "trans_time"),
    "datetime": ("datetime", "timestamp"),
    "card_number": ("card_number", "card", "card_no",
                    "fuel_card"),
    "driver": ("driver", "driver_name"),
    "unit": ("unit", "unit_number", "truck", "truck_number",
             "vehicle", "vehicle_number"),
    "odometer": ("odometer", "odometer_reading", "odo",
                 "mileage"),
    "gallons": ("gallons", "quantity", "qty", "volume"),
    "amount": ("amount", "total", "total_amount", "charge"),
    "price_per_gallon": ("price_per_gallon", "price", "ppg",
                         "unit_price"),
    "product": ("product", "product_name", "fuel_type",
                "purchase_type"),
    "location": ("location", "station", "merchant"),
    "state": ("state", "state_code"),
}

REQUIRED_COLUMNS = ("gallons", "amount")

DEFAULT_TANK_CAPACITY = Decimal("150")
DEFAULT_MIN_GAP_MINUTES = 30
DEFAULT_MIN_MPG = 4.0
DEFAULT_MAX_MPG = 12.0
DEFAULT_HOURS_START = datetime.time(5, 0)
DEFAULT_HOURS_END = datetime.time(23, 0)
PRICE_TOLERANCE = Decimal("0.10")
HIGH_PRICE_MARGIN = Decimal("1.20")
HIGH_PRICE_MIN_GROUP = 3

# Suggested next step per rule, printed with each finding in the
# text output and carried in the JSON "action" field, so a fleet
# manager who is not a fraud analyst knows what to do next.
RULE_ACTIONS = {
    "over-tank-gallons": (
        "compare the receipt with the tank size, then dispute "
        "any gallons the tank cannot hold"),
    "duplicate-fill": (
        "check whether the two fills are one split transaction, "
        "and dispute the second charge if they are not"),
    "odometer-regression": (
        "ask the driver for the current odometer reading and "
        "fix the export, since the mileage is wrong"),
    "non-fuel-purchase": (
        "ask the driver what was bought, then recover the "
        "amount from the driver or dispute the charge"),
    "price-mismatch": (
        "ask the driver for the receipt, then dispute any "
        "overcharge with the card provider"),
    "shared-card": "call the card provider to dispute the charge",
    "duplicate-transaction": (
        "check the card statement so the duplicate is not "
        "paid twice"),
    "invalid-number": (
        "fix the bad value in the export and rerun the audit"),
    "negative-value": (
        "check whether the row is a credit, and dispute the "
        "charge with the card provider if it is not"),
    "invalid-date": (
        "fix the date in the export and rerun the audit"),
    "malformed-row": (
        "re-export the file from the card provider, since "
        "this row is broken"),
    "implausible-mpg": (
        "check the receipts for both fills, since the gallons "
        "or the odometer look wrong"),
    "zero-miles-fill": (
        "ask the driver where the truck was between the two "
        "fills"),
    "off-hours-fill": (
        "ask the driver who made the fill and why it was "
        "outside operating hours"),
    "high-price": (
        "check the receipt for a wrong price, then plan the "
        "route around a cheaper station"),
    "missing-odometer": (
        "ask the driver for the odometer reading so the MPG "
        "checks can run"),
    "missing-card": (
        "add the card number to the export so the purchase "
        "can be tied to a card"),
    "missing-unit": (
        "add the unit number to the export so the purchase "
        "can be tied to a truck"),
    "missing-product": (
        "check the receipt so the purchase can be classified "
        "as fuel or non-fuel"),
    "unknown-product": (
        "check the receipt and add the product to the "
        "built-in fuel or non-fuel list"),
    "future-date": (
        "check the export date, since a future transaction "
        "usually means a wrong date"),
}

DEFAULT_ACTION = (
    "review the transaction against the receipt, then "
    "contact the card provider if the charge looks wrong")


class Finding:
    """One audit finding for one transaction or for the file."""

    def __init__(self, txn_id, rule, severity, message):
        self.txn_id = txn_id
        self.rule = rule
        self.severity = severity
        self.message = message
        self.action = RULE_ACTIONS.get(rule, DEFAULT_ACTION)

    def as_dict(self):
        """Return the finding as a plain dict."""
        return {"transaction_id": self.txn_id, "rule": self.rule,
                "severity": self.severity, "message": self.message,
                "action": self.action}

    def __str__(self):
        return "%s [%s] %s: %s" % (self.severity.upper(), self.rule,
                                   self.txn_id, self.message)


def parse_date(value):
    """Parse a date string, or return None when it is invalid.

    Accepted formats are YYYY-MM-DD, YYYY-MM-DD HH:MM:SS, ISO
    datetime with T, YYYY/MM/DD, DD/MM/YYYY, and MM/DD/YYYY.
    A blank value returns None.
    """
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def parse_time(value):
    """Parse an HH:MM or HH:MM:SS time string, or None."""
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    for fmt in TIME_FORMATS:
        try:
            return datetime.datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    return None


def parse_decimal(value):
    """Parse a decimal number, or return None when invalid.

    A blank value returns None. Dollar signs, commas, and
    surrounding spaces are stripped, so $1,234.56 parses.
    """
    if value is None:
        return None
    value = value.strip().replace("$", "").replace(",", "")
    if value == "":
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def parse_int(value):
    """Parse an integer, or return None when invalid."""
    if value is None:
        return None
    value = value.strip().replace(",", "")
    if value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_hours(value):
    """Parse an HH:MM operating-hours value, or raise ValueError."""
    parsed = parse_time(value)
    if parsed is None:
        raise ValueError(
            "hours must be HH:MM, got %r" % value)
    return parsed


def resolve_columns(header):
    """Map export header names to the canonical column names.

    Returns a dict of canonical name to header index, or None
    when a required column is missing. Header matching is
    case-insensitive and ignores surrounding spaces. The date
    is accepted as a single datetime column or as separate
    date and time columns.
    """
    lowered = [name.strip().lower() for name in header]
    resolved = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                resolved[canonical] = lowered.index(alias)
                break
    if "datetime" not in resolved and "date" not in resolved:
        return None
    for required in REQUIRED_COLUMNS:
        if required not in resolved:
            return None
    return resolved


def median(values):
    """Return the median of a list of Decimals."""
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


class Transaction:
    """One transaction, parsed from one CSV row."""

    def __init__(self, txn_id, when, card, driver, unit, odometer,
                 gallons, amount, price, product, location,
                 state, line_no):
        self.txn_id = txn_id
        self.when = when
        self.card = card
        self.driver = driver
        self.unit = unit
        self.odometer = odometer
        self.gallons = gallons
        self.amount = amount
        self.price = price
        self.product = product
        self.location = location
        self.state = state
        self.line_no = line_no


def load_transactions(path, as_of, tank_capacity, min_gap,
                      min_mpg, max_mpg, hours_start, hours_end):
    """Read the transactions CSV and return (txns, findings, has_time).

    A duplicate transaction ID is reported once and the
    duplicate row is skipped, so one purchase cannot count
    twice. A malformed row is reported and skipped, never a
    crash. When the export has no time column, the off-hours
    check is skipped. Sequence checks (odometer, MPG,
    duplicate fill) run per unit over the rows in time order.
    """
    txns = []
    findings = []
    seen_ids = set()
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError("file is empty, no header row found")
        columns = resolve_columns(header)
        if columns is None:
            raise ValueError(
                "missing required column: the file needs a date "
                "or datetime column plus gallons and amount")
        expected = len(header)
        has_time = "time" in columns or "datetime" in columns

        def cell(row, canonical):
            if canonical not in columns:
                return ""
            return row[columns[canonical]].strip()

        for line_no, row in enumerate(reader, start=2):
            if not row or all(not c.strip() for c in row):
                continue
            if len(row) != expected:
                findings.append(Finding(
                    "row %d" % line_no, "malformed-row", "error",
                    "row has %d fields but the header has %d, so "
                    "the row was skipped" % (len(row), expected)))
                continue
            txn_id = cell(row, "transaction_id")
            label = txn_id if txn_id else "row %d" % line_no
            if txn_id:
                if txn_id in seen_ids:
                    findings.append(Finding(
                        txn_id, "duplicate-transaction", "error",
                        "transaction ID already appears in this "
                        "export, so this duplicate row was "
                        "skipped"))
                    continue
                seen_ids.add(txn_id)
            date_text = cell(row, "date")
            time_text = cell(row, "time")
            when = None
            has_real_time = False
            if "datetime" in columns:
                when = parse_date(cell(row, "datetime"))
                has_real_time = when is not None
                if cell(row, "datetime") and when is None:
                    findings.append(Finding(
                        label, "invalid-date", "error",
                        "datetime %r is not a recognised date"
                        % cell(row, "datetime")))
            else:
                parsed_date = parse_date(date_text)
                if date_text and parsed_date is None:
                    findings.append(Finding(
                        label, "invalid-date", "error",
                        "date %r is not a recognised date"
                        % date_text))
                elif parsed_date is not None:
                    parsed_time = parse_time(time_text)
                    if time_text and parsed_time is None:
                        findings.append(Finding(
                            label, "invalid-date", "warning",
                            "time %r is not a recognised time, "
                            "so it was ignored" % time_text))
                        parsed_time = datetime.time(0, 0)
                    elif parsed_time is not None:
                        has_real_time = True
                    else:
                        parsed_time = datetime.time(0, 0)
                    when = datetime.datetime.combine(
                        parsed_date.date(), parsed_time)
            if when is not None and when.date() > as_of:
                findings.append(Finding(
                    label, "future-date", "warning",
                    "transaction date %s is in the future"
                    % when.date()))
            card = cell(row, "card_number")
            driver = cell(row, "driver")
            unit = cell(row, "unit")
            if not card:
                findings.append(Finding(
                    label, "missing-card", "warning",
                    "no fuel card number, so the purchase cannot "
                    "be tied to a card"))
            if not unit:
                findings.append(Finding(
                    label, "missing-unit", "warning",
                    "no unit number, so the purchase cannot be "
                    "tied to a truck"))
            odometer_text = cell(row, "odometer")
            odometer = None
            if odometer_text == "":
                findings.append(Finding(
                    label, "missing-odometer", "warning",
                    "no odometer reading, so MPG and odometer "
                    "checks cannot run for this fill"))
            else:
                odometer = parse_int(odometer_text)
                if odometer is None or odometer < 0:
                    findings.append(Finding(
                        label, "invalid-number", "error",
                        "odometer %r is not a valid whole number"
                        % odometer_text))
                    odometer = None
            gallons_text = cell(row, "gallons")
            gallons = parse_decimal(gallons_text)
            if gallons_text == "" or gallons is None:
                findings.append(Finding(
                    label, "invalid-number", "error",
                    "gallons %r is missing or not a valid number"
                    % gallons_text))
                gallons = None
            elif gallons < 0:
                findings.append(Finding(
                    label, "negative-value", "error",
                    "gallons %s is negative" % gallons))
            amount_text = cell(row, "amount")
            amount = parse_decimal(amount_text)
            if amount_text == "" or amount is None:
                findings.append(Finding(
                    label, "invalid-number", "error",
                    "amount %r is missing or not a valid number"
                    % amount_text))
                amount = None
            elif amount < 0:
                findings.append(Finding(
                    label, "negative-value", "error",
                    "amount %s is negative" % amount))
            price_text = cell(row, "price_per_gallon")
            price = parse_decimal(price_text)
            if price_text != "" and price is None:
                findings.append(Finding(
                    label, "invalid-number", "error",
                    "price per gallon %r is not a valid number"
                    % price_text))
                price = None
            elif price is not None and price < 0:
                findings.append(Finding(
                    label, "negative-value", "error",
                    "price per gallon %s is negative" % price))
            product = cell(row, "product")
            location = cell(row, "location")
            state = cell(row, "state").upper()
            if product == "":
                findings.append(Finding(
                    label, "missing-product", "warning",
                    "no product listed, so the purchase cannot be "
                    "checked as fuel or non-fuel"))
            else:
                lowered_product = product.lower()
                if lowered_product in NON_FUEL_PRODUCTS:
                    findings.append(Finding(
                        label, "non-fuel-purchase", "error",
                        "product %r is not fuel, but it was bought "
                        "on a fuel card" % product))
                elif lowered_product not in FUEL_PRODUCTS:
                    findings.append(Finding(
                        label, "unknown-product", "warning",
                        "product %r is not a recognised fuel or "
                        "non-fuel product" % product))
            if gallons is not None and gallons > tank_capacity:
                findings.append(Finding(
                    label, "over-tank-gallons", "error",
                    "gallons %s is above the tank capacity of %s"
                    % (gallons, tank_capacity)))
            if gallons is not None and amount is not None \
                    and price is not None:
                expected_amount = gallons * price
                if abs(amount - expected_amount) > PRICE_TOLERANCE:
                    findings.append(Finding(
                        label, "price-mismatch", "error",
                        "amount %s does not match gallons %s "
                        "times price %s (expected %s)"
                        % (amount, gallons, price,
                           expected_amount)))
            if when is not None and has_real_time:
                fill_time = when.time()
                if fill_time < hours_start or fill_time >= hours_end:
                    findings.append(Finding(
                        label, "off-hours-fill", "warning",
                        "fill at %s is outside operating hours "
                        "%s to %s"
                        % (fill_time.strftime("%H:%M"),
                           hours_start.strftime("%H:%M"),
                           hours_end.strftime("%H:%M"))))
            txns.append(Transaction(txn_id or label, when, card,
                                    driver, unit, odometer, gallons,
                                    amount, price, product, location,
                                    state, line_no))

    # High price per gallon, against the median for the state.
    prices_by_state = {}
    for txn in txns:
        if txn.price is not None and txn.state:
            prices_by_state.setdefault(txn.state, []).append(
                txn.price)
    state_medians = {}
    for state, prices in prices_by_state.items():
        if len(prices) >= HIGH_PRICE_MIN_GROUP:
            state_medians[state] = median(prices)
    for txn in txns:
        if txn.price is not None and txn.state in state_medians:
            threshold = state_medians[txn.state] * HIGH_PRICE_MARGIN
            if txn.price > threshold:
                findings.append(Finding(
                    txn.txn_id, "high-price", "warning",
                    "price %s in %s is above the state median "
                    "%s by more than 20%%"
                    % (txn.price, txn.state,
                       state_medians[txn.state])))

    # Per-unit sequence checks, in time order.
    by_unit = {}
    for txn in txns:
        if txn.unit:
            by_unit.setdefault(txn.unit, []).append(txn)
    for unit, unit_txns in by_unit.items():
        ordered = sorted(
            (txn for txn in unit_txns if txn.when is not None),
            key=lambda txn: txn.when)
        previous = None
        for txn in ordered:
            if previous is not None and txn.odometer is not None \
                    and previous.odometer is not None:
                miles = txn.odometer - previous.odometer
                if miles < 0:
                    findings.append(Finding(
                        txn.txn_id, "odometer-regression", "error",
                        "odometer %d is below the previous reading "
                        "%d for unit %s"
                        % (txn.odometer, previous.odometer, unit)))
                elif miles == 0:
                    findings.append(Finding(
                        txn.txn_id, "zero-miles-fill", "warning",
                        "odometer did not move since the previous "
                        "fill for unit %s, so the truck drove no "
                        "miles between fills" % unit))
                elif txn.gallons is not None and txn.gallons > 0:
                    mpg = miles / float(txn.gallons)
                    if mpg < min_mpg or mpg > max_mpg:
                        findings.append(Finding(
                            txn.txn_id, "implausible-mpg", "warning",
                            "MPG %.2f for unit %s is outside the "
                            "plausible range %.1f to %.1f"
                            % (mpg, unit, min_mpg, max_mpg)))
            if txn.odometer is not None:
                previous = txn
        # Duplicate fills: same unit again within the gap.
        for index in range(1, len(ordered)):
            earlier = ordered[index - 1]
            later = ordered[index]
            gap = (later.when - earlier.when).total_seconds() / 60
            if 0 <= gap < min_gap:
                findings.append(Finding(
                    later.txn_id, "duplicate-fill", "error",
                    "fill %.0f minutes after the previous fill "
                    "for unit %s, below the minimum gap of %d "
                    "minutes"
                    % (gap, unit, min_gap)))

    # One card on two units in the same day.
    card_day_units = {}
    for txn in sorted((t for t in txns if t.when is not None),
                      key=lambda t: t.when):
        if not txn.card or not txn.unit:
            continue
        key = (txn.card, txn.when.date())
        seen = card_day_units.setdefault(key, set())
        if seen and txn.unit not in seen:
            findings.append(Finding(
                txn.txn_id, "shared-card", "error",
                "card %s was already used on unit %s on %s, but "
                "this fill is on unit %s"
                % (txn.card, sorted(seen)[0], txn.when.date(),
                   txn.unit)))
        seen.add(txn.unit)

    return txns, findings, has_time


def analyze_file(path, as_of=None, tank_capacity=DEFAULT_TANK_CAPACITY,
                 min_gap=DEFAULT_MIN_GAP_MINUTES,
                 min_mpg=DEFAULT_MIN_MPG,
                 max_mpg=DEFAULT_MAX_MPG,
                 hours_start=DEFAULT_HOURS_START,
                 hours_end=DEFAULT_HOURS_END):
    """Audit one transactions file and return the report dict."""
    if as_of is None:
        as_of = datetime.date.today()
    txns, findings, has_time = load_transactions(
        path, as_of, tank_capacity, min_gap, min_mpg, max_mpg,
        hours_start, hours_end)
    units = {}
    cards = set()
    total_gallons = Decimal("0")
    total_amount = Decimal("0")
    for txn in txns:
        if txn.card:
            cards.add(txn.card)
        if txn.gallons is not None:
            total_gallons += txn.gallons
        if txn.amount is not None:
            total_amount += txn.amount
        if txn.unit:
            entry = units.setdefault(
                txn.unit, {"fills": 0, "gallons": Decimal("0"),
                           "amount": Decimal("0"), "miles": 0,
                           "mpg_gallons": Decimal("0")})
            entry["fills"] += 1
            if txn.gallons is not None:
                entry["gallons"] += txn.gallons
            if txn.amount is not None:
                entry["amount"] += txn.amount
    for unit, unit_txns in _group_by_unit(txns).items():
        ordered = sorted(
            (txn for txn in unit_txns if txn.when is not None),
            key=lambda txn: txn.when)
        previous = None
        for txn in ordered:
            if previous is not None and txn.odometer is not None \
                    and previous.odometer is not None:
                miles = txn.odometer - previous.odometer
                if miles > 0 and txn.gallons is not None \
                        and txn.gallons > 0:
                    units[unit]["miles"] += miles
                    units[unit]["mpg_gallons"] += txn.gallons
            if txn.odometer is not None:
                previous = txn
    unit_rows = []
    for unit in sorted(units):
        entry = units[unit]
        mpg = None
        if entry["mpg_gallons"] > 0:
            mpg = entry["miles"] / float(entry["mpg_gallons"])
        avg_price = None
        if entry["gallons"] > 0:
            avg_price = entry["amount"] / entry["gallons"]
        unit_rows.append({
            "unit": unit, "fills": entry["fills"],
            "gallons": float(entry["gallons"]),
            "amount": float(entry["amount"]),
            "avg_price": float(avg_price) if avg_price is not None
                         else None,
            "mpg": mpg,
        })
    summary = {
        "transactions": len(txns),
        "units": len(units),
        "cards": len(cards),
        "gallons": float(total_gallons),
        "amount": float(total_amount),
        "findings": len(findings),
        "errors": sum(1 for f in findings if f.severity == "error"),
        "warnings": sum(1 for f in findings
                        if f.severity == "warning"),
        "time_column": has_time,
    }
    return {"summary": summary, "units": unit_rows,
            "findings": [f.as_dict() for f in findings]}


def _group_by_unit(txns):
    """Group transactions by unit number."""
    grouped = {}
    for txn in txns:
        if txn.unit:
            grouped.setdefault(txn.unit, []).append(txn)
    return grouped


def format_text(report):
    """Render the audit as plain text."""
    summary = report["summary"]
    lines = []
    for finding in report["findings"]:
        lines.append("%s [%s] %s: %s"
                     % (finding["severity"].upper(),
                        finding["rule"],
                        finding["transaction_id"],
                        finding["message"]))
        lines.append("  next: %s" % finding["action"])
    if report["units"]:
        lines.append("")
        lines.append("Per unit:")
        for row in report["units"]:
            avg = ("$%.2f/gal" % row["avg_price"]
                   if row["avg_price"] is not None else "-")
            mpg = ("%.2f mpg" % row["mpg"]
                   if row["mpg"] is not None else "-")
            fill_word = "fill" if row["fills"] == 1 else "fills"
            lines.append("%s: %d %s, %.2f gallons, $%.2f, %s, %s"
                         % (row["unit"], row["fills"], fill_word,
                            row["gallons"], row["amount"], avg, mpg))
    lines.append("")
    lines.append("Transactions: %d, units: %d, cards: %d, "
                 "gallons: %.2f, amount: $%.2f, findings: %d, "
                 "errors: %d, warnings: %d"
                 % (summary["transactions"], summary["units"],
                    summary["cards"], summary["gallons"],
                    summary["amount"], summary["findings"],
                    summary["errors"], summary["warnings"]))
    if not summary["time_column"]:
        lines.append("Note: no time column found, so the "
                     "off-hours check was skipped.")
    if not report["findings"]:
        lines.append("All transactions clean.")
    return "\n".join(lines) + "\n"


def format_json(report):
    """Render the audit as JSON."""
    return json.dumps(report, indent=2) + "\n"


def parse_as_of(value):
    """Parse a YYYY-MM-DD reference date, or raise ValueError."""
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(
            "--as-of must be a YYYY-MM-DD date, got %r" % value)


def main(argv=None):
    """Entry point for the fuel-card-doctor CLI."""
    parser = argparse.ArgumentParser(
        description="Audit a fuel card transactions CSV export "
                    "for fraud and billing patterns: over-tank "
                    "fills, duplicate fills, odometer regression, "
                    "implausible MPG, off-hours fills, non-fuel "
                    "purchases, price mismatches, and shared cards.")
    parser.add_argument("path",
                        help="CSV file of fuel card transactions")
    parser.add_argument("--tank-capacity", type=Decimal,
                        default=DEFAULT_TANK_CAPACITY,
                        help="tank capacity in gallons; a fill "
                             "above it is flagged (default: 150)")
    parser.add_argument("--min-gap", type=int,
                        default=DEFAULT_MIN_GAP_MINUTES,
                        help="minimum minutes between fills on one "
                             "unit before the later fill counts as "
                             "a duplicate (default: 30)")
    parser.add_argument("--min-mpg", type=float,
                        default=DEFAULT_MIN_MPG,
                        help="lowest plausible MPG between fills "
                             "(default: 4.0)")
    parser.add_argument("--max-mpg", type=float,
                        default=DEFAULT_MAX_MPG,
                        help="highest plausible MPG between fills "
                             "(default: 12.0)")
    parser.add_argument("--hours-start", default="05:00",
                        help="operating hours start as HH:MM "
                             "(default: 05:00)")
    parser.add_argument("--hours-end", default="23:00",
                        help="operating hours end as HH:MM "
                             "(default: 23:00)")
    parser.add_argument("--as-of", default=None,
                        help="reference date as YYYY-MM-DD "
                             "(default: today)")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of text")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if any error is found")
    parser.add_argument("--strict", action="store_true",
                        help="with --check, also exit 1 on warnings")
    parser.add_argument("--out", help="write the report to a file")
    parser.add_argument("--version", action="version",
                        version="%(prog)s " + __version__)
    args = parser.parse_args(argv)
    try:
        as_of = parse_as_of(args.as_of) if args.as_of else None
        hours_start = parse_hours(args.hours_start)
        hours_end = parse_hours(args.hours_end)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    try:
        report = analyze_file(
            args.path, as_of=as_of,
            tank_capacity=args.tank_capacity,
            min_gap=args.min_gap, min_mpg=args.min_mpg,
            max_mpg=args.max_mpg, hours_start=hours_start,
            hours_end=hours_end)
    except FileNotFoundError:
        print("error: file not found: %s" % args.path, file=sys.stderr)
        return 2
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print("error: cannot read %s: %s" % (args.path, exc),
              file=sys.stderr)
        return 2
    if args.json:
        output = format_json(report)
    else:
        output = format_text(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(output)
    else:
        print(output, end="")
    summary = report["summary"]
    if args.check:
        if summary["errors"]:
            return 1
        if args.strict and summary["warnings"]:
            return 1
    return 0


def main_entry():
    """Console script entry point."""
    sys.exit(main())


if __name__ == "__main__":
    main_entry()
