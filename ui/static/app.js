const steps = new Map();
const providerDefaults = {
  groq: "llama-3.3-70b-versatile",
  openai: "gpt-4o-mini",
  anthropic: "claude-3-5-haiku-latest",
  openrouter: "openai/gpt-4o-mini",
};

let lastSubmitAt = 0;
let reconnects = 0;
let activeTourIndex = 0;
let activeTourSteps = [];

const tours = {
  home: [
    {
      selector: '[data-tour="connect"]',
      title: "Kết nối bộ não AI",
      text: "Chọn Groq, OpenAI, Anthropic hoặc OpenRouter. Khóa chỉ được giữ trong phiên hiện tại.",
    },
    {
      selector: '[data-tour="run"]',
      title: "Khởi tạo bản điều hành",
      text: "Xem thử không tiêu tốn quota mô hình. Chạy VIZOR sẽ tạo báo cáo điều hành đầy đủ.",
    },
    {
      selector: '[data-tour="engine"]',
      title: "Theo dõi bộ máy",
      text: "Khu vực này biến xử lý phía sau thành một chuỗi trực quan: dữ liệu, tín hiệu, mô hình, phản biện AI và báo cáo.",
    },
    {
      selector: '[data-tour="timeline"]',
      title: "Nắm từng bước",
      text: "Mỗi điểm dừng cho biết VIZOR đang làm gì bằng ngôn ngữ dễ hiểu.",
    },
    {
      selector: '[data-tour="logs"]',
      title: "Đọc diễn biến",
      text: "Luồng tường thuật giải thích hành động của hệ thống mà không phơi bày nhiễu kỹ thuật.",
    },
  ],
  settings: [
    {
      selector: '[data-tour="connect-hero"]',
      title: "Trung tâm kết nối",
      text: "Trang này là nơi chuẩn bị mô hình AI và nguồn dữ liệu trước khi chạy VIZOR.",
    },
    {
      selector: '[data-tour="provider-choice"]',
      title: "Chọn nhà cung cấp",
      text: "Bạn có thể thử nhiều nhà cung cấp AI khác nhau mà không cần sửa cấu hình trên máy.",
    },
    {
      selector: '[data-tour="provider-key"]',
      title: "Khóa chỉ lưu trong phiên",
      text: "API key được giữ trong session server hiện tại, không ghi vào tệp .env.",
    },
    {
      selector: '[data-tour="provider-model"]',
      title: "Tùy chỉnh mô hình",
      text: "Giữ mặc định để chạy nhanh, hoặc nhập đúng tên model bạn muốn kiểm thử.",
    },
    {
      selector: '[data-tour="data-source"]',
      title: "Nguồn dữ liệu",
      text: "VIZOR dùng PostgreSQL của TNBIKE. Bạn có thể thay thông tin kết nối khi demo ở môi trường khác.",
    },
    {
      selector: '[data-tour="connection-actions"]',
      title: "Kiểm tra trước khi chạy",
      text: "Lưu cấu hình, kiểm tra DB và kiểm tra AI để giảm lỗi khi tạo báo cáo thật.",
    },
  ],
  reports: [
    {
      selector: '[data-tour="reports-hero"]',
      title: "Kho bản điều hành",
      text: "Mỗi lần chạy pipeline, VIZOR tạo một bản báo cáo mới để bạn so sánh và chia sẻ.",
    },
    {
      selector: '[data-tour="report-history"]',
      title: "Lịch sử báo cáo",
      text: "Danh sách bên trái giữ các báo cáo theo thời điểm tạo. Chọn bất kỳ bản nào để xem lại.",
    },
    {
      selector: '[data-tour="report-preview"]',
      title: "Xem báo cáo trực tiếp",
      text: "Khung xem giúp đọc bản HTML đầy đủ mà không rời khỏi sản phẩm.",
    },
    {
      selector: '[data-tour="report-downloads"]',
      title: "Tải xuống",
      text: "Dùng HTML để chia sẻ bản trình bày, hoặc Markdown để chỉnh sửa và đưa vào tài liệu nội bộ.",
    },
  ],
};

function qs(selector) {
  return document.querySelector(selector);
}

function qsa(selector) {
  return Array.from(document.querySelectorAll(selector));
}

function refreshIcons() {
  if (window.lucide) {
    window.lucide.createIcons();
  }
}

function setText(selector, text) {
  const el = qs(selector);
  if (el) el.textContent = text;
}

function addLog(event) {
  const panel = qs("#log-panel");
  if (!panel) return;
  const line = document.createElement("div");
  const status = (event.status || "info").toLowerCase();
  const elapsed = Number(event.elapsed_ms || 0).toLocaleString("vi-VN");
  line.className = `log-line ${status}`;
  line.innerHTML = `<strong>${event.message || "Cập nhật từ VIZOR"}</strong><span>${event.detail || `${elapsed} ms`}</span>`;
  panel.appendChild(line);
  while (panel.children.length > 80) panel.removeChild(panel.firstChild);
  panel.scrollTop = panel.scrollHeight;
}

function updateEngine(event) {
  const consoleEl = qs(".glass-console");
  const title = qs("#engine-title");
  const detail = qs("#engine-detail");
  if (!consoleEl || !title || !detail) return;
  consoleEl.classList.toggle("running", event.status === "running");
  if (event.status === "success") consoleEl.classList.remove("running");
  title.textContent = event.message || "VIZOR đang vận hành.";
  detail.textContent = event.detail || (event.status === "success" ? "Báo cáo mới nhất đã sẵn sàng để mở." : "Các tín hiệu đang đi qua bộ máy phân tích.");
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
    if (event.status === "running") icon.textContent = "•";
    else if (event.status === "success") icon.textContent = "✓";
    else if (event.status === "error") icon.textContent = "!";
    else icon.textContent = String(event.step);
  }
  if (time) {
    const elapsed = Number(event.elapsed_ms || 0).toLocaleString("vi-VN");
    time.textContent = event.status === "running" ? "đang xử lý" : `${elapsed} ms`;
  }
}

function markPreviousStepsComplete(stepNumber) {
  for (const [number, el] of steps.entries()) {
    if (number < stepNumber && !el.classList.contains("success")) {
      el.classList.remove("running", "error");
      el.classList.add("success");
      const icon = el.querySelector(".step-icon");
      const time = el.querySelector(".step-time");
      if (icon) icon.textContent = "✓";
      if (time) time.textContent = "hoàn tất";
    }
  }
}

function connectWs() {
  if (!qs("#stepper")) return;
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${protocol}://${window.location.host}/ws/pipeline`);
  ws.onopen = () => {
    reconnects = 0;
    setText("#ws-state", "đã kết nối");
  };
  ws.onmessage = (message) => {
    const event = JSON.parse(message.data);
    if (event.step) markPreviousStepsComplete(Number(event.step));
    addLog(event);
    updateStep(event);
    updateEngine(event);
    if (event.status === "success" && event.report) {
      const link = qs("#latest-report-link");
      if (link) link.href = `/reports?file=${encodeURIComponent(event.report)}`;
      setText("#run-state", "sẵn sàng");
    }
    if (event.status === "running") setText("#run-state", "đang chạy");
    if (event.status === "error") setText("#run-state", "cần kiểm tra");
  };
  ws.onclose = () => {
    setText("#ws-state", "đang nối lại");
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
  if (!response.ok) throw new Error(json.message || json.detail || "Yêu cầu không thành công");
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
    const data = new FormData(form);
    data.set("dry_run", event.submitter?.dataset?.dryRun === "true" ? "true" : "false");
    try {
      const response = await fetch("/api/pipeline/run", { method: "POST", body: data });
      const json = await response.json();
      if (!response.ok) throw new Error(json.message || "Không thể khởi chạy pipeline");
      addLog({ step: 0, status: "info", message: "VIZOR đã nhận nhiệm vụ.", detail: "Chuỗi tạo bản điều hành đang bắt đầu." });
    } catch (error) {
      addLog({ step: 0, status: "error", message: "VIZOR chưa thể khởi chạy.", detail: error.message });
    }
  });
}

function selectedProvider(form) {
  const radio = form?.querySelector('input[name="llm_provider"]:checked');
  const select = form?.querySelector('select[name="llm_provider"]');
  return radio?.value || select?.value || "groq";
}

function applyProviderDefault(form) {
  if (!form) return;
  const provider = selectedProvider(form);
  const modelInput = form.querySelector('input[name="llm_model"]');
  if (modelInput && !modelInput.value.trim()) {
    modelInput.placeholder = providerDefaults[provider] || "Tự động";
  }
}

function bindSettingsForms() {
  qsa('input[name="llm_provider"], select[name="llm_provider"]').forEach((input) => {
    input.addEventListener("change", () => {
      const form = input.closest("form");
      qsa(".provider-option").forEach((option) => option.classList.toggle("selected", option.contains(input) && input.checked));
      applyProviderDefault(form);
    });
  });

  const quick = qs("#quick-settings-form");
  if (quick) {
    applyProviderDefault(quick);
    quick.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const json = await postForm("/api/settings", quick);
        addLog({ step: 0, status: "success", message: `Đã kết nối ${json.provider}.`, detail: "Nhà cung cấp AI đã sẵn sàng trong phiên này." });
      } catch (error) {
        addLog({ step: 0, status: "error", message: "Kết nối không thành công.", detail: error.message });
      }
    });
  }

  const settings = qs("#settings-form");
  const result = qs("#settings-result");
  if (settings) {
    applyProviderDefault(settings);
    settings.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const json = await postForm("/api/settings", settings);
        result.textContent = `Đã lưu ${json.provider} cho phiên hiện tại.`;
        result.className = "result ok";
      } catch (error) {
        result.textContent = error.message;
        result.className = "result err";
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
    if (!response.ok) throw new Error(json.message || "Kiểm tra kết nối không thành công");
    return json.message;
  }
  if (dbButton) {
    dbButton.addEventListener("click", async () => {
      try {
        result.textContent = await runCheck("/api/health/db");
        result.className = "result ok";
      } catch (error) {
        result.textContent = error.message;
        result.className = "result err";
      }
    });
  }
  if (llmButton) {
    llmButton.addEventListener("click", async () => {
      try {
        result.textContent = await runCheck("/api/health/llm");
        result.className = "result ok";
      } catch (error) {
        result.textContent = error.message;
        result.className = "result err";
      }
    });
  }
}

function showTour(index) {
  const card = qs("[data-tour-card]");
  const backdrop = qs("[data-tour-backdrop]");
  if (!card || !backdrop) return;
  if (!activeTourSteps.length) return;
  qsa(".tour-focus").forEach((el) => el.classList.remove("tour-focus"));
  activeTourIndex = index;
  const step = activeTourSteps[index];
  const target = qs(step.selector);
  if (target) {
    target.classList.add("tour-focus");
    target.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  qs("[data-tour-count]").textContent = `${index + 1}/${activeTourSteps.length}`;
  qs("[data-tour-title]").textContent = step.title;
  qs("[data-tour-text]").textContent = step.text;
  qs("[data-tour-next]").textContent = index === activeTourSteps.length - 1 ? "Hoàn tất" : "Tiếp tục";
  card.hidden = false;
  backdrop.hidden = false;
}

function closeTour() {
  const card = qs("[data-tour-card]");
  const backdrop = qs("[data-tour-backdrop]");
  if (card) card.hidden = true;
  if (backdrop) backdrop.hidden = true;
  qsa(".tour-focus").forEach((el) => el.classList.remove("tour-focus"));
  const page = document.body.dataset.page || "home";
  localStorage.setItem(`vizor-tour-seen-${page}`, "1");
}

function bindTour() {
  const page = document.body.dataset.page || "home";
  activeTourSteps = (tours[page] || tours.home).filter((step) => qs(step.selector));
  qsa("[data-tour-start]").forEach((button) => {
    button.addEventListener("click", () => showTour(0));
  });
  qs("[data-tour-next]")?.addEventListener("click", () => {
    if (activeTourIndex >= activeTourSteps.length - 1) closeTour();
    else showTour(activeTourIndex + 1);
  });
  qs("[data-tour-skip]")?.addEventListener("click", closeTour);
  qs("[data-tour-backdrop]")?.addEventListener("click", closeTour);
  if (activeTourSteps.length && !localStorage.getItem(`vizor-tour-seen-${page}`)) {
    setTimeout(() => showTour(0), 700);
  }
}

async function hydrateHealthBadges() {
  try {
    const response = await fetch("/api/health/db");
    const json = await response.json();
    if (json.ok) addLog({ step: 0, status: "success", message: "Nguồn dữ liệu đã sẵn sàng.", detail: json.message });
  } catch {
    addLog({ step: 0, status: "error", message: "Nguồn dữ liệu chưa sẵn sàng.", detail: "Kiểm tra PostgreSQL trước khi chạy VIZOR." });
  }
}

document.addEventListener("DOMContentLoaded", () => {
  qsa(".step").forEach((el) => steps.set(Number(el.dataset.step), el));
  refreshIcons();
  connectWs();
  bindPipelineForm();
  bindSettingsForms();
  bindHealthChecks();
  bindTour();
  hydrateHealthBadges();
});
