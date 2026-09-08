from __future__ import annotations

import shutil
import unicodedata
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING, WD_TAB_ALIGNMENT
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor


REFERENCE = Path('/Users/geniusalu/Desktop/软著/机械雷达智能自动化运维平台/机械雷达智能自动化运维平台_源码.docx')
FRONT = Path('/Users/geniusalu/Documents/brain-drone-swarm-console')
BACK = Path('/Users/geniusalu/Documents/brain-drone-swarm-server')
OUT_DIR = FRONT / 'deliverables'
OUT = OUT_DIR / '脑控无人机蜂群作战系统V1.0_源码.docx'

LINES_PER_PAGE = 52
PAGES_PER_PART = 30
MAX_COLUMNS = 82
FONT = 'Songti SC'


def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in {'W', 'F'} else 1 for ch in text)


def take_columns(text: str, limit: int) -> int:
    width = 0
    for index, char in enumerate(text):
        width += 2 if unicodedata.east_asian_width(char) in {'W', 'F'} else 1
        if width > limit:
            return index
    return len(text)


def wrap_source_line(line: str, limit: int = MAX_COLUMNS) -> list[str]:
    line = line.rstrip('\r\n').expandtabs(4)
    if not line:
        # Soft-copyright source materials count effective program lines; omit
        # blank source rows so every numbered row contains actual material.
        return []
    output: list[str] = []
    remaining = line
    base_indent = remaining[: len(remaining) - len(remaining.lstrip(' '))]
    continuation = base_indent + '    '
    while display_width(remaining) > limit:
        cut = take_columns(remaining, limit)
        preferred = max(remaining.rfind(token, 0, cut) for token in (' ', ',', '.', ')', ']', '}'))
        if preferred >= max(12, cut // 2):
            cut = preferred + (0 if remaining[preferred] == ' ' else 1)
        piece = remaining[:cut].rstrip()
        if not piece:
            piece = remaining[:cut]
        output.append(piece)
        remaining = continuation + remaining[cut:].lstrip()
    output.append(remaining)
    return output


def marker(path: Path, root: Path, label: str) -> str:
    rel = path.relative_to(root).as_posix()
    suffix = path.suffix.lower()
    if suffix in {'.py', '.qss', '.yaml', '.yml'}:
        return f'# ===== {label}: {rel} ====='
    if suffix == '.sql':
        return f'-- ===== {label}: {rel} ====='
    return f'// ===== {label}: {rel} ====='


def ordered_front_files() -> list[Path]:
    preferred = [
        FRONT / 'main.py',
        FRONT / 'src/main.py',
        FRONT / 'src/app.py',
        FRONT / 'src/container.py',
        FRONT / 'src/settings.py',
        FRONT / 'src/domain/models.py',
    ]
    included = {p.resolve() for p in preferred if p.exists()}
    rest = sorted(
        p for p in (FRONT / 'src').rglob('*')
        if p.is_file() and p.suffix.lower() in {'.py', '.qss'} and p.resolve() not in included
    )
    return [p for p in preferred if p.exists()] + rest


def ordered_back_files() -> list[Path]:
    java_root = BACK / 'src/main/java'
    resource_root = BACK / 'src/main/resources'
    main_class = java_root / 'com/braincontrol/brain_drone_swarm_server/BrainDroneSwarmServerApplication.java'
    java_files = sorted(java_root.rglob('*.java'))
    if main_class in java_files:
        java_files.remove(main_class)
        java_files.insert(0, main_class)
    resources = sorted(
        p for p in resource_root.rglob('*')
        if p.is_file() and p.suffix.lower() in {'.yaml', '.yml', '.properties', '.sql', '.xml'}
    )
    return java_files + resources


def collect_listing() -> tuple[list[str], int, int]:
    listing: list[str] = []
    front_raw = 0
    back_raw = 0
    for path in ordered_front_files():
        listing.extend(wrap_source_line(marker(path, FRONT, '前端源码')))
        lines = path.read_text(encoding='utf-8').splitlines()
        front_raw += len(lines)
        for line in lines:
            listing.extend(wrap_source_line(line))
    for path in ordered_back_files():
        listing.extend(wrap_source_line(marker(path, BACK, '后端源码')))
        lines = path.read_text(encoding='utf-8').splitlines()
        back_raw += len(lines)
        for line in lines:
            listing.extend(wrap_source_line(line))
    return listing, front_raw, back_raw


def clear_body(document: Document) -> None:
    body = document._element.body
    for child in list(body):
        if child.tag != qn('w:sectPr'):
            body.remove(child)


OUT_DIR.mkdir(parents=True, exist_ok=True)
shutil.copy2(REFERENCE, OUT)
doc = Document(OUT)
clear_body(doc)

section = doc.sections[0]
section.page_width = Cm(21)
section.page_height = Cm(29.7)
section.top_margin = Cm(2.54)
section.bottom_margin = Cm(2.54)
section.left_margin = Cm(3.175)
section.right_margin = Cm(3.175)
section.header_distance = Cm(1.25)
section.footer_distance = Cm(1.25)
section.header.is_linked_to_previous = True
section.footer.is_linked_to_previous = True
section.header.paragraphs[0].clear()
section.footer.paragraphs[0].clear()

style = doc.styles['Normal']
style.font.name = FONT
style._element.rPr.rFonts.set(qn('w:ascii'), FONT)
style._element.rPr.rFonts.set(qn('w:hAnsi'), FONT)
style._element.rPr.rFonts.set(qn('w:eastAsia'), FONT)
style.font.size = Pt(9)
style.font.color.rgb = RGBColor(0, 0, 0)
style.paragraph_format.space_before = Pt(0)
style.paragraph_format.space_after = Pt(0)
style.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
style.paragraph_format.line_spacing = Pt(11.5)

listing, front_raw, back_raw = collect_listing()
part_size = LINES_PER_PAGE * PAGES_PER_PART
if len(listing) < part_size * 2:
    raise RuntimeError(f'完整源码排版后只有 {len(listing)} 行，不足前后各 {part_size} 行')

numbered = list(enumerate(listing, start=1))
selected = numbered[:part_size] + numbered[-part_size:]

for output_index, (source_number, code) in enumerate(selected):
    paragraph = doc.add_paragraph(style='Normal')
    fmt = paragraph.paragraph_format
    fmt.alignment = WD_ALIGN_PARAGRAPH.LEFT
    fmt.left_indent = Cm(0)
    fmt.right_indent = Cm(0)
    fmt.first_line_indent = Cm(0)
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(0)
    fmt.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    fmt.line_spacing = Pt(11.5)
    fmt.tab_stops.add_tab_stop(Inches(0.46), WD_TAB_ALIGNMENT.LEFT)
    if output_index and output_index % LINES_PER_PAGE == 0:
        fmt.page_break_before = True
    run = paragraph.add_run(f'{source_number}.\t{code}')
    run.font.name = FONT
    run._element.rPr.rFonts.set(qn('w:ascii'), FONT)
    run._element.rPr.rFonts.set(qn('w:hAnsi'), FONT)
    run._element.rPr.rFonts.set(qn('w:eastAsia'), FONT)
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0, 0, 0)

doc.core_properties.title = '脑控无人机蜂群作战系统 V1.0 源程序鉴别材料'
doc.core_properties.subject = 'Python PySide6 前端与 Spring Boot 后端源程序前后各连续三十页'
doc.core_properties.author = '软件著作权人'
doc.core_properties.comments = (
    f'原始源程序量：前端 {front_raw} 行，后端 {back_raw} 行；'
    f'完整排版 {len(listing)} 行；提交前后各 {part_size} 行。'
)
doc.save(OUT)

print(f'OUTPUT={OUT}')
print(f'FRONT_RAW={front_raw}')
print(f'BACK_RAW={back_raw}')
print(f'RAW_TOTAL={front_raw + back_raw}')
print(f'FORMATTED_TOTAL={len(listing)}')
print(f'FIRST_RANGE=1-{part_size}')
print(f'LAST_RANGE={numbered[-part_size][0]}-{numbered[-1][0]}')
print(f'PAGES={len(selected) // LINES_PER_PAGE}')
