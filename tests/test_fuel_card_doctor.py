"""Tests for fuel_card_doctor."""

import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import fuel_card_doctor as fcd
from decimal import Decimal

HEADER = ("transaction_id,date,time,card_number,driver,unit,"
          "odometer,gallons,amount,price_per_gallon,product,"
          "location,state\n")

AS_OF = datetime.date(2026, 11, 11)


def txn_row(txn_id="T-1", date="2026-11-01", time="08:00",
            card="1111", driver="Al Fox", unit="U-1",
            odometer="100000", gallons="80.0", amount="320.00",
            price="4.00", product="diesel", location="Pilot",
            state="TX"):
    """Build one CSV row with the standard header columns."""
    return ("%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n"
            % (txn_id, date, time, card, driver, unit, odometer,
               gallons, amount, price, product, location, state))


def analyze_text(text, **kwargs):
    """Analyze CSV text and return the report dict."""
    with tempfile.NamedTemporaryFile("w", suffix=".csv",
                                     delete=False) as handle:
        handle.write(text)
        path = handle.name
    try:
        return fcd.analyze_file(
            path, as_of=kwargs.get("as_of", AS_OF),
            tank_capacity=kwargs.get("tank_capacity",
                                     Decimal("150")),
            min_gap=kwargs.get("min_gap", 30),
            min_mpg=kwargs.get("min_mpg", 4.0),
            max_mpg=kwargs.get("max_mpg", 12.0),
            hours_start=kwargs.get("hours_start",
                                   datetime.time(5, 0)),
            hours_end=kwargs.get("hours_end",
                                 datetime.time(23, 0)))
    finally:
        os.unlink(path)


def rules(report):
    """Return the sorted list of rule names in the report."""
    return sorted(f["rule"] for f in report["findings"])


def findings_for(report, rule):
    """Return all findings with this rule."""
    return [f for f in report["findings"] if f["rule"] == rule]


def finding_for(report, rule):
    """Return the first finding with this rule, or None."""
    found = findings_for(report, rule)
    return found[0] if found else None


class TestParsing(unittest.TestCase):
    def test_parse_date_formats(self):
        self.assertEqual(
            fcd.parse_date("2026-11-01").date(),
            datetime.date(2026, 11, 1))
        self.assertEqual(
            fcd.parse_date("2026-11-01 08:15:00").date(),
            datetime.date(2026, 11, 1))
        self.assertEqual(
            fcd.parse_date("2026-11-01T08:15:00").date(),
            datetime.date(2026, 11, 1))
        self.assertEqual(
            fcd.parse_date("2026/11/01").date(),
            datetime.date(2026, 11, 1))
        self.assertEqual(
            fcd.parse_date("01/11/2026").date(),
            datetime.date(2026, 11, 1))

    def test_parse_date_invalid_and_blank(self):
        self.assertIsNone(fcd.parse_date("not-a-date"))
        self.assertIsNone(fcd.parse_date(""))
        self.assertIsNone(fcd.parse_date(None))
        self.assertIsNone(fcd.parse_date("2026-13-01"))

    def test_parse_time_formats(self):
        self.assertEqual(fcd.parse_time("08:15"),
                         datetime.time(8, 15))
        self.assertEqual(fcd.parse_time("08:15:30"),
                         datetime.time(8, 15, 30))
        self.assertIsNone(fcd.parse_time("25:00"))
        self.assertIsNone(fcd.parse_time(""))
        self.assertIsNone(fcd.parse_time(None))

    def test_parse_decimal(self):
        self.assertEqual(fcd.parse_decimal("80.0"),
                         Decimal("80.0"))
        self.assertEqual(fcd.parse_decimal("$1,234.56"),
                         Decimal("1234.56"))
        self.assertEqual(fcd.parse_decimal(" 4.10 "),
                         Decimal("4.10"))
        self.assertIsNone(fcd.parse_decimal("abc"))
        self.assertIsNone(fcd.parse_decimal(""))
        self.assertIsNone(fcd.parse_decimal(None))

    def test_parse_int(self):
        self.assertEqual(fcd.parse_int("152300"), 152300)
        self.assertEqual(fcd.parse_int("152,300"), 152300)
        self.assertIsNone(fcd.parse_int("abc"))
        self.assertIsNone(fcd.parse_int(""))
        self.assertIsNone(fcd.parse_int(None))

    def test_parse_hours(self):
        self.assertEqual(fcd.parse_hours("05:00"),
                         datetime.time(5, 0))
        with self.assertRaises(ValueError):
            fcd.parse_hours("morning")

    def test_median(self):
        self.assertEqual(
            fcd.median([Decimal("4.00"), Decimal("4.10"),
                        Decimal("4.20")]),
            Decimal("4.10"))
        self.assertEqual(
            fcd.median([Decimal("4.00"), Decimal("4.20")]),
            Decimal("4.10"))


class TestHeaderHandling(unittest.TestCase):
    def test_missing_required_column(self):
        text = "transaction_id,date,time\nT-1,2026-11-01,08:00\n"
        with tempfile.NamedTemporaryFile("w", suffix=".csv",
                                        delete=False) as handle:
            handle.write(text)
            path = handle.name
        try:
            with self.assertRaises(ValueError):
                fcd.analyze_file(path, as_of=AS_OF)
        finally:
            os.unlink(path)

    def test_missing_date_column(self):
        text = "transaction_id,gallons,amount\nT-1,80.0,320.00\n"
        with tempfile.NamedTemporaryFile("w", suffix=".csv",
                                        delete=False) as handle:
            handle.write(text)
            path = handle.name
        try:
            with self.assertRaises(ValueError):
                fcd.analyze_file(path, as_of=AS_OF)
        finally:
            os.unlink(path)

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv",
                                        delete=False) as handle:
            handle.write("")
            path = handle.name
        try:
            with self.assertRaises(ValueError):
                fcd.analyze_file(path, as_of=AS_OF)
        finally:
            os.unlink(path)

    def test_header_aliases(self):
        text = ("id,trans_date,trans_time,card,truck,odo,qty,"
                "total,ppg,fuel_type\n"
                "T-1,2026-11-01,08:00,1111,U-1,100000,80.0,"
                "320.00,4.00,diesel\n")
        report = analyze_text(text)
        self.assertEqual(report["summary"]["transactions"], 1)
        self.assertEqual(report["summary"]["findings"], 0)

    def test_datetime_column(self):
        text = ("transaction_id,datetime,card_number,unit,"
                "odometer,gallons,amount,price_per_gallon,"
                "product\n"
                "T-1,2026-11-01 02:30:00,1111,U-1,100000,80.0,"
                "320.00,4.00,diesel\n")
        report = analyze_text(text)
        self.assertEqual(rules(report), ["off-hours-fill"])

    def test_bom_header(self):
        text = "\ufeff" + HEADER + txn_row()
        report = analyze_text(text)
        self.assertEqual(report["summary"]["transactions"], 1)
        self.assertEqual(report["summary"]["findings"], 0)

    def test_no_time_column_skips_off_hours(self):
        text = ("transaction_id,date,card_number,unit,odometer,"
                "gallons,amount,price_per_gallon,product\n"
                "T-1,2026-11-01,1111,U-1,100000,80.0,320.00,"
                "4.00,diesel\n")
        report = analyze_text(text)
        self.assertEqual(report["summary"]["findings"], 0)
        self.assertFalse(report["summary"]["time_column"])


class TestRowRules(unittest.TestCase):
    def test_clean_row(self):
        report = analyze_text(HEADER + txn_row())
        self.assertEqual(report["summary"]["findings"], 0)
        self.assertEqual(report["summary"]["transactions"], 1)
        self.assertEqual(report["summary"]["gallons"], 80.0)
        self.assertEqual(report["summary"]["amount"], 320.0)

    def test_over_tank_gallons(self):
        report = analyze_text(HEADER + txn_row(gallons="165.0",
                                                amount="660.00"))
        self.assertEqual(rules(report), ["over-tank-gallons"])
        self.assertEqual(
            finding_for(report, "over-tank-gallons")["severity"],
            "error")

    def test_tank_capacity_boundary(self):
        report = analyze_text(HEADER + txn_row(gallons="150.0",
                                                amount="600.00"))
        self.assertEqual(report["summary"]["findings"], 0)

    def test_tank_capacity_override(self):
        report = analyze_text(
            HEADER + txn_row(gallons="165.0", amount="660.00"),
            tank_capacity=Decimal("240"))
        self.assertEqual(report["summary"]["findings"], 0)

    def test_non_fuel_purchase(self):
        for product in ("merchandise", "food", "cash advance",
                        "lottery"):
            report = analyze_text(
                HEADER + txn_row(product=product))
            self.assertEqual(rules(report), ["non-fuel-purchase"],
                             product)

    def test_fuel_products_pass(self):
        for product in ("diesel", "DEF", "gasoline", "oil",
                        "diesel exhaust fluid"):
            report = analyze_text(
                HEADER + txn_row(product=product))
            self.assertEqual(report["summary"]["findings"], 0,
                             product)

    def test_unknown_product_warning(self):
        report = analyze_text(HEADER + txn_row(product="widgets"))
        self.assertEqual(rules(report), ["unknown-product"])
        self.assertEqual(
            finding_for(report, "unknown-product")["severity"],
            "warning")

    def test_missing_product_warning(self):
        report = analyze_text(HEADER + txn_row(product=""))
        self.assertEqual(rules(report), ["missing-product"])

    def test_price_mismatch(self):
        report = analyze_text(
            HEADER + txn_row(gallons="45.0", amount="200.00",
                             price="4.10"))
        self.assertEqual(rules(report), ["price-mismatch"])

    def test_price_mismatch_tolerance(self):
        report = analyze_text(
            HEADER + txn_row(gallons="45.0", amount="184.55",
                             price="4.10"))
        self.assertEqual(report["summary"]["findings"], 0)

    def test_price_mismatch_just_over_tolerance(self):
        report = analyze_text(
            HEADER + txn_row(gallons="45.0", amount="184.70",
                             price="4.10"))
        self.assertEqual(rules(report), ["price-mismatch"])

    def test_missing_odometer_warning(self):
        report = analyze_text(HEADER + txn_row(odometer=""))
        self.assertEqual(rules(report), ["missing-odometer"])

    def test_invalid_odometer(self):
        report = analyze_text(HEADER + txn_row(odometer="abc"))
        self.assertEqual(rules(report), ["invalid-number"])

    def test_invalid_gallons(self):
        report = analyze_text(HEADER + txn_row(gallons="lots"))
        self.assertEqual(rules(report), ["invalid-number"])

    def test_invalid_amount(self):
        report = analyze_text(HEADER + txn_row(amount="free"))
        self.assertEqual(rules(report), ["invalid-number"])

    def test_invalid_price(self):
        report = analyze_text(HEADER + txn_row(price="high"))
        self.assertEqual(rules(report), ["invalid-number"])

    def test_negative_gallons_and_amount(self):
        report = analyze_text(
            HEADER + txn_row(gallons="-10.0", amount="-41.00",
                             price="4.10"))
        self.assertEqual(
            rules(report),
            ["negative-value", "negative-value"])

    def test_missing_card_and_unit(self):
        report = analyze_text(
            HEADER + txn_row(card="", unit=""))
        self.assertEqual(
            rules(report), ["missing-card", "missing-unit"])

    def test_off_hours_fill(self):
        report = analyze_text(HEADER + txn_row(time="02:30"))
        self.assertEqual(rules(report), ["off-hours-fill"])

    def test_off_hours_boundaries(self):
        for time in ("05:00", "12:00", "22:59"):
            report = analyze_text(HEADER + txn_row(time=time))
            self.assertEqual(report["summary"]["findings"], 0,
                             time)
        for time in ("04:59", "23:00"):
            report = analyze_text(HEADER + txn_row(time=time))
            self.assertEqual(rules(report), ["off-hours-fill"],
                             time)

    def test_off_hours_override(self):
        report = analyze_text(
            HEADER + txn_row(time="02:30"),
            hours_start=datetime.time(0, 0),
            hours_end=datetime.time(23, 59))
        self.assertEqual(report["summary"]["findings"], 0)

    def test_future_date(self):
        report = analyze_text(
            HEADER + txn_row(date="2026-12-01"))
        self.assertEqual(rules(report), ["future-date"])

    def test_invalid_date(self):
        report = analyze_text(
            HEADER + txn_row(date="not-a-date"))
        self.assertEqual(rules(report), ["invalid-date"])

    def test_invalid_time_warning(self):
        report = analyze_text(HEADER + txn_row(time="noon"))
        self.assertEqual(rules(report), ["invalid-date"])

    def test_duplicate_transaction_skipped(self):
        text = HEADER + txn_row(txn_id="T-1") + txn_row(txn_id="T-1")
        report = analyze_text(text)
        self.assertEqual(rules(report), ["duplicate-transaction"])
        self.assertEqual(report["summary"]["transactions"], 1)

    def test_malformed_row_skipped(self):
        text = HEADER + txn_row() + "T-2,2026-11-02\n"
        report = analyze_text(text)
        self.assertEqual(rules(report), ["malformed-row"])
        self.assertEqual(report["summary"]["transactions"], 1)

    def test_blank_lines_skipped(self):
        text = HEADER + "\n" + txn_row() + "\n"
        report = analyze_text(text)
        self.assertEqual(report["summary"]["transactions"], 1)
        self.assertEqual(report["summary"]["findings"], 0)


class TestSequenceRules(unittest.TestCase):
    def test_odometer_regression(self):
        text = HEADER + txn_row(txn_id="T-1", odometer="100000") \
            + txn_row(txn_id="T-2", date="2026-11-02",
                      time="09:00", odometer="99900",
                      gallons="70.0", amount="287.00", price="4.10")
        report = analyze_text(text)
        self.assertEqual(rules(report), ["odometer-regression"])

    def test_zero_miles_fill(self):
        text = HEADER + txn_row(txn_id="T-1", odometer="100000") \
            + txn_row(txn_id="T-2", date="2026-11-02",
                      time="09:00", odometer="100000",
                      gallons="70.0", amount="287.00", price="4.10")
        report = analyze_text(text)
        self.assertEqual(rules(report), ["zero-miles-fill"])

    def test_implausible_mpg_high(self):
        text = HEADER + txn_row(txn_id="T-1", odometer="100000") \
            + txn_row(txn_id="T-2", date="2026-11-02",
                      time="09:00", odometer="100600",
                      gallons="30.0", amount="126.00", price="4.20")
        report = analyze_text(text)
        self.assertEqual(rules(report), ["implausible-mpg"])
        self.assertIn("20.00",
                      finding_for(report, "implausible-mpg")
                      ["message"])

    def test_implausible_mpg_low(self):
        text = HEADER + txn_row(txn_id="T-1", odometer="100000") \
            + txn_row(txn_id="T-2", date="2026-11-02",
                      time="09:00", odometer="100200",
                      gallons="80.0", amount="320.00", price="4.00")
        report = analyze_text(text)
        self.assertEqual(rules(report), ["implausible-mpg"])

    def test_mpg_boundaries_pass(self):
        text = HEADER + txn_row(txn_id="T-1", odometer="100000") \
            + txn_row(txn_id="T-2", date="2026-11-02",
                      time="09:00", odometer="100480",
                      gallons="80.0", amount="320.00", price="4.00")
        report = analyze_text(text)
        self.assertEqual(report["summary"]["findings"], 0)

    def test_mpg_override(self):
        text = HEADER + txn_row(txn_id="T-1", odometer="100000") \
            + txn_row(txn_id="T-2", date="2026-11-02",
                      time="09:00", odometer="100600",
                      gallons="30.0", amount="126.00", price="4.20")
        report = analyze_text(text, max_mpg=25.0)
        self.assertEqual(report["summary"]["findings"], 0)

    def test_duplicate_fill(self):
        text = HEADER + txn_row(txn_id="T-1", odometer="100000") \
            + txn_row(txn_id="T-2", date="2026-11-01",
                      time="08:15", odometer="100000",
                      gallons="40.0", amount="164.00", price="4.10")
        report = analyze_text(text)
        self.assertIn("duplicate-fill", rules(report))
        self.assertIn("zero-miles-fill", rules(report))

    def test_duplicate_fill_boundary(self):
        text = HEADER + txn_row(txn_id="T-1", odometer="100000") \
            + txn_row(txn_id="T-2", date="2026-11-01",
                      time="08:30", odometer="100100",
                      gallons="40.0", amount="164.00", price="4.10")
        report = analyze_text(text)
        self.assertNotIn("duplicate-fill", rules(report))

    def test_duplicate_fill_gap_override(self):
        text = HEADER + txn_row(txn_id="T-1", odometer="100000") \
            + txn_row(txn_id="T-2", date="2026-11-01",
                      time="08:15", odometer="100000",
                      gallons="40.0", amount="164.00", price="4.10")
        report = analyze_text(text, min_gap=10)
        self.assertNotIn("duplicate-fill", rules(report))

    def test_sequence_skips_missing_odometer(self):
        text = HEADER + txn_row(txn_id="T-1", odometer="") \
            + txn_row(txn_id="T-2", date="2026-11-02",
                      time="09:00", odometer="100000",
                      gallons="70.0", amount="287.00", price="4.10")
        report = analyze_text(text)
        self.assertEqual(rules(report), ["missing-odometer"])

    def test_shared_card_same_day(self):
        text = HEADER \
            + txn_row(txn_id="T-1", card="1111", unit="U-1") \
            + txn_row(txn_id="T-2", date="2026-11-01",
                      time="14:00", card="1111", unit="U-2",
                      odometer="50000", gallons="60.0",
                      amount="246.00", price="4.10")
        report = analyze_text(text)
        self.assertEqual(rules(report), ["shared-card"])

    def test_shared_card_different_day_passes(self):
        text = HEADER \
            + txn_row(txn_id="T-1", card="1111", unit="U-1") \
            + txn_row(txn_id="T-2", date="2026-11-02",
                      time="09:00", card="1111", unit="U-2",
                      odometer="50000", gallons="60.0",
                      amount="246.00", price="4.10")
        report = analyze_text(text)
        self.assertEqual(report["summary"]["findings"], 0)

    def test_shared_card_same_unit_passes(self):
        text = HEADER \
            + txn_row(txn_id="T-1", card="1111", unit="U-1") \
            + txn_row(txn_id="T-2", date="2026-11-01",
                      time="14:00", card="1111", unit="U-1",
                      odometer="100480", gallons="60.0",
                      amount="246.00", price="4.10")
        report = analyze_text(text)
        self.assertEqual(report["summary"]["findings"], 0)


class TestHighPrice(unittest.TestCase):
    def _prices_text(self, prices, state="TX"):
        rows = ""
        for index, price in enumerate(prices):
            rows += txn_row(txn_id="T-%d" % (index + 1),
                            date="2026-11-%02d" % (index + 1),
                            time="08:00", card="111%d" % index,
                            unit="U-%d" % (index + 1),
                            odometer="100000",
                            gallons="50.0",
                            amount=str(50.0 * price),
                            price="%.2f" % price, state=state)
        return HEADER + rows

    def test_high_price_above_median(self):
        report = analyze_text(self._prices_text(
            [4.00, 4.05, 4.10, 4.10, 5.25]))
        found = findings_for(report, "high-price")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["transaction_id"], "T-5")

    def test_high_price_small_group_skipped(self):
        report = analyze_text(self._prices_text([4.00, 5.25]))
        self.assertEqual(report["summary"]["findings"], 0)

    def test_high_price_boundary(self):
        report = analyze_text(self._prices_text(
            [4.00, 4.05, 4.10, 4.10, 4.92]))
        self.assertNotIn("high-price", rules(report))

    def test_high_price_per_state(self):
        text = HEADER \
            + txn_row(txn_id="T-1", state="TX", price="4.00") \
            + txn_row(txn_id="T-2", date="2026-11-02",
                      state="TX", price="4.00", card="2222",
                      unit="U-2", odometer="50000") \
            + txn_row(txn_id="T-3", date="2026-11-03",
                      state="TX", price="4.00", card="3333",
                      unit="U-3", odometer="60000") \
            + txn_row(txn_id="T-4", date="2026-11-04",
                      state="NM", price="6.00", card="4444",
                      unit="U-4", odometer="70000",
                      gallons="50.0", amount="300.00")
        report = analyze_text(text)
        self.assertEqual(report["summary"]["findings"], 0)


class TestSummaryAndOutput(unittest.TestCase):
    def test_sample_counts(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))),
            "examples", "transactions.csv")
        report = fcd.analyze_file(path, as_of=AS_OF)
        summary = report["summary"]
        self.assertEqual(summary["transactions"], 16)
        self.assertEqual(summary["units"], 9)
        self.assertEqual(summary["cards"], 8)
        self.assertEqual(summary["gallons"], 868.0)
        self.assertEqual(summary["amount"], 3627.8)
        self.assertEqual(summary["findings"], 19)
        self.assertEqual(summary["errors"], 12)
        self.assertEqual(summary["warnings"], 7)

    def test_sample_per_unit_table(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))),
            "examples", "transactions.csv")
        report = fcd.analyze_file(path, as_of=AS_OF)
        units = {row["unit"]: row for row in report["units"]}
        self.assertEqual(units["U-101"]["fills"], 4)
        self.assertEqual(units["U-101"]["gallons"], 283.0)
        self.assertAlmostEqual(units["U-101"]["mpg"], 6.08, places=2)
        self.assertEqual(units["U-104"]["mpg"], 7.5)
        self.assertIsNone(units["U-102"]["mpg"])
        self.assertIsNone(units["U-110"]["avg_price"])

    def test_clean_sample(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))),
            "examples", "clean.csv")
        report = fcd.analyze_file(path, as_of=AS_OF)
        self.assertEqual(report["summary"]["findings"], 0)
        self.assertEqual(report["summary"]["gallons"], 220.0)
        self.assertEqual(report["summary"]["amount"], 890.0)

    def test_warnings_only_sample(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))),
            "examples", "warnings-only.csv")
        report = fcd.analyze_file(path, as_of=AS_OF)
        self.assertEqual(
            rules(report),
            ["missing-odometer", "off-hours-fill"])
        self.assertEqual(report["summary"]["errors"], 0)
        self.assertEqual(report["summary"]["warnings"], 2)

    def test_json_output_shape(self):
        report = analyze_text(HEADER + txn_row())
        text = fcd.format_json(report)
        parsed = json.loads(text)
        self.assertEqual(parsed["summary"]["transactions"], 1)
        self.assertEqual(parsed["units"][0]["unit"], "U-1")
        self.assertEqual(parsed["findings"], [])

    def test_text_output_clean_line(self):
        report = analyze_text(HEADER + txn_row())
        text = fcd.format_text(report)
        self.assertIn("All transactions clean.", text)
        self.assertIn("Transactions: 1, units: 1, cards: 1", text)

    def test_text_output_no_time_note(self):
        text = ("transaction_id,date,card_number,unit,odometer,"
                "gallons,amount,price_per_gallon,product\n"
                "T-1,2026-11-01,1111,U-1,100000,80.0,320.00,"
                "4.00,diesel\n")
        report = analyze_text(text)
        self.assertIn("no time column found",
                      fcd.format_text(report))

    def test_out_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv",
                                        delete=False) as handle:
            handle.write(HEADER + txn_row())
            csv_path = handle.name
        out_path = csv_path + ".out"
        try:
            code = fcd.main([csv_path, "--as-of", "2026-11-11",
                             "--out", out_path])
            self.assertEqual(code, 0)
            with open(out_path, encoding="utf-8") as handle:
                self.assertIn("All transactions clean.",
                              handle.read())
        finally:
            os.unlink(csv_path)
            if os.path.exists(out_path):
                os.unlink(out_path)


class TestCliExitCodes(unittest.TestCase):
    def run_main(self, text, args):
        """Run main on CSV text and return the exit code."""
        with tempfile.NamedTemporaryFile("w", suffix=".csv",
                                        delete=False) as handle:
            handle.write(text)
            path = handle.name
        try:
            return fcd.main([path, "--as-of", "2026-11-11"] + args)
        finally:
            os.unlink(path)

    def test_check_fails_on_error(self):
        code = self.run_main(
            HEADER + txn_row(gallons="165.0", amount="660.00"),
            ["--check"])
        self.assertEqual(code, 1)

    def test_check_passes_clean(self):
        code = self.run_main(HEADER + txn_row(), ["--check"])
        self.assertEqual(code, 0)

    def test_check_passes_warnings_only(self):
        code = self.run_main(HEADER + txn_row(time="02:30"),
                             ["--check"])
        self.assertEqual(code, 0)

    def test_strict_fails_on_warnings(self):
        code = self.run_main(HEADER + txn_row(time="02:30"),
                             ["--check", "--strict"])
        self.assertEqual(code, 1)

    def test_missing_file_exit_2(self):
        code = fcd.main(["/tmp/does-not-exist-xyz.csv"])
        self.assertEqual(code, 2)

    def test_bad_as_of_exit_2(self):
        code = self.run_main(HEADER + txn_row(),
                             ["--as-of", "yesterday"])
        self.assertEqual(code, 2)

    def test_bad_hours_exit_2(self):
        code = self.run_main(HEADER + txn_row(),
                             ["--hours-start", "dawn"])
        self.assertEqual(code, 2)

    def test_subprocess_sample(self):
        here = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        result = subprocess.run(
            [sys.executable, "fuel_card_doctor.py",
             "examples/transactions.csv", "--as-of", "2026-11-11",
             "--check"],
            cwd=here, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("findings: 19", result.stdout)


class TestActions(unittest.TestCase):
    ALL_RULES = (
        "duplicate-fill", "duplicate-transaction", "future-date",
        "high-price", "implausible-mpg", "invalid-date",
        "invalid-number", "malformed-row", "missing-card",
        "missing-odometer", "missing-product", "missing-unit",
        "negative-value", "non-fuel-purchase", "off-hours-fill",
        "odometer-regression", "over-tank-gallons",
        "price-mismatch", "shared-card", "unknown-product",
        "zero-miles-fill",
    )

    def test_action_mapping_covers_every_rule(self):
        self.assertEqual(sorted(fcd.RULE_ACTIONS),
                         sorted(self.ALL_RULES))
        for rule, action in fcd.RULE_ACTIONS.items():
            self.assertTrue(action.strip(), rule)

    def test_sample_finding_carries_action(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))),
            "examples", "transactions.csv")
        report = fcd.analyze_file(path, as_of=AS_OF)
        self.assertEqual(len(report["findings"]), 19)
        for finding in report["findings"]:
            self.assertIn(finding["rule"], fcd.RULE_ACTIONS)
            self.assertTrue(finding["action"].strip(),
                            finding["rule"])

    def test_shared_card_action(self):
        text = HEADER \
            + txn_row(txn_id="T-1", card="1111", unit="U-1") \
            + txn_row(txn_id="T-2", date="2026-11-01",
                      time="14:00", card="1111", unit="U-2",
                      odometer="50000", gallons="60.0",
                      amount="246.00", price="4.10")
        report = analyze_text(text)
        self.assertEqual(
            finding_for(report, "shared-card")["action"],
            "call the card provider to dispute the charge")

    def test_price_mismatch_action(self):
        report = analyze_text(
            HEADER + txn_row(gallons="45.0", amount="200.00",
                             price="4.10"))
        action = finding_for(report, "price-mismatch")["action"]
        self.assertTrue(
            action.startswith("ask the driver for the receipt"),
            action)

    def test_missing_field_actions(self):
        report = analyze_text(
            HEADER + txn_row(card="", unit="", product=""))
        for rule in ("missing-card", "missing-unit",
                     "missing-product"):
            self.assertIn(rule, rules(report))
            self.assertTrue(
                finding_for(report, rule)["action"].strip(), rule)

    def test_unknown_rule_gets_default_action(self):
        finding = fcd.Finding("T-1", "brand-new-rule", "warning",
                              "something new")
        self.assertEqual(finding.action, fcd.DEFAULT_ACTION)

    def test_text_output_shows_action(self):
        report = analyze_text(
            HEADER + txn_row(gallons="165.0", amount="660.00"))
        output = fcd.format_text(report)
        self.assertIn(
            "next: compare the receipt with the tank size",
            output)

    def test_text_output_action_for_every_sample_finding(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))),
            "examples", "transactions.csv")
        report = fcd.analyze_file(path, as_of=AS_OF)
        output = fcd.format_text(report)
        self.assertEqual(output.count("  next: "), 19)

    def test_json_output_carries_action(self):
        report = analyze_text(
            HEADER + txn_row(gallons="165.0", amount="660.00"))
        parsed = json.loads(fcd.format_json(report))
        self.assertEqual(
            parsed["findings"][0]["action"],
            fcd.RULE_ACTIONS["over-tank-gallons"])


if __name__ == "__main__":
    unittest.main()
