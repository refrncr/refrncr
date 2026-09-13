# Native Excel dates regression fixture

`native_excel_dates.xlsx` contains the two relevant columns extracted from the
user-supplied `business_objects_anwb date inconsistencies.xlsx` for REF-159:

- Date updated: 24 original text datetime values.
- Entry date: 24 native Excel date cells for 3 April 2026, preserving `mm-dd-yy`.

The worksheet title and cell number formats are preserved. Other business
columns and workbook metadata are omitted. The stored mask is intentionally
not treated as a representation of the user's locale-dependent Excel display.
