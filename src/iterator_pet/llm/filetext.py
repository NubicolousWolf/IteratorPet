"""把文件**内容**读出来（用户 2026-10-30 拍板：10000 字上限、内容进历史）。

只做"读成一段纯文本"，不做任何解释。三条边界：

1. **只读用户点名的后缀**：``.txt .md .csv .doc .docx .xlsx``。其余后缀一律只给名字——
   二进制读出来只会是乱码，模型拿到乱码反而会瞎编；
2. **最多读 4 MB 原始字节、最多交 10000 字**：不是防谁，是别让一次拖放把内存和 token 打爆；
   截断时明确告诉模型"后面还有"，免得它以为文件就这么长；
3. **全部用标准库**：``docx``/``xlsx`` 本质是 zip + XML，用 `zipfile` + `re` 解，
   不引入 python-docx / openpyxl，打包和版本都少两个坑。

编码：``utf-8-sig`` → ``gb18030`` → ``latin-1`` 依次试（本机中文 Windows 的老文件多是 GBK）。
"""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path

TEXT_SUFFIXES = (".txt", ".md", ".csv", ".doc", ".docx", ".xlsx")

MAX_CONTENT_CHARS = 10_000
MAX_READ_BYTES = 4 * 1024 * 1024

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_W_T = re.compile(r"<w:t[^>]*>(.*?)</w:t>", re.S)
_XLSX_T = re.compile(r"<t[^>]*>(.*?)</t>", re.S)
_XLSX_CELL = re.compile(r"<c\b([^>]*)>(.*?)</c>", re.S)
_XLSX_V = re.compile(r"<v[^>]*>(.*?)</v>", re.S)
_XLSX_INLINE = re.compile(r"<is>(.*?)</is>", re.S)


def is_text_suffix(name: str) -> bool:
    return Path(str(name or "")).suffix.lower() in TEXT_SUFFIXES


def clean_text(raw: str) -> str:
    """去控制字符、统一换行、压掉连续空行（保留段落感）。"""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="ignore")


def _read_plain(path: Path) -> str:
    with path.open("rb") as fh:
        data = fh.read(MAX_READ_BYTES)
    return _decode(data)


def _read_docx(path: Path) -> str:
    """``.docx`` = zip + ``word/document.xml``，段落在 ``</w:p>`` 处换行。"""
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
    xml = xml.replace("</w:p>", "\n")
    return html.unescape("".join(_W_T.findall(xml)))


def _read_xlsx(path: Path) -> str:
    """``.xlsx``：共享字符串 + 每行单元格，用制表符/换行拼成一眼能读的表。"""
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            sx = zf.read("xl/sharedStrings.xml").decode("utf-8", errors="ignore")
            shared = [html.unescape(re.sub(r"<[^>]+>", "", item))
                      for item in _XLSX_T.findall(sx)]
        sheets = sorted(n for n in names
                        if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
        rows: list[str] = []
        for sheet in sheets[:3]:                      # 最多三个工作表，够看内容了
            xml = zf.read(sheet).decode("utf-8", errors="ignore")
            for row in re.split(r"</row>", xml):
                cells: list[str] = []
                for attrs, body in _XLSX_CELL.findall(row):
                    value = ""
                    if 't="s"' in attrs:                # 指向共享字符串
                        match = _XLSX_V.search(body)
                        if match and match.group(1).strip().isdigit():
                            index = int(match.group(1))
                            value = shared[index] if index < len(shared) else ""
                    elif 't="inlineStr"' in attrs:       # 内联字符串
                        inline = _XLSX_INLINE.search(body)
                        if inline:
                            value = re.sub(r"<[^>]+>", "", inline.group(1))
                    else:                                # 数字/日期/布尔
                        match = _XLSX_V.search(body)
                        value = match.group(1) if match else ""
                    cells.append(html.unescape(value.strip()))
                if any(cells):
                    rows.append("\t".join(cells))
        return "\n".join(rows)


def _read_doc(path: Path) -> str:
    """旧版二进制 ``.doc`` **解不了**，只能尽力抽出可读片段（失败就空手而归）。

    诚实说明比假装成功好：抽出来是乱码时，调用方会退化成"只给名字"。
    """
    with path.open("rb") as fh:
        data = fh.read(MAX_READ_BYTES)
    best = ""
    for encoding in ("utf-16-le", "gb18030"):
        try:
            text = data.decode(encoding, errors="ignore")
        except LookupError:                           # pragma: no cover
            continue
        runs = re.findall(r"[\u4e00-\u9fff\u3000-\u303fA-Za-z0-9，。！？、；：（）“”‘’\s]{8,}", text)
        candidate = "\n".join(run.strip() for run in runs if run.strip())
        if len(candidate) > len(best):
            best = candidate
    return best


def read_content(name: str, path: Path | None = None) -> tuple[str, str]:
    """返回 ``(内容, 说明)``。内容为空时说明原因；内容非空时说明是否截断。"""
    target = Path(path) if path is not None else Path(name)
    suffix = target.suffix.lower()
    if suffix not in TEXT_SUFFIXES:
        return "", "这个后缀不读内容"
    try:
        if suffix == ".docx":
            text = _read_docx(target)
        elif suffix == ".xlsx":
            text = _read_xlsx(target)
        elif suffix == ".doc":
            text = _read_doc(target)
        else:
            text = _read_plain(target)
    except (OSError, KeyError, zipfile.BadZipFile, ValueError) as exc:
        return "", f"读不了（{type(exc).__name__}）"
    text = clean_text(text)
    if not text:
        return "", "里面没有可读的文字"
    if len(text) > MAX_CONTENT_CHARS:
        return text[:MAX_CONTENT_CHARS], f"太长，只取了前 {MAX_CONTENT_CHARS} 字"
    return text, ""
