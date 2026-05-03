// onboarding.js
// Handles all step transitions, API calls, and UI state.

const API_BASE = "";  // same origin

// ── State ──────────────────────────────────────────────────────────────────────

const state = {
  step:         1,
  connected:    false,
  alertSent:    false,
  serviceName:  "",
  baseUrl:      "",
  engineers:    [],
  tenantId:     null,
};

// ── Progress indicator ─────────────────────────────────────────────────────────

function updateProgress(step) {
  for (let i = 1; i <= 3; i++) {
    const circle = document.getElementById(`circle-${i}`);
    const label  = document.getElementById(`label-${i}`);
    circle.className = "step-circle";
    label.className  = "step-label";

    if (i < step) {
      circle.className += " done";
      label.className  += " done";
      circle.textContent = "✓";
    } else if (i === step) {
      circle.className += " active";
      label.className  += " active";
      circle.textContent = i;
    } else {
      circle.textContent = i;
    }

    if (i < 3) {
      const line = document.getElementById(`line-${i}`);
      line.className = "step-line" + (i < step ? " done" : "");
    }
  }
}

function goToStep(n) {
  document.querySelectorAll(".step").forEach(el => el.classList.remove("active"));
  const next = document.getElementById(`step-${n}`);
  if (next) {
    next.classList.add("active");
    state.step = n;
    updateProgress(n);
    if (n === 3) populateReview();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
}

// ── Status box ─────────────────────────────────────────────────────────────────

function showStatus(id, type, message) {
  const el = document.getElementById(id);
  el.className   = `status-box ${type}`;
  el.style.display = "block";
  el.innerHTML   = message;
}

function hideStatus(id) {
  document.getElementById(id).style.display = "none";
}

// ── Field validation ───────────────────────────────────────────────────────────

function setLoading(btnId, loading, label = null) {
  const btn = document.getElementById(btnId);
  if (!btn) return;
  btn.disabled   = loading;
  if (loading) {
    btn.innerHTML = `<span class="spinner"></span>${label || "Loading..."}`;
  } else {
    btn.textContent = label || btn.dataset.label || btn.textContent;
  }
}

function validateRequired(fieldId, errId) {
  const val = document.getElementById(fieldId).value.trim();
  if (!val) {
    document.getElementById(fieldId).classList.add("error");
    document.getElementById(errId).style.display = "block";
    return false;
  }
  document.getElementById(fieldId).classList.remove("error");
  document.getElementById(errId).style.display = "none";
  return true;
}

function validateUrl(fieldId, errId) {
  const val = document.getElementById(fieldId).value.trim();
  if (!val || !val.startsWith("http")) {
    document.getElementById(fieldId).classList.add("error");
    document.getElementById(errId).style.display = "block";
    return false;
  }
  document.getElementById(fieldId).classList.remove("error");
  document.getElementById(errId).style.display = "none";
  return true;
}

function validatePhone(val) {
  return /^\+?[0-9]{7,15}$/.test(val.replace(/\s/g, ""));
}

// ── Step 1: Test connection ────────────────────────────────────────────────────

async function testConnection() {
  const nameOk = validateRequired("service-name", "err-service-name");
  const urlOk  = validateUrl("base-url", "err-base-url");
  if (!nameOk || !urlOk) return;

  const baseUrl     = document.getElementById("base-url").value.trim();
  const serviceName = document.getElementById("service-name").value.trim();

  setLoading("btn-test-conn", true, "Testing connection...");
  hideStatus("conn-status");

  try {
    const res  = await fetch(`${API_BASE}/onboarding/test-connection`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ base_url: baseUrl }),
    });
    const data = await res.json();

    if (res.ok && data.status === "connected") {
      const score     = data.health_score ?? "–";
      const scoreInt  = parseInt(score);
      const scoreClass = scoreInt >= 70 ? "health-green" : scoreInt >= 40 ? "health-yellow" : "health-red";

      showStatus("conn-status", "success",
        `✅ <strong>Connected</strong><br>
        <div class="health-badge">
          Health score: <span class="health-score ${scoreClass}">${score}</span>/100
        </div>`
      );

      document.getElementById("base-url").classList.add("valid");
      document.getElementById("btn-next-1").style.display = "block";

      state.connected   = true;
      state.baseUrl     = baseUrl;
      state.serviceName = serviceName;

    } else {
      showStatus("conn-status", "error",
        `❌ <strong>Connection failed</strong><br>${data.detail || data.error || "Could not reach /health/alerts"}`
      );
    }
  } catch (e) {
    showStatus("conn-status", "error",
      `❌ <strong>Request failed</strong><br>Check your URL and try again.`
    );
  } finally {
    setLoading("btn-test-conn", false, "Test Connection");
  }
}

// ── Step 2: Send test alert ────────────────────────────────────────────────────

async function sendTestAlert() {
  const nameOk  = validateRequired("eng1-name",  "err-eng1-name");
  const phoneRaw = document.getElementById("eng1-phone").value.trim();
  let   phoneOk  = true;

  if (!phoneRaw || !validatePhone(phoneRaw)) {
    document.getElementById("eng1-phone").classList.add("error");
    document.getElementById("err-eng1-phone").style.display = "block";
    phoneOk = false;
  } else {
    document.getElementById("eng1-phone").classList.remove("error");
    document.getElementById("err-eng1-phone").style.display = "none";
  }

  if (!nameOk || !phoneOk) return;

  // Collect engineers
  const engineers = [];
  const eng1name  = document.getElementById("eng1-name").value.trim();
  const eng1phone = document.getElementById("eng1-phone").value.trim();
  engineers.push({ name: eng1name, phone: eng1phone });

  const eng2name  = document.getElementById("eng2-name").value.trim();
  const eng2phone = document.getElementById("eng2-phone").value.trim();
  if (eng2name && eng2phone) {
    engineers.push({ name: eng2name, phone: eng2phone });
  }

  const phones = engineers.map(e => e.phone);
  state.engineers = engineers;

  setLoading("btn-test-alert", true, "Sending...");
  hideStatus("alert-status");

  try {
    const res  = await fetch(`${API_BASE}/onboarding/test-alert`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ phone_numbers: phones }),
    });
    const data = await res.json();

    if (res.ok) {
      showStatus("alert-status", "success",
        `📱 <strong>Check your phone</strong><br>A test message was sent to ${phones.join(", ")}`
      );
      document.getElementById("btn-next-2").style.display = "block";
      state.alertSent = true;
    } else {
      showStatus("alert-status", "error",
        `❌ <strong>Send failed</strong><br>${data.detail || "Could not send WhatsApp message"}`
      );
    }
  } catch (e) {
    showStatus("alert-status", "error",
      `❌ <strong>Request failed</strong><br>Please try again.`
    );
  } finally {
    setLoading("btn-test-alert", false, "Send Test Alert");
  }
}

// ── Step 3: Review + Activate ──────────────────────────────────────────────────

function populateReview() {
  const box = document.getElementById("review-box");
  const engineers = state.engineers.map(e =>
    `<div>👤 ${e.name} — ${e.phone}</div>`
  ).join("");

  box.innerHTML = `
    <div style="margin-bottom:8px;">
      <span style="color:#64748b">Service:</span>
      <strong style="color:#e2e8f0;margin-left:8px;">${state.serviceName}</strong>
    </div>
    <div style="margin-bottom:8px;">
      <span style="color:#64748b">Health URL:</span>
      <strong style="color:#e2e8f0;margin-left:8px;">${state.baseUrl}/health/alerts</strong>
    </div>
    <div style="color:#64748b;margin-bottom:4px;">Engineers:</div>
    <div style="color:#e2e8f0;">${engineers}</div>
  `;
}

async function activate() {
  setLoading("btn-activate", true, "Activating...");
  hideStatus("activate-status");

  try {
    const res  = await fetch(`${API_BASE}/onboarding/activate`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({
        service_name: state.serviceName,
        base_url:     state.baseUrl,
        engineers:    state.engineers,
      }),
    });
    const data = await res.json();

    if (res.ok) {
      state.tenantId = data.tenant_id;

      // Final screen
      document.querySelectorAll(".step").forEach(el => el.classList.remove("active"));
      document.getElementById("step-4").classList.add("active");
      updateProgress(4);

      document.getElementById("tenant-id-box").innerHTML =
        `Tenant ID: <span>${data.tenant_id}</span><br>Save this for future reference.`;

    } else {
      showStatus("activate-status", "error",
        `❌ <strong>Activation failed</strong><br>${data.detail || "Please try again"}`
      );
    }
  } catch (e) {
    showStatus("activate-status", "error",
      `❌ <strong>Request failed</strong><br>Please try again.`
    );
  } finally {
    setLoading("btn-activate", false, "Activate Monitoring");
  }
}

// ── Init ───────────────────────────────────────────────────────────────────────

updateProgress(1);

// Allow Enter key on inputs
document.addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  if (state.step === 1) testConnection();
  if (state.step === 2) sendTestAlert();
});
