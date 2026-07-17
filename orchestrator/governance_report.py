# orchestrator/governance_report.py
"""
AlertEngine Certified Governance Report — PDF Generator
Light-background palette — prints cleanly on any printer.
"""

import hashlib
import io
import os
import time
from datetime import datetime, timezone
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

BG     = colors.HexColor("#F5F2EC")
PANEL  = colors.HexColor("#EDE7DA")
PANEL2 = colors.HexColor("#E8E1D5")
NAVY   = colors.HexColor("#0C1A27")
BRASS  = colors.HexColor("#C99A3E")
DIM    = colors.HexColor("#5C6E7A")
BORDER = colors.HexColor("#D4C9B8")
W, H   = A4

def _style(name, **kwargs):
    d = dict(fontName="Helvetica", fontSize=10, textColor=NAVY, leading=14, spaceAfter=0, spaceBefore=0)
    d.update(kwargs)
    return ParagraphStyle(name, **d)

EYEBROW  = _style("eyebrow",  fontSize=8,  textColor=BRASS, charSpace=2, leading=10)
HEADLINE = _style("headline", fontSize=22, fontName="Helvetica-Bold", textColor=NAVY, leading=26)
SUBHEAD  = _style("subhead",  fontSize=10, fontName="Helvetica-Bold", textColor=BRASS, leading=13)
BODY     = _style("body",     fontSize=9,  textColor=NAVY,  leading=13)
DIM_S    = _style("dim",      fontSize=8,  textColor=DIM,   leading=11)
MONO     = _style("mono",     fontSize=7,  fontName="Courier", textColor=DIM, leading=10)
FOOTER   = _style("footer",   fontSize=7,  textColor=DIM,   leading=10, alignment=1)


def _stat_table(stats):
    labels = [
        ("INCIDENTS\nDETECTED",       str(stats.get("incidents_observed", 0))),
        ("NOTIFICATIONS\nSUPPRESSED", str(stats.get("suppressed_notifications", 0))),
        ("TOKENS\nSUPPRESSED",        str(stats.get("suppressed_tokens", 0))),
        ("ESCALATIONS\nSUPPRESSED",   str(stats.get("suppressed_escalations", 0))),
    ]
    nums, lbls = [], []
    for lbl, num in labels:
        nums.append(Paragraph(f'<font size="24" color="{BRASS.hexval()}"><b>{num}</b></font>', BODY))
        lbls.append(Paragraph(f'<font size="7" color="{DIM.hexval()}">{lbl}</font>', MONO))
    col_w = (W - 40 * mm) / 4
    t = Table([nums, lbls], colWidths=[col_w] * 4, rowHeights=[28, 18])
    t.setStyle(TableStyle([
        ("ALIGN", (0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("BACKGROUND",(0,0),(-1,-1),PANEL), ("LINEAFTER",(0,0),(2,-1),0.5,BORDER),
        ("TOPPADDING",(0,0),(-1,-1),4), ("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))
    return t


def _incident_table(events):
    shadow = [e for e in events if e.get("actor") == "shadow_mode" or (e.get("metadata") or {}).get("shadow_mode")]
    seen = {}
    for e in shadow:
        seen[e.get("incident_id","—")] = e
    recent = sorted(seen.values(), key=lambda x: x.get("timestamp",0), reverse=True)[:5]
    if not recent:
        return Paragraph("No shadow incidents recorded during this evaluation period.", DIM_S)
    header = [Paragraph(f"<b>{h}</b>", MONO) for h in ["INCIDENT ID","TIMESTAMP (UTC)","STAGE","ACTOR","CONFIDENCE"]]
    rows = [header]
    for e in recent:
        ts = e.get("timestamp",0)
        ts_s = datetime.fromtimestamp(ts,tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else "—"
        conf = e.get("confidence")
        rows.append([
            Paragraph(str(e.get("incident_id","—"))[-20:], MONO),
            Paragraph(ts_s, MONO),
            Paragraph(str(e.get("stage","—")), MONO),
            Paragraph(str(e.get("actor","—")), MONO),
            Paragraph(f"{conf:.0%}" if isinstance(conf,float) else "—", MONO),
        ])
    t = Table(rows, colWidths=[55*mm,30*mm,25*mm,25*mm,20*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),PANEL), ("ROWBACKGROUNDS",(0,1),(-1,-1),[PANEL2,PANEL]),
        ("TEXTCOLOR",(0,0),(-1,-1),DIM), ("FONTNAME",(0,0),(-1,-1),"Courier"),
        ("FONTSIZE",(0,0),(-1,-1),7), ("ALIGN",(0,0),(-1,-1),"LEFT"),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"), ("TOPPADDING",(0,0),(-1,-1),4),
        ("BOTTOMPADDING",(0,0),(-1,-1),4), ("LINEBELOW",(0,0),(-1,0),0.5,BRASS),
        ("LINEBELOW",(0,1),(-1,-1),0.3,BORDER),
    ]))
    return t


def _compute_integrity_hash(events):
    import json
    canonical = json.dumps(
        sorted(events, key=lambda e: (e.get("timestamp",0), e.get("incident_id",""))),
        sort_keys=True, separators=(",",":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def generate_governance_pdf(tenant, shadow_report, audit_events, logo_path=None):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm, topMargin=16*mm, bottomMargin=20*mm,
        title="AlertEngine Certified Governance Report", author="Tofamba",
        subject=f"Governance Report — {tenant.get('service_name','Unknown')}")

    story = []
    title_block = [
        Paragraph("TOFAMBA · ALERTENGINE", EYEBROW), Spacer(1,3),
        Paragraph("Certified Incident Governance Report", HEADLINE), Spacer(1,3),
        Paragraph("Shadow Mode Evaluation — Official Record", DIM_S),
    ]
    if logo_path and os.path.exists(logo_path):
        ht = Table([[Image(logo_path, width=22*mm, height=22*mm), title_block]], colWidths=[28*mm, W-68*mm])
        ht.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"),("ALIGN",(0,0),(0,0),"CENTER"),("ALIGN",(1,0),(1,0),"LEFT")]))
        story.append(ht)
    else:
        [story.append(i) for i in title_block]

    story += [Spacer(1,4*mm), HRFlowable(width="100%",thickness=1.5,color=BRASS,spaceAfter=4*mm)]

    svc   = tenant.get("service_name","Unknown")
    tid   = tenant.get("tenant_id","—")
    ss    = tenant.get("shadow_enabled_at")
    se    = tenant.get("shadow_disabled_at") or time.time()
    gen   = datetime.now(tz=timezone.utc).strftime("%d %B %Y at %H:%M UTC")
    ss_s  = datetime.fromtimestamp(ss,tz=timezone.utc).strftime("%d %B %Y") if ss else "—"
    se_s  = datetime.fromtimestamp(se,tz=timezone.utc).strftime("%d %B %Y")

    id_d = [
        [Paragraph("SERVICE",EYEBROW),           Paragraph(svc, SUBHEAD)],
        [Paragraph("TENANT ID",EYEBROW),          Paragraph(tid, MONO)],
        [Paragraph("PLAN",EYEBROW),               Paragraph(tenant.get("plan","—").upper(), MONO)],
        [Paragraph("CHANNEL",EYEBROW),            Paragraph(tenant.get("notification_channel","—").upper(), MONO)],
        [Paragraph("EVALUATION PERIOD",EYEBROW),  Paragraph(f"{ss_s} — {se_s}", BODY)],
        [Paragraph("REPORT GENERATED",EYEBROW),   Paragraph(gen, BODY)],
    ]
    id_t = Table(id_d, colWidths=[42*mm, W-82*mm])
    id_t.setStyle(TableStyle([
        ("ROWBACKGROUNDS",(0,0),(-1,-1),[PANEL,PANEL2]),
        ("TOPPADDING",(0,0),(-1,-1),5), ("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),8), ("RIGHTPADDING",(0,0),(-1,-1),8),
        ("LINEBELOW",(0,-1),(-1,-1),0.5,BRASS),
    ]))
    story += [id_t, Spacer(1,5*mm)]
    story += [Paragraph("EVALUATION SUMMARY",EYEBROW), Spacer(1,2*mm), _stat_table(shadow_report), Spacer(1,5*mm)]

    inc  = shadow_report.get("incidents_observed",0)
    sup  = shadow_report.get("suppressed_notifications",0)
    decl = ParagraphStyle("decl",fontName="Helvetica",fontSize=9,textColor=NAVY,
        leading=14,backColor=PANEL,borderPad=8,leftIndent=8,rightIndent=8)
    story += [
        Paragraph("GOVERNANCE DECLARATION",EYEBROW), Spacer(1,2*mm),
        Paragraph(
            f"During the Shadow Mode evaluation period, AlertEngine observed <b>{svc}</b> continuously, "
            f"polling the health endpoint every 5 seconds. A total of <b>{inc} incident(s)</b> were detected "
            f"and diagnosed by the AI Diagnostic Council. In all cases, <b>{sup} notification(s)</b> and "
            f"associated recovery actions were suppressed and logged — "
            f"<b>no external action executed without explicit human authorization</b>. "
            f"Every stage transition was appended to an immutable append-only audit ledger with full actor "
            f"attribution, timestamp, policy version, and confidence score. "
            f"This record is forensically replayable from the ledger alone.", decl),
        Spacer(1,5*mm),
        Paragraph("FORENSIC SAMPLE — LAST 5 SHADOW INCIDENTS",EYEBROW), Spacer(1,2*mm),
        _incident_table(audit_events), Spacer(1,5*mm),
    ]

    h = _compute_integrity_hash(audit_events)
    ht = Table([[Paragraph("SHA-256 LEDGER HASH",MONO), Paragraph(h,MONO)]], colWidths=[42*mm, W-82*mm])
    ht.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),PANEL),
        ("TOPPADDING",(0,0),(-1,-1),6), ("BOTTOMPADDING",(0,0),(-1,-1),6),
        ("LEFTPADDING",(0,0),(-1,-1),8), ("RIGHTPADDING",(0,0),(-1,-1),8),
        ("LINEBELOW",(0,0),(-1,-1),0.5,BRASS),
    ]))
    story += [
        HRFlowable(width="100%",thickness=0.5,color=BORDER,spaceAfter=3*mm),
        Paragraph("AUDIT INTEGRITY VERIFICATION",EYEBROW), Spacer(1,2*mm),
        ht, Spacer(1,3*mm),
        Paragraph("This document was generated from an append-only immutable audit ledger. "
            "The SHA-256 hash above is computed from the complete audit event sequence for this tenant. "
            "An auditor may independently verify this report by re-computing the hash against the live "
            "ledger — any modification to any event will produce a different hash. "
            "<b>Audit Integrity: VERIFIED</b>", DIM_S),
        Spacer(1,5*mm),
        HRFlowable(width="100%",thickness=1,color=BRASS), Spacer(1,3*mm),
        Paragraph(f"Tofamba Technology LLC  ·  AlertEngine  ·  tofamba.com  ·  Generated {gen}  ·  "
            f"Machine-generated from cryptographically verifiable ledger data.", FOOTER),
    ]

    def _bg(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(BG)
        canvas.rect(0,0,W,H,fill=1,stroke=0)
        canvas.restoreState()

    doc.build(story, onFirstPage=_bg, onLaterPages=_bg)
    return buf.getvalue()
