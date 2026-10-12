"""Turn a Google Sheet into passages for the chatbot.

For every spreadsheet:
- Cell formulas that are the same apart from shifted cell references (like =D2/65 in F2 and
  =D3/65 in F3) are grouped and named, like [Formula 3]. Each passage writes a formula out once
  and its cells refer to it by name, instead of repeating it in every cell.
- Named ranges (like CURRENT_YEAR) are read from the Sheets API, and named functions
  (like CALC_PAYROLL) from Google's Excel export of the file.
- For spreadsheets listed in DESCRIBE_FORMULAS_IN, Claude writes a plain-English description of
  each formula group and named function. The descriptions are what get embedded, so questions in
  everyday words can find them. Other spreadsheets embed the start of the raw formula instead.

Each passage is a pair: (text to embed, text Claude reads when answering).
"""
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET

# Spreadsheets whose formulas Claude describes. This costs API credits; other spreadsheets don't.
DESCRIBE_FORMULAS_IN = ["Payroll 2026", "Business Expenses 2026", "HSA Transaction report 2026"]
DESCRIBE_MODEL = "claude-opus-5-5"
DESCRIBE_PRICES = (4.00, 20.00)  # dollars per million tokens sent and received, for DESCRIBE_MODEL
# Descriptions already written, so later runs only pay for new or changed formulas.
SAVED_DESCRIPTIONS = "formula_descriptions.json"

# Characters per passage. The embedding model reads about the first 2,000 characters of a passage.
PASSAGE_CHARS = 2000
EMBED_FORMULA_CHARS = 150  # how much of a raw formula to embed when it has no description

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSX_NAMESPACE = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

# A cell reference like A1 or $B$2, or a column like the C's in C:C. Function names (followed by
# "(") and tab names (followed by "!") are left alone.
REFERENCE = re.compile(r"(?<![A-Za-z0-9_.])(\$?)([A-Z]{1,3})(\$?)(\d*)(?![A-Za-z0-9_(.!])")
# A range after relative_form has rewritten its ends, like C[0]R[-1]:C[0]R3.
RANGE = re.compile(r"(C\[?-?\d+\]?(?:R\[?-?\d+\]?)?):(C\[?-?\d+\]?(?:R\[?-?\d+\]?)?)")
END = re.compile(r"(C\[?-?\d+\]?)(R\[?-?\d+\]?)?")
# Text in double quotes, or a tab name in single quotes. References inside these aren't changed.
QUOTED = re.compile(r"(\"[^\"]*\"|'[^']*')")
QUOTED_TEXT = re.compile(r"(\"[^\"]*\")")
# A cell or range, maybe on another tab, like B6, $B$6, 'Sched C'!B46, Sheet1!A3:A18 or Sheet1!$A$3:$A.
TAB_REFERENCE = re.compile(
    r"(?:(?:'(?P<quoted_tab>[^']+)'|(?P<tab>[A-Za-z0-9_]+))!)?"
    r"\$?(?P<start_col>[A-Z]{1,3})\$?(?P<start_row>\d*)"
    r"(?::\$?(?P<end_col>[A-Z]{1,3})\$?(?P<end_row>\d*))?"
)

DESCRIBE_SYSTEM = """You explain spreadsheet formulas to the person who owns the spreadsheet.
For each named function and formula group you're given, write one or two plain sentences saying
what it calculates. Refer to columns by their headings and to named ranges by name, not by cell
letters, where you can. A formula group is one formula copied into many cells with its cell
references shifted for each cell, so describe what it does for any one row or column (for example,
"this row's Amount divided by 65"). A formula may come with "Where:" labels saying what the cells it
refers to are, taken from the row label and column heading next to each cell; use them.
Use everyday words. No jargon."""


def column_letter(n):
    """0 -> A, 25 -> Z, 26 -> AA."""
    letters = ""
    n += 1
    while n:
        n, remainder = divmod(n - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def column_number(letters):
    """A -> 0, Z -> 25, AA -> 26."""
    n = 0
    for letter in letters:
        n = n * 26 + ord(letter) - 64
    return n - 1


def relative_form(formula, row, col):
    """Rewrite a formula's references relative to its own cell, so copies of one formula match.

    In F2, =D2/65 becomes =C[-2]R[0]/65. In F3, =D3/65 becomes the same thing.
    References with $ stay fixed, the same way they do when a formula is copied.
    """
    def relative(match):
        col_fixed, letters, row_fixed, digits = match.groups()
        c = column_number(letters)
        text = f"C{c}" if col_fixed else f"C[{c - col}]"
        if digits:
            r = int(digits)
            text += f"R{r}" if row_fixed else f"R[{r - row}]"
        return text

    parts = QUOTED.split(formula)
    for i in range(0, len(parts), 2):  # even-numbered parts are outside quotes
        parts[i] = REFERENCE.sub(relative, parts[i])
        parts[i] = RANGE.sub(same_order_range, parts[i])
    return "".join(parts)


def same_order_range(match):
    """Put a rewritten range's ends in a fixed order.

    When Google Sheets copies a range like $F2:F$3 down one row, it writes it as $F$3:F4: the same
    range, but the $ signs move between the two ends. Sorting the column ends and the row ends
    separately makes both versions come out the same.
    """
    ends = [END.fullmatch(end).groups() for end in match.groups()]
    cols = sorted(col for col, _ in ends)
    rows = sorted(row or "" for _, row in ends)
    return f"{cols[0]}{rows[0]}:{cols[1]}{rows[1]}"


def find_heading_row(rows):
    """Guess which row holds the column headings: of the first 5 rows, the one with the most text
    cells (not numbers or formulas). Returns its index, or None if the tab is empty."""
    def text_cells(row):
        return sum(
            1 for c in row
            if c.get("userEnteredValue", {}).get("stringValue", "").strip()
        )
    candidates = [(text_cells(row), -i) for i, row in enumerate(rows[:5])]
    if not candidates or max(candidates)[0] == 0:
        return next((i for i, row in enumerate(rows) if any(c.get("formattedValue") for c in row)), None)
    return -max(candidates)[1]


def cell_label(tab, row, col, with_heading=True):
    """What a cell is, from its row label and column heading, like "Standard deduction" or
    "Total income / Amount". Row and col are 0-based. "" if there's nothing to go on."""
    parts = []
    rows = tab["rows"]
    if row < len(rows):
        # The row label is the nearest text cell to the left, usually in column A.
        for c in range(min(col, len(rows[row])) - 1, -1, -1):
            text = rows[row][c].get("userEnteredValue", {}).get("stringValue", "").strip()
            if text:
                parts.append(text)
                break
    if with_heading and col in tab["headings"] and row != tab["heading_row"]:
        parts.append(tab["headings"][col])
    return " / ".join(parts)


def reference_labels(formula, tab, tabs_by_title, limit=10):
    """Labels for the cells a formula refers to, like ["B6 = Total income", "B7 = Adjustments"].

    Covers single cells, and ranges within one column (labeled by the column heading) or one row
    (labeled by the row label). References to other tabs use that tab's labels.
    """
    labels = []
    for part in QUOTED_TEXT.split(formula)[::2]:  # skip text in double quotes
        for m in TAB_REFERENCE.finditer(part):
            before = part[m.start() - 1] if m.start() else ""
            after = part[m.end()] if m.end() < len(part) else ""
            if (before and (before.isalnum() or before in "_.")) or (after and (after.isalnum() or after in "_(.!")):
                continue
            target = tabs_by_title.get(m["quoted_tab"] or m["tab"]) if (m["quoted_tab"] or m["tab"]) else tab
            if target is None or not (m["start_row"] or m["end_col"]):
                continue
            col = column_number(m["start_col"])
            row = int(m["start_row"]) - 1 if m["start_row"] else None
            if row is None:  # a whole column, like J:J
                label = target["headings"].get(col, "") if column_number(m["end_col"]) == col else ""
            elif m["end_col"]:  # a range
                end_col = column_number(m["end_col"])
                end_row = int(m["end_row"]) - 1 if m["end_row"] else None
                if end_col == col:
                    label = target["headings"].get(col, "")
                elif end_row == row:
                    label = cell_label(target, row, col, with_heading=False)
                else:
                    label = ""
            else:
                label = cell_label(target, row, col)
            if label:
                text = f"{m.group().replace('$', '')} = {label}"
                if text not in labels:
                    labels.append(text)
            if len(labels) >= limit:
                return labels
    return labels


def read_named_functions(drive, file_id):
    """Return {name: definition} for the spreadsheet's named functions, from Google's Excel export."""
    try:
        data = drive.files().export(fileId=file_id, mimeType=XLSX).execute()
        workbook = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("xl/workbook.xml"))
    except Exception as e:  # e.g. the file is too big for Google to export
        print(f"    couldn't read named functions: {e}")
        return {}
    functions = {}
    for name in workbook.iterfind(".//m:definedName", XLSX_NAMESPACE):
        definition = (name.text or "").replace("_xlfn.", "").replace("_xlpm.", "").strip()
        if definition.upper().startswith("LAMBDA("):
            functions[name.get("name")] = definition
    return functions


def range_text(grid_range, tab_titles):
    """Turn a Sheets API range into text like 'Settings!B1' or 'Sheet1!A3:A'."""
    title = tab_titles.get(grid_range.get("sheetId", 0), "?")
    start_row, start_col = grid_range.get("startRowIndex", 0), grid_range.get("startColumnIndex", 0)
    end_row, end_col = grid_range.get("endRowIndex"), grid_range.get("endColumnIndex")
    start = f"{column_letter(start_col)}{start_row + 1}"
    if end_row == start_row + 1 and end_col == start_col + 1:
        return f"{title}!{start}"
    end = (column_letter(end_col - 1) if end_col else "") + (str(end_row) if end_row else "")
    return f"{title}!{start}:{end}"


def describe(client, file_name, tabs, named_ranges, functions, functions_to_describe, groups):
    """Ask Claude to describe the named functions in `functions_to_describe` and each formula group.

    All of `functions` are sent so Claude can understand formulas that use them.
    Returns ({function name: description}, {formula number: description}).
    """
    lines = [f"Spreadsheet: {file_name}", "", "Tabs and column headings:"]
    lines += [f"- {tab['title']}: {tab['heading_list'] or '(no headings)'}" for tab in tabs]
    if named_ranges:
        lines += ["", "Named ranges:"] + [f"- {line}" for line in named_ranges]
    if functions:
        lines += ["", "Named functions:"]
        lines += [f'<function name="{name}">\n{definition}\n</function>' for name, definition in functions.items()]
    if functions_to_describe:
        lines += ["", "Describe these named functions: " + ", ".join(functions_to_describe)]
    else:
        lines += ["", "Don't describe any named functions this time; they're only here for reference."]
    lines += ["", "Formula groups to describe (each shown as written in its first cell):"]
    for g in groups:
        where = f"\nWhere: {'; '.join(g['labels'])}" if g["labels"] else ""
        lines.append(f'<formula number="{g["number"]}" tab="{g["tab"]}" column="{g["column"]}" '
                     f'cells="{g["span"]}">\n{g["formula"]}{where}\n</formula>')

    schema = {
        "type": "object",
        "properties": {
            "functions": {"type": "array", "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
                "required": ["name", "description"],
                "additionalProperties": False,
            }},
            "formulas": {"type": "array", "items": {
                "type": "object",
                "properties": {"number": {"type": "integer"}, "description": {"type": "string"}},
                "required": ["number", "description"],
                "additionalProperties": False,
            }},
        },
        "required": ["functions", "formulas"],
        "additionalProperties": False,
    }
    with client.beta.messages.stream(
        model=DESCRIBE_MODEL,
        max_tokens=64000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # if the model declines, Anthropic retries on another model
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
        system=DESCRIBE_SYSTEM,
        messages=[{"role": "user", "content": "\n".join(lines)}],
    ) as stream:
        message = stream.get_final_message()

    cost = (message.usage.input_tokens * DESCRIBE_PRICES[0]
            + message.usage.output_tokens * DESCRIBE_PRICES[1]) / 1_000_000
    print(f"    Claude described the formulas: about ${cost:.3f}")
    if message.stop_reason != "end_turn":
        print(f"    no descriptions (Claude stopped: {message.stop_reason})")
        return {}, {}
    result = json.loads(next(block.text for block in message.content if block.type == "text"))
    return ({f["name"]: f["description"] for f in result["functions"]},
            {f["number"]: f["description"] for f in result["formulas"]})


def saved_or_new_descriptions(client, file, name, tabs, named_ranges, functions, groups):
    """Descriptions for a spreadsheet's named functions and formulas.

    Descriptions are saved in SAVED_DESCRIPTIONS, so Claude is only asked about named functions
    and formulas that are new or changed since the last run. A formula counts as unchanged if it's
    the same apart from shifted references, in the same tab, with the same labels.

    Returns ({function name: description}, {formula group number: description}).
    """
    try:
        with open(SAVED_DESCRIPTIONS, encoding="utf-8") as f:
            saved = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        saved = {}

    function_key = {fn: f"{file['id']}|function|{fn}|{definition}" for fn, definition in functions.items()}
    formula_key = {
        g["number"]: f"{file['id']}|formula|{tab}|{relative}|{'; '.join(g['labels'])}"
        for (tab, relative), g in groups.items()
    }
    new_functions = [fn for fn, key in function_key.items() if key not in saved]
    new_groups = [g for g in groups.values() if formula_key[g["number"]] not in saved]

    if new_functions or new_groups:
        print(f"    asking Claude about {len(new_functions)} named functions and {len(new_groups)} formulas "
              f"({len(functions) - len(new_functions) + len(groups) - len(new_groups)} already described)")
        described_functions, described_formulas = describe(
            client, name, tabs, named_ranges, functions, new_functions, new_groups)
        for fn, description in described_functions.items():
            if fn in function_key:
                saved[function_key[fn]] = description
        for number, description in described_formulas.items():
            if number in formula_key:
                saved[formula_key[number]] = description
        with open(SAVED_DESCRIPTIONS, "w", encoding="utf-8") as f:
            json.dump(saved, f, indent=1)

    return ({fn: saved[key] for fn, key in function_key.items() if key in saved},
            {number: saved[key] for number, key in formula_key.items() if key in saved})


def pack(lines, limit=PASSAGE_CHARS):
    """Split lines into groups of about `limit` characters."""
    groups, current, size = [], [], 0
    for line in lines:
        if current and size + len(line) > limit:
            groups.append(current)
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        groups.append(current)
    return groups


def sheet_passages(drive, sheets, client, file):
    """Return the passages for one Google Sheet, as (text to embed, text Claude reads) pairs."""
    result = sheets.spreadsheets().get(
        spreadsheetId=file["id"],
        includeGridData=True,
        fields="namedRanges(name,range),"
               "sheets(properties(sheetId,title),data.rowData.values(userEnteredValue,formattedValue))",
    ).execute()
    # Shown in every passage: the name with its folder path, like "Tax/Tax 2025/HSA report".
    name = f"{file['folder']}/{file['name']}" if file.get("folder") else file["name"]

    # Read every tab's cells and column headings, and group the formulas.
    tabs, groups, tab_titles = [], {}, {}
    for sheet in result.get("sheets", []):
        title = sheet["properties"]["title"]
        tab_titles[sheet["properties"].get("sheetId", 0)] = title
        rows = [row.get("values", []) for grid in sheet.get("data", []) for row in grid.get("rowData", [])]
        heading_row = find_heading_row(rows)
        # Only text counts as a heading, not numbers or dates.
        headings = {} if heading_row is None else {
            col: c["userEnteredValue"]["stringValue"].strip()
            for col, c in enumerate(rows[heading_row])
            if c.get("userEnteredValue", {}).get("stringValue", "").strip()
        }
        tab = {
            "title": title,
            "rows": rows,
            "headings": headings,
            "heading_row": heading_row,
            "heading_list": ", ".join(f"{column_letter(col)} = {h}" for col, h in headings.items()),
            "cells": [],  # (cell name, value shown, formula group number or None)
        }
        for row_num, row in enumerate(rows, 1):
            for col, cell in enumerate(row):
                shown = cell.get("formattedValue", "")
                formula = cell.get("userEnteredValue", {}).get("formulaValue")
                if not (formula or shown):
                    continue
                cell_name = f"{column_letter(col)}{row_num}"
                if col in headings and row_num - 1 != heading_row:
                    cell_name += f" ({headings[col]})"
                if not formula:
                    tab["cells"].append((cell_name, shown, None))
                    continue
                key = (title, relative_form(formula, row_num, col))
                if key not in groups:
                    groups[key] = {
                        "number": len(groups) + 1,
                        "tab": title,
                        "formula": formula,
                        "first": f"{column_letter(col)}{row_num}",
                        "column": f"{column_letter(col)} ({headings.get(col, 'no heading')})",
                        "cells": [],
                        "shifts": "[" in key[1],  # has references that change from cell to cell
                    }
                groups[key]["cells"].append(f"{column_letter(col)}{row_num}")
                tab["cells"].append((cell_name, shown, groups[key]["number"]))
        tabs.append(tab)

    tabs_by_title = {tab["title"]: tab for tab in tabs}
    for g in groups.values():
        count = len(g["cells"])
        g["span"] = g["first"] if count == 1 else f"{count} cells, {g['cells'][0]} to {g['cells'][-1]}"
        g["labels"] = reference_labels(g["formula"], tabs_by_title[g["tab"]], tabs_by_title)

    # Named ranges, with the value shown in the cell when the range is a single cell.
    named_ranges = []
    for named in result.get("namedRanges", []):
        where = range_text(named["range"], tab_titles)
        value = ""
        r = named["range"]
        tab = next((t for t in tabs if t["title"] == tab_titles.get(r.get("sheetId", 0))), None)
        if tab and ":" not in where:
            try:
                value = tab["rows"][r.get("startRowIndex", 0)][r.get("startColumnIndex", 0)].get("formattedValue", "")
            except IndexError:
                pass
        named_ranges.append(f"{named['name']} = {where}" + (f" -> {value}" if value else ""))

    functions = read_named_functions(drive, file["id"])

    function_descriptions, formula_descriptions = {}, {}
    if file["name"] in DESCRIBE_FORMULAS_IN and client and (groups or functions):
        function_descriptions, formula_descriptions = saved_or_new_descriptions(
            client, file, name, tabs, named_ranges, functions, groups)

    passages = []

    # 1. Overview of the spreadsheet: its tabs and column headings.
    overview = [f"File: {name} (Google Sheet). Its tabs:"]
    overview += [f"- {t['title']}: {len(t['rows'])} rows. Columns: {t['heading_list'] or '(none)'}" for t in tabs]
    passages.append(("\n".join(overview),) * 2)

    # 2. Named ranges.
    for lines in pack(named_ranges):
        text = f"File: {name} | Named ranges\n" + "\n".join(lines)
        passages.append((text, text))

    # 3. One passage per named function.
    for fn, definition in functions.items():
        description = function_descriptions.get(fn, "")
        header = f"File: {name} | Named function {fn}"
        embed = f"{header}: {description or ' '.join(definition.split())[:EMBED_FORMULA_CHARS * 2]}"
        read = f"{header}\n" + (f"What it does: {description}\n" if description else "") + f"Definition: {definition}"
        passages.append((embed, read))

    # 4. The cells of each tab. A formula used in only one cell is written in that cell's line.
    # A formula used in several cells is named [Formula n], written out once at the top of each
    # passage that uses it, and its cells refer to it by name.
    by_number = {g["number"]: g for g in groups.values()}
    names = {}  # group number -> n in [Formula n], only for formulas used in several cells
    for g in groups.values():
        if len(g["cells"]) > 1:
            names[g["number"]] = len(names) + 1

    def formula_block(number):
        """(text to embed, text Claude reads) defining a named formula."""
        g = by_number[number]
        description = formula_descriptions.get(number, "")
        raw = " ".join(g["formula"].split())
        where = "; ".join(g["labels"])
        embed = f"[Formula {names[number]}]: " + (description or raw[:EMBED_FORMULA_CHARS] + (f" (where {where})" if where else ""))
        read = (f"[Formula {names[number]}]: " + (f"{description}\n  " if description else "")
                + f"Written in {g['first']} as: {g['formula']}"
                + (f"\n  Where (in {g['first']}): {where}" if where else "")
                + f"\n  Used in {g['span']}"
                + ("; in each cell the references shift with the cell's position." if g["shifts"] else "."))
        return embed, read

    def cell_line(cell_name, shown, number):
        """(text to embed, text Claude reads) for one cell."""
        if number is None:
            line = f"{cell_name}: {shown}"
            return line, line
        if number in names:
            line = f"{cell_name}: [Formula {names[number]}] -> {shown}"
            return line, line
        g = by_number[number]
        description = formula_descriptions.get(number, "")
        raw = " ".join(g["formula"].split())
        where = f" (where {'; '.join(g['labels'])})" if g["labels"] else ""
        embed = f"{cell_name}: {description or raw[:EMBED_FORMULA_CHARS] + where} -> {shown}"
        read = f"{cell_name}: {g['formula']} -> {shown}{where}" + (f"  [{description}]" if description else "")
        return embed, read

    for tab in tabs:
        header = f"File: {name} | Tab: {tab['title']} | Columns: {tab['heading_list'][:300]}"

        def make_passage(lines, defined):
            blocks = [formula_block(number) for number in defined]
            embed = "\n".join([header] + [e for e, _ in blocks] + [e for e, _ in lines])
            read = "\n".join([header] + (["Formulas:"] + [r for _, r in blocks] + ["Cells:"] if blocks else [])
                             + [r for _, r in lines])
            return embed, read

        lines, defined, size = [], [], len(header)
        for cell_name, shown, number in tab["cells"]:
            line = cell_line(cell_name, shown, number)
            needs_block = number in names and number not in defined
            extra = len(line[1]) + 1 + (len(formula_block(number)[1]) if needs_block else 0)
            if lines and size + extra > PASSAGE_CHARS:
                passages.append(make_passage(lines, defined))
                lines, defined, size = [], [], len(header)
                needs_block = number in names
                extra = len(line[1]) + 1 + (len(formula_block(number)[1]) if needs_block else 0)
            if needs_block:
                defined.append(number)
            lines.append(line)
            size += extra
        if lines:
            passages.append(make_passage(lines, defined))
    return passages
