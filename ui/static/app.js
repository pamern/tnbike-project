const steps = new Map();
let lastSubmitAt = 0;
let reconnects = 0;

function qs(selector) {
  return document.querySelector(selector);
}

function addLog(event) {
  const panel = qs("#log-panel");
  if (!panel) return;
  const line = document.createElement("div");
  const level = (event.level || event.status || "info").toLowerCase();
  line.className = `log-line ${level}`;
  const elapsed = Number(event.elapsed_ms || 0).toLocaleString("vi-VN");
  line.textContent = `[${level.toUpperCase()}] step=${event.step || 0} ${elapsed}ms ${event.message || ""}`;
  panel.appendChild(line);
  while (panel.children.length > 200) panel.removeChild(panel.firstChild);
  panel.scrollTop = panel.scrollHeight;
}

function updateStep(event) {
  if (!event.step) return;
  const el = steps.get(Number(event.step));
  if (!el) return;
  el.classList.remove("running", "success", "error");
  if (["running", "success", "error"].includes(event.status)) {
    el.classList.add(event.status);
  }
  const icon = el.querySelector(".step-icon");
  const time = el.querySelector(".step-time");
  if (icon) {
    if (event.status === "running") icon.textContent = "⏳";
    else if (event.status === "success") icon.textContent = "✓";
    else if (event.status === "error") icon.textContent = "✗";
    else icon.textContent = String(event.step);
  }
  if (time) time.textContent = `${Number(event.elapsed_ms || 0).toLocaleString("vi-VN")} ms`;
}

function setText(selector, text) {
  const el = qs(selector);
  if (el) el.textContent = text;
}

function connectWs() {
  if (!qs("#stepper")) return;
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${protocol}://${window.location.host}/ws/pipeline`);
  ws.onopen = () => {
    reconnects = 0;
    setText("#ws-state", "connected");
  };
  ws.onmessage = (message) => {
    const event = JSON.parse(message.data);
    addLog(event);
    updateStep(event);
    if (event.status === "success" && event.report) {
      const link = qs("#latest-report-link");
      if (link) link.href = `/reports?file=${encodeURIComponent(event.report)}`;
      setText("#run-state", "idle");
    }
    if (event.status === "running") setText("#run-state", "running");
    if (event.status === "error") setText("#run-state", "error");
  };
  ws.onclose = () => {
    setText("#ws-state", "disconnected");
    if (reconnects < 5) {
      reconnects += 1;
      setTimeout(connectWs, 2000);
    }
  };
}

async function postForm(url, form) {
  const data = new FormData(form);
  const response = await fetch(url, { method: "POST", body: data });
  const json = await response.json();
  if (!response.ok) throw new Error(json.message || json.detail || "Request failed");
  return json;
}

function bindPipelineForm() {
  const form = qs("#pipeline-form");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const now = Date.now();
    if (now - lastSubmitAt < 3000) return;
    lastSubmitAt = now;
    const submitter = event.submitter;
    const data = new FormData(form);
    data.set("dry_run", submitter?.dataset?.dryRun === "true" ? "true" : "false");
    try {
      await fetch("/api/pipeline/run", { method: "POST", body: data }).then(async (response) => {
        const json = await response.json();
        if (!response.ok) throw new Error(json.message || "Pipeline request failed");
        return json;
      });
      addLog({ step: 0, status: "info", level: "INFO", message: "Pipeline request accepted.", elapsed_ms: 0 });
    } catch (error) {
      addLog({ step: 0, status: "error", level: "ERROR", message: error.message, elapsed_ms: 0 });
    }
  });
}

function bindSettingsForms() {
  const quick = qs("#quick-settings-form");
  if (quick) {
    quick.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await postForm("/api/settings", quick);
        addLog({ step: 0, status: "success", level: "SUCCESS", message: "Session keys saved.", elapsed_ms: 0 });
      } catch (error) {
        addLog({ step: 0, status: "error", level: "ERROR", message: error.message, elapsed_ms: 0 });
      }
    });
  }

  const settings = qs("#settings-form");
  const result = qs("#settings-result");
  if (settings) {
    settings.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await postForm("/api/settings", settings);
        result.textContent = "Đã lưu vào session.";
        result.className = "result wide ok";
      } catch (error) {
        result.textContent = error.message;
        result.className = "result wide err";
      }
    });
  }
}

function bindHealthChecks() {
  const result = qs("#settings-result");
  const dbButton = qs("#test-db");
  const llmButton = qs("#test-llm");
  async function runCheck(url) {
    const response = await fetch(url);
    const json = await response.json();
    if (!response.ok) throw new Error(json.message || "Health check failed");
    return json.message;
  }
  if (dbButton) {
    dbButton.addEventListener("click", async () => {
      try {
        result.textContent = await runCheck("/api/health/db");
        result.className = "result wide ok";
      } catch (error) {
        result.textContent = error.message;
        result.className = "result wide err";
      }
    });
  }
  if (llmButton) {
    llmButton.addEventListener("click", async () => {
      try {
        result.textContent = await runCheck("/api/health/llm");
        result.className = "result wide ok";
      } catch (error) {
        result.textContent = error.message;
        result.className = "result wide err";
      }
    });
  }
}

async function hydrateHealthBadges() {
  const dbBadge = qs("#db-badge");
  if (!dbBadge) return;
  try {
    const response = await fetch("/api/health/db");
    const json = await response.json();
    dbBadge.textContent = json.ok ? "DB: OK" : "DB: error";
    dbBadge.classList.toggle("ok", Boolean(json.ok));
    dbBadge.classList.toggle("err", !json.ok);
  } catch {
    dbBadge.textContent = "DB: error";
    dbBadge.classList.add("err");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".step").forEach((el) => steps.set(Number(el.dataset.step), el));
  connectWs();
  bindPipelineForm();
  bindSettingsForms();
  bindHealthChecks();
  hydrateHealthBadges();
});
