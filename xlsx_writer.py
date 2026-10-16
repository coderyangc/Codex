"""依赖第三方库的最小 XLSX 写入器。

性能采集 EXE 需要在没有 Python/Excel 插件的 Windows 电脑上直接生成工作簿，
因此这里使用标准库写出 OOXML 文件。数值保持为数值单元格，中文表头使用
inlineStr，避免依赖共享字符串表。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence
from xml.sax.saxutils import escape
import re
import zipfile


INVALID_XML_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")
NUMBER_RE = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$")


def _clean(value: Any) -> str:
    text = "" if value is None else str(value)
    return INVALID_XML_RE.sub("", text)


def _xml_text(value: Any) -> str:
    return escape(_clean(value), {"'": "&apos;", '"': "&quot;"})


def _column_name(index: int) -> str:
    result = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _cell(row: int, col: int, value: Any, header: bool = False) -> str:
    ref = f"{_column_name(col)}{row}"
    if value is None or value == "":
        return ""
    if _is_number(value):
        return f'<c r="{ref}" s="{2 if header else 1}"><v>{value}</v></c>'
    return (
        f'<c r="{ref}" t="inlineStr" s="{2 if header else 0}">'
        f'<is><t xml:space="preserve">{_xml_text(value)}</t></is></c>'
    )


def _sheet_xml(rows: Sequence[Sequence[Any]], widths: Sequence[int]) -> str:
    safe_rows = list(rows) or [[""]]
    max_col = max((len(row) for row in safe_rows), default=1)
    max_col = max(1, max_col)
    max_row = max(1, len(safe_rows))
    cells: list[str] = []
    for row_index, row in enumerate(safe_rows, start=1):
        cells.append(
            f'<row r="{row_index}">' + "".join(
                _cell(row_index, col_index, value, header=row_index == 1)
                for col_index, value in enumerate(row)
            ) + "</row>"
        )
    end_ref = f"{_column_name(max_col - 1)}{max_row}"
    cols = []
    for index in range(max_col):
        width = widths[index] if index < len(widths) else 16
        width = max(10, min(int(width), 60))
        cols.append(f'<col min="{index + 1}" max="{index + 1}" width="{width}" customWidth="1"/>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<dimension ref="A1:{end_ref}"/><sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/></sheetView></sheetViews>'
        f'<cols>{"".join(cols)}</cols><sheetData>{"".join(cells)}</sheetData>'
        f'<autoFilter ref="A1:{end_ref}"/><pageMargins left="0.25" right="0.25" '
        'top="0.5" bottom="0.5" header="0.3" footer="0.3"/></worksheet>'
    )


def _styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="0"/><fonts count="2">'
        '<font><sz val="10"/><name val="Microsoft YaHei"/></font>'
        '<font><b/><sz val="10"/><name val="Microsoft YaHei"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="D9EAF7"/>'
        '<bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" applyNumberFormat="1"/>'
        '<xf numFmtId="0" fontId="1" fillId="1" borderId="0" applyFont="1" applyFill="1"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )


def _content_types(sheet_count: int) -> str:
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        f"{overrides}</Types>"
    )


def _workbook_xml(names: Sequence[str]) -> str:
    sheets = "".join(
        f'<sheet name="{_xml_text(name)[:31]}" sheetId="{index}" r:id="rId{index + 1}"/>'
        for index, name in enumerate(names, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{sheets}</sheets></workbook>'
    )


def _workbook_rels(sheet_count: int) -> str:
    rels = "".join(
        f'<Relationship Id="rId{index + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{rels}<Relationship Id="rId{sheet_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>'
    )


def write_workbook(path: str | Path, sheets: Iterable[tuple[str, Sequence[Sequence[Any]]]]) -> Path:
    """写出一个可被 Excel 直接打开的中文工作簿。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    prepared = [(str(name), list(rows)) for name, rows in sheets]
    if not prepared:
        prepared = [("采集数据", [[""]])]
    names = [name for name, _ in prepared]
    now = datetime.now().isoformat(timespec="seconds")
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        '<dc:creator>Android App Performance Monitor</dc:creator>'
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
        '</cp:coreProperties>'
    )
    app = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        '<Application>Android App Performance Monitor</Application></Properties>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        '</Relationships>'
    )
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types(len(prepared)))
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("docProps/core.xml", core)
        archive.writestr("docProps/app.xml", app)
        archive.writestr("xl/workbook.xml", _workbook_xml(names))
        archive.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(len(prepared)))
        archive.writestr("xl/styles.xml", _styles_xml())
        for index, (_, rows) in enumerate(prepared, start=1):
            max_cols = max((len(row) for row in rows), default=1)
            widths = []
            for col in range(max_cols):
                longest = max((len(_clean(row[col])) for row in rows if col < len(row)), default=10)
                widths.append(min(60, max(12, longest * 1.5)))
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _sheet_xml(rows, widths))
    return target

