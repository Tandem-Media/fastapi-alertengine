import { useState, useEffect } from "react";

// ── Design tokens ──────────────────────────────────────────────────────────────
const T = {
  ink:      "#0C1A27",
  ink2:     "#162535",
  ink3:     "#1E3145",
  paper:    "#E8E1D5",
  dim:      "#A89E90",
  brass:    "#C99A3E",
  brassDim: "#7A5E22",
  green:    "#4A9970",
  red:      "#C1564A",
  border:   "rgba(232,225,213,0.12)",
  borderBr: "rgba(201,154,62,0.35)",
};

const css = {
  page: {
    minHeight: "100vh",
    background: T.ink,
    color: T.paper,
    fontFamily: "'IBM Plex Sans', system-ui, sans-serif",
    fontSize: 15,
    lineHeight: 1.55,
  },
  mono: { fontFamily: "'IBM Plex Mono', monospace" },
  serif: { fontFamily: "'Fraunces', serif" },
};

// ── Lemon Squeezy checkout links — replace with real URLs once set up ─────────
const CHECKOUT = {
  starter:    "https://tofamba.lemonsqueezy.com/checkout/starter",
  growth:     "https://tofamba.lemonsqueezy.com/checkout/growth",
  team:       "https://tofamba.lemonsqueezy.com/checkout/team",
  compliance: "https://tofamba.lemonsqueezy.com/checkout/compliance",
  contact:    "mailto:tofambatech@outlook.com",
};

// ── Shared components ─────────────────────────────────────────────────────────

function Label({ children }) {
  return (
    <div style={{
      ...css.mono,
      fontSize: 11,
      letterSpacing: "0.12em",
      textTransform: "uppercase",
      color: T.brass,
      marginBottom: 7,
    }}>{children}</div>
  );
}

function Input({ value, onChange, placeholder, type = "text", disabled }) {
  return (
    <input
      type={type}
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      disabled={disabled}
      style={{
        width: "100%",
        background: T.ink,
        border: `1px solid ${T.border}`,
        borderRadius: 3,
        color: T.paper,
        fontFamily: "'IBM Plex Sans', sans-serif",
        fontSize: 14,
        padding: "10px 12px",
        boxSizing: "border-box",
        outline: "none",
        opacity: disabled ? 0.5 : 1,
      }}
    />
  );
}

function Btn({ children, onClick, variant = "primary", disabled, small }) {
  const styles = {
    primary: { background: T.brass, color: T.ink, border: "none" },
    ghost:   { background: "transparent", color: T.paper, border: `1px solid ${T.borderBr}` },
    danger:  { background: "transparent", color: T.red, border: `1px solid rgba(193,86,74,0.4)` },
    green:   { background: T.green, color: "#fff", border: "none" },
  };
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        ...styles[variant],
        fontFamily: "'IBM Plex Sans', sans-serif",
        fontSize: small ? 12 : 14,
        fontWeight: 500,
        padding: small ? "6px 12px" : "11px 20px",
        borderRadius: 3,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
      }}
    >{children}</button>
  );
}

function Panel({ children, style }) {
  return (
    <div style={{
      background: T.ink2,
      border: `1px solid ${T.border}`,
      borderRadius: 3,
      padding: 28,
      marginBottom: 20,
      ...style,
    }}>{children}</div>
  );
}

function Msg({ text, type }) {
  if (!text) return null;
  const colors = {
    error:   { bg: "rgba(193,86,74,0.12)", border: "rgba(193,86,74,0.4)", color: "#F0B4AC" },
    success: { bg: "rgba(74,153,112,0.12)", border: "rgba(74,153,112,0.4)", color: "#A9D8BC" },
    info:    { bg: "rgba(201,154,62,0.10)", border: "rgba(201,154,62,0.3)", color: "#E8C87A" },
  };
  const c = colors[type] || colors.info;
  return (
    <div style={{
      background: c.bg,
      border: `1px solid ${c.border}`,
      color: c.color,
      borderRadius: 3,
      padding: "11px 14px",
      fontSize: 13.5,
      marginBottom: 18,
    }}>{text}</div>
  );
}

function LedgerRow({ label, value }) {
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "140px 1fr",
      gap: "4px 16px",
      padding: "11px 0",
      borderBottom: `1px solid ${T.border}`,
    }}>
      <div style={{ ...css.mono, fontSize: 11, letterSpacing: "0.06em", textTransform: "uppercase", color: T.dim }}>{label}</div>
      <div style={{ ...css.mono, fontSize: 13, wordBreak: "break-all" }}>{value || "—"}</div>
    </div>
  );
}

function Stamp({ live }) {
  return (
    <div style={{ display: "flex", justifyContent: "center", margin: "8px 0 28px" }}>
      <div style={{
        width: 148,
        height: 148,
        borderRadius: "50%",
        border: `3px solid ${live ? T.green : T.brass}`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        textAlign: "center",
        transform: "rotate(-7deg)",
        boxShadow: `inset 0 0 0 6px ${live ? "rgba(74,153,112,0.10)" : "rgba(201,154,62,0.10)"}`,
        position: "relative",
      }}>
        <div style={{
          position: "absolute",
          width: 124, height: 124,
          borderRadius: "50%",
          border: `1px dashed ${live ? "#2E5C42" : T.brassDim}`,
        }} />
        <div style={{
          ...css.serif,
          fontWeight: 600,
          fontSize: 13,
          letterSpacing: "0.04em",
          textTransform: "uppercase",
          color: live ? T.green : T.brass,
          lineHeight: 1.3,
          position: "relative",
        }}>
          {live ? "Live —\nAuthorized" : "Observing\nOnly"}
        </div>
      </div>
    </div>
  );
}

function ReportCell({ num, label }) {
  return (
    <div style={{
      background: T.ink,
      border: `1px solid ${T.border}`,
      borderRadius: 3,
      padding: "14px 16px",
    }}>
      <div style={{ ...css.serif, fontWeight: 600, fontSize: 26, lineHeight: 1 }}>{num}</div>
      <div style={{ ...css.mono, fontSize: 10, letterSpacing: "0.06em", textTransform: "uppercase", color: T.dim, marginTop: 6 }}>{label}</div>
    </div>
  );
}

// ── CUSTOMER ONBOARDING FORM ─────────────────────────────────────────────────

function OnboardForm({ onSuccess }) {
  const [step, setStep] = useState(1); // 1=service, 2=channel, 3=done
  const [service, setService] = useState({ name: "", url: "" });
  const [channel, setChannel] = useState("telegram");
  const [telegram, setTelegram] = useState({ token: "", chatId: "" });
  const [whatsapp, setWhatsapp] = useState({ token: "", phoneId: "", number: "" });
  const [detecting, setDetecting] = useState(false);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState(null);

  const API = "https://enthusiastic-perception-production-a16b.up.railway.app";

  async function detectChatId() {
    if (!telegram.token) { setMsg({ text: "Paste your bot token first.", type: "error" }); return; }
    setDetecting(true);
    setMsg(null);
    try {
      const r = await fetch(`${API}/telegram/detect-chat-id`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ telegram_bot_token: telegram.token }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Detection failed");
      setTelegram(t => ({ ...t, chatId: d.chat_id }));
      setMsg({ text: `Found: ${d.first_name || "chat"} (${d.chat_id})`, type: "success" });
    } catch (e) {
      setMsg({ text: e.message, type: "error" });
    } finally {
      setDetecting(false);
    }
  }

  async function register() {
    setLoading(true);
    setMsg(null);
    try {
      const body = {
        service_name: service.name,
        health_url: service.url,
        notification_channel: channel,
        plan: "solo",
        whatsapp_numbers: channel !== "telegram" ? [whatsapp.number.startsWith("+") ? whatsapp.number : `+${whatsapp.number}`] : [],
        telegram_bot_token: channel === "telegram" ? telegram.token : undefined,
        telegram_chat_id: channel === "telegram" ? telegram.chatId : undefined,
        meta_access_token: channel === "whatsapp_meta" ? whatsapp.token : undefined,
        meta_phone_number_id: channel === "whatsapp_meta" ? whatsapp.phoneId : undefined,
      };
      const r = await fetch(`${API}/onboard`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Registration failed");
      onSuccess(d);
    } catch (e) {
      setMsg({ text: e.message, type: "error" });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ maxWidth: 560, margin: "0 auto", padding: "48px 24px" }}>
      <div style={{ ...css.mono, fontSize: 11, letterSpacing: "0.16em", color: T.brass, textTransform: "uppercase", marginBottom: 4 }}>AlertEngine</div>
      <h1 style={{ ...css.serif, fontWeight: 600, fontSize: 34, margin: "0 0 6px", letterSpacing: "-0.01em" }}>Connect your service</h1>
      <p style={{ color: T.dim, margin: "0 0 32px", maxWidth: "44ch" }}>
        AlertEngine watches your API and detects incidents — Claude diagnoses them, but nothing happens without your approval.
      </p>

      {/* Step indicator */}
      <div style={{ display: "flex", gap: 8, marginBottom: 28, alignItems: "center" }}>
        {[1, 2].map(s => (
          <div key={s} style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{
              width: 24, height: 24,
              borderRadius: "50%",
              background: step > s ? T.green : step === s ? T.brass : "transparent",
              border: `2px solid ${step >= s ? (step > s ? T.green : T.brass) : T.border}`,
              display: "flex", alignItems: "center", justifyContent: "center",
              ...css.mono, fontSize: 11, color: step >= s ? T.ink : T.dim,
              fontWeight: 600,
            }}>{step > s ? "✓" : s}</div>
            <span style={{ ...css.mono, fontSize: 11, color: step === s ? T.paper : T.dim, textTransform: "uppercase", letterSpacing: "0.08em" }}>
              {s === 1 ? "Your service" : "Notifications"}
            </span>
            {s < 2 && <div style={{ width: 24, height: 1, background: T.border }} />}
          </div>
        ))}
      </div>

      <Msg text={msg?.text} type={msg?.type} />

      {step === 1 && (
        <Panel>
          <div style={{ ...css.serif, fontWeight: 600, fontSize: 18, marginBottom: 6 }}>Your service</div>
          <p style={{ color: T.dim, fontSize: 13.5, margin: "0 0 22px" }}>AlertEngine polls your health endpoint every 5 seconds.</p>
          <div style={{ marginBottom: 18 }}>
            <Label>Service name</Label>
            <Input value={service.name} onChange={v => setService(s => ({ ...s, name: v }))} placeholder="e.g. MutemoOS, PayFlow API" />
          </div>
          <div style={{ marginBottom: 24 }}>
            <Label>Health check URL</Label>
            <Input value={service.url} onChange={v => setService(s => ({ ...s, url: v }))} placeholder="https://yourapp.railway.app/health/alerts" />
            <div style={{ fontSize: 12, color: T.dim, marginTop: 7 }}>
              Add the AlertEngine SDK to your FastAPI app: <span style={{ ...css.mono, color: T.brass }}>pip install fastapi-alertengine</span>
            </div>
          </div>
          <Btn onClick={() => {
            if (!service.name || !service.url) { setMsg({ text: "Service name and URL are both required.", type: "error" }); return; }
            setMsg(null);
            setStep(2);
          }}>Continue →</Btn>
        </Panel>
      )}

      {step === 2 && (
        <Panel>
          <div style={{ ...css.serif, fontWeight: 600, fontSize: 18, marginBottom: 6 }}>How to reach you</div>
          <p style={{ color: T.dim, fontSize: 13.5, margin: "0 0 20px" }}>When AlertEngine detects an incident, it needs to contact you for authorization.</p>

          {/* Channel selector */}
          <div style={{ display: "flex", gap: 10, marginBottom: 24 }}>
            {[
              { id: "telegram", label: "Telegram" },
              { id: "whatsapp_meta", label: "WhatsApp" },
            ].map(c => (
              <button key={c.id} onClick={() => setChannel(c.id)} style={{
                flex: 1,
                padding: "10px 0",
                borderRadius: 3,
                border: `2px solid ${channel === c.id ? T.brass : T.border}`,
                background: channel === c.id ? "rgba(201,154,62,0.08)" : "transparent",
                color: channel === c.id ? T.brass : T.dim,
                fontFamily: "'IBM Plex Sans', sans-serif",
                fontSize: 14,
                fontWeight: channel === c.id ? 600 : 400,
                cursor: "pointer",
              }}>{c.label}</button>
            ))}
          </div>

          {channel === "telegram" && (
            <>
              <div style={{ marginBottom: 16 }}>
                <Label>Bot token</Label>
                <Input value={telegram.token} onChange={v => setTelegram(t => ({ ...t, token: v }))} placeholder="123456789:ABCdef..." />
                <div style={{ fontSize: 12, color: T.dim, marginTop: 6 }}>Message @BotFather in Telegram → /newbot → copy the token</div>
              </div>
              <div style={{ marginBottom: 24 }}>
                <Label>Chat ID</Label>
                <div style={{ display: "flex", gap: 10 }}>
                  <div style={{ flex: 1 }}>
                    <Input value={telegram.chatId} onChange={v => setTelegram(t => ({ ...t, chatId: v }))} placeholder="Auto-filled by Detect" />
                  </div>
                  <Btn variant="ghost" onClick={detectChatId} disabled={detecting}>{detecting ? "Detecting…" : "Detect"}</Btn>
                </div>
                <div style={{ fontSize: 12, color: T.dim, marginTop: 6 }}>Open your bot in Telegram, send it any message, then click Detect</div>
              </div>
            </>
          )}

          {channel === "whatsapp_meta" && (
            <>
              <div style={{ marginBottom: 16 }}>
                <Label>Meta access token</Label>
                <Input value={whatsapp.token} onChange={v => setWhatsapp(w => ({ ...w, token: v }))} placeholder="EAAYLPxVD..." />
                <div style={{ fontSize: 12, color: T.dim, marginTop: 6 }}>From developers.facebook.com → your app → WhatsApp → API Setup → Generate access token</div>
              </div>
              <div style={{ marginBottom: 16 }}>
                <Label>Phone number ID</Label>
                <Input value={whatsapp.phoneId} onChange={v => setWhatsapp(w => ({ ...w, phoneId: v }))} placeholder="1080376445168853" />
              </div>
              <div style={{ marginBottom: 24 }}>
                <Label>Your WhatsApp number</Label>
                <Input value={whatsapp.number} onChange={v => setWhatsapp(w => ({ ...w, number: v }))} placeholder="+263785023897" />
                <div style={{ fontSize: 12, color: T.dim, marginTop: 6 }}>Must be verified in Meta's API Setup page first</div>
              </div>
            </>
          )}

          <div style={{ display: "flex", gap: 10 }}>
            <Btn variant="ghost" onClick={() => { setMsg(null); setStep(1); }}>← Back</Btn>
            <Btn onClick={register} disabled={loading}>{loading ? "Connecting…" : "Connect service"}</Btn>
          </div>
        </Panel>
      )}
    </div>
  );
}

// ── PAYWALL ───────────────────────────────────────────────────────────────────

function Paywall({ report, daysUsed, onContact }) {
  const PLANS = [
    {
      id: "starter",
      name: "Starter",
      price: "$19",
      period: "/mo",
      tag: "First production app",
      features: [
        "1 service monitored",
        "5 incidents / month",
        "Telegram alerts",
        "AI diagnosis",
        "Human-authorized recovery",
        "Immutable audit trail",
      ],
      cta: "Start for $19",
      href: CHECKOUT.starter,
      highlight: false,
    },
    {
      id: "growth",
      name: "Growth",
      price: "$99",
      period: "/mo",
      tag: "Most chosen",
      features: [
        "1 service monitored",
        "10 incidents / month",
        "WhatsApp + Telegram",
        "AI diagnosis + Diff-in-Pocket",
        "Human-authorized recovery",
        "Immutable audit trail",
      ],
      cta: "Go live on Growth",
      href: CHECKOUT.growth,
      highlight: true,
    },
    {
      id: "team",
      name: "Team",
      price: "$299",
      period: "/mo",
      tag: "Multi-service",
      features: [
        "3 services monitored",
        "50 incidents / month",
        "WhatsApp + Telegram",
        "Diagnostic Council (dual-model)",
        "Dissent alerts",
        "Immutable audit trail",
      ],
      cta: "Go live on Team",
      href: CHECKOUT.team,
      highlight: false,
    },
  ];

  const daysLeft = Math.max(0, 30 - (daysUsed || 0));
  const expired = daysLeft === 0;

  return (
    <div style={{ maxWidth: 740, margin: "0 auto", padding: "48px 24px" }}>

      {/* Header */}
      <div style={{ ...css.mono, fontSize: 11, letterSpacing: "0.16em", color: T.brass, textTransform: "uppercase", marginBottom: 4 }}>AlertEngine</div>
      <h1 style={{ ...css.serif, fontWeight: 600, fontSize: 32, margin: "4px 0 8px", letterSpacing: "-0.01em" }}>
        {expired ? "Your evaluation has ended." : `${daysLeft} days left in Shadow Mode.`}
      </h1>
      <p style={{ color: T.dim, margin: "0 0 32px", maxWidth: "52ch", fontSize: 15 }}>
        {expired
          ? "Your 30-day Shadow Mode evaluation is complete. Choose a plan to enable human-authorized recovery and go live."
          : "Shadow Mode ends in " + daysLeft + " days. Here's what AlertEngine observed during your evaluation — everything it would have done with your approval."}
      </p>

      {/* Governance report summary — the proof */}
      {report && (
        <div style={{
          background: T.ink2,
          border: `1px solid ${T.borderBr}`,
          borderRadius: 3,
          padding: "20px 24px",
          marginBottom: 32,
        }}>
          <div style={{ ...css.mono, fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase", color: T.brass, marginBottom: 14 }}>
            Your governance report
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 16 }}>
            {[
              { num: report.incidents_observed, label: "Incidents observed" },
              { num: report.suppressed_notifications, label: "Alerts suppressed" },
              { num: report.suppressed_tokens, label: "Tokens suppressed" },
              { num: report.suppressed_escalations, label: "Escalations suppressed" },
            ].map(c => (
              <div key={c.label}>
                <div style={{ ...css.serif, fontWeight: 600, fontSize: 28, lineHeight: 1, color: T.paper }}>{c.num}</div>
                <div style={{ ...css.mono, fontSize: 10, letterSpacing: "0.06em", textTransform: "uppercase", color: T.dim, marginTop: 5 }}>{c.label}</div>
              </div>
            ))}
          </div>
          <p style={{ fontSize: 13, color: T.dim, borderLeft: `2px solid ${T.brassDim}`, paddingLeft: 12, margin: 0 }}>
            {report.incidents_observed > 0
              ? `AlertEngine detected ${report.incidents_observed} incident${report.incidents_observed !== 1 ? "s" : ""} and suppressed ${report.suppressed_notifications} notification${report.suppressed_notifications !== 1 ? "s" : ""} during your evaluation. Every action was logged to the immutable audit trail.`
              : "No incidents detected during your evaluation — your service was healthy throughout. AlertEngine was watching."}
          </p>
        </div>
      )}

      {/* Plan cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 14, marginBottom: 28 }}>
        {PLANS.map(plan => (
          <div key={plan.id} style={{
            background: plan.highlight ? T.ink3 : T.ink2,
            border: `2px solid ${plan.highlight ? T.brass : T.border}`,
            borderRadius: 3,
            padding: "22px 20px",
            display: "flex",
            flexDirection: "column",
            position: "relative",
          }}>
            {plan.highlight && (
              <div style={{
                position: "absolute",
                top: -11,
                left: "50%",
                transform: "translateX(-50%)",
                background: T.brass,
                color: T.ink,
                ...css.mono,
                fontSize: 10,
                letterSpacing: "0.1em",
                textTransform: "uppercase",
                fontWeight: 600,
                padding: "3px 10px",
                borderRadius: 2,
                whiteSpace: "nowrap",
              }}>{plan.tag}</div>
            )}

            <div style={{ marginBottom: 4 }}>
              <span style={{ ...css.serif, fontWeight: 600, fontSize: 18 }}>{plan.name}</span>
              {!plan.highlight && (
                <div style={{ ...css.mono, fontSize: 10, color: T.dim, marginTop: 2, textTransform: "uppercase", letterSpacing: "0.06em" }}>{plan.tag}</div>
              )}
            </div>

            <div style={{ marginBottom: 18 }}>
              <span style={{ ...css.serif, fontWeight: 600, fontSize: 30 }}>{plan.price}</span>
              <span style={{ ...css.mono, fontSize: 12, color: T.dim }}>{plan.period}</span>
            </div>

            <div style={{ flex: 1, marginBottom: 20 }}>
              {plan.features.map(f => (
                <div key={f} style={{ display: "flex", gap: 8, alignItems: "flex-start", marginBottom: 8 }}>
                  <span style={{ color: T.green, fontSize: 13, lineHeight: "20px", flexShrink: 0 }}>✓</span>
                  <span style={{ fontSize: 13, color: T.dim, lineHeight: "20px" }}>{f}</span>
                </div>
              ))}
            </div>

            <a
              href={plan.href}
              target="_blank"
              rel="noreferrer"
              style={{
                display: "block",
                textAlign: "center",
                padding: "11px 16px",
                borderRadius: 3,
                background: plan.highlight ? T.brass : "transparent",
                border: `1px solid ${plan.highlight ? T.brass : T.borderBr}`,
                color: plan.highlight ? T.ink : T.paper,
                fontFamily: "'IBM Plex Sans', sans-serif",
                fontSize: 14,
                fontWeight: 600,
                textDecoration: "none",
              }}
            >{plan.cta}</a>
          </div>
        ))}
      </div>

      {/* Higher tiers + contact */}
      <div style={{
        background: T.ink2,
        border: `1px solid ${T.border}`,
        borderRadius: 3,
        padding: "18px 24px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        flexWrap: "wrap",
        gap: 12,
      }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 14 }}>Compliance · Platform · Enterprise</div>
          <div style={{ fontSize: 13, color: T.dim, marginTop: 3 }}>
            $799/mo · $1,500/mo · Custom — Slack, DLQ, voice escalation, dedicated deployment
          </div>
        </div>
        <a
          href={CHECKOUT.contact}
          style={{
            padding: "10px 18px",
            borderRadius: 3,
            border: `1px solid ${T.borderBr}`,
            color: T.paper,
            fontFamily: "'IBM Plex Sans', sans-serif",
            fontSize: 13,
            textDecoration: "none",
            whiteSpace: "nowrap",
          }}
        >Contact Tofamba →</a>
      </div>

      <p style={{ fontSize: 12, color: T.dim, marginTop: 20, textAlign: "center" }}>
        Questions? WhatsApp or email <a href={CHECKOUT.contact} style={{ color: T.brass }}>tofambatech@outlook.com</a>
      </p>
    </div>
  );
}

// ── CUSTOMER DASHBOARD ────────────────────────────────────────────────────────

function CustomerDashboard({ tenantId, secret, onForget }) {
  const [tenant, setTenant] = useState(null);
  const [report, setReport] = useState(null);
  const [msg, setMsg] = useState(null);
  const [testLoading, setTestLoading] = useState(false);
  const [goLiveLoading, setGoLiveLoading] = useState(false);

  const API = "https://enthusiastic-perception-production-a16b.up.railway.app";

  async function load() {
    try {
      const [tr, rr] = await Promise.all([
        fetch(`${API}/tenant/${tenantId}`).then(r => r.json()),
        fetch(`${API}/tenant/${tenantId}/shadow/report`, {
          headers: { "x-tenant-secret": secret },
        }).then(r => r.json()),
      ]);
      setTenant(tr);
      setReport(rr);
    } catch (e) {
      setMsg({ text: "Could not load dashboard.", type: "error" });
    }
  }

  useEffect(() => { load(); }, []);

  async function fireTest() {
    setTestLoading(true);
    await fetch(`${API}/tenant/${tenantId}/test`, { method: "POST" });
    setTimeout(() => { load(); setTestLoading(false); }, 800);
  }

  async function goLive() {
    if (!confirm("Going live means real notifications fire. Continue?")) return;
    setGoLiveLoading(true);
    await fetch(`${API}/tenant/${tenantId}/shadow`, {
      method: "DELETE",
      headers: { "x-tenant-secret": secret },
    });
    await load();
    setGoLiveLoading(false);
  }

  const live = tenant && !tenant.shadow_mode;

  // Calculate days in shadow mode — show paywall at 30 days
  const daysUsed = tenant?.shadow_enabled_at
    ? Math.floor((Date.now() / 1000 - tenant.shadow_enabled_at) / 86400)
    : 0;
  const trialExpired = !live && daysUsed >= 30;

  // Show paywall if trial expired
  if (trialExpired && report) {
    return <Paywall report={report} daysUsed={daysUsed} />;
  }

  return (
    <div style={{ maxWidth: 580, margin: "0 auto", padding: "48px 24px" }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 4 }}>
        <span style={{ ...css.mono, fontSize: 11, letterSpacing: "0.14em", color: T.brass, textTransform: "uppercase" }}>AlertEngine</span>
        <span style={{ ...css.mono, fontSize: 11, color: T.dim }}>{tenantId}</span>
      </div>
      <h1 style={{ ...css.serif, fontWeight: 600, fontSize: 30, margin: "4px 0 4px", letterSpacing: "-0.01em" }}>
        {tenant?.service_name || "Your service"}
      </h1>
      <div style={{ height: 1, background: T.border, margin: "20px 0 28px" }} />

      <Msg text={msg?.text} type={msg?.type} />

      {/* Trial countdown banner — shown when in shadow mode with days remaining */}
      {!live && daysUsed > 0 && daysUsed < 30 && (
        <div style={{
          background: "rgba(201,154,62,0.08)",
          border: `1px solid ${T.borderBr}`,
          borderRadius: 3,
          padding: "12px 16px",
          marginBottom: 20,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 8,
        }}>
          <span style={{ fontSize: 13.5, color: T.paper }}>
            <span style={{ ...css.mono, color: T.brass, fontWeight: 600 }}>{30 - daysUsed} days</span> remaining in Shadow Mode evaluation
          </span>
          <a href={CHECKOUT.growth} target="_blank" rel="noreferrer" style={{
            fontSize: 12,
            color: T.brass,
            textDecoration: "underline",
            fontFamily: "'IBM Plex Sans', sans-serif",
          }}>Upgrade early →</a>
        </div>
      )}

      {tenant && <Stamp live={live} />}

      {tenant && (
        <Panel>
          <div style={{ ...css.serif, fontWeight: 600, fontSize: 17, marginBottom: 16 }}>Connection</div>
          <LedgerRow label="Status" value={live ? "Live — authorized" : "Shadow mode — observing"} />
          <LedgerRow label="Service" value={tenant.service_name} />
          <LedgerRow label="Channel" value={tenant.notification_channel} />
          <LedgerRow label="Health URL" value={tenant.health_url} />
        </Panel>
      )}

      {report && (
        <Panel>
          <div style={{ ...css.serif, fontWeight: 600, fontSize: 17, marginBottom: 16 }}>Evaluation report</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 18 }}>
            <ReportCell num={report.incidents_observed} label="Incidents observed" />
            <ReportCell num={report.suppressed_notifications} label="Notifications suppressed" />
            <ReportCell num={report.suppressed_tokens} label="Tokens suppressed" />
            <ReportCell num={report.suppressed_escalations} label="Escalations suppressed" />
          </div>
          <p style={{ fontSize: 13, color: T.dim, borderLeft: `2px solid ${T.brassDim}`, paddingLeft: 12, margin: 0 }}>
            {report.summary}
          </p>
        </Panel>
      )}

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <Btn variant="ghost" onClick={fireTest} disabled={testLoading}>{testLoading ? "Firing…" : "Send test incident"}</Btn>
        <Btn variant="ghost" onClick={load}>Refresh</Btn>
        {!live && <Btn variant="green" onClick={goLive} disabled={goLiveLoading}>{goLiveLoading ? "Going live…" : "Go live"}</Btn>}
        <Btn variant="danger" onClick={onForget}>Forget this device</Btn>
      </div>
    </div>
  );
}

// ── PROVIDER DASHBOARD ────────────────────────────────────────────────────────

function ProviderDashboard() {
  const [tenants, setTenants] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(null);
  const [adminKey, setAdminKey] = useState(() => {
    try { return localStorage.getItem("ae_admin_key") || ""; } catch { return ""; }
  });
  const [keySet, setKeySet] = useState(false);
  const [error, setError] = useState(null);

  const API = "https://enthusiastic-perception-production-a16b.up.railway.app";

  async function loadTenants() {
    if (!adminKey) return;
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`${API}/admin/tenants?admin_key=${encodeURIComponent(adminKey)}`);
      if (r.status === 403) { setError("Admin key incorrect."); setLoading(false); return; }
      const d = await r.json();
      const list = Array.isArray(d) ? d : (d.tenants || []);
      setTenants(list);
      setKeySet(true);
      try { localStorage.setItem("ae_admin_key", adminKey); } catch {}
    } catch (e) {
      setError("Could not reach orchestrator.");
    } finally {
      setLoading(false);
    }
  }

  const total = tenants.length;
  const live = tenants.filter(t => !t.shadow_mode).length;
  const shadow = tenants.filter(t => t.shadow_mode).length;

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: "48px 24px" }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
        <span style={{ ...css.mono, fontSize: 11, letterSpacing: "0.16em", color: T.brass, textTransform: "uppercase" }}>Tofamba · AlertEngine</span>
        {keySet && <Btn variant="ghost" onClick={loadTenants} small>↻ Refresh</Btn>}
      </div>
      <h1 style={{ ...css.serif, fontWeight: 600, fontSize: 30, margin: "4px 0 28px", letterSpacing: "-0.01em" }}>Provider console</h1>

      {/* Admin key gate */}
      {!keySet && (
        <Panel style={{ maxWidth: 420 }}>
          <div style={{ ...css.serif, fontWeight: 600, fontSize: 17, marginBottom: 6 }}>Admin access</div>
          <p style={{ color: T.dim, fontSize: 13.5, margin: "0 0 18px" }}>Enter your ADMIN_KEY from the Railway environment variables.</p>
          {error && <Msg text={error} type="error" />}
          <div style={{ marginBottom: 16 }}>
            <Label>Admin key</Label>
            <Input value={adminKey} onChange={setAdminKey} placeholder="Your ADMIN_KEY value" type="password" />
          </div>
          <Btn onClick={loadTenants} disabled={loading || !adminKey}>{loading ? "Checking…" : "Access console"}</Btn>
        </Panel>
      )}

      {keySet && (
        <>
          {error && <Msg text={error} type="error" />}

          {/* Summary row */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 14, marginBottom: 28 }}>
            {[
              { num: total, label: "Total tenants" },
              { num: live, label: "Live" },
              { num: shadow, label: "In shadow mode" },
            ].map(c => (
              <div key={c.label} style={{
                background: T.ink2,
                border: `1px solid ${T.border}`,
                borderRadius: 3,
                padding: "18px 20px",
              }}>
                <div style={{ ...css.serif, fontWeight: 600, fontSize: 32, lineHeight: 1 }}>{c.num}</div>
                <div style={{ ...css.mono, fontSize: 10.5, letterSpacing: "0.06em", textTransform: "uppercase", color: T.dim, marginTop: 6 }}>{c.label}</div>
              </div>
            ))}
          </div>

          {/* Tenant list */}
          <div style={{ display: "grid", gridTemplateColumns: selected ? "1fr 1fr" : "1fr", gap: 16, alignItems: "start" }}>
            <div>
              <div style={{ ...css.mono, fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase", color: T.dim, marginBottom: 12 }}>Connected services</div>
              {loading && <div style={{ color: T.dim, fontSize: 13 }}>Loading…</div>}
              {!loading && !tenants.length && (
                <div style={{ color: T.dim, fontSize: 13, padding: "24px 0" }}>No tenants yet. Share the onboarding form to get your first connection.</div>
              )}
              {tenants.map(t => (
                <div key={t.tenant_id} onClick={() => setSelected(t.tenant_id === selected ? null : t.tenant_id)} style={{
                  background: selected === t.tenant_id ? T.ink3 : T.ink2,
                  border: `1px solid ${selected === t.tenant_id ? T.borderBr : T.border}`,
                  borderRadius: 3,
                  padding: "14px 16px",
                  marginBottom: 10,
                  cursor: "pointer",
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                    <div>
                      <div style={{ fontWeight: 600, fontSize: 14 }}>{t.service_name || t.tenant_id}</div>
                      <div style={{ ...css.mono, fontSize: 11, color: T.dim, marginTop: 3 }}>{t.tenant_id}</div>
                    </div>
                    <div style={{
                      ...css.mono,
                      fontSize: 10,
                      letterSpacing: "0.08em",
                      textTransform: "uppercase",
                      padding: "3px 8px",
                      borderRadius: 2,
                      background: t.shadow_mode ? "rgba(201,154,62,0.12)" : "rgba(74,153,112,0.12)",
                      color: t.shadow_mode ? T.brass : T.green,
                      border: `1px solid ${t.shadow_mode ? "rgba(201,154,62,0.25)" : "rgba(74,153,112,0.25)"}`,
                    }}>{t.shadow_mode ? "Shadow" : "Live"}</div>
                  </div>
                  <div style={{ marginTop: 8, display: "flex", gap: 12 }}>
                    <span style={{ fontSize: 12, color: T.dim }}>{t.notification_channel}</span>
                    <span style={{ fontSize: 12, color: T.dim }}>·</span>
                    <span style={{ fontSize: 12, color: T.dim }}>{t.plan || "solo"}</span>
                    <span style={{ fontSize: 12, color: T.dim }}>·</span>
                    <span style={{ fontSize: 12, color: t.status === "active" ? T.green : T.brass }}>{t.status}</span>
                  </div>
                </div>
              ))}
            </div>

            {/* Tenant detail panel */}
            {selected && (() => {
              const t = tenants.find(x => x.tenant_id === selected);
              if (!t) return null;
              return (
                <div>
                  <div style={{ ...css.mono, fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase", color: T.dim, marginBottom: 12 }}>Detail</div>
                  <Panel>
                    <div style={{ ...css.serif, fontWeight: 600, fontSize: 17, marginBottom: 16 }}>{t.service_name}</div>
                    <LedgerRow label="Tenant ID" value={t.tenant_id} />
                    <LedgerRow label="Status" value={t.status} />
                    <LedgerRow label="Mode" value={t.shadow_mode ? "Shadow" : "Live"} />
                    <LedgerRow label="Channel" value={t.notification_channel} />
                    <LedgerRow label="Plan" value={t.plan} />
                    <LedgerRow label="Health URL" value={t.health_url} />
                    {t.shadow_enabled_at && <LedgerRow label="Shadow since" value={new Date(t.shadow_enabled_at * 1000).toLocaleDateString()} />}
                    {t.shadow_disabled_at && <LedgerRow label="Went live" value={new Date(t.shadow_disabled_at * 1000).toLocaleDateString()} />}
                    <div style={{ marginTop: 16 }}>
                      <a href={`${API}/audit/${t.tenant_id}/report`} target="_blank" rel="noreferrer">
                        <Btn variant="ghost" small>View audit report ↗</Btn>
                      </a>
                    </div>
                  </Panel>
                </div>
              );
            })()}
          </div>
        </>
      )}
    </div>
  );
}

// ── SECRET REVEAL ─────────────────────────────────────────────────────────────

function SecretReveal({ data, onContinue }) {
  const [copied, setCopied] = useState(false);

  function copy() {
    navigator.clipboard.writeText(data.tenant_secret);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div style={{ maxWidth: 560, margin: "0 auto", padding: "48px 24px" }}>
      <div style={{ ...css.mono, fontSize: 11, letterSpacing: "0.16em", color: T.brass, textTransform: "uppercase", marginBottom: 4 }}>AlertEngine</div>
      <h1 style={{ ...css.serif, fontWeight: 600, fontSize: 30, margin: "4px 0 18px" }}>Service connected</h1>
      <Panel>
        <div style={{ fontSize: 13.5, color: T.dim, marginBottom: 16 }}>
          Your tenant secret is shown once only. Save it before continuing — there's no way to retrieve it again.
        </div>
        <div style={{ background: T.ink, border: `1px dashed ${T.borderBr}`, borderRadius: 3, padding: 16, marginBottom: 16 }}>
          <Label>Tenant ID</Label>
          <div style={{ ...css.mono, fontSize: 13, marginBottom: 14 }}>{data.tenant_id}</div>
          <Label>Tenant secret</Label>
          <div style={{ ...css.mono, fontSize: 12, wordBreak: "break-all", color: T.paper, marginBottom: 12 }}>{data.tenant_secret}</div>
          <Btn variant="ghost" onClick={copy} small>{copied ? "Copied ✓" : "Copy secret"}</Btn>
        </div>
        <div style={{ fontSize: 13, color: "#F0C16B", marginBottom: 20 }}>
          ⚠ AlertEngine has started watching your service in Shadow Mode. Every incident is detected but nothing is sent until you go live.
        </div>
        <Btn onClick={onContinue}>Continue to dashboard →</Btn>
      </Panel>
    </div>
  );
}

// ── ROOT ──────────────────────────────────────────────────────────────────────

const STORE_KEY = "ae_tenant_v2";

function load() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY)); } catch { return null; }
}
function save(d) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(d)); } catch {}
}

export default function App() {
  const [view, setView] = useState("loading"); // loading | onboard | secret | customer | provider
  const [registered, setRegistered] = useState(null);
  const [stored, setStored] = useState(null);
  const [tab, setTab] = useState("customer"); // customer | provider (top-level toggle)

  useEffect(() => {
    const s = load();
    if (s) { setStored(s); setView("customer"); }
    else { setView("onboard"); }
  }, []);

  function handleSuccess(data) {
    setRegistered(data);
    setView("secret");
  }

  function handleContinue() {
    save({ tenant_id: registered.tenant_id, secret: registered.tenant_secret });
    setStored({ tenant_id: registered.tenant_id, secret: registered.tenant_secret });
    setView("customer");
  }

  function handleForget() {
    if (!confirm("Forget this tenant on this device? You'll need the tenant ID and secret to reconnect.")) return;
    localStorage.removeItem(STORE_KEY);
    setStored(null);
    setRegistered(null);
    setView("onboard");
  }

  return (
    <div style={css.page}>
      {/* Top nav — only show when not mid-onboarding */}
      {(view === "customer" || view === "provider") && (
        <div style={{
          borderBottom: `1px solid ${T.border}`,
          display: "flex",
          justifyContent: "center",
          gap: 0,
        }}>
          {[
            { id: "customer", label: "My service" },
            { id: "provider", label: "Provider console" },
          ].map(t => (
            <button key={t.id} onClick={() => { setView(t.id); setTab(t.id); }} style={{
              background: "none",
              border: "none",
              borderBottom: view === t.id ? `2px solid ${T.brass}` : "2px solid transparent",
              color: view === t.id ? T.paper : T.dim,
              fontFamily: "'IBM Plex Sans', sans-serif",
              fontSize: 13,
              padding: "14px 24px",
              cursor: "pointer",
              marginBottom: -1,
            }}>{t.label}</button>
          ))}
        </div>
      )}

      {view === "loading" && (
        <div style={{ textAlign: "center", padding: 80, color: T.dim }}>Loading…</div>
      )}

      {view === "onboard" && <OnboardForm onSuccess={handleSuccess} />}

      {view === "secret" && registered && (
        <SecretReveal data={registered} onContinue={handleContinue} />
      )}

      {view === "customer" && stored && (
        <CustomerDashboard
          tenantId={stored.tenant_id}
          secret={stored.secret}
          onForget={handleForget}
        />
      )}

      {view === "provider" && <ProviderDashboard />}
    </div>
  );
}
