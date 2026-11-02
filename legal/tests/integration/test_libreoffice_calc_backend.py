from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import unittest

from fanno_legal.backends.spreadsheet.libreoffice_calc import (
    LibreOfficeCalcBackend,
)


LEGAL_ROOT = Path(__file__).parents[2]

INVOICE = (
    LEGAL_ROOT
    / "tasks"
    / "reference"
    / "representative-180-v0.1"
    / "sources"
    / "harvey-lab"
    / (
        "identify-excessive-or-duplicative-"
        "research-charges-in-litigation-invoice"
    )
    / "documents"
    / "hl-july-2024-invoice.xlsx"
)


def calc_uno_is_available() -> bool:
    if not Path(
        "/usr/bin/python3"
    ).is_file():
        return False

    if shutil.which("soffice") is None:
        return False

    try:
        completed = subprocess.run(
            [
                "/usr/bin/python3",
                "-c",
                "import uno",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except (
        OSError,
        subprocess.TimeoutExpired,
    ):
        return False

    return (
        completed.returncode == 0
        and INVOICE.is_file()
    )


@unittest.skipUnless(
    calc_uno_is_available(),
    "LibreOffice Calc PyUNO is unavailable",
)
class LibreOfficeCalcBackendTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.backend = (
            LibreOfficeCalcBackend(
                system_python="/usr/bin/python3",
                soffice_executable="soffice",
                timeout_seconds=60,
            )
        )

    def test_inspect_real_invoice(
        self,
    ) -> None:
        result = (
            self.backend.inspect_workbook(
                source=INVOICE
            )
        )

        self.assertEqual(
            result.backend,
            "libreoffice-calc-uno",
        )

        names = [
            item["name"]
            for item in result.data["sheets"]
        ]

        self.assertEqual(
            names,
            [
                "Summary",
                "Fee Detail",
                "Disbursements",
                "Discount",
            ],
        )

    def test_read_real_invoice_range(
        self,
    ) -> None:
        result = self.backend.read_range(
            source=INVOICE,
            sheet="Fee Detail",
            cell_range="A1:I3",
            max_range_cells=100,
        )

        self.assertEqual(
            result.data["rows"],
            3,
        )
        self.assertEqual(
            result.data["columns"],
            9,
        )

        first_row = (
            result.data["cells"][0]
        )

        self.assertEqual(
            first_row[0]["display_text"],
            "Line",
        )
        self.assertEqual(
            first_row[2]["display_text"],
            "Timekeeper",
        )
        self.assertEqual(
            first_row[8]["display_text"],
            "Task Description",
        )

    def test_find_real_invoice_value(
        self,
    ) -> None:
        result = self.backend.find(
            source=INVOICE,
            query="Westlaw",
            sheet=None,
            case_sensitive=False,
            max_results=20,
            max_scan_cells=250000,
        )

        self.assertGreaterEqual(
            result.data["total_matches"],
            1,
        )

        self.assertTrue(
            any(
                "Westlaw"
                in item["display_text"]
                for item
                in result.data["matches"]
            )
        )


if __name__ == "__main__":
    unittest.main()