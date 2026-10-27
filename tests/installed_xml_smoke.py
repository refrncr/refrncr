"""Run with an installed wheel's isolated Python (-I), without development dependencies."""

import importlib.util
import io
import sys
import unittest
import zipfile
from pathlib import Path

import defusedxml
import openpyxl.xml
from defusedxml.common import DefusedXmlException
from openpyxl import Workbook

import refrncr
from refrncr.record_import import UNREADABLE_XLSX_MESSAGE, ImportValidationError, parse_source


class InstalledXmlSmoke(unittest.TestCase):
    def test_runtime_install_has_safe_parser_and_no_borrowed_dev_environment(self):
        self.assertTrue(sys.flags.isolated)
        self.assertTrue(openpyxl.xml.DEFUSEDXML)
        for module in (refrncr, defusedxml, openpyxl):
            self.assertTrue(Path(module.__file__).is_relative_to(sys.prefix))
        for module in ("pytest", "cyclonedx", "py_serializable"):
            self.assertIsNone(importlib.util.find_spec(module))

    def test_real_excel_import_and_entity_rejection(self):
        workbook = Workbook()
        workbook.active.append(["code"])
        workbook.active.append(["1001"])
        content = io.BytesIO()
        workbook.save(content)
        workbook.close()
        self.assertEqual(
            parse_source("normal.xlsx", content.getvalue(), "Sheet").rows[0].values,
            ("1001",),
        )
        malicious = io.BytesIO()
        with zipfile.ZipFile(content) as source, zipfile.ZipFile(malicious, "w") as target:
            for name in source.namelist():
                xml = source.read(name)
                if name == "xl/worksheets/sheet1.xml":
                    xml = b'<!DOCTYPE worksheet [<!ENTITY payload "small">]>' + xml
                target.writestr(name, xml)
        with self.assertRaises(ImportValidationError) as error:
            parse_source("entity.xlsx", malicious.getvalue())
        self.assertEqual(str(error.exception), UNREADABLE_XLSX_MESSAGE)
        cause = error.exception
        while cause.__cause__ is not None:
            cause = cause.__cause__
        self.assertIsInstance(cause, DefusedXmlException)


if __name__ == "__main__":
    unittest.main()
