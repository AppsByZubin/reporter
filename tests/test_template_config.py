from unittest import TestCase

from openpyxl import load_workbook

from common.constants import DEFAULT_TEMPLATE, SECTION_BY_BOT


class TemplateConfigTests(TestCase):
    def test_titanbot_section_matches_template(self) -> None:
        section = SECTION_BY_BOT["titanbot"]
        workbook = load_workbook(DEFAULT_TEMPLATE)
        worksheet = workbook.active

        self.assertEqual(
            worksheet.cell(section["title_row"], 5).value.strip().lower(),
            "titanbot",
        )
        self.assertEqual(
            worksheet.cell(section["header_row"], 5).value,
            "trade_id",
        )
        self.assertEqual(
            worksheet.cell(section["total_row"], 15).value,
            "Total:",
        )
        self.assertIn(section["table"], worksheet.tables)
