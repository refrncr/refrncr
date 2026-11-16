from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from datetime import datetime
from datetime import time as datetime_time

from refrncr.datasets.datatypes import DATASET_FIELD_TYPES, get_datatype
from refrncr.imports.contracts import (
    ColumnMappingAdvice,
    ColumnTypeProfile,
    MappingAssistance,
    MappingCandidate,
    MappingCompleteness,
    ParsedSource,
    mapping_issues,
    required_mapping_fields,
)
from refrncr.imports.source_formats import (
    SourceFormatError,
    canonical_source,
    formats_for,
)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_DATA_ROWS = 10_000
MAX_COLUMNS = 250
MAX_ZIP_ENTRIES = 1_000
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_REPORTED_ROWS = 100
PREVIEW_ROWS = 50
IMPORT_TTL_SECONDS = 15 * 60
MAX_STAGED_BYTES = 50 * 1024 * 1024
MAX_WORKSHEET_SOURCE_ROWS = 100_001
PROFILE_SCAN_ROWS = 100
PROFILE_SAMPLE_VALUES = 25
DISPLAY_SAMPLE_VALUES = 3
DISPLAY_SAMPLE_CHARS = 80
CANCEL_WAIT_SECONDS = 5.0

UNREADABLE_XLSX_MESSAGE = (
    "We couldn't read this modern Excel workbook. Open it in Excel or LibreOffice, save a new "
    ".xlsx copy, and upload that copy. Password-protected, damaged, or unsupported workbooks "
    "cannot be imported."
)


def normalize_mapping_name(value: str) -> str:
    """Return a deterministic comparison form while preserving no display state."""
    normalized = unicodedata.normalize("NFC", value.strip())
    normalized = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", normalized)
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", normalized)
    normalized = re.sub(r"[_\-\s]+", " ", normalized)
    decomposed = unicodedata.normalize("NFKD", normalized.casefold())
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(re.sub(r"[^\w]+", " ", without_marks).split())


def format_verdict(state, label, examples, total, failures):
    """Describe the existing full-column validation with bounded display examples."""
    if state == "undecided":
        return None
    if state == "failing":
        return f"{failures} of {total} values do not fit {label}."
    if state == "transformed":
        example = next(item for item in examples if item["source"] != item["stored"])
        return f"Read as {label} — {example['source']} is stored as {example['stored']}."
    return f"Read as {label} — all {total} values parsed, stored exactly as written."


def safe_sample(value: str) -> str:
    if len(value) <= DISPLAY_SAMPLE_CHARS:
        return value
    return value[: DISPLAY_SAMPLE_CHARS - 1] + "…"


def temporal_example(value, data_type):
    """Render typed Excel dates for the target without changing the parser input."""
    if isinstance(value, datetime) and data_type in {"date", "datetime", "timestamp"}:
        if data_type == "date" and value.time() == datetime_time():
            return value.date().isoformat()
        return value.isoformat()
    return str(value)


def native_date_verdict(value, stored, data_type, state):
    months = (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    )
    written = f"{value.day} {months[value.month - 1]} {value.year}"
    if isinstance(value, datetime) and data_type != "date":
        written += f" at {value.time().isoformat()}"
    reason = "; no day/month guessing needed" if state == "clean" else ""
    return f"Read as a real Excel date cell — {written}, stored as {stored}{reason}."


def distinct_samples(values, limit=DISPLAY_SAMPLE_VALUES):
    """Show a column's distinct values, so examples say something about its content.

    A column whose rows repeat ("candidate, candidate, candidate") tells the reader
    nothing. A column that really holds one value shows that one value once, which is
    itself the useful fact.
    """
    seen, distinct = set(), []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        distinct.append(value)
        if len(distinct) == limit:
            break
    return tuple(distinct)


def profile_source_columns(source: ParsedSource) -> tuple[ColumnTypeProfile, ...]:
    """Profile at most 100 rows and 25 non-empty, non-formula values per column."""
    sampled: list[list[object]] = [[] for _header in source.headers]
    for row in source.rows[:PROFILE_SCAN_ROWS]:
        for index, value in enumerate(row.values):
            value = row.source_value(index)
            if index in row.formulas or value is None or value == "":
                continue
            if len(sampled[index]) < PROFILE_SAMPLE_VALUES:
                sampled[index].append(value)
    profiles = []
    for values in sampled:
        compatible = set()
        for data_type in DATASET_FIELD_TYPES:
            if values and all(_profile_value_matches(data_type, value) for value in values):
                compatible.add(data_type)
        profiles.append(
            ColumnTypeProfile(
                tuple(values),
                tuple(safe_sample(str(value)) for value in distinct_samples(values)),
                frozenset(compatible),
            )
        )
    return tuple(profiles)


def _profile_value_matches(data_type: str, value) -> bool:
    for profile in formats_for(data_type):
        try:
            canonical_source(data_type, value, profile.id)
            return True
        except SourceFormatError:
            continue
    # Local timestamps need a timezone decision, but still support a named mapping.
    if data_type == "timestamp":
        return _profile_value_matches("datetime", value)
    return False


def _name_match(header: str, field) -> tuple[int, str, str] | None:
    normalized_header = normalize_mapping_name(header)
    normalized_key = normalize_mapping_name(field.key)
    normalized_name = normalize_mapping_name(field.name)
    if header == field.key:
        return 600, "strong", "Exact field key match"
    if normalized_header == normalized_key:
        return 500, "strong", "Field key matches after separator normalization"
    if normalized_header == normalized_name:
        return 400, "strong", "Field name matches after separator normalization"
    header_tokens = frozenset(normalized_header.split())
    field_tokens = frozenset(normalized_name.split()) | frozenset(normalized_key.split())
    overlap = header_tokens & field_tokens
    if not overlap:
        return None
    difference = len(header_tokens ^ field_tokens)
    return 120 + 20 * len(overlap) - 10 * difference, "limited", "Header words overlap"


def rank_mapping_candidates(header: str, profile: ColumnTypeProfile, fields) -> tuple:
    candidates = []
    for field in fields:
        name_match = _name_match(header, field)
        if name_match is None:
            continue
        name_score, strength, reason = name_match
        compatibility = profile.compatibility(field.data_type)
        score = name_score
        if compatibility == "compatible":
            if field.data_type != "string":
                score += 20
                reason += f"; values match the {get_datatype(field.data_type).label} field type"
        elif compatibility == "incompatible":
            score -= 80
            reason += f"; values do not match the {get_datatype(field.data_type).label} field type"
        confidence = (
            "Low"
            if compatibility == "incompatible"
            else "High"
            if strength == "strong"
            else "Medium"
        )
        candidates.append(MappingCandidate(field.key, field.name, confidence, reason, score))
    candidates.sort(key=lambda candidate: (-candidate.score, candidate.field_key))
    return tuple(candidates[:3])


def build_mapping_assistance(
    source: ParsedSource, fields, identifier, selected_mapping=None, *, mode="create"
) -> MappingAssistance:
    profiles = profile_source_columns(source)
    candidate_sets = [
        rank_mapping_candidates(header, profiles[index], fields)
        for index, header in enumerate(source.headers)
    ]
    warnings: list[str | None] = [None] * len(source.headers)
    for index, candidates in enumerate(candidate_sets):
        if len(candidates) > 1 and candidates[0].score - candidates[1].score < 20:
            warnings[index] = "Multiple possible fields — choose one"
            candidate_sets[index] = tuple(
                replace(
                    candidate,
                    confidence="Medium" if candidate.confidence == "High" else candidate.confidence,
                    reason="Multiple possible fields — choose one",
                )
                for candidate in candidates
            )
    if selected_mapping is None:
        selections: list[str | None] = ["" if candidates else None for candidates in candidate_sets]
        eligible: dict[str, list[tuple[int, int]]] = {}
        for index, candidates in enumerate(candidate_sets):
            if not candidates or candidates[0].confidence != "High":
                continue
            if len(candidates) > 1 and candidates[0].score - candidates[1].score < 20:
                warnings[index] = "Multiple possible fields — choose one"
                continue
            eligible.setdefault(candidates[0].field_key, []).append((candidates[0].score, index))
        for field_key, matches in eligible.items():
            matches.sort(key=lambda item: (-item[0], item[1]))
            if len(matches) == 1 or matches[0][0] > matches[1][0]:
                selections[matches[0][1]] = field_key
                losers = matches[1:]
            else:
                losers = matches
            for _score, index in losers:
                warnings[index] = "Another source column also matches this field — choose one"
    else:
        selections = list(selected_mapping[: len(source.headers)])
        selections.extend("" for _index in range(len(source.headers) - len(selections)))
    columns = tuple(
        ColumnMappingAdvice(
            header,
            profiles[index].display_values,
            candidate_sets[index],
            selections[index],
            warnings[index],
        )
        for index, header in enumerate(source.headers)
    )
    required = required_mapping_fields(fields, identifier, mode)
    completeness = MappingCompleteness(
        sum(selection == "" for selection in selections),
        sum(selection is None for selection in selections),
    )
    required_keys = tuple(
        dict.fromkeys([field.key for field in required] + list(identifier.field_keys))
    )
    errors = mapping_issues(selections, fields, identifier, mode)
    return MappingAssistance(
        columns,
        completeness,
        tuple(fields),
        required_keys,
        tuple(identifier.field_keys),
        tuple(errors),
    )
