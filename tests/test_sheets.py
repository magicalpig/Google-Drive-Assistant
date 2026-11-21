import io
import zipfile

import sheets
from sheets import (column_letter, column_number, find_heading_row, pack, range_text,
                    read_named_functions, reference_labels, relative_form, sheet_passages)


# ---- Fake Google API objects ----

class FakeRequest:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def execute(self):
        if self.error:
            raise self.error
        return self.result


class FakeDrive:
    """Stands in for the Drive API. export() returns `xlsx` bytes, or raises `error`."""
    def __init__(self, xlsx=None, error=None):
        self.xlsx, self.error = xlsx, error

    def files(self):
        return self

    def export(self, fileId, mimeType):
        return FakeRequest(self.xlsx, self.error)


class FakeSheets:
    """Stands in for the Sheets API. get() returns `result`."""
    def __init__(self, result):
        self.result = result

    def spreadsheets(self):
        return self

    def get(self, **kwargs):
        return FakeRequest(self.result)


def text(s):
    return {"userEnteredValue": {"stringValue": s}, "formattedValue": s}


def number(n):
    return {"userEnteredValue": {"numberValue": n}, "formattedValue": str(n)}


def formula(f, shown):
    return {"userEnteredValue": {"formulaValue": f}, "formattedValue": shown}


def xlsx_with_names(names):
    """Bytes of a minimal .xlsx file whose workbook.xml has these {name: definition} defined names."""
    defined = "".join(f'<definedName name="{n}">{d}</definedName>' for n, d in names.items())
    workbook = ('<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f"<definedNames>{defined}</definedNames></workbook>")
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("xl/workbook.xml", workbook)
    return data.getvalue()


# ---- Column letters ----

def test_column_letter():
    assert [column_letter(n) for n in (0, 25, 26, 701, 702)] == ["A", "Z", "AA", "ZZ", "AAA"]


def test_column_number_reverses_column_letter():
    for n in range(800):
        assert column_number(column_letter(n)) == n


# ---- relative_form: spotting copies of one formula ----

def test_copied_formulas_match():
    # =D2/65 in F2 and =D3/65 in F3 (row numbers start at 1, columns at 0).
    assert relative_form("=D2/65", 2, 5) == relative_form("=D3/65", 3, 5) == "=C[-2]R[0]/65"


def test_different_formulas_dont_match():
    assert relative_form("=D2/65", 2, 5) != relative_form("=E2/65", 2, 5)


def test_dollar_references_stay_fixed():
    assert relative_form("=D2*$B$1", 2, 5) == relative_form("=D3*$B$1", 3, 5)
    assert "C1R1" in relative_form("=D2*$B$1", 2, 5)


def test_text_in_quotes_is_left_alone():
    assert relative_form('=IF(D2>0,"A1","")', 2, 5) == '=IF(C[-2]R[0]>0,"A1","")'


def test_tab_names_and_functions_are_left_alone():
    assert relative_form("=SUM(Sheet1!D2)", 2, 5) == "=SUM(Sheet1!C[-2]R[0])"


def test_range_with_moving_dollar_signs_matches():
    # Google Sheets writes $F2:F$3 (in row 2) copied down two rows as $F$3:F4, not $F4:F$3.
    assert relative_form("=SUM($F2:F$3)", 2, 7) == relative_form("=SUM($F$3:F4)", 4, 7)


# ---- Headings and labels ----

def test_heading_row_is_the_row_with_most_text():
    rows = [[text("Payroll")], [text("Name"), text("Hours"), text("Pay")], [text("Ann"), number(8), number(120)]]
    assert find_heading_row(rows) == 1


def test_heading_row_of_empty_tab_is_none():
    assert find_heading_row([]) is None


def test_reference_labels_use_row_label_and_heading():
    rows = [[text("Item"), text("Amount")], [text("Total income"), number(100)]]
    tab = {"rows": rows, "headings": {0: "Item", 1: "Amount"}, "heading_row": 0}
    assert reference_labels("=B2*2", tab, {"Sheet1": tab}) == ["B2 = Total income / Amount"]


def test_reference_labels_for_a_column_range():
    rows = [[text("Item"), text("Amount")]]
    tab = {"rows": rows, "headings": {0: "Item", 1: "Amount"}, "heading_row": 0}
    assert reference_labels("=SUM(B2:B10)", tab, {"Sheet1": tab}) == ["B2:B10 = Amount"]


# ---- Named functions from the Excel export ----

def test_named_functions_are_read_and_cleaned():
    xlsx = xlsx_with_names({
        "CALC_PAY": "_xlfn.LAMBDA(_xlpm.hours,_xlpm.rate,_xlpm.hours*_xlpm.rate)",
        "CURRENT_YEAR": "Constants!$B$1",  # a named range, not a function
    })
    assert read_named_functions(FakeDrive(xlsx), "id") == {"CALC_PAY": "LAMBDA(hours,rate,hours*rate)"}


def test_named_functions_empty_when_export_fails():
    assert read_named_functions(FakeDrive(error=RuntimeError("file too big")), "id") == {}


# ---- Small helpers ----

def test_range_text():
    tabs = {0: "Sheet1", 7: "Settings"}
    assert range_text({"sheetId": 7, "startRowIndex": 0, "endRowIndex": 1,
                       "startColumnIndex": 1, "endColumnIndex": 2}, tabs) == "Settings!B1"
    assert range_text({"sheetId": 0, "startRowIndex": 2, "startColumnIndex": 0, "endColumnIndex": 1},
                      tabs) == "Sheet1!A3:A"


def test_pack_keeps_groups_under_the_limit():
    lines = ["x" * 40] * 10
    groups = pack(lines, limit=100)
    assert sum(len(g) for g in groups) == 10
    assert all(sum(len(line) + 1 for line in g) <= 100 + 41 for g in groups)
    assert len(groups) > 1


# ---- sheet_passages: a whole spreadsheet ----

def test_formula_copied_down_a_column_is_written_once(monkeypatch):
    monkeypatch.setattr(sheets, "DESCRIBE_FORMULAS_IN", [])
    result = {"sheets": [{
        "properties": {"sheetId": 0, "title": "Hours"},
        "data": [{"rowData": [
            {"values": [text("Name"), text("Hours"), text("Rate")]},
            {"values": [text("Ann"), number(650), formula("=B2/65", "10")]},
            {"values": [text("Bob"), number(130), formula("=B3/65", "2")]},
        ]}],
    }]}
    file = {"id": "abc", "name": "Payroll", "folder": "Work"}
    passages = sheet_passages(FakeDrive(error=RuntimeError("no export")), FakeSheets(result), None, file)

    read = "\n".join(r for _, r in passages)
    assert "File: Work/Payroll | Tab: Hours" in read
    assert read.count("Written in C2 as: =B2/65") == 1
    assert "C2 (Rate): [Formula 1] -> 10" in read
    assert "C3 (Rate): [Formula 1] -> 2" in read
    assert "=B3/65" not in read
    for embed, _ in passages:
        assert embed.strip()
