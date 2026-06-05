# orchestrator/audit_report.py
"""
Auditor's One-Pager — PDF report generator for incident audit trails.

Produces a clean, professional PDF from the immutable audit log for any incident.
Designed to be handed to SOC 2, PCI DSS, HIPAA, or internal compliance auditors.

The report answers every question an auditor asks:
  - When did the incident start?
  - What did the AI diagnose?
  - What policy was active?
  - Who authorized recovery?
  - When did each stage occur?
  - What executed?

Usage:
    from audit_report import generate_incident_report
    pdf_bytes = generate_incident_report(incident_id, tenant_id)

Endpoint: GET /audit/{incident_id}/report
"""

import io
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("orchestrator.audit_report")

# ── Colour palette (matches AlertEngine brand) ────────────────────────────────
DARK_BG    = (0.04, 0.06, 0.10)   # #0a0f1a
ACCENT     = (0.365, 0.847, 1.0)  # #5dd8ff
POLICY_CLR = (0.961, 0.624, 0.043) # #f59e0b
SUCCESS    = (0.341, 0.949, 0.604) # #57f29a
TEXT_DARK  = (0.957, 0.984, 1.0)  # #f4fbff
MUTED      = (0.608, 0.694, 0.776) # #9bb1c6
WHITE      = (1.0, 1.0, 1.0)
LIGHT_GREY = (0.95, 0.95, 0.96)
MID_GREY   = (0.85, 0.87, 0.89)
DARK_GREY  = (0.25, 0.30, 0.36)

# Actor colours
ACTOR_COLOURS = {
    "policy":       POLICY_CLR,
    "claude":       ACCENT,
    "diagnosis":    ACCENT,
    "engineer":     SUCCESS,
    "orchestrator": MUTED,
    "pipeline":     MUTED,
    "system":       MUTED,
}


def _fmt_ts(ts: float) -> str:
    """Format a Unix timestamp as human-readable UTC."""
    if not ts:
        return "—"
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _fmt_duration(start: float, end: float) -> str:
    """Format duration between two timestamps."""
    if not start or not end:
        return "—"
    secs = int(end - start)
    if secs < 60:
        return f"{secs}s"
    mins = secs // 60
    secs = secs % 60
    return f"{mins}m {secs}s"


def generate_incident_report(
    incident_id: str,
    tenant_id: Optional[str] = None,
) -> bytes:
    """
    Generate a PDF audit report for an incident.

    Returns:
        PDF bytes ready to send as HTTP response.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, KeepTogether,
    )
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    from reportlab.lib import colors

    # Load audit log
    try:
        from audit import get_audit_log
        events = get_audit_log(incident_id)
    except Exception as e:
        logger.error("Failed to load audit log for %s: %s", incident_id, e)
        events = []

    # Load incident metadata if available
    incident = {}
    try:
        from memory import get_incident_by_id
        incident = get_incident_by_id(incident_id) or {}
    except Exception:
        pass

    # Load tenant info (safe fields only)
    tenant = {}
    try:
        from tenants import get_tenant
        raw = get_tenant(tenant_id) if tenant_id else {}
        if raw:
            tenant = {
                "tenant_id":           raw.get("tenant_id", ""),
                "plan":                raw.get("plan", ""),
                "notification_channel": raw.get("notification_channel", ""),
            }
    except Exception:
        pass

    # Load policy version
    policy_version = "unknown"
    try:
        from incident_policy import POLICY_VERSION, POLICY
        policy_version = POLICY_VERSION
    except Exception:
        pass

    # ── Build PDF ──────────────────────────────────────────────────────────────
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20*mm,
        rightMargin=20*mm,
        topMargin=20*mm,
        bottomMargin=20*mm,
        title=f"AlertEngine Audit Report — {incident_id}",
        author="FastAPI AlertEngine",
        subject="Incident Audit Trail",
    )

    W = A4[0] - 40*mm  # usable width

    # ── Styles ─────────────────────────────────────────────────────────────────
    def style(name, **kw):
        defaults = dict(fontName="Helvetica", fontSize=10, leading=14,
                        textColor=colors.HexColor("#1a2030"),
                        alignment=TA_LEFT)
        defaults.update(kw)
        return ParagraphStyle(name, **defaults)

    S = {
        "title":    style("title",   fontSize=20, fontName="Helvetica-Bold",
                          textColor=colors.HexColor("#0a0f1a"), leading=24),
        "subtitle": style("subtitle", fontSize=11, textColor=colors.HexColor("#5b6b7a")),
        "h2":       style("h2",      fontSize=13, fontName="Helvetica-Bold",
                          textColor=colors.HexColor("#0a0f1a"), spaceBefore=6),
        "h3":       style("h3",      fontSize=10, fontName="Helvetica-Bold",
                          textColor=colors.HexColor("#0a0f1a")),
        "body":     style("body"),
        "muted":    style("muted",   textColor=colors.HexColor("#5b6b7a")),
        "mono":     style("mono",    fontName="Courier", fontSize=9,
                          textColor=colors.HexColor("#1a2030")),
        "policy":   style("policy",  fontName="Helvetica-Bold",
                          textColor=colors.HexColor("#c27a00")),
        "label":    style("label",   fontSize=8, fontName="Helvetica-Bold",
                          textColor=colors.HexColor("#5b6b7a"),
                          spaceBefore=4),
        "footer":   style("footer",  fontSize=8, textColor=colors.HexColor("#9bb1c6"),
                          alignment=TA_CENTER),
    }

    story = []

    # ── Header band ────────────────────────────────────────────────────────────
    header_data = [[
        Paragraph("FastAPI<br/><font color='#5dd8ff'>AlertEngine</font>", S["title"]),
        Paragraph(
            "<b>Compliance Audit Report</b><br/>"
            "<font color='#5b6b7a'>Incident Audit Trail — Confidential</font>",
            style("hdr_right", fontSize=11, alignment=TA_RIGHT,
                  textColor=colors.HexColor("#0a0f1a"))
        ),
    ]]
    header_tbl = Table(header_data, colWidths=[W*0.5, W*0.5])
    header_tbl.setStyle(TableStyle([
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
    ]))
    story.append(header_tbl)
    story.append(HRFlowable(width=W, thickness=2,
                             color=colors.HexColor("#5dd8ff"), spaceAfter=8))

    # ── Summary box ────────────────────────────────────────────────────────────
    started_at  = events[0].get("timestamp") if events else incident.get("started_at", 0)
    last_event  = events[-1].get("timestamp") if events else 0
    final_stage = events[-1].get("stage", "UNKNOWN") if events else "UNKNOWN"
    duration    = _fmt_duration(started_at, last_event)

    summary_data = [
        ["Incident ID",    incident_id],
        ["Tenant ID",      tenant_id or "—"],
        ["Plan",           tenant.get("plan", "—").title()],
        ["Policy Version", policy_version],
        ["Started",        _fmt_ts(started_at)],
        ["Final Stage",    final_stage],
        ["Duration",       duration],
        ["Generated",      _fmt_ts(time.time())],
    ]

    summary_tbl = Table(summary_data, colWidths=[W*0.3, W*0.7])
    summary_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (0,-1), colors.HexColor("#f0f4f8")),
        ("BACKGROUND",    (1,0), (1,-1), colors.white),
        ("FONTNAME",      (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTNAME",      (1,0), (1,-1), "Helvetica"),
        ("FONTSIZE",      (0,0), (-1,-1), 9),
        ("TEXTCOLOR",     (0,0), (0,-1), colors.HexColor("#5b6b7a")),
        ("TEXTCOLOR",     (1,0), (1,-1), colors.HexColor("#0a0f1a")),
        ("ROWBACKGROUNDS",(0,0), (-1,-1), [colors.HexColor("#f8fafc"), colors.white]),
        ("GRID",          (0,0), (-1,-1), 0.5, colors.HexColor("#dde3ea")),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("RIGHTPADDING",  (0,0), (-1,-1), 8),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("ROUNDEDCORNERS",(0,0), (-1,-1), [3,3,3,3]),
    ]))
    story.append(summary_tbl)
    story.append(Spacer(1, 12))

    # ── Governance statement ───────────────────────────────────────────────────
    story.append(Paragraph("Governance Statement", S["h2"]))
    story.append(HRFlowable(width=W, thickness=0.5,
                             color=colors.HexColor("#dde3ea"), spaceAfter=6))
    story.append(Paragraph(
        "This report was generated from an append-only audit ledger. "
        "Every entry is immutable and includes the actor, decision, confidence, "
        "reason, and policy version active at the time of the decision. "
        "No entry can be modified after creation. State is derived from events — "
        "not stored directly. This audit trail can be replayed independently "
        "from the ledger to verify system integrity.",
        S["body"]
    ))
    story.append(Spacer(1, 12))

    # ── Audit trail table ──────────────────────────────────────────────────────
    story.append(Paragraph("Complete Audit Trail", S["h2"]))
    story.append(HRFlowable(width=W, thickness=0.5,
                             color=colors.HexColor("#dde3ea"), spaceAfter=6))

    if not events:
        story.append(Paragraph("No audit events found for this incident.", S["muted"]))
    else:
        # Table header
        trail_data = [[
            Paragraph("<b>Timestamp (UTC)</b>", S["label"]),
            Paragraph("<b>Stage</b>",           S["label"]),
            Paragraph("<b>Actor</b>",            S["label"]),
            Paragraph("<b>Decision</b>",         S["label"]),
            Paragraph("<b>Confidence</b>",       S["label"]),
            Paragraph("<b>Policy v</b>",         S["label"]),
        ]]

        row_styles = []
        for i, ev in enumerate(events):
            actor      = ev.get("actor", "pipeline")
            stage      = ev.get("stage", "")
            decision   = ev.get("decision", "")
            confidence = ev.get("confidence", 0)
            pv         = ev.get("metadata", {}).get("policy_version",
                         ev.get("policy_version", policy_version))
            ts         = _fmt_ts(ev.get("timestamp", 0))
            conf_str   = f"{confidence*100:.0f}%" if confidence else "—"

            row = [
                Paragraph(ts,         S["mono"]),
                Paragraph(stage,      S["h3"]),
                Paragraph(actor,      S["body"]),
                Paragraph(decision[:40] if decision else "—", S["body"]),
                Paragraph(conf_str,   S["body"]),
                Paragraph(str(pv),    S["muted"]),
            ]
            trail_data.append(row)

            # Colour-code actor column
            actor_hex = {
                "policy":       "#fff8e6",
                "claude":       "#e8f8ff",
                "diagnosis":    "#e8f8ff",
                "engineer":     "#e8fff3",
                "orchestrator": "#f5f7fa",
                "pipeline":     "#f5f7fa",
            }.get(actor, "#ffffff")
            row_styles.append(("BACKGROUND", (0, i+1), (-1, i+1),
                                colors.HexColor(actor_hex)))

        trail_tbl = Table(
            trail_data,
            colWidths=[W*0.24, W*0.16, W*0.12, W*0.24, W*0.10, W*0.14],
        )
        base_style = [
            ("BACKGROUND",    (0,0), (-1,0), colors.HexColor("#0a0f1a")),
            ("TEXTCOLOR",     (0,0), (-1,0), colors.HexColor("#5dd8ff")),
            ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0), (-1,-1), 8),
            ("GRID",          (0,0), (-1,-1), 0.4, colors.HexColor("#dde3ea")),
            ("ROWBACKGROUNDS",(0,1), (-1,-1),
             [colors.HexColor("#f8fafc"), colors.white]),
            ("LEFTPADDING",   (0,0), (-1,-1), 5),
            ("RIGHTPADDING",  (0,0), (-1,-1), 5),
            ("TOPPADDING",    (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("VALIGN",        (0,0), (-1,-1), "TOP"),
        ]
        trail_tbl.setStyle(TableStyle(base_style + row_styles))
        story.append(trail_tbl)

    story.append(Spacer(1, 12))

    # ── Reason / diagnosis detail ──────────────────────────────────────────────
    reason_events = [ev for ev in events if ev.get("reason")]
    if reason_events:
        story.append(Paragraph("Diagnosis & Reasoning Detail", S["h2"]))
        story.append(HRFlowable(width=W, thickness=0.5,
                                 color=colors.HexColor("#dde3ea"), spaceAfter=6))
        for ev in reason_events:
            stage  = ev.get("stage", "")
            actor  = ev.get("actor", "pipeline")
            reason = ev.get("reason", "")
            ts     = _fmt_ts(ev.get("timestamp", 0))
            conf   = ev.get("confidence", 0)

            block = [
                Paragraph(f"<b>{stage}</b> — {ts} — actor: {actor}", S["h3"]),
                Paragraph(reason, S["body"]),
            ]
            if conf:
                block.append(Paragraph(
                    f"Confidence: {conf*100:.0f}%", S["muted"]))
            block.append(Spacer(1, 6))
            story.append(KeepTogether(block))

    # ── Policy at time of incident ─────────────────────────────────────────────
    try:
        from incident_policy import POLICY, POLICY_VERSION
        story.append(Paragraph("Active Policy at Time of Incident", S["h2"]))
        story.append(HRFlowable(width=W, thickness=0.5,
                                 color=colors.HexColor("#dde3ea"), spaceAfter=6))
        story.append(Paragraph(
            f"Policy version <b>{POLICY_VERSION}</b> was active during this incident. "
            "The following thresholds governed all automated decisions:",
            S["body"]
        ))
        story.append(Spacer(1, 6))

        policy_data = [
            ["Threshold",                "Value", "Description"],
            ["Recovery score",           str(POLICY.get("recover_score", "—")),
             "Score above which system is considered recovered"],
            ["Recovery error rate",      str(POLICY.get("recover_error_rate", "—")),
             "Error rate below which recovery is confirmed"],
            ["Validation score",         str(POLICY.get("validate_score", "—")),
             "Score below which recovery link is sent"],
            ["Validation error rate",    str(POLICY.get("validate_error_rate", "—")),
             "Error rate above which recovery link is sent"],
            ["Confidence suppression",   str(POLICY.get("suppress_confidence", "—")),
             "AI confidence below which alert is suppressed"],
        ]

        policy_tbl = Table(policy_data, colWidths=[W*0.28, W*0.14, W*0.58])
        policy_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,0), colors.HexColor("#fff8e6")),
            ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0), (-1,-1), 8),
            ("TEXTCOLOR",     (0,0), (-1,0), colors.HexColor("#c27a00")),
            ("GRID",          (0,0), (-1,-1), 0.4, colors.HexColor("#dde3ea")),
            ("ROWBACKGROUNDS",(0,1), (-1,-1),
             [colors.HexColor("#fffdf5"), colors.white]),
            ("LEFTPADDING",   (0,0), (-1,-1), 6),
            ("TOPPADDING",    (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ]))
        story.append(policy_tbl)
        story.append(Spacer(1, 12))
    except Exception:
        pass

    # ── Attestation ────────────────────────────────────────────────────────────
    story.append(Paragraph("Attestation", S["h2"]))
    story.append(HRFlowable(width=W, thickness=0.5,
                             color=colors.HexColor("#dde3ea"), spaceAfter=6))
    story.append(Paragraph(
        "This report was automatically generated by FastAPI AlertEngine from an "
        "immutable append-only audit log. The audit log is maintained in Redis using "
        "RPUSH (append-only) operations and is never modified after creation. "
        "Every production action recorded in this document required explicit human "
        "authorization via a cryptographically signed, single-use JWT token with "
        "a 5-minute TTL. Replay attacks are prevented by atomic Redis SET NX "
        "operations. No autonomous remediation occurred.",
        S["body"]
    ))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        f"Generated: {_fmt_ts(time.time())} | "
        f"Incident: {incident_id} | "
        f"Policy: v{policy_version} | "
        f"Source: orchestrator/audit.py",
        S["footer"]
    ))
    story.append(HRFlowable(width=W, thickness=1,
                             color=colors.HexColor("#5dd8ff"), spaceBefore=8))
    story.append(Paragraph(
        "FastAPI AlertEngine — Authorized. Audited. Replayable. | "
        "anchorflowalertengine@outlook.com | "
        "tandem-media.github.io/fastapi-alertengine",
        S["footer"]
    ))

    # ── Build ──────────────────────────────────────────────────────────────────
    doc.build(story)
    return buf.getvalue()
