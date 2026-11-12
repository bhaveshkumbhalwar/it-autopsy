"""PDF autopsy report generation with reportlab (no external services)."""

from __future__ import annotations

import io
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.config import settings
from app.core.models import FAILURE_LABELS

SEVERITY_COLORS = {
    "LOW": colors.HexColor("#2f855a"),
    "MODERATE": colors.HexColor("#b7791f"),
    "HIGH": colors.HexColor("#c05621"),
    "CRITICAL": colors.HexColor("#c53030"),
}


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("TitleX", parent=base["Title"], fontSize=20, spaceAfter=2, textColor=colors.HexColor("#111827")),
        "sub": ParagraphStyle("SubX", parent=base["Normal"], fontSize=9, textColor=colors.HexColor("#6b7280"), spaceAfter=10),
        "h2": ParagraphStyle("H2X", parent=base["Heading2"], fontSize=12.5, spaceBefore=12, spaceAfter=5, textColor=colors.HexColor("#111827")),
        "body": ParagraphStyle("BodyX", parent=base["BodyText"], fontSize=9.5, leading=13.5, alignment=TA_LEFT, textColor=colors.HexColor("#1f2937")),
        "small": ParagraphStyle("SmallX", parent=base["BodyText"], fontSize=8, leading=11, textColor=colors.HexColor("#6b7280")),
        "quote": ParagraphStyle("QuoteX", parent=base["BodyText"], fontSize=8.5, leading=11.5, textColor=colors.HexColor("#374151"), leftIndent=6),
    }


def _esc(text: Any) -> str:
    s = "" if text is None else str(text)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _table(rows: list[list[Any]], widths: list[float], header: bool = True) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#1f2937")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e5e7eb")),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")))
        style.append(("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"))
    table.setStyle(TableStyle(style))
    return table


def build_report_pdf(analysis, execution) -> bytes:
    st = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Agent Autopsy — {execution.agent_name if execution else 'report'}",
        author=settings.app_name,
    )
    story: list[Any] = []
    agent = execution.agent_name if execution else "Unknown agent"
    story.append(Paragraph("Agent Autopsy Report", st["title"]))
    story.append(
        Paragraph(
            f"{_esc(agent)} · execution {_esc(execution.execution_id if execution else '')} · "
            f"generated {analysis.created_at.strftime('%Y-%m-%d %H:%M UTC') if analysis.created_at else ''} "
            f"· engine {analysis.engine_version}",
            st["sub"],
        )
    )
    story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#e5e7eb")))

    sev_color = SEVERITY_COLORS.get(analysis.severity, colors.grey)
    summary_rows = [
        [Paragraph("<b>Overall status</b>", st["body"]), Paragraph(_esc(analysis.status).upper(), st["body"])],
        [Paragraph("<b>Severity</b>", st["body"]), Paragraph(f'<font color="{sev_color.hexval()}"><b>{analysis.severity}</b></font>', st["body"])],
        [
            Paragraph("<b>Failure category</b>", st["body"]),
            Paragraph(
                _esc(", ".join(FAILURE_LABELS.get(c["category"], c["category"]) for c in (analysis.categories or [])) or "None detected"),
                st["body"],
            ),
        ],
        [Paragraph("<b>Failed step</b>", st["body"]), Paragraph(_esc(analysis.failed_step_index if analysis.failed_step_index is not None else "n/a"), st["body"])],
        [Paragraph("<b>Recoverable</b>", st["body"]), Paragraph(_esc(analysis.recoverable), st["body"])],
        [Paragraph("<b>Confidence</b>", st["body"]), Paragraph(f"{analysis.confidence:.0%} (heuristic, not calibrated)", st["body"])],
    ]
    story.append(Spacer(1, 8))
    story.append(_table(summary_rows, [42 * mm, 132 * mm], header=False))

    story.append(Paragraph("1. Execution summary", st["h2"]))
    story.append(Paragraph(_esc(analysis.what_happened), st["body"]))
    if execution and execution.task_description:
        story.append(Paragraph("<b>Task:</b> " + _esc(execution.task_description), st["small"]))

    story.append(Paragraph("2. Root cause", st["h2"]))
    story.append(Paragraph(_esc(analysis.root_cause), st["body"]))

    story.append(Paragraph("3. Supporting evidence", st["h2"]))
    if analysis.evidence:
        rows = [[Paragraph("<b>Step</b>", st["small"]), Paragraph("<b>Field</b>", st["small"]), Paragraph("<b>Quote</b>", st["small"]), Paragraph("<b>Source</b>", st["small"])]]
        for ev in analysis.evidence[:20]:
            rows.append(
                [
                    Paragraph(_esc(ev.get("step_index") if ev.get("step_index") is not None else "—"), st["small"]),
                    Paragraph(_esc(ev.get("field")), st["small"]),
                    Paragraph(_esc(ev.get("quote"))[:400], st["quote"]),
                    Paragraph(_esc(ev.get("source") or "rule"), st["small"]),
                ]
            )
        story.append(_table(rows, [14 * mm, 24 * mm, 110 * mm, 18 * mm]))
    else:
        story.append(Paragraph("No evidence rows were recorded for this analysis.", st["small"]))

    story.append(Paragraph("4. Timeline", st["h2"]))
    if analysis.timeline:
        rows = [[Paragraph("<b>#</b>", st["small"]), Paragraph("<b>Step</b>", st["small"]), Paragraph("<b>Status</b>", st["small"]), Paragraph("<b>ms</b>", st["small"])]]
        for item in analysis.timeline[:40]:
            label = _esc(item.get("label"))
            if item.get("is_failure"):
                label = f'<font color="#c53030"><b>{label}</b></font>'
            rows.append(
                [
                    Paragraph(_esc(item.get("index")), st["small"]),
                    Paragraph(label, st["small"]),
                    Paragraph(_esc(item.get("status")), st["small"]),
                    Paragraph(_esc(item.get("duration_ms") if item.get("duration_ms") is not None else "—"), st["small"]),
                ]
            )
        story.append(_table(rows, [10 * mm, 116 * mm, 24 * mm, 16 * mm]))

    story.append(Paragraph("5. Impact assessment", st["h2"]))
    story.append(Paragraph(_esc(analysis.impact), st["body"]))

    story.append(Paragraph("6. Recommended fixes", st["h2"]))
    if analysis.recommendations:
        items = []
        for rec in analysis.recommendations:
            items.append(
                ListItem(
                    Paragraph(
                        f"<b>{_esc(rec.get('fix'))}</b><br/>"
                        f"<i>Priority:</i> {_esc(rec.get('priority'))} · <i>Problem:</i> {_esc(rec.get('problem'))}<br/>"
                        f"<i>Why:</i> {_esc(rec.get('reason'))}<br/><i>Benefit:</i> {_esc(rec.get('benefit'))}",
                        st["body"],
                    )
                )
            )
        story.append(ListFlowable(items, bulletType="1", leftIndent=12, bulletFontSize=8))
    else:
        story.append(Paragraph("No recommendations: no failure was detected.", st["small"]))

    story.append(Paragraph("7. Prevention strategies", st["h2"]))
    if analysis.prevention:
        story.append(ListFlowable([ListItem(Paragraph(_esc(p), st["body"])) for p in analysis.prevention], bulletType="bullet", leftIndent=12))
    else:
        story.append(Paragraph("—", st["small"]))

    story.append(Paragraph("Diagnosis notes", st["h2"]))
    story.append(ListFlowable([ListItem(Paragraph(_esc(n), st["small"])) for n in (analysis.detection_notes or [])] or [ListItem(Paragraph("—", st["small"]))], bulletType="bullet", leftIndent=12))

    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#e5e7eb")))
    story.append(
        Paragraph(
            f"Generated by {settings.app_name} v{settings.app_version}. "
            "Diagnoses are derived from the uploaded trace only; confidence values are heuristic. "
            "No uploaded code or command is ever executed.",
            st["small"],
        )
    )

    doc.build(story)
    return buffer.getvalue()
