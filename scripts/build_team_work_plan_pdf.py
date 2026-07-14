"""Build a polished Chinese PDF from docs/team_work_plan_v0.1.md."""

from __future__ import annotations

import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "team_work_plan_v0.1.md"
OUTPUT = ROOT / "output" / "pdf" / "OSS-Mentor_四人技术分工方案_v0.1.pdf"

PAGE_WIDTH, PAGE_HEIGHT = A4
GREEN = colors.HexColor("#176B50")
GREEN_DARK = colors.HexColor("#104D3B")
GREEN_SOFT = colors.HexColor("#E3F0E9")
ORANGE = colors.HexColor("#DF6C3D")
ORANGE_SOFT = colors.HexColor("#F8E5DC")
INK = colors.HexColor("#17201D")
MUTED = colors.HexColor("#66716D")
PAPER = colors.HexColor("#F8F6EF")
LINE = colors.HexColor("#D8D7CE")
WHITE = colors.white


def register_fonts() -> None:
    pdfmetrics.registerFont(TTFont("CN", r"C:\Windows\Fonts\msyh.ttc"))
    pdfmetrics.registerFont(TTFont("CNBold", r"C:\Windows\Fonts\msyhbd.ttc"))
    pdfmetrics.registerFont(TTFont("Mono", r"C:\Windows\Fonts\consola.ttf"))
    pdfmetrics.registerFontFamily("CN", normal="CN", bold="CNBold")


def inline_markup(value: str) -> str:
    escaped = html.escape(value.strip())
    escaped = re.sub(
        r"`([^`]+)`",
        lambda match: (
            '<font name="Mono" color="#176B50" size="8">'
            + match.group(1)
            + "</font>"
        ),
        escaped,
    )
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
    return escaped


def make_styles() -> dict[str, ParagraphStyle]:
    sample = getSampleStyleSheet()
    styles: dict[str, ParagraphStyle] = {}
    styles["body"] = ParagraphStyle(
        "BodyCN",
        parent=sample["BodyText"],
        fontName="CN",
        fontSize=9.2,
        leading=14.6,
        textColor=INK,
        wordWrap="CJK",
        spaceAfter=5,
    )
    styles["bullet"] = ParagraphStyle(
        "BulletCN",
        parent=styles["body"],
        leftIndent=15,
        firstLineIndent=-9,
        bulletIndent=4,
        spaceAfter=3,
    )
    styles["h1"] = ParagraphStyle(
        "SectionCN",
        parent=styles["body"],
        fontName="CNBold",
        fontSize=18,
        leading=24,
        textColor=GREEN_DARK,
        spaceBefore=16,
        spaceAfter=10,
        keepWithNext=True,
        outlineLevel=0,
    )
    styles["h2"] = ParagraphStyle(
        "SubsectionCN",
        parent=styles["body"],
        fontName="CNBold",
        fontSize=13,
        leading=18,
        textColor=INK,
        spaceBefore=12,
        spaceAfter=7,
        keepWithNext=True,
        outlineLevel=1,
    )
    styles["h3"] = ParagraphStyle(
        "TaskCN",
        parent=styles["body"],
        fontName="CNBold",
        fontSize=10.4,
        leading=15,
        textColor=GREEN,
        spaceBefore=9,
        spaceAfter=5,
        keepWithNext=True,
        outlineLevel=2,
    )
    styles["table"] = ParagraphStyle(
        "TableCN",
        parent=styles["body"],
        fontSize=7.7,
        leading=11.2,
        spaceAfter=0,
    )
    styles["table_header"] = ParagraphStyle(
        "TableHeaderCN",
        parent=styles["table"],
        fontName="CNBold",
        textColor=WHITE,
        alignment=TA_LEFT,
    )
    styles["toc_title"] = ParagraphStyle(
        "TOCTitleCN",
        parent=styles["h1"],
        fontSize=22,
        leading=28,
        spaceAfter=18,
    )
    styles["toc_0"] = ParagraphStyle(
        "TOC0CN",
        parent=styles["body"],
        fontName="CNBold",
        fontSize=10,
        leading=17,
        leftIndent=0,
        firstLineIndent=0,
        textColor=INK,
    )
    styles["toc_1"] = ParagraphStyle(
        "TOC1CN",
        parent=styles["body"],
        fontSize=8.5,
        leading=14,
        leftIndent=12,
        firstLineIndent=0,
        textColor=MUTED,
    )
    styles["cover_title"] = ParagraphStyle(
        "CoverTitleCN",
        parent=styles["body"],
        fontName="CNBold",
        fontSize=31,
        leading=43,
        textColor=INK,
        alignment=TA_LEFT,
    )
    styles["cover_subtitle"] = ParagraphStyle(
        "CoverSubtitleCN",
        parent=styles["body"],
        fontSize=13,
        leading=22,
        textColor=MUTED,
    )
    return styles


class TeamPlanDocTemplate(BaseDocTemplate):
    def __init__(self, filename: str, styles: dict[str, ParagraphStyle]) -> None:
        super().__init__(
            filename,
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=19 * mm,
            bottomMargin=18 * mm,
            title="OSS-Mentor 四人技术分工方案 v0.1",
            author="OSS-Mentor 项目组",
            subject="四人技术分工与两周实施计划",
        )
        self.styles = styles
        cover_frame = Frame(
            18 * mm,
            18 * mm,
            PAGE_WIDTH - 36 * mm,
            PAGE_HEIGHT - 36 * mm,
            id="cover-frame",
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        content_frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            self.width,
            self.height,
            id="content-frame",
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        self.addPageTemplates(
            [
                PageTemplate(id="cover", frames=[cover_frame], onPage=self.draw_cover),
                PageTemplate(id="content", frames=[content_frame], onPage=self.draw_content),
            ]
        )

    def draw_cover(self, canvas, doc) -> None:
        canvas.saveState()
        canvas.setFillColor(PAPER)
        canvas.rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT, fill=1, stroke=0)
        canvas.setFillColor(GREEN)
        canvas.rect(0, PAGE_HEIGHT - 9 * mm, PAGE_WIDTH, 9 * mm, fill=1, stroke=0)
        canvas.setFillColor(ORANGE)
        canvas.circle(PAGE_WIDTH - 27 * mm, PAGE_HEIGHT - 31 * mm, 14 * mm, fill=1, stroke=0)
        canvas.setFillColor(GREEN_SOFT)
        canvas.circle(PAGE_WIDTH - 16 * mm, PAGE_HEIGHT - 50 * mm, 8 * mm, fill=1, stroke=0)
        canvas.restoreState()

    def draw_content(self, canvas, doc) -> None:
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(18 * mm, PAGE_HEIGHT - 13 * mm, PAGE_WIDTH - 18 * mm, PAGE_HEIGHT - 13 * mm)
        canvas.setFont("CN", 7.3)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, PAGE_HEIGHT - 10 * mm, "OSS-Mentor · 四人技术分工方案")
        canvas.drawRightString(PAGE_WIDTH - 18 * mm, 9 * mm, f"{doc.page}")
        canvas.setFillColor(GREEN)
        canvas.circle(18 * mm, 9.5 * mm, 1.1 * mm, fill=1, stroke=0)
        canvas.restoreState()

    def afterFlowable(self, flowable) -> None:
        if isinstance(flowable, Paragraph):
            style_name = flowable.style.name
            levels = {"SectionCN": 0, "SubsectionCN": 1, "TaskCN": 2}
            if style_name in levels:
                level = levels[style_name]
                text = flowable.getPlainText()
                key = f"heading-{level}-{self.seq.nextf('heading')}"
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(text, key, level=level, closed=level > 0)
                self.notify("TOCEntry", (level, text, self.page, key))


def make_table(rows: list[list[str]], styles: dict[str, ParagraphStyle], width: float) -> Table:
    count = max(len(row) for row in rows)
    normalized = [row + [""] * (count - len(row)) for row in rows]
    if count == 2:
        ratios = [0.30, 0.70]
    elif count == 3:
        ratios = [0.22, 0.34, 0.44]
    elif count == 4:
        ratios = [0.16, 0.24, 0.28, 0.32]
    else:
        ratios = [1 / count] * count
    data = []
    for row_index, row in enumerate(normalized):
        style = styles["table_header"] if row_index == 0 else styles["table"]
        data.append([Paragraph(inline_markup(cell), style) for cell in row])
    table = Table(
        data,
        colWidths=[width * ratio for ratio in ratios],
        repeatRows=1,
        hAlign="LEFT",
        splitByRow=1,
    )
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), GREEN_DARK),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.35, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    for row_index in range(1, len(data)):
        background = colors.white if row_index % 2 else PAPER
        commands.append(("BACKGROUND", (0, row_index), (-1, row_index), background))
    table.setStyle(TableStyle(commands))
    return table


def make_code_block(code: str, styles: dict[str, ParagraphStyle], width: float) -> Table:
    code_font = "CN" if any(ord(character) > 127 for character in code) else "Mono"
    code_style = ParagraphStyle(
        "CodeBlock",
        fontName=code_font,
        fontSize=7.4,
        leading=11.2,
        textColor=colors.HexColor("#25322D"),
        leftIndent=0,
        rightIndent=0,
    )
    pre = Preformatted(code.rstrip(), code_style, maxLineLength=92)
    block = Table([[pre]], colWidths=[width], hAlign="LEFT")
    block.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EEF1ED")),
                ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                ("LINEBEFORE", (0, 0), (0, -1), 3, GREEN),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return block


def make_pipeline(styles: dict[str, ParagraphStyle], width: float) -> Table:
    cell_style = ParagraphStyle(
        "PipelineCell",
        parent=styles["table"],
        fontName="CNBold",
        fontSize=8,
        leading=12,
        alignment=TA_CENTER,
        textColor=GREEN_DARK,
    )
    stages = [
        "A<br/>采集候选任务",
        "B<br/>清洗与特征提取",
        "C<br/>推荐与离线评估",
        "D<br/>API 与网页集成",
        "v0.4<br/>可运行版本",
    ]
    row = []
    stage_width = width * 0.168
    arrow_width = width * 0.04
    widths = []
    for index, stage in enumerate(stages):
        row.append(Paragraph(stage, cell_style))
        widths.append(stage_width)
        if index < len(stages) - 1:
            row.append(Paragraph("→", cell_style))
            widths.append(arrow_width)
    table = Table([row], colWidths=widths, hAlign="LEFT")
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]
    for index in range(0, len(row), 2):
        commands.extend(
            [
                ("BACKGROUND", (index, 0), (index, 0), GREEN_SOFT),
                ("BOX", (index, 0), (index, 0), 0.6, colors.HexColor("#B8D4C5")),
            ]
        )
    table.setStyle(TableStyle(commands))
    return table


def parse_markdown(text: str, styles: dict[str, ParagraphStyle], width: float) -> list:
    lines = text.splitlines()
    story: list = []
    paragraph_lines: list[str] = []
    index = 0

    def flush_paragraph() -> None:
        if paragraph_lines:
            joined = " ".join(line.strip() for line in paragraph_lines)
            story.append(Paragraph(inline_markup(joined), styles["body"]))
            paragraph_lines.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            index += 1
            continue
        if stripped.startswith("```"):
            flush_paragraph()
            language = stripped[3:].strip()
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if language == "mermaid":
                story.extend([Spacer(1, 3), make_pipeline(styles, width), Spacer(1, 7)])
            else:
                story.extend(
                    [Spacer(1, 2), make_code_block("\n".join(code_lines), styles, width), Spacer(1, 7)]
                )
            index += 1
            continue
        heading = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            markdown_level = len(heading.group(1))
            if markdown_level == 1:
                index += 1
                continue
            style_key = {2: "h1", 3: "h2", 4: "h3"}[markdown_level]
            if markdown_level == 2:
                story.append(CondPageBreak(48 * mm))
            story.append(Paragraph(inline_markup(heading.group(2)), styles[style_key]))
            index += 1
            continue
        if stripped.startswith("|") and index + 1 < len(lines):
            delimiter = lines[index + 1].strip()
            if delimiter.startswith("|") and re.fullmatch(r"[|:\-\s]+", delimiter):
                flush_paragraph()
                raw_rows: list[list[str]] = []
                while index < len(lines) and lines[index].strip().startswith("|"):
                    current = lines[index].strip()
                    if not re.fullmatch(r"[|:\-\s]+", current):
                        raw_rows.append([cell.strip() for cell in current.strip("|").split("|")])
                    index += 1
                story.extend([make_table(raw_rows, styles, width), Spacer(1, 7)])
                continue
        bullet = re.match(r"^-\s+(.+)$", stripped)
        if bullet:
            flush_paragraph()
            story.append(
                Paragraph(
                    "<font color='#176B50'>●</font> " + inline_markup(bullet.group(1)),
                    styles["bullet"],
                )
            )
            index += 1
            continue
        paragraph_lines.append(stripped)
        index += 1
    flush_paragraph()
    return story


def build_cover(styles: dict[str, ParagraphStyle], width: float) -> list:
    meta_style = ParagraphStyle(
        "CoverMeta",
        parent=styles["body"],
        fontName="CNBold",
        fontSize=9,
        leading=14,
        textColor=GREEN,
    )
    chip_style = ParagraphStyle(
        "Chip",
        parent=styles["body"],
        fontName="CNBold",
        fontSize=8.5,
        leading=12,
        alignment=TA_CENTER,
        textColor=GREEN_DARK,
    )
    chips = Table(
        [[Paragraph("4 人团队", chip_style), Paragraph("2 周迭代", chip_style), Paragraph("纯技术分工", chip_style)]],
        colWidths=[width * 0.22] * 3,
        hAlign="LEFT",
    )
    chips.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), GREEN_SOFT),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#B8D4C5")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, WHITE),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    role_data = [
        ["A", "数据采集", "任务从哪里来"],
        ["B", "任务特征", "任务需要什么能力"],
        ["C", "推荐评估", "任务应该推荐给谁"],
        ["D", "系统集成", "系统如何稳定运行"],
    ]
    role_rows = []
    for letter, title, detail in role_data:
        badge = Paragraph(f"<b>{letter}</b>", chip_style)
        body = Paragraph(f"<b>{title}</b><br/><font color='#66716D'>{detail}</font>", styles["body"])
        role_rows.append([badge, body])
    roles = Table(role_rows, colWidths=[18 * mm, width - 18 * mm], hAlign="LEFT")
    roles.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), GREEN),
                ("TEXTCOLOR", (0, 0), (0, -1), WHITE),
                ("BACKGROUND", (1, 0), (1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return [
        Spacer(1, 31 * mm),
        Paragraph("OSS-Mentor", meta_style),
        Spacer(1, 4 * mm),
        Paragraph("四人技术分工方案", styles["cover_title"]),
        Spacer(1, 3 * mm),
        Paragraph("候选池扩充、任务特征、离线评估与系统工程", styles["cover_subtitle"]),
        Spacer(1, 10 * mm),
        chips,
        Spacer(1, 16 * mm),
        roles,
        Spacer(1, 19 * mm),
        Paragraph("版本 v0.1　·　制定日期 2026-07-14", styles["cover_subtitle"]),
        Spacer(1, 3 * mm),
        Paragraph("当前基础：本地 MVP、双通道匹配、自定义画像与反馈闭环已完成", styles["body"]),
        NextPageTemplate("content"),
        PageBreak(),
    ]


def build() -> Path:
    register_fonts()
    styles = make_styles()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = TeamPlanDocTemplate(str(OUTPUT), styles)
    story = build_cover(styles, doc.width)
    toc = TableOfContents()
    toc.levelStyles = [styles["toc_0"], styles["toc_1"], styles["toc_1"]]
    story.extend(
        [
            Paragraph("目录", styles["toc_title"]),
            toc,
            PageBreak(),
        ]
    )
    story.extend(parse_markdown(SOURCE.read_text(encoding="utf-8"), styles, doc.width))
    doc.multiBuild(story)
    return OUTPUT


if __name__ == "__main__":
    print(build())
