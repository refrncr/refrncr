"""Require openpyxl's safe XML parser without blocking diagnostic CLI imports."""

import os

import openpyxl.xml


def require_xml_protection() -> None:
    # openpyxl chooses its parser at import time. Check that choice as well as
    # configuration loaded later (for example by the CLI's .env reader).
    if not openpyxl.xml.DEFUSEDXML or os.environ.get("OPENPYXL_DEFUSEDXML", "True") != "True":
        raise RuntimeError(
            "Excel XML protection is unavailable. Install refrncr with its runtime "
            "dependencies (including defusedxml), remove OPENPYXL_DEFUSEDXML overrides "
            "that disable protection, and restart."
        )
