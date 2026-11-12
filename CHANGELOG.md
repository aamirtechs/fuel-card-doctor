# Changelog

## Unreleased

* Every finding now carries a short suggested action: the
  text output prints it on a `next:` line under the finding,
  and the JSON `findings` gain an `action` field, so a fleet
  manager who is not a fraud analyst knows the next step for
  each flagged transaction.

## 1.0.0 - 2026-11-12

Initial release.

* Fuel card transactions audit over a CSV export (EFS,
  Comdata, WEX style): fills over tank capacity, duplicate
  fills minutes apart, odometer regression, implausible MPG
  between consecutive fills, zero-mile fills, off-hours
  fills, non-fuel purchases on a fuel card, prices above the
  state median, amounts that do not match gallons times
  price, one card on two units in the same day, missing
  odometers, cards, units, and products, duplicate
  transaction IDs, and bad numbers or dates.
* Fraud signals flagged as errors: over-tank fills, duplicate
  fills, odometer regression, non-fuel purchases, price
  mismatches, shared cards, duplicate transaction IDs, and
  invalid or negative numbers. Implausible MPG, zero-mile
  fills, off-hours fills, high prices, and missing fields
  flagged as warnings.
* High-price check compares against the median price for the
  state inside the same file, so no external price table is
  needed; a state needs at least 3 transactions before the
  check runs.
* Per-unit table with fills, gallons, amount, average price
  per gallon, and MPG computed from miles driven between
  consecutive fills. Money math uses Decimal, so gallons
  times price is exact to the cent.
* Dates accepted as YYYY-MM-DD, YYYY-MM-DD HH:MM:SS, ISO
  datetime, YYYY/MM/DD, DD/MM/YYYY, or MM/DD/YYYY, with a
  single datetime column or separate date and time columns;
  invalid, missing, and future dates are reported.
* Data errors reported and skipped, never a crash: duplicate
  transaction IDs (the duplicate row is skipped), malformed
  rows, and unparseable values. A blank line is skipped
  silently.
* Text and --json output, --out file output, and a --check CI
  gate that exits 1 on errors (and on warnings with --strict).
  --as-of pins the reference date; --tank-capacity,
  --min-gap, --min-mpg, --max-mpg, --hours-start, and
  --hours-end tune the thresholds.
* Zero dependencies, Python 3.10 or newer.
