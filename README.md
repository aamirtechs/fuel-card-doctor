# fuel-card-doctor

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/aamirtechs/fuel-card-doctor/blob/main/LICENSE) [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/aamirtechs/fuel-card-doctor/issues)

A zero-dependency Python CLI that audits a fuel card
transactions CSV export for the fraud and billing patterns
that drain a fleet's fuel budget: fills over tank capacity,
duplicate fills minutes apart, odometer readings that go
backwards, implausible MPG between consecutive fills, fills
with no miles driven, fills outside operating hours,
non-fuel purchases on a fuel card, prices above the state
median, amounts that do not match gallons times price, one
card used on two units in the same day, missing odometers,
duplicate transaction IDs, and bad numbers or dates.

Fuel theft costs US trucking about $2.5 billion a year, 73%
of commercial fleets report theft incidents, and 3 to 5% of
total fleet fuel spend is lost to fraud. The usual detection
is a paid telematics platform or a manual spreadsheet audit
that takes 2 to 4 days for a 25 to 50 truck fleet. This tool
is the offline audit of the exported transactions.

## Install

Requires Python 3.10 or newer. No dependencies beyond the
standard library.

```bash
pip install fuel-card-doctor
```

Or run it straight from the source checkout:

```bash
python3 fuel_card_doctor.py examples/transactions.csv --as-of 2026-11-11
```

## Input format

A CSV export with a header row, as produced by EFS, Comdata,
WEX, and similar fuel card providers. Columns used:
`transaction_id`, `date`, `time`, `card_number`, `driver`,
`unit`, `odometer`, `gallons`, `amount`, `price_per_gallon`,
`product`, `location`, and `state`. A single `datetime`
column works instead of separate `date` and `time` columns.
Aliases such as `txn_id`, `truck`, `odo`, `qty`, `total`,
`ppg`, and `fuel_type` are accepted too. A UTF-8 BOM header
is handled. Dates are accepted as YYYY-MM-DD, YYYY-MM-DD
HH:MM:SS, ISO datetime, YYYY/MM/DD, DD/MM/YYYY, or
MM/DD/YYYY. Amounts may carry a dollar sign and commas.

```csv
transaction_id,date,time,card_number,driver,unit,odometer,gallons,amount,price_per_gallon,product,location,state
T-1001,2026-11-01,08:15,1234,Rosa Diaz,U-101,152300,95.0,380.00,4.00,diesel,Pilot,TX
T-1002,2026-11-02,09:40,1234,Rosa Diaz,U-101,152850,88.0,356.40,4.05,diesel,Loves,TX
T-1003,2026-11-03,02:30,1234,Rosa Diaz,U-101,153200,60.0,246.00,4.10,diesel,TA,OK
```

## What it checks

Errors, the ones that are a live fraud signal or break the
audit:

* `over-tank-gallons`: gallons above `--tank-capacity`
  (default 150). An 80-gallon purchase on a 60-gallon tank is
  the classic skimming tell. Fleets with dual tanks should
  pass a higher capacity, for example `--tank-capacity 240`.
* `duplicate-fill`: a fill on the same unit less than
  `--min-gap` minutes (default 30) after the previous fill.
  Split transactions are a standard fraud red flag.
* `odometer-regression`: an odometer reading below the
  previous reading for the same unit, which means inflated
  odometer entries or a data problem.
* `non-fuel-purchase`: a non-fuel product (merchandise, food,
  cash advance, lottery, and the rest of the built-in list)
  bought on a fuel card.
* `price-mismatch`: the amount does not match gallons times
  price per gallon within $0.10, which catches billing errors
  and overcharges.
* `shared-card`: the same card used on two different units on
  the same calendar day, which means the card was handed to
  another driver or cloned.
* `duplicate-transaction`: a transaction ID that already
  appears in the export. The duplicate row is skipped, so one
  purchase cannot count twice.
* `invalid-number`: gallons, amount, price, or odometer that
  is missing or not a valid number.
* `negative-value`: negative gallons, amount, or price.
* `invalid-date`: a date or datetime that is not a recognised
  date.
* `malformed-row`: a row with the wrong number of fields. The
  row is skipped and reported, never a crash.

Warnings, transactions worth following up:

* `implausible-mpg`: MPG between consecutive fills for one
  unit outside `--min-mpg` to `--max-mpg` (default 4.0 to
  12.0 for a diesel semi). Too high means gallons were
  under-reported or the odometer was inflated; too low means
  fuel went somewhere other than the tank.
* `zero-miles-fill`: the odometer did not move since the
  previous fill for the same unit, so the truck drove no
  miles between fills.
* `off-hours-fill`: a fill outside `--hours-start` to
  `--hours-end` (default 05:00 to 23:00). After-hours
  purchases are a common sign of fuel abuse.
* `high-price`: a price per gallon more than 20% above the
  median price for its state in the same file. A state needs
  at least 3 transactions in the file before this check runs.
* `missing-odometer`: no odometer reading, so MPG and
  odometer checks cannot run for that fill.
* `missing-card`: no fuel card number, so the purchase cannot
  be tied to a card.
* `missing-unit`: no unit number, so the purchase cannot be
  tied to a truck.
* `missing-product`: no product listed, so the purchase cannot
  be checked as fuel or non-fuel.
* `unknown-product`: a product that is neither a recognised
  fuel product nor a known non-fuel product.
* `future-date`: a transaction date in the future.

## What to do about each finding

Each finding carries a short suggested action for a fleet
manager who is not a fraud analyst. The text output prints
it on a `next:` line under the finding, and the JSON
`findings` carry the same text in an `action` field:

| Rule | Suggested action |
| --- | --- |
| `duplicate-fill` | check whether the two fills are one split transaction, and dispute the second charge if they are not |
| `duplicate-transaction` | check the card statement so the duplicate is not paid twice |
| `future-date` | check the export date, since a future transaction usually means a wrong date |
| `high-price` | check the receipt for a wrong price, then plan the route around a cheaper station |
| `implausible-mpg` | check the receipts for both fills, since the gallons or the odometer look wrong |
| `invalid-date` | fix the date in the export and rerun the audit |
| `invalid-number` | fix the bad value in the export and rerun the audit |
| `malformed-row` | re-export the file from the card provider, since this row is broken |
| `missing-card` | add the card number to the export so the purchase can be tied to a card |
| `missing-odometer` | ask the driver for the odometer reading so the MPG checks can run |
| `missing-product` | check the receipt so the purchase can be classified as fuel or non-fuel |
| `missing-unit` | add the unit number to the export so the purchase can be tied to a truck |
| `negative-value` | check whether the row is a credit, and dispute the charge with the card provider if it is not |
| `non-fuel-purchase` | ask the driver what was bought, then recover the amount from the driver or dispute the charge |
| `odometer-regression` | ask the driver for the current odometer reading and fix the export, since the mileage is wrong |
| `off-hours-fill` | ask the driver who made the fill and why it was outside operating hours |
| `over-tank-gallons` | compare the receipt with the tank size, then dispute any gallons the tank cannot hold |
| `price-mismatch` | ask the driver for the receipt, then dispute any overcharge with the card provider |
| `shared-card` | call the card provider to dispute the charge |
| `unknown-product` | check the receipt and add the product to the built-in fuel or non-fuel list |
| `zero-miles-fill` | ask the driver where the truck was between the two fills |

A rule without a built-in suggestion falls back to a generic
action: review the transaction against the receipt, then contact the card provider if the charge looks wrong.

## Usage

```bash
python3 fuel_card_doctor.py examples/transactions.csv --as-of 2026-11-11
```

Captured output:

```
WARNING [off-hours-fill] T-1003: fill at 02:30 is outside operating hours 05:00 to 23:00
  next: ask the driver who made the fill and why it was outside operating hours
WARNING [off-hours-fill] T-1004: fill at 02:45 is outside operating hours 05:00 to 23:00
  next: ask the driver who made the fill and why it was outside operating hours
ERROR [over-tank-gallons] T-1005: gallons 165.0 is above the tank capacity of 150
  next: compare the receipt with the tank size, then dispute any gallons the tank cannot hold
ERROR [non-fuel-purchase] T-1007: product 'merchandise' is not fuel, but it was bought on a fuel card
  next: ask the driver what was bought, then recover the amount from the driver or dispute the charge
ERROR [price-mismatch] T-1009: amount 200.00 does not match gallons 45.0 times price 4.10 (expected 184.500)
  next: ask the driver for the receipt, then dispute any overcharge with the card provider
WARNING [missing-odometer] T-1012: no odometer reading, so MPG and odometer checks cannot run for this fill
  next: ask the driver for the odometer reading so the MPG checks can run
ERROR [invalid-number] T-1013: odometer 'abc' is not a valid whole number
  next: fix the bad value in the export and rerun the audit
ERROR [duplicate-transaction] T-1001: transaction ID already appears in this export, so this duplicate row was skipped
  next: check the card statement so the duplicate is not paid twice
ERROR [malformed-row] row 16: row has 2 fields but the header has 13, so the row was skipped
  next: re-export the file from the card provider, since this row is broken
WARNING [future-date] T-1015: transaction date 2026-12-01 is in the future
  next: check the export date, since a future transaction usually means a wrong date
ERROR [invalid-date] T-1016: date 'not-a-date' is not a recognised date
  next: fix the date in the export and rerun the audit
ERROR [negative-value] T-1017: gallons -10.0 is negative
  next: check whether the row is a credit, and dispute the charge with the card provider if it is not
ERROR [negative-value] T-1017: amount -41.00 is negative
  next: check whether the row is a credit, and dispute the charge with the card provider if it is not
WARNING [high-price] T-1011: price 5.25 in TX is above the state median 4.10 by more than 20%
  next: check the receipt for a wrong price, then plan the route around a cheaper station
WARNING [zero-miles-fill] T-1004: odometer did not move since the previous fill for unit U-101, so the truck drove no miles between fills
  next: ask the driver where the truck was between the two fills
ERROR [duplicate-fill] T-1004: fill 15 minutes after the previous fill for unit U-101, below the minimum gap of 30 minutes
  next: check whether the two fills are one split transaction, and dispute the second charge if they are not
ERROR [odometer-regression] T-1006: odometer 97900 is below the previous reading 98400 for unit U-102
  next: ask the driver for the current odometer reading and fix the export, since the mileage is wrong
WARNING [implausible-mpg] T-1008: MPG 20.00 for unit U-103 is outside the plausible range 4.0 to 12.0
  next: check the receipts for both fills, since the gallons or the odometer look wrong
ERROR [shared-card] T-1011: card 1234 was already used on unit U-101 on 2026-11-03, but this fill is on unit U-105
  next: call the card provider to dispute the charge

Per unit:
U-101: 4 fills, 283.00 gallons, $1146.40, $4.05/gal, 6.08 mpg
U-102: 2 fills, 235.00 gallons, $947.00, $4.03/gal, -
U-103: 2 fills, 80.00 gallons, $336.00, $4.20/gal, 20.00 mpg
U-104: 2 fills, 85.00 gallons, $350.00, $4.12/gal, 7.50 mpg
U-105: 1 fill, 80.00 gallons, $420.00, $5.25/gal, -
U-106: 2 fills, 65.00 gallons, $264.40, $4.07/gal, -
U-108: 1 fill, 25.00 gallons, $102.50, $4.10/gal, -
U-109: 1 fill, 25.00 gallons, $102.50, $4.10/gal, -
U-110: 1 fill, -10.00 gallons, $-41.00, -, -

Transactions: 16, units: 9, cards: 8, gallons: 868.00, amount: $3627.80, findings: 19, errors: 12, warnings: 7
```

The sample tells a story in one page. Card 1234 fills at
02:30 and again 15 minutes later with no miles driven, then
the same card shows up on a different unit the same day at
$5.25 a gallon. Unit U-102 has an odometer that went
backwards, U-103 ran at 20 MPG, and one fill is 15 gallons
over the 150-gallon tank capacity. The duplicate transaction
and the malformed row are left out, so 16 transactions are
counted.

The reference date defaults to today. `--as-of` pins it, so
the future-date check above is reproducible; without it the
check moves as time passes.

Use it as a CI gate: `--check` exits 1 when any error is
found, and 0 otherwise. On the sample above it exits 1:

```bash
python3 fuel_card_doctor.py examples/transactions.csv --as-of 2026-11-11 --check
```

On `examples/clean.csv`, three clean transactions, it exits 0:

```
Per unit:
U-201: 2 fills, 160.00 gallons, $644.00, $4.03/gal, 6.00 mpg
U-202: 1 fill, 60.00 gallons, $246.00, $4.10/gal, -

Transactions: 3, units: 2, cards: 2, gallons: 220.00, amount: $890.00, findings: 0, errors: 0, warnings: 0
All transactions clean.
```

With `--strict`, `--check` also fails on warnings.
`examples/warnings-only.csv` has two warnings, an off-hours
fill and a missing odometer, so it passes `--check` (exit 0)
and fails `--check --strict` (exit 1).

`--json` emits the summary, per-unit table, and findings as
JSON, with an `action` field on every finding:

```bash
python3 fuel_card_doctor.py examples/transactions.csv --as-of 2026-11-11 --json
```

Captured output (first findings and summary):

```json
[
  {
    "transaction_id": "T-1003",
    "rule": "off-hours-fill",
    "severity": "warning",
    "message": "fill at 02:30 is outside operating hours 05:00 to 23:00",
    "action": "ask the driver who made the fill and why it was outside operating hours"
  },
  {
    "transaction_id": "T-1004",
    "rule": "off-hours-fill",
    "severity": "warning",
    "message": "fill at 02:45 is outside operating hours 05:00 to 23:00",
    "action": "ask the driver who made the fill and why it was outside operating hours"
  },
  {
    "transaction_id": "T-1005",
    "rule": "over-tank-gallons",
    "severity": "error",
    "message": "gallons 165.0 is above the tank capacity of 150",
    "action": "compare the receipt with the tank size, then dispute any gallons the tank cannot hold"
  }
]
```

```json
{
  "transactions": 16,
  "units": 9,
  "cards": 8,
  "gallons": 868.0,
  "amount": 3627.8,
  "findings": 19,
  "errors": 12,
  "warnings": 7,
  "time_column": true
}
```

`--out` writes the report to a file instead of printing it.

## Options

| Flag | Meaning |
| --- | --- |
| `--tank-capacity` | Tank capacity in gallons; a fill above it is flagged (default: 150) |
| `--min-gap` | Minimum minutes between fills on one unit before the later fill counts as a duplicate (default: 30) |
| `--min-mpg` | Lowest plausible MPG between fills (default: 4.0) |
| `--max-mpg` | Highest plausible MPG between fills (default: 12.0) |
| `--hours-start` | Operating hours start as HH:MM (default: 05:00) |
| `--hours-end` | Operating hours end as HH:MM (default: 23:00) |
| `--as-of` | Reference date as YYYY-MM-DD (default: today) |
| `--json` | Emit JSON instead of text |
| `--check` | Exit 1 if any error is found |
| `--strict` | With --check, also exit 1 on warnings |
| `--out` | Write the report to a file |

## Edge cases

* A fill exactly at the tank capacity is not flagged; only
  above it is. A fill exactly `--min-gap` minutes after the
  previous fill is not a duplicate; only below the gap is.
* MPG exactly at `--min-mpg` or `--max-mpg` passes; only
  outside the range is flagged. A fill at exactly
  `--hours-start` passes, and one at exactly `--hours-end`
  is off-hours.
* The high-price check compares against the median price for
  the state inside the same file, so it needs no external
  price table. A state with fewer than 3 transactions in the
  file is skipped.
* Per-unit MPG is miles driven between consecutive fills
  divided by the gallons bought on the later fills, so a
  duplicate fill with no miles does not drag the average
  down. Units with no valid odometer pair show `-` instead
  of an MPG.
* A price mismatch uses a $0.10 tolerance, so normal rounding
  does not trip it. A $0.05 difference passes; a $0.20
  difference fails.
* A duplicate transaction ID row is skipped from the
  analysis, so one purchase cannot count twice. A row with a
  bad odometer is kept, with the odometer checks skipped for
  that row.
* A transaction with no time column is never flagged
  off-hours; the report notes that the check was skipped. An
  invalid time is a warning and the fill is treated as having
  no usable time.
* A blank line in the file is skipped silently. A row with too
  many or too few fields is a malformed-row error and is
  skipped; the rest of the file is still analyzed.
* Money math uses Decimal, so gallons times price is exact to
  the cent.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

83 tests, all passing: date, time, and decimal parsing,
header aliases, datetime columns, BOM headers, every rule
including the tank capacity, min-gap, MPG, and operating
hours boundaries and the --tank-capacity, --min-gap,
--min-mpg, --max-mpg, and --hours overrides, duplicate
transaction IDs, shared cards on the same day versus
different days, high-price medians and the 3-transaction
minimum, price-mismatch tolerance, malformed rows, invalid,
missing, and future dates, sample counts, per-unit MPG,
JSON output shape, the per-rule suggested actions in the
text and JSON output, and `--check` and `--strict` exit codes.
The same 83 tests pass under pytest.

## License

MIT. See [LICENSE](LICENSE).
