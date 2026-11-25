const $ = (selector, root = document) => root.querySelector(selector);
const conditions = $("#conditions");
const template = $("#conditionTemplate");
let lastPayload = null;
let lastData = null;
let activeMode = "close";
let screenBusy = false;
let updateBusy = false;
let currentAudit = null;
let lastRankingData = null;
let lastRankingPayload = null;
let auditBusy = false;
let auditForceQueued = false;
let rankingBusy = false;
let rankingRefreshTimer = null;
let rankingNextRefreshAt = 0;
let chartState = null;
let chartData = null;
let priceChart = null;
let indicatorChart = null;
let chartRefreshTimer = null;
let chartResizeObserver = null;
let chartRequestBusy = false;
let pendingChartRefresh = false;
let pendingChartPreserveRange = false;
let chartSyncing = false;
let chartCrosshairSyncing = false;
let carouselTimer = null;
let carouselIndex = 0;
let carouselPaused = false;
let carouselItems = [];
let carouselIntervalMs = 15000;
let carouselRankRefreshTimer = null;
let carouselRankStartTimer = null;
let carouselRankBusy = false;
let carouselRankConfig = null;
let carouselStartBusy = false;
const keyLevelBooleanMetrics = new Set([
  "kl_support_exists", "kl_support_htf", "kl_support_retest_pending",
  "kl_resistance_exists", "kl_resistance_htf", "kl_resistance_retest_pending",
  "kl_event_support_touch", "kl_event_support_hammer", "kl_event_resistance_inverted_hammer",
  "kl_event_resistance_touch", "kl_event_support_break",
  "kl_event_resistance_break", "kl_event_support_retest", "kl_event_resistance_retest",
]);
const keyLevelMetrics = new Set([
  "kl_support_exists", "kl_support_top", "kl_support_bottom", "kl_support_score",
  "kl_support_touches", "kl_support_confluence", "kl_support_htf", "kl_support_retest_pending",
  "kl_support_distance_atr", "kl_resistance_exists", "kl_resistance_top", "kl_resistance_bottom",
  "kl_resistance_score", "kl_resistance_touches", "kl_resistance_confluence", "kl_resistance_htf",
  "kl_resistance_retest_pending", "kl_resistance_distance_atr", "kl_event_support_touch", "kl_event_support_hammer",
  "kl_event_resistance_inverted_hammer",
  "kl_event_resistance_touch", "kl_event_support_break", "kl_event_resistance_break",
  "kl_event_support_retest", "kl_event_resistance_retest", "kl_previous_day_high",
  "kl_previous_day_low", "kl_previous_week_high", "kl_previous_week_low", "kl_day_open", "kl_session_vwap",
]);
const keyLevelSignalNames = new Map([
  ["support_exists", "??????"], ["resistance_exists", "??????"],
  ["high_quality_support", "?????"], ["high_quality_resistance", "?????"],
  ["support_hammer_rejection", "???????"],
  ["resistance_inverted_hammer_rejection", "????????"],
  ["support_near", "??????"], ["resistance_near", "??????"],
  ["support_touch", "??????"], ["resistance_touch", "??????"],
  ["support_break", "??????"], ["resistance_break", "??????"],
  ["support_retest", "??????"], ["resistance_retest", "??????"],
  ["price_above_vwap", "?????? VWAP"], ["price_below_vwap", "?????? VWAP"],
  ["price_above_previous_day_high", "????????"], ["price_below_previous_day_low", "????????"],
  ["price_above_previous_week_high", "????????"], ["price_below_previous_week_low", "????????"],
  ["price_above_day_open", "????????"], ["price_below_day_open", "????????"],
]);

function renderAuditEntries() {
  const query = $("#marketAuditSearch").value.trim().toUpperCase();
  const body = $("#marketAuditRows");
  body.replaceChildren();
  const states = { pending: "???", not_listed: "?????", inactive: "????" };
  for (const item of currentAudit?.entries || []) {
    if (query && !item.symbol.toUpperCase().includes(query)) continue;
    const row = document.createElement("tr");
    for (const value of [item.symbol, states[item.status] || item.status, item.reason]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    }
    body.appendChild(row);
  }
}

function renderAudit(data) {
  currentAudit = data;
  const failed = data.status === "error" || data.stale;
  const checked = data.checkedAt ? new Date(data.checkedAt).toLocaleTimeString("zh-CN") : "??????";
  $(".market-audit").classList.toggle("has-error", failed);
  $("#marketAuditStatus").textContent = failed
    ? `??${data.status === "error" ? "???15 ??????" : "???"} ? ???? ${checked} ? ??????`
    : `??? ${data.availableMarkets || 0} ? ? ????? ${data.excludedMarkets || 0} ? ? ??? ${data.pendingMarkets || 0} ? ? ${checked}${data.refreshing ? " ? ????" : ""}`;
  if (failed && data.verifiedSymbols?.length) {
    $("#marketAuditStatus").textContent += " ? ???????????????????";
  }
  $("#marketAuditSummary").textContent = `?????????${data.pendingMarkets ? `?${data.pendingMarkets} ?????` : ""}`;
  $("#marketAuditHint").textContent = data.errors?.length ? data.errors.join("?") : "??? 60 ?????????????? 15 ????????????????????????????";
  const change = data.changes?.at(-1);
  $("#marketAuditChanges").textContent = change ? (change.initial ? `????????? ${change.added.length} ????` : `??????????? ${change.added.length} ?${change.added.length ? `?${change.added.slice(0, 8).join("?")}?` : ""}??????? ${change.removed.length} ?${change.removed.length ? `?${change.removed.slice(0, 8).join("?")}?` : ""}?`) : "???????";
  renderAuditEntries();
}

async function loadMarketAudit(force = false) {
  const button = $("#auditRefresh");
  if (auditBusy) {
    if (force) {
      auditForceQueued = true;
      button.disabled = true;
      button.textContent = "?????";
    }
    return;
  }
  auditBusy = true;
  button.disabled = true;
  if (force) button.textContent = "????";
  try {
    const data = await requestJson("/api/market-audit", { method: force ? "POST" : "GET" }, {
      timeoutMs: force ? 90000 : 15000,
      retries: force ? 0 : 1,
    });
    renderAudit(data);
    if (lastData) renderResults(lastData, activeMode);
    if (lastRankingData) renderRanking(lastRankingData);
  } catch {
    if (currentAudit) renderAudit({ ...currentAudit, status: "error", stale: true, errors: ["?????????????????????"] });
    $("#marketAuditStatus").textContent = "????????????????";
    if (lastData) renderResults(lastData, activeMode);
    if (lastRankingData) renderRanking(lastRankingData);
  } finally {
    auditBusy = false;
    const runQueuedRefresh = auditForceQueued;
    auditForceQueued = false;
    button.disabled = runQueuedRefresh;
    button.textContent = runQueuedRefresh ? "?????" : "????";
    if (runQueuedRefresh) void loadMarketAudit(true);
  }
}

function verifiedRows(items) {
  if (!Array.isArray(items)) return [];
  // Screening and ranking responses are produced from the same verified
  // market directory and carry this decision on each row. Prefer that
  // snapshot so a concurrent audit refresh cannot temporarily hide all rows.
  const hasRowVerification = items.some(item => Object.prototype.hasOwnProperty.call(item || {}, "webTradeStatus"));
  if (hasRowVerification) {
    return items.filter(item => item?.webTradeStatus === "available" && item.webListed === true);
  }
  if (!currentAudit || !Array.isArray(currentAudit.verifiedSymbols)) return items;
  const names = new Set(currentAudit.verifiedSymbols || []);
  return items.filter(item => names.has(item.symbol));
}

function carouselSelection(source = $("#carouselSource")?.value || "screen") {
  const mode = $("#carouselMode")?.value || "close";
  if (source === "ranking") {
    const data = lastRankingData || {};
    return {
      source,
      mode,
      results: verifiedRows(data.results || []),
      timeframe: data.timeframe || $("#rankingTimeframe").value,
      quote: data.quote || $("#quote").value,
      conditions: [],
      ranking: {
        ...(lastRankingPayload || {}),
        quote: lastRankingPayload?.quote || data.quote || $("#quote").value,
        timeframe: lastRankingPayload?.timeframe || data.timeframe || $("#rankingTimeframe").value,
        direction: lastRankingPayload?.direction || data.direction || $("#rankingDirection").value,
        maxSymbols: lastRankingPayload?.maxSymbols || Number($("#maxSymbols").value) || 100,
        symbols: lastRankingPayload?.symbols ?? $("#symbols").value,
        refreshSeconds: Number($("#rankingRefreshSeconds").value) || 0,
        firstRefreshSeconds: rankingNextRefreshAt
          ? Math.max(1, Math.ceil((rankingNextRefreshAt - Date.now()) / 1000))
          : Number($("#rankingRefreshSeconds").value) || 0,
      },
    };
  }
  const data = lastData || {};
  return {
    source: "screen",
    mode,
    results: verifiedRows(mode === "live" ? (data.liveResults || []) : (data.closeResults || data.results || [])),
    timeframe: data.timeframe || $("#timeframe").value,
    quote: $("#quote").value,
    conditions: lastPayload?.conditions || [],
  };
}

function updateCarouselHint() {
  const hint = $("#carouselResultHint");
  if (!hint) return;
  const selection = carouselSelection();
  if (!selection.results.length) {
    hint.textContent = selection.source === "ranking" ? "????????" : "???????";
    return;
  }
  const label = selection.source === "ranking" ? `${selection.timeframe} ??` : (selection.mode === "live" ? "????" : "????");
  hint.textContent = `${selection.results.length} ?${label}?????`;
}

function addCondition(defaults = {}) {
  const row = template.content.firstElementChild.cloneNode(true);
  const indicator = defaults.indicator || "ema";
  $(".condition-indicator", row).value = indicator;
  $(".condition-timeframe", row).value = defaults.timeframe || $("#timeframe").value;
  $(".condition-operator", row).value = defaults.operator || ">";
  $(".condition-value-type", row).value = defaults.valueType || (keyLevelBooleanMetrics.has(indicator) ? "boolean" : "number");
  $(".condition-value", row).value = defaults.value ?? "";
  $(".condition-boolean-value", row).value = String(defaults.booleanValue ?? true);
  $(".condition-period", row).value = defaults.period || 20;
  $(".condition-value-indicator", row).value = defaults.valueIndicator || "ema";
  $(".condition-value-period", row).value = defaults.valuePeriod || 60;
  const signal = defaults.keyLevelSignal || "high_quality_support";
  $(".condition-key-level-signal", row).value = signal;
  $(".condition-key-level-threshold", row).value = defaults.keyLevelThreshold ?? (signal.endsWith("_near") ? 0.5 : 75);
  $(".condition-key-level-lookback", row).value = String(defaults.keyLevelLookbackBars ?? 24);
  row.dataset.keyLevelSignal = signal;
  row.addEventListener("input", updateConditionRow);
  row.addEventListener("change", updateConditionRow);
  $(".remove-condition", row).addEventListener("click", () => { row.remove(); renumber(); });
  conditions.appendChild(row);
  updateConditionRow({ target: $(".condition-value-type", row) }, row);
  renumber();
}

function setupCollapsiblePanels() {
  document.querySelectorAll(".collapse-header").forEach(header => {
    header.addEventListener("click", () => {
      const panel = header.closest(".collapsible-panel");
      const body = document.getElementById(header.getAttribute("aria-controls"));
      const open = header.getAttribute("aria-expanded") === "true";
      header.setAttribute("aria-expanded", String(!open));
      body.classList.toggle("hidden", open);
      panel.classList.toggle("is-open", !open);
      const state = $(".collapse-state", header);
      state.childNodes[0].textContent = open ? "?? " : "?? ";
      $(".collapse-chevron", header).textContent = open ? "?" : "?";
      localStorage.setItem(`deepcoin-panel-${panel.dataset.panel}`, open ? "closed" : "open");
    });
    const panel = header.closest(".collapsible-panel");
    const saved = localStorage.getItem(`deepcoin-panel-${panel.dataset.panel}`);
    if (saved === "closed" && header.getAttribute("aria-expanded") === "true") header.click();
    if (saved === "open" && header.getAttribute("aria-expanded") === "false") header.click();
  });
}

function updateConditionRow(event, explicitRow) {
  const row = explicitRow || event.target.closest(".condition-row");
  const indicatorSelect = $(".condition-indicator", row);
  const operator = $(".condition-operator", row);
  if (operator.value === "within_percent" && event.target === operator) indicatorSelect.value = "price";
  if (operator.value === "within_percent" && event.target === indicatorSelect && indicatorSelect.value !== "price") operator.value = "<=";
  const indicator = indicatorSelect.value;
  const isProximity = operator.value === "within_percent";
  const isKeyLevelCondition = indicator === "key_levels";
  row.classList.toggle("is-key-level", isKeyLevelCondition);
  const typeSelect = $(".condition-value-type", row);
  const isBooleanMetric = keyLevelBooleanMetrics.has(indicator);
  if (isProximity) typeSelect.value = "indicator";
  else if (isBooleanMetric) typeSelect.value = "boolean";
  else if (typeSelect.value === "boolean") typeSelect.value = "number";
  const type = typeSelect.value;
  for (const option of typeSelect.options) {
    option.disabled = isBooleanMetric ? option.value !== "boolean"
      : isProximity ? option.value !== "indicator" : option.value === "boolean";
  }
  for (const option of indicatorSelect.options) option.disabled = isProximity && option.value !== "price";
  const rightIndicatorSelect = $(".condition-value-indicator", row);
  const priceLineIndicators = ["ma", "ema", "bb_upper", "bb_middle", "bb_lower"];
  if (isProximity && !priceLineIndicators.includes(rightIndicatorSelect.value)) rightIndicatorSelect.value = "ema";
  for (const option of rightIndicatorSelect.options) {
    option.disabled = isProximity && !priceLineIndicators.includes(option.value);
  }
  if (isBooleanMetric && !["=", "!="].includes(operator.value)) operator.value = "=";
  for (const option of operator.options) {
    option.disabled = isBooleanMetric && !["=", "!="].includes(option.value);
  }
  const signalField = $(".key-level-signal-field", row);
  const thresholdField = $(".key-level-threshold-field", row);
  const lookbackField = $(".key-level-lookback-field", row);
  signalField.classList.toggle("hidden", !isKeyLevelCondition);
  thresholdField.classList.add("hidden");
  lookbackField.classList.add("hidden");
  if (isKeyLevelCondition) {
    const signalSelect = $(".condition-key-level-signal", row);
    const signal = signalSelect.value;
    const threshold = $(".condition-key-level-threshold", row);
    if (event.target?.classList?.contains("condition-key-level-signal") && signal !== row.dataset.keyLevelSignal) {
      threshold.value = signal.endsWith("_near") ? "0.5"
        : ["support_hammer_rejection", "resistance_inverted_hammer_rejection"].includes(signal) ? "60" : "75";
    }
    row.dataset.keyLevelSignal = signal;
    const isRejectionPattern = ["support_hammer_rejection", "resistance_inverted_hammer_rejection"].includes(signal);
    const needsThreshold = signal.startsWith("high_quality_") || signal.endsWith("_near") || isRejectionPattern;
    thresholdField.classList.toggle("hidden", !needsThreshold);
    lookbackField.classList.toggle("hidden", !isRejectionPattern);
    threshold.min = "0";
    threshold.max = signal.endsWith("_near") ? "10" : "100";
    $(".key-level-threshold-label", row).textContent = signal.endsWith("_near")
      ? "???? (ATR)" : signal === "support_hammer_rejection" ? "????????"
        : signal === "resistance_inverted_hammer_rejection" ? "????????" : "?????";
    for (const selector of [".left-period-field", ".condition-operator", ".condition-value-type", ".numeric-value-field", ".boolean-value-field", ".right-indicator-field", ".right-period-field"]) {
      $(selector, row).closest(".condition-field")?.classList.add("hidden");
    }
    const thresholdSummary = !needsThreshold ? ""
      : signal.endsWith("_near") ? `????? ${threshold.value || "???"} ATR`
        : signal === "support_hammer_rejection" ? `??????? ? ${threshold.value || "???"}`
          : signal === "resistance_inverted_hammer_rejection" ? `??????? ? ${threshold.value || "???"}`
          : `???? ? ${threshold.value || "???"}`;
    const lookbackSummary = isRejectionPattern
      ? `????? ${$(".condition-key-level-lookback", row).value} ???? K ?` : "";
    $(".condition-summary", row).textContent = `?????????? ? ${keyLevelSignalNames.get(signal) || signal}${thresholdSummary}${lookbackSummary}`;
    return;
  }
  for (const selector of [".condition-operator", ".condition-value-type", ".numeric-value-field", ".boolean-value-field", ".right-indicator-field", ".right-period-field"]) {
    $(selector, row).closest(".condition-field")?.classList.remove("hidden");
  }
  $(".left-period-field", row).classList.toggle("hidden", ["price", "change24h", "volume24h"].includes(indicator) || keyLevelMetrics.has(indicator));
  $(".numeric-value-field", row).classList.toggle("hidden", type !== "number" && !isProximity);
  $(".numeric-value-field", row).childNodes[0].textContent = isProximity ? "???? (%)" : "??";
  const valueInput = $(".condition-value", row);
  if (isProximity) {
    valueInput.min = "0";
    valueInput.max = "100";
    valueInput.placeholder = "?? 0.5";
  } else {
    valueInput.removeAttribute("min");
    valueInput.removeAttribute("max");
    valueInput.placeholder = "?? 50";
  }
  $(".boolean-value-field", row).classList.toggle("hidden", type !== "boolean");
  $(".right-indicator-field", row).classList.toggle("hidden", type !== "indicator");
  $(".right-period-field", row).classList.toggle("hidden", type !== "indicator");
  const names = {
    price: "???", change24h: "24h???", volume24h: "24h???", ma: "MA", ema: "EMA", rsi: "RSI",
    bb_upper: "????", bb_middle: "????", bb_lower: "????", bb_width: "????",
    kl_support_exists: "??????", kl_support_top: "????", kl_support_bottom: "????",
    kl_support_score: "?????", kl_support_touches: "??????", kl_support_confluence: "??????",
    kl_support_htf: "???????", kl_support_retest_pending: "??????", kl_support_distance_atr: "??? ATR",
    kl_resistance_exists: "??????", kl_resistance_top: "????", kl_resistance_bottom: "????",
    kl_resistance_score: "?????", kl_resistance_touches: "??????", kl_resistance_confluence: "??????",
    kl_resistance_htf: "???????", kl_resistance_retest_pending: "??????", kl_resistance_distance_atr: "??? ATR",
    kl_event_support_touch: "??????", kl_event_support_hammer: "????????", kl_event_resistance_touch: "??????",
    kl_event_support_break: "??????", kl_event_resistance_break: "??????",
    kl_event_support_retest: "????????", kl_event_resistance_retest: "????????",
    kl_previous_day_high: "?????", kl_previous_day_low: "?????",
    kl_previous_week_high: "?????", kl_previous_week_low: "?????",
    kl_day_open: "?????", kl_session_vwap: "?? VWAP",
  };
  const operators = { ">": ">", ">=": "?", "<": "<", "<=": "?", "=": "=", "!=": "?" };
  const noPeriod = ["price", "change24h", "volume24h"].includes(indicator) || keyLevelMetrics.has(indicator);
  const left = `${names[indicator] || indicator}${noPeriod ? "" : $(".condition-period", row).value}`;
  let right = type === "indicator"
    ? `${names[$(".condition-value-indicator", row).value] || $(".condition-value-indicator", row).value}${keyLevelMetrics.has($(".condition-value-indicator", row).value) ? "" : $(".condition-value-period", row).value}`
    : type === "boolean" ? $(".condition-boolean-value", row).selectedOptions[0].textContent : $(".condition-value", row).value;
  $(".condition-summary", row).textContent = isProximity
    ? `?????${left} ?? ${right || "?????"} ??? ${valueInput.value || "???"}%`
    : `?????${left} ${operators[operator.value] || operator.value} ${right || "???"}`;
}

function renumber() {
  [...conditions.children].forEach((row, index) => $(".condition-index", row).textContent = String(index + 1).padStart(2, "0"));
}

function readConditions() {
  return [...conditions.children].map(row => ({
    timeframe: $(".condition-timeframe", row).value,
    indicator: $(".condition-indicator", row).value,
    period: Number($(".condition-period", row).value) || 20,
    operator: $(".condition-operator", row).value,
    valueType: $(".condition-value-type", row).value,
    value: Number($(".condition-value", row).value),
    valueText: $(".condition-value", row).value.trim(),
    booleanValue: $(".condition-boolean-value", row).value === "true",
    valueIndicator: $(".condition-value-indicator", row).value,
    valuePeriod: Number($(".condition-value-period", row).value) || 60,
    keyLevelSignal: $(".condition-key-level-signal", row).value,
    keyLevelThreshold: Number($(".condition-key-level-threshold", row).value),
    keyLevelThresholdText: $(".condition-key-level-threshold", row).value.trim(),
    keyLevelLookbackBars: Number($(".condition-key-level-lookback", row).value) || 24,
  }));
}

function validateClientConditions(conditionList) {
  for (let index = 0; index < conditionList.length; index += 1) {
    const condition = conditionList[index];
    if (condition.operator === "within_percent") {
      if (condition.indicator !== "price" || condition.valueType !== "indicator"
          || !["ma", "ema", "bb_upper", "bb_middle", "bb_lower"].includes(condition.valueIndicator)) {
        return `? ${index + 1} ????????????????????`;
      }
      if (!condition.valueText || !Number.isFinite(condition.value) || condition.value < 0 || condition.value > 100) {
        return `? ${index + 1} ????????? 0 ? 100% ???`;
      }
      continue;
    }
    if (condition.indicator === "key_levels") {
      if (!keyLevelSignalNames.has(condition.keyLevelSignal)) return `? ${index + 1} ??????????????`;
      if ((condition.keyLevelSignal.startsWith("high_quality_") || condition.keyLevelSignal.endsWith("_near")
          || ["support_hammer_rejection", "resistance_inverted_hammer_rejection"].includes(condition.keyLevelSignal))
          && (!condition.keyLevelThresholdText || !Number.isFinite(condition.keyLevelThreshold)
            || condition.keyLevelThreshold < 0 || condition.keyLevelThreshold > (condition.keyLevelSignal.endsWith("_near") ? 10 : 100))) {
        return `? ${index + 1} ???????????`;
      }
      if (["support_hammer_rejection", "resistance_inverted_hammer_rejection"].includes(condition.keyLevelSignal)
          && (!Number.isInteger(condition.keyLevelLookbackBars)
            || condition.keyLevelLookbackBars < 1 || condition.keyLevelLookbackBars > 100)) {
        return `? ${index + 1} ??K???????? 1 ? 100 ????`;
      }
      continue;
    }
    if (keyLevelBooleanMetrics.has(condition.indicator)) {
      if (condition.valueType !== "boolean" || !["=", "!="].includes(condition.operator)) return `? ${index + 1} ??????????????????????????`;
      continue;
    }
    if (condition.valueType === "boolean") return `? ${index + 1} ????????????????`;
    if (condition.valueType !== "indicator") continue;
    if (condition.indicator !== condition.valueIndicator || (!keyLevelMetrics.has(condition.indicator) && condition.period !== condition.valuePeriod)) continue;
    if ([">", "<", "!=", "=", ">=", "<="].includes(condition.operator)) {
      const names = { price: "???", ma: "MA", ema: "EMA", rsi: "RSI", bb_upper: "????", bb_middle: "????", bb_lower: "????", ...Object.fromEntries([...keyLevelMetrics].map(metric => [metric, metric])) };
      const label = `${names[condition.indicator] || condition.indicator}(${condition.period})`;
      if ([">", "<", "!="].includes(condition.operator)) return `? ${index + 1} ??????${label} ??????????????????????`;
      return `? ${index + 1} ???????${label} ???????????????`;
    }
  }
  return "";
}

function formatNumber(value, digits = 4) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "?";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: digits }).format(Number(value));
}

function formatVolume(value) {
  if (value === null || value === undefined) return "?";
  const n = Number(value);
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return formatNumber(n, 2);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

function renderChartButton(item, timeframe, source) {
  return `<button class="chart-open-button" type="button" data-inst-id="${escapeHtml(item.instId)}" data-symbol="${escapeHtml(item.symbol)}" data-chart-timeframe="${escapeHtml(timeframe || "1h")}" data-chart-source="${source}" title="?? ${escapeHtml(item.symbol)} ?? K ??????">??</button>`;
}

function renderKeyLevelSummary(levels) {
  const entries = Object.entries(levels || {});
  if (!entries.length) return "?";
  return `<div class="key-level-cell">${entries.map(([timeframe, values]) => {
    const support = Number(values.kl_support_exists) === 1
      ? `${formatNumber(values.kl_support_bottom, 8)} - ${formatNumber(values.kl_support_top, 8)} ? ${formatNumber(values.kl_support_score, 0)}`
      : "?";
    const resistance = Number(values.kl_resistance_exists) === 1
      ? `${formatNumber(values.kl_resistance_bottom, 8)} - ${formatNumber(values.kl_resistance_top, 8)} ? ${formatNumber(values.kl_resistance_score, 0)}`
      : "?";
    const refs = [
      ["??", values.kl_previous_day_high], ["??", values.kl_previous_day_low],
      ["??", values.kl_previous_week_high], ["??", values.kl_previous_week_low],
      ["?", values.kl_day_open], ["VWAP", values.kl_session_vwap],
    ].filter(([, value]) => value !== null && value !== undefined)
      .map(([label, value]) => `${label} ${formatNumber(value, 8)}`);
    return `<span><b>${timeframe}</b> ? ${support} / ? ${resistance}${refs.length ? `<small>${refs.join(" ? ")}</small>` : ""}</span>`;
  }).join("")}</div>`;
}

function renderTradeLink(item) {
  const symbol = String(item.symbol || "").trim().toUpperCase();
  const expectedUrl = `https://www.deepcoin.com/swap/zh/${encodeURIComponent(item.webSymbol || symbol)}`;
  const auditUnavailable = currentAudit && (currentAudit.stale || currentAudit.status !== "ready");
  if (auditUnavailable || item.webTradeStatus !== "available" || item.tradeUrl !== expectedUrl) {
    const wrapper = document.createElement("span");
    wrapper.className = "trade-pair-unavailable";
    const name = document.createElement("strong");
    name.textContent = symbol;
    wrapper.appendChild(name);
    const note = document.createElement("span");
    note.className = "trade-link-note";
    note.textContent = auditUnavailable ? "???????" : item.webCatalogStatus === "unavailable" ? "????????" : "???????";
    note.title = auditUnavailable
      ? "???????????????????????????????"
      : item.webTradeReason || "??????????????????? BTCUSDT????????????????";
    note.tabIndex = 0;
    wrapper.appendChild(note);
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "copy-trade-symbol";
    copy.dataset.symbol = symbol;
    copy.textContent = "????";
    copy.title = `?? ${symbol}?? Deepcoin ??????`;
    wrapper.appendChild(copy);
    return wrapper.outerHTML;
  }
  const link = document.createElement("a");
  link.className = "trade-pair-link";
  link.href = `/trade?${new URLSearchParams({ instId: item.instId })}`;
  link.dataset.tradeUrl = item.tradeUrl;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.title = `??????? ${symbol} ? Deepcoin ????`;
  link.setAttribute("aria-label", `${symbol}???????? Deepcoin ????`);
  const name = document.createElement("strong");
  name.textContent = symbol;
  const icon = document.createElement("span");
  icon.className = "trade-link-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = "?";
  name.appendChild(icon);
  link.appendChild(name);
  return link.outerHTML;
}

document.addEventListener("click", async event => {
  const button = event.target.closest(".copy-trade-symbol");
  if (!button) return;
  const symbol = button.dataset.symbol;
  try {
    await navigator.clipboard.writeText(symbol);
    button.textContent = "???";
    setTimeout(() => { button.textContent = "????"; }, 1800);
  } catch {
    window.prompt("?????????? Deepcoin ???????", symbol);
  }
});

function renderRanking(data) {
  const body = $("#rankingBody");
  const rawItems = Array.isArray(data.results) ? data.results : [];
  const items = verifiedRows(rawItems);
  const rankCarouselButton = $("#rankCarouselButton");
  if (rankCarouselButton) {
    // Keep the button available while the website directory is refreshing so
    // the click handler can explain why no verified rows are available.
    rankCarouselButton.disabled = carouselStartBusy || !rawItems.length;
    if (carouselStartBusy) rankCarouselButton.setAttribute("aria-busy", "true");
    else rankCarouselButton.removeAttribute("aria-busy");
  }
  updateCarouselHint();
  const directionLabel = data.direction === "asc" ? "??" : "??";
  const catalog = data.websiteCatalog || {};
  $("#rankMeta").textContent = `?? ${data.scanned}/${data.totalMarkets} ?????${catalog.excludedMarkets ? ` ? ?????? ${catalog.excludedMarkets}` : ""} ? ${data.timeframe} ? ${data.durationMs || 0}ms ??`;
  if (catalog.errors?.length) {
    $("#message").textContent = `????????????????????????${catalog.errors.join("?")}`;
    $("#message").classList.remove("hidden");
  }
  if (data.errors?.length) {
    $("#message").textContent = `????????????${data.errors.join("?")}`;
    $("#message").classList.remove("hidden");
  }
  if (!items.length) {
    const waiting = currentAudit && (currentAudit.stale || currentAudit.status !== "ready");
    body.innerHTML = `<tr class="empty-row"><td colspan="7"><div class="empty-state"><div class="empty-mark">?</div><strong>${waiting ? "????????" : "??????????"}</strong><span>${waiting ? "?????????????????" : `?? ${data.failed || 0} ????`}</span></div></td></tr>`;
    return;
  }
  body.innerHTML = items.map((original, index) => {
    const item = { ...original, rank: index + 1 };
    const changeClass = item.changePercent >= 0 ? "positive" : "negative";
    const closeClass = item.closeChangePercent >= 0 ? "positive" : "negative";
    return `<tr><td class="rank-number">${item.rank}</td><td><div class="pair-actions">${renderTradeLink(item)}${renderChartButton(item, data.timeframe, "ranking")}</div><span class="pair-sub">${item.quote} ??</span></td><td class="mono">${formatNumber(item.price, 8)}</td><td class="mono">${formatNumber(item.periodStartPrice, 8)}</td><td class="mono ${changeClass}">${item.changePercent >= 0 ? "+" : ""}${formatNumber(item.changePercent, 2)}%</td><td class="mono ${closeClass}">${item.closeChangePercent >= 0 ? "+" : ""}${formatNumber(item.closeChangePercent, 2)}%</td><td class="mono">${formatVolume(item.volume24h)}</td></tr>`;
  }).join("");
}

function scheduleRankingRefresh() {
  clearTimeout(rankingRefreshTimer);
  rankingRefreshTimer = null;
  rankingNextRefreshAt = 0;
  const seconds = Number($("#rankingRefreshSeconds").value) || 0;
  if (!seconds) {
    $("#rankAutoStatus").textContent = lastRankingData ? "???????" : "?????? 60 ?????";
    return;
  }
  if (!lastRankingData) {
    $("#rankAutoStatus").textContent = `????????? ${seconds} ?????`;
    return;
  }
  rankingNextRefreshAt = Date.now() + seconds * 1000;
  $("#rankAutoStatus").textContent = `? ${seconds} ???????? ${new Date(rankingNextRefreshAt).toLocaleTimeString("zh-CN")}`;
  rankingRefreshTimer = setTimeout(() => runRanking({ automatic: true }), seconds * 1000);
}

async function runRanking({ automatic = false } = {}) {
  const button = $("#rankButton");
  if (rankingBusy) return;
  clearTimeout(rankingRefreshTimer);
  rankingRefreshTimer = null;
  rankingNextRefreshAt = 0;
  rankingBusy = true;
  if (automatic) $("#rankAutoStatus").textContent = "?????????????";
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.textContent = "???";
  try {
    const requestPayload = {
      quote: $("#quote").value,
      timeframe: $("#rankingTimeframe").value,
      direction: $("#rankingDirection").value,
      maxSymbols: Number($("#maxSymbols").value) || 100,
      symbols: $("#symbols").value,
    };
    const query = new URLSearchParams({
      ...requestPayload,
      cacheSeconds: automatic ? String(Number($("#rankingRefreshSeconds").value) || 0) : "0",
    });
    const response = await requestWithRetry(`/api/price-change?${query.toString()}`, {}, { timeoutMs: 180000, retries: 1 });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "???????");
    $("#message").classList.add("hidden");
    lastRankingData = data;
    lastRankingPayload = requestPayload;
    await loadMarketAudit();
    renderRanking(data);
    if (data.unavailableSymbols?.length) {
      $("#message").textContent = `${data.unavailableSymbols.join("?")} ????????????????????????????????????????`;
      $("#message").classList.remove("hidden");
    }
  } catch (error) {
    $("#message").textContent = error.name === "TypeError" ? "?????????????????" : error.message;
    $("#message").classList.remove("hidden");
  } finally {
    rankingBusy = false;
    scheduleRankingRefresh();
    button.disabled = false;
    button.removeAttribute("aria-busy");
    button.textContent = "????";
  }
}

function renderResults(data, mode = activeMode) {
  const results = verifiedRows(mode === "live" ? (data.liveResults || []) : (data.closeResults || data.results || []));
  updateCarouselHint();
  renderScreeningDiagnostics(data, mode);
  $("#resultCount").textContent = results.length;
  $("#priceHeader").textContent = mode === "live" ? "???" : "???";
  const failedText = data.failedCandles ? ` ? ${data.failedCandles} ? K ???` : "";
  const performanceText = data.durationMs ? ` ? ${data.durationMs}ms` : "";
  const catalog = data.websiteCatalog || {};
  const catalogText = catalog.excludedMarkets ? ` ? ?????? ${catalog.excludedMarkets}` : "";
  $("#scanMeta").textContent = `?? ${data.scanned}/${data.totalMarkets} ????? ? ?? ${data.timeframes?.join(" / ") || data.timeframe}${catalogText}${failedText}${performanceText} ? ${new Date(data.updatedAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })} ??`;
  if (catalog.errors?.length) {
    $("#message").textContent = `????????????????????????${catalog.errors.join("?")}`;
    $("#message").classList.remove("hidden");
  }
  if (data.candleErrors?.length) {
    $("#message").textContent = `?? K ??????${data.candleErrors.join("?")}`;
    $("#message").classList.remove("hidden");
  }
  const body = $("#resultsBody");
  if (!results.length) {
    const waiting = currentAudit && (currentAudit.stale || currentAudit.status !== "ready");
    const diagnostic = data.screeningDiagnostics;
    const modeDiagnostic = diagnostic?.[mode];
    let hint = waiting ? "?????????????????" : data.unavailableSymbols?.length ? "?????????????????????" : "??????????????";
    if (!waiting && !data.unavailableSymbols?.length && diagnostic) {
      if (diagnostic.selectedMarkets > 0 && diagnostic.candleCandidates === 0) {
        hint = `${diagnostic.selectedMarkets} ???????? 24h ????????`;
      } else if (modeDiagnostic?.missingData) {
        hint = `${modeDiagnostic.missingData} ????????????????????????????`;
      } else if (modeDiagnostic?.conditionsNotMet) {
        hint = `${modeDiagnostic.conditionsNotMet} ??????????????????????????`;
      } else if (diagnostic.selectedMarkets === 0) {
        hint = "?????????????";
      }
    }
    body.innerHTML = `<tr class="empty-row"><td colspan="9"><div class="empty-state"><div class="empty-mark">?</div><strong>${waiting ? "????????" : "??????????"}</strong><span>${hint}</span></div></td></tr>`;
    return;
  }
  body.innerHTML = results.map(item => {
    const changeClass = item.change24h >= 0 ? "positive" : "negative";
    const chartTimeframe = item.timeframes?.[0] || data.timeframe || "1h";
    return `<tr><td><div class="pair-actions">${renderTradeLink(item)}${renderChartButton(item, chartTimeframe, "screen")}</div><span class="pair-sub">${item.quote} ??</span></td><td class="mono">${formatNumber(item.price, 8)}</td><td class="mono ${changeClass}">${item.change24h >= 0 ? "+" : ""}${formatNumber(item.change24h, 2)}%</td><td class="mono">${formatVolume(item.volume24h)}</td><td class="mono">${formatNumber(item.ema20, 8)}</td><td class="mono">${formatNumber(item.ma20, 8)}</td><td><span class="band-value">${formatNumber(item.bbLower, 6)} <i>?</i> ${formatNumber(item.bbUpper, 6)}</span><span class="pair-sub">?? ${formatNumber(item.bbWidth, 2)}%</span></td><td>${renderKeyLevelSummary(item.keyLevelsByTimeframe)}</td><td><span class="data-ok">${item.timeframes?.join(" / ") || "?"}</span><span class="pair-sub">${item.candles || 0} ? K ?</span></td></tr>`;
  }).join("");
}

async function loadCarouselDisplays() {
  const select = $("#carouselDisplay");
  if (!select) return;
  try {
    const data = await requestJson("/api/displays", {}, { timeoutMs: 10000, retries: 1 });
    select.innerHTML = (data.displays || []).map(display =>
      `<option value="${display.index}">${escapeHtml(display.name || `??? ${display.index + 1}`)}${display.primary ? "???" : ""} ? ${display.width || "??"}?${display.height || ""}</option>`
    ).join("");
    for (const [index, display] of (data.displays || []).entries()) {
      const option = select.options[index];
      if (option) option.textContent = `??? ${Number(display.screenNumber) || Number(display.index) + 1}${display.primary ? "????" : ""} ? ${display.width || "??"}?${display.height || ""}`;
    }
  } catch (error) {
    select.innerHTML = "<option value=\"0\">?????????????</option>";
    $("#carouselStatus").textContent = error.message;
  }
}

async function startCarouselFromPanel(sourceOverride) {
  const status = $("#carouselStatus");
  const button = $("#carouselStart");
  const rankButton = $("#rankCarouselButton");
  if (carouselStartBusy) return;
  carouselStartBusy = true;
  if (sourceOverride) $("#carouselSource").value = sourceOverride;
  button.disabled = true;
  rankButton.disabled = true;
  status.textContent = "?????????";
  try {
    const selection = carouselSelection();
    const { mode, results } = selection;
    if (!results.length) {
      const message = selection.source === "ranking"
        ? "???????????????????????????????????"
        : "??????????????????????";
      status.textContent = message;
      $("#message").textContent = message;
      $("#message").classList.remove("hidden");
      return;
    }
    const interval = Math.max(5, Math.min(3600, Number($("#carouselInterval").value) || 15));
    status.textContent = "?????????";
    const data = await requestJson("/api/carousel", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        instIds: results.map(item => item.instId),
        quote: selection.quote,
        timeframe: selection.timeframe,
        mode,
        interval,
        displayIndex: Number($("#carouselDisplay").value) || 0,
        conditions: selection.conditions,
        chartIndicators: readChartIndicators(),
        source: selection.source,
        ranking: selection.source === "ranking" ? selection.ranking : undefined,
      }),
    }, { timeoutMs: 15000, retries: 0 });
    const skipped = Number(data.skippedCount) || 0;
    status.textContent = `??? ${Number(data.startedCount) || results.length} ????????? ${data.pid}?${skipped ? `???? ${skipped} ??????` : ""}`;
  } catch (error) {
    status.textContent = error.message;
  } finally {
    carouselStartBusy = false;
    button.disabled = false;
    const rankingRows = Array.isArray(lastRankingData?.results) ? lastRankingData.results : [];
    rankButton.disabled = !rankingRows.length;
    rankButton.removeAttribute("aria-busy");
  }
}

function renderScreeningDiagnostics(data, mode) {
  const panel = $("#screenDiagnostics");
  const diagnostics = data.screeningDiagnostics;
  const selected = diagnostics?.selectedMarkets;
  if (!diagnostics || selected == null) {
    panel.classList.add("hidden");
    return;
  }
  const current = diagnostics[mode] || { matched: 0, conditionsNotMet: 0, missingData: 0 };
  $("#screenDiagnosticsSummary").textContent =
    `???? ${selected} ??24h ?????? ${diagnostics.tickerFiltered || 0} ???? K ??? ${diagnostics.candleCandidates || 0} ??` +
    `??${mode === "live" ? "??" : "??"}????? ${current.matched} ??????? ${current.conditionsNotMet} ?????? ${current.missingData} ??`;
  const conditionRows = (diagnostics.conditions || []).map(item => {
    const close = item.close || {};
    const live = item.live || {};
    return `<li>${escapeHtml(item.label)}????? ${close.passed || 0} / ??? ${close.notMet || 0} / ?? ${close.missing || 0}????? ${live.passed || 0} / ??? ${live.notMet || 0} / ?? ${live.missing || 0}</li>`;
  });
  $("#screenDiagnosticsConditions").innerHTML = conditionRows.length
    ? conditionRows.join("")
    : "<li>???????</li>";
  panel.classList.remove("hidden");
}

const chartTimeframes = ["1m", "5m", "15m", "30m", "1h", "4h", "12h", "1d", "1w"];

function readChartIndicators() {
  const settings = [
    ["ema", "chartIndicatorEma", "chartIndicatorEmaPeriod"],
    ["ma", "chartIndicatorMa", "chartIndicatorMaPeriod"],
  ];
  const result = [];
  for (const [name, enabledId, periodId] of settings) {
    if (!$("#" + enabledId)?.checked) continue;
    result.push({ name, period: Math.max(1, Math.min(200, Number($("#" + periodId).value) || 20)) });
  }
  if ($("#chartIndicatorBb")?.checked) {
    result.push({
      name: "bb",
      period: Math.max(1, Math.min(200, Number($("#chartIndicatorBbPeriod").value) || 20)),
      deviations: Math.max(0.1, Math.min(10, Number($("#chartIndicatorBbDeviation").value) || 2)),
    });
  }
  return result;
}

function applyChartIndicators(indicators) {
  const entries = Array.isArray(indicators) ? indicators : [];
  const byName = new Map(entries.map(item => [String(item.name || "").toLowerCase(), item]));
  const setControl = (name, enabledId, periodId) => {
    const item = byName.get(name);
    const enabled = $("#" + enabledId);
    const period = $("#" + periodId);
    if (enabled) enabled.checked = Boolean(item);
    if (period && item?.period) period.value = item.period;
  };
  setControl("ema", "chartIndicatorEma", "chartIndicatorEmaPeriod");
  setControl("ma", "chartIndicatorMa", "chartIndicatorMaPeriod");
  const bb = byName.get("bb") || byName.get("bollinger") || byName.get("bollinger_bands");
  $("#chartIndicatorBb") && ($("#chartIndicatorBb").checked = Boolean(bb));
  if (bb?.period) $("#chartIndicatorBbPeriod").value = bb.period;
  if (bb?.deviations) $("#chartIndicatorBbDeviation").value = bb.deviations;
}
const chartKeyLevelNames = new Map([
  ["support_exists", "??????"], ["resistance_exists", "??????"],
  ["high_quality_support", "?????"], ["high_quality_resistance", "?????"],
  ["support_hammer_rejection", "???????"],
  ["resistance_inverted_hammer_rejection", "????????"],
  ["support_near", "??????"], ["resistance_near", "??????"],
  ["support_touch", "??????"], ["resistance_touch", "??????"],
  ["support_break", "??????"], ["resistance_break", "??????"],
  ["support_retest", "??????"], ["resistance_retest", "??????"],
  ["price_above_vwap", "?????? VWAP"], ["price_below_vwap", "?????? VWAP"],
  ["price_above_previous_day_high", "????????"], ["price_below_previous_day_low", "????????"],
  ["price_above_previous_week_high", "????????"], ["price_below_previous_week_low", "????????"],
  ["price_above_day_open", "????????"], ["price_below_day_open", "????????"],
]);
const chartIndicatorNames = {
  price: "??", change24h: "24h ???", volume24h: "24h ???",
  ma: "MA", ema: "EMA", rsi: "RSI", bb_upper: "????", bb_middle: "????",
  bb_lower: "????", bb_width: "????", key_levels: "?????",
};
const chartOperatorNames = { ">": ">", ">=": "?", "<": "<", "<=": "?", "=": "=", "!=": "?" };
const chartColors = ["#4c8bf5", "#f2a900", "#c678dd", "#f07178", "#56b6c2", "#98c379", "#e06c75"];

async function requestJson(url, options = {}, { timeoutMs = 30000, retries = 1 } = {}) {
  let lastError;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(url, { ...options, signal: controller.signal });
      const text = await response.text();
      let data;
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        throw new Error("??????????????????????");
      }
      if (!response.ok) {
        const error = new Error(data.error || `?????HTTP ${response.status}?`);
        error.retryable = [408, 429, 500, 502, 503, 504].includes(response.status);
        throw error;
      }
      return data;
    } catch (error) {
      lastError = error;
      const retryable = error.name === "TypeError" || error.retryable;
      if (!retryable || attempt >= retries) {
        if (error.name === "AbortError") {
          throw new Error(`?????? ${Math.round(timeoutMs / 1000)} ??????????????????????`);
        }
        throw error;
      }
      await new Promise(resolve => setTimeout(resolve, 400 * (attempt + 1)));
    } finally {
      clearTimeout(timeout);
    }
  }
  throw lastError || new Error("????");
}

async function requestWithRetry(url, options = {}, { timeoutMs = 30000, retries = 1 } = {}) {
  let lastError;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(url, { ...options, signal: controller.signal });
      if (response.ok || ![408, 429, 500, 502, 503, 504].includes(response.status) || attempt >= retries) {
        return response;
      }
      await response.body?.cancel();
      lastError = new Error(`?????HTTP ${response.status}?`);
    } catch (error) {
      lastError = error;
      if (error.name === "AbortError") {
        throw new Error(`?????? ${Math.round(timeoutMs / 1000)} ??????????????????????`);
      }
      if (error.name !== "TypeError") throw error;
      if (attempt >= retries) throw error;
    } finally {
      clearTimeout(timeout);
    }
    await new Promise(resolve => setTimeout(resolve, 400 * (attempt + 1)));
  }
  throw lastError || new Error("????");
}

function chartIndicatorLabel(name, period) {
  const base = chartIndicatorNames[name] || name || "??";
  return ["ma", "ema", "rsi", "bb_upper", "bb_middle", "bb_lower", "bb_width"].includes(name)
    ? `${base}${period || 20}` : base;
}

function chartConditionLabel(condition) {
  if (condition.indicator === "key_levels") {
    const signal = chartKeyLevelNames.get(condition.keyLevelSignal) || condition.keyLevelSignal;
    const threshold = condition.keyLevelThreshold;
    if (["support_hammer_rejection", "resistance_inverted_hammer_rejection"].includes(condition.keyLevelSignal)) {
      return `${signal} ? ${formatNumber(threshold, 1)} ? ??${condition.keyLevelLookbackBars || 24}?`;
    }
    if (condition.keyLevelSignal?.startsWith("high_quality_") && threshold !== undefined) return `${signal} ? ${formatNumber(threshold, 1)}`;
    if (condition.keyLevelSignal?.endsWith("_near") && threshold !== undefined) return `${signal} ? ${formatNumber(threshold, 2)} ATR`;
    return signal;
  }
  const left = chartIndicatorLabel(condition.indicator, condition.period);
  if (condition.valueType === "boolean") return `${left} ${chartOperatorNames[condition.operator] || condition.operator} ${condition.booleanValue ? "?" : "?"}`;
  const right = condition.valueType === "indicator"
    ? chartIndicatorLabel(condition.valueIndicator, condition.valuePeriod)
    : formatNumber(condition.value, 4);
  if (condition.operator === "within_percent") {
    return `${left} ?? ${right} ??? ${formatNumber(condition.value, 2)}%`;
  }
  return `${left} ${chartOperatorNames[condition.operator] || condition.operator} ${right}`;
}

function updateChartReadout(data) {
  const target = $("#chartConditionReadout");
  target.replaceChildren();
  const rows = data.conditions || [];
  if (!rows.length) {
    const label = document.createElement("span");
    label.className = "chart-condition-empty";
    label.textContent = "??????????";
    target.appendChild(label);
    return;
  }
  rows.forEach(condition => {
    const chip = document.createElement("span");
    const pass = chartState.mode === "live" ? condition.livePass : condition.closePass;
    chip.className = `chart-condition-chip${condition.onChart ? (pass ? " is-pass" : " is-fail") : " is-muted"}`;
    chip.textContent = `${condition.timeframe} ? ${chartConditionLabel(condition)}${condition.onChart ? ` ? ${pass ? "??" : "???"}` : " ? ????"}`;
    if (condition.onChart && condition.closeValue !== undefined) {
      chip.title = `??? ${formatNumber(condition.closeValue, 8)} ? ??? ${formatNumber(condition.liveValue, 8)}`;
    }
    target.appendChild(chip);
  });
}

function updateChartLevelReadout(data) {
  const target = $("#chartLevelReadout");
  target.replaceChildren();
  const zones = data.zones || [];
  target.classList.toggle("hidden", !zones.length);
  if (!zones.length) return;
  const price = Number(data.livePrice ?? data.closePrice);
  const tolerance = Number(data.zoneSideTolerance) || 0;
  const nearest = kind => zones.filter(zone => zone.kind === kind).sort((left, right) => {
    const distance = zone => price > zone.top ? price - zone.top : price < zone.bottom ? zone.bottom - price : 0;
    return distance(left) - distance(right);
  }).filter(zone => kind === 1
    ? (zone.top + zone.bottom) / 2 <= price + tolerance
    : (zone.top + zone.bottom) / 2 >= price - tolerance)[0];
  const table = document.createElement("table");
  table.className = "chart-level-table";
  table.innerHTML = "<thead><tr><th>??</th><th>????</th><th>?? / ??</th></tr></thead>";
  const body = document.createElement("tbody");
  const precision = data.pricePrecision ?? 8;
  for (const [kind, label, className] of [[-1, "??", "resistance"], [1, "??", "support"]]) {
    const zone = nearest(kind);
    const row = document.createElement("tr");
    row.className = className;
    const region = document.createElement("th");
    region.textContent = zone?.awaitingRetest ? `${label}??` : label;
    const range = document.createElement("td");
    range.textContent = zone ? `${formatNumber(zone.bottom, precision)} ? ${formatNumber(zone.top, precision)}` : "?";
    const score = document.createElement("td");
    score.textContent = zone ? `${formatNumber(zone.score, 0)} / T${formatNumber(zone.touches, 0)}${zone.containsHtf ? " ? HTF" : ""}` : "?";
    if (zone?.score >= 75) score.classList.add("is-strong");
    row.append(region, range, score);
    body.appendChild(row);
  }
  table.appendChild(body);
  target.appendChild(table);
}

function chartPlotColor(plot, index) {
  if (plot.name === "bb_upper" || plot.name === "bb_lower") return "#5b9cf5";
  if (plot.name === "bb_middle") return "#f0b44d";
  if (plot.name === "rsi") return "#28b8a7";
  if (plot.name === "bb_width") return "#c678dd";
  return chartColors[index % chartColors.length];
}

function renderChartLegend(indicators, zoneSeries, events) {
  const target = $("#chartLegend");
  target.replaceChildren();
  const volumeEntry = document.createElement("span");
  volumeEntry.className = "chart-legend-item";
  const volumeSwatch = document.createElement("i");
  volumeSwatch.className = "chart-volume-swatch";
  volumeEntry.append(volumeSwatch, document.createTextNode("???"));
  target.appendChild(volumeEntry);
  indicators.forEach(seriesEntry => {
    const { plot, index, latestValue } = seriesEntry;
    const entry = document.createElement("span");
    entry.className = "chart-legend-item";
    const swatch = document.createElement("i");
    swatch.style.backgroundColor = chartPlotColor(plot, index);
    const name = document.createElement("span");
    name.textContent = plot.title;
    const value = document.createElement("b");
    value.className = "chart-legend-value";
    value.dataset.seriesName = `${plot.name}:${plot.period || ""}:${plot.deviations || ""}`;
    value.textContent = formatNumber(latestValue, plot.pane === "indicator" ? 2 : (chartData?.pricePrecision ?? 8));
    entry.append(swatch, name, value);
    target.appendChild(entry);
    seriesEntry.valueElement = value;
  });
  if (zoneSeries?.length) {
    for (const [kind, label, color] of [[1, "????", "#26a69a"], [-1, "????", "#ef5350"]]) {
      if (!zoneSeries.some(zone => zone.kind === kind)) continue;
      const entry = document.createElement("span");
      entry.className = "chart-legend-item";
      const swatch = document.createElement("i");
      swatch.style.backgroundColor = color;
      entry.append(swatch, document.createTextNode(label));
      target.appendChild(entry);
    }
  }
  if (events?.length) {
    const entry = document.createElement("span");
    entry.className = "chart-legend-item chart-event-legend";
    entry.textContent = "? ???? / ? ?????";
    target.appendChild(entry);
  }
  if (!target.childElementCount) target.classList.add("hidden");
  else target.classList.remove("hidden");
}

function updateChartLegendValues(seriesEntries, seriesData, time) {
  for (const entry of seriesEntries) {
    const point = seriesData?.get(entry.series);
    const value = point?.value ?? (time === undefined ? undefined : entry.valuesByTime.get(time)) ?? entry.latestValue;
    if (entry.valueElement) entry.valueElement.textContent = formatNumber(value, entry.plot.pane === "indicator" ? 2 : (chartData?.pricePrecision ?? 8));
  }
}

function updateChartOhlc(candle, volume, time, fallback) {
  const target = $("#chartOhlcReadout");
  target.replaceChildren();
  const point = candle || fallback;
  if (!point) return;
  if (time !== undefined && time !== null && typeof time === "number") {
    const date = document.createElement("span");
    date.className = "chart-ohlc-date";
    date.textContent = new Date(time * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
    target.appendChild(date);
  }
  const precision = chartData?.pricePrecision ?? 8;
  const changeClass = Number(point.close) >= Number(point.open) ? "positive" : "negative";
  for (const [label, key] of [["?", "open"], ["?", "high"], ["?", "low"], ["?", "close"]]) {
    const cell = document.createElement("span");
    cell.className = `chart-ohlc-cell${key === "close" ? ` ${changeClass}` : ""}`;
    const name = document.createElement("i");
    name.textContent = label;
    const value = document.createElement("b");
    value.textContent = formatNumber(point[key], precision);
    cell.append(name, value);
    target.appendChild(cell);
  }
  const volumeCell = document.createElement("span");
  volumeCell.className = "chart-ohlc-cell chart-ohlc-volume";
  const volumeName = document.createElement("i");
  volumeName.textContent = "?";
  const volumeValue = document.createElement("b");
  volumeValue.textContent = formatVolume(volume?.value ?? point.volume);
  volumeCell.append(volumeName, volumeValue);
  target.appendChild(volumeCell);
}

function chartOptions(container, height, scaleMargins = { top: 0.08, bottom: 0.08 }, showTimeScale = true) {
  return {
    width: Math.max(320, container.clientWidth),
    height,
    layout: { background: { color: "#0b1016" }, textColor: "#9aa7b5", fontFamily: "Segoe UI, Microsoft YaHei, sans-serif", fontSize: 11 },
    grid: { vertLines: { color: "#1b2530" }, horzLines: { color: "#1b2530" } },
    rightPriceScale: { borderColor: "#27313d", scaleMargins },
    timeScale: { visible: showTimeScale, borderColor: "#27313d", timeVisible: true, secondsVisible: false, rightOffset: 6 },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal, vertLine: { color: "#7d8996", width: 1, style: LightweightCharts.LineStyle.Dashed, labelBackgroundColor: "#364150" }, horzLine: { color: "#7d8996", width: 1, style: LightweightCharts.LineStyle.Dashed, labelBackgroundColor: "#364150" } },
    handleScroll: true,
    handleScale: true,
  };
}

function mainChartHeight() {
  if (document.documentElement.classList.contains("chart-page")) {
    return Math.max(260, document.querySelector(".chart-stage")?.clientHeight || window.innerHeight * 0.65);
  }
  return window.matchMedia("(max-width: 700px)").matches
    ? Math.max(300, Math.min(420, window.innerHeight * 0.43))
    : Math.max(390, Math.min(600, window.innerHeight * 0.55));
}

function lowerChartHeight() {
  if (document.documentElement.classList.contains("chart-page")) {
    return Math.max(95, Math.min(190, window.innerHeight * 0.17));
  }
  return window.matchMedia("(max-width: 700px)").matches ? 112 : 142;
}

function attachZoneOverlay(chart, priceSeries, zones, holder) {
  if (!zones?.length) return;
  const canvas = document.createElement("canvas");
  canvas.className = "chart-zone-overlay";
  holder.appendChild(canvas);
  const context = canvas.getContext("2d");
  const draw = () => {
    const bounds = holder.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(bounds.width * ratio);
    canvas.height = Math.round(bounds.height * ratio);
    canvas.style.width = `${bounds.width}px`;
    canvas.style.height = `${bounds.height}px`;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, bounds.width, bounds.height);
    context.save();
    context.beginPath();
    context.rect(0, 0, Math.min(bounds.width, chart.timeScale().width()), bounds.height);
    context.clip();
    for (const zone of zones) {
      const color = zone.kind === 1 ? "22,134,109" : "198,94,86";
      const upper = priceSeries.priceToCoordinate(zone.top);
      const lower = priceSeries.priceToCoordinate(zone.bottom);
      if (upper === null || lower === null) continue;
      let startX = chart.timeScale().timeToCoordinate(zone.startTime);
      if (startX === null) {
        const visibleRange = chart.timeScale().getVisibleRange();
        if (!visibleRange || zone.startTime > visibleRange.from) continue;
        startX = 0;
      }
      startX = Math.max(0, startX);
      const endX = Math.min(bounds.width, chart.timeScale().width());
      if (startX >= endX) continue;
      const top = Math.max(0, Math.min(upper, lower));
      const bottom = Math.min(bounds.height, Math.max(upper, lower));
      if (bottom <= top) continue;
      const transparency = Math.max(66, Math.min(92, Math.round(91 - ((zone.score || 50) - 50) * 0.9)));
      const opacity = 1 - transparency / 100;
      const fillColor = zone.awaitingRetest ? "245,158,11" : color;
      context.fillStyle = `rgba(${fillColor},${zone.awaitingRetest ? 0.14 : opacity})`;
      context.fillRect(startX, top, endX - startX, bottom - top);
      context.strokeStyle = `rgba(${fillColor},${zone.score >= 75 ? 0.9 : 0.58})`;
      context.lineWidth = zone.score >= 75 ? 2 : 1;
      context.beginPath();
      context.moveTo(startX, upper);
      context.lineTo(endX, upper);
      context.moveTo(startX, lower);
      context.lineTo(endX, lower);
      context.moveTo(startX, top);
      context.lineTo(startX, bottom);
      context.stroke();
      const originY = zone.originPrice == null ? null : priceSeries.priceToCoordinate(zone.originPrice);
      if (originY !== null && Number.isFinite(originY) && originY >= 0 && originY <= bounds.height) {
        context.beginPath();
        context.arc(startX, originY, 3, 0, Math.PI * 2);
        context.fillStyle = "rgba(255,255,255,0.96)";
        context.strokeStyle = `rgba(${color},0.98)`;
        context.lineWidth = 1.5;
        context.fill();
        context.stroke();
      }
    }
    context.restore();
  };
  chart.timeScale().subscribeVisibleLogicalRangeChange(draw);
  chart.timeScale().subscribeSizeChange(draw);
  requestAnimationFrame(draw);
}

function drawChart(data, preserveRange = false) {
  const mainHolder = $("#priceChart");
  const indicatorHolder = $("#indicatorChart");
  const priorRange = preserveRange && priceChart ? priceChart.timeScale().getVisibleLogicalRange() : null;
  const hasIndicatorPane = (data.indicators || []).some(plot => plot.pane === "indicator");
  chartResizeObserver?.disconnect();
  if (priceChart) priceChart.remove();
  if (indicatorChart) indicatorChart.remove();
  priceChart = null;
  indicatorChart = null;
  mainHolder.replaceChildren();
  indicatorHolder.replaceChildren();
  indicatorHolder.classList.toggle("hidden", !hasIndicatorPane);

  const candleData = data.candles || [];
  const candlesByTime = new Map(candleData.map(candle => [candle.time, candle]));
  const seriesEntries = [];
  const paneEntries = [];
  let paneSeries = [];
  priceChart = LightweightCharts.createChart(mainHolder, chartOptions(
    mainHolder, mainChartHeight(), { top: 0.18, bottom: 0.22 }, !hasIndicatorPane,
  ));
  const candles = priceChart.addCandlestickSeries({
    upColor: "#26a69a", downColor: "#ef5350", borderUpColor: "#26a69a", borderDownColor: "#ef5350",
    wickUpColor: "#26a69a", wickDownColor: "#ef5350", priceLineVisible: false,
  });
  candles.setData(candleData);
  const volumeSeries = priceChart.addHistogramSeries({
    priceScaleId: "volume", priceFormat: { type: "volume" }, priceLineVisible: false,
    lastValueVisible: false, base: 0,
  });
  volumeSeries.setData(candleData.map(item => ({
    time: item.time, value: Number(item.volume) || 0,
    color: item.close >= item.open ? "rgba(38,166,154,0.48)" : "rgba(239,83,80,0.48)",
  })));
  volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.84, bottom: 0.02 }, visible: false });
  const modeKey = chartState.mode === "live" ? "live" : "closed";
  const pricePlots = (data.indicators || []).filter(plot => plot.pane === "price");
  pricePlots.forEach((plot, index) => {
    const series = priceChart.addLineSeries({
      color: chartPlotColor(plot, index), lineWidth: 2, title: plot.title,
      priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: true,
    });
    const points = plot[modeKey] || [];
    series.setData(points);
    seriesEntries.push({ plot, index, series, latestValue: points.at(-1)?.value, valuesByTime: new Map(points.map(point => [point.time, point.value])) });
  });

  attachZoneOverlay(priceChart, candles, data.zones || [], mainHolder);

  if (data.events?.length) candles.setMarkers(data.events);
  const features = chartState.mode === "live" ? data.liveFeatures : data.closeFeatures;
  const referenceLines = [
    ["kl_previous_day_high", "???", "#8a9996"], ["kl_previous_day_low", "???", "#8a9996"],
    ["kl_previous_week_high", "???", "#a07e2e"], ["kl_previous_week_low", "???", "#a07e2e"],
    ["kl_day_open", "????", "#557eae"], ["kl_session_vwap", "?? VWAP", "#865aa1"],
  ];
  for (const [key, title, color] of referenceLines) {
    const value = Number(features?.[key]);
    if (!Number.isFinite(value)) continue;
    candles.createPriceLine({ price: value, color, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true, title });
  }
  const livePrice = Number(data.livePrice);
  if (Number.isFinite(livePrice)) {
    candles.createPriceLine({ price: livePrice, color: "#f5a623", lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: "???" });
  }
  const candleLows = candleData.map(item => item.low);
  const candleHighs = candleData.map(item => item.high);
  const visibleLow = Math.min(...candleLows);
  const visibleHigh = Math.max(...candleHighs);
  for (const threshold of data.thresholds || []) {
    if (threshold.pane !== "indicator" && threshold.value >= visibleLow && threshold.value <= visibleHigh) candles.createPriceLine({
      price: threshold.value, color: "#8c9999", lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: threshold.title,
    });
  }

  if (hasIndicatorPane) {
    indicatorChart = LightweightCharts.createChart(indicatorHolder, chartOptions(indicatorHolder, lowerChartHeight()));
    const panePlots = (data.indicators || []).filter(plot => plot.pane === "indicator");
    paneSeries = [];
    panePlots.forEach((plot, index) => {
      const series = indicatorChart.addLineSeries({
        color: chartPlotColor(plot, index), lineWidth: 2, title: plot.title,
        priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: true,
      });
      const points = plot[modeKey] || [];
      series.setData(points);
      paneSeries.push(series);
      paneEntries.push({ plot, index, series, latestValue: points.at(-1)?.value, valuesByTime: new Map(points.map(point => [point.time, point.value])) });
      if (plot.name === "rsi") {
        series.createPriceLine({ price: 70, color: "#c65e56", lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true, title: "70" });
        series.createPriceLine({ price: 30, color: "#16866d", lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true, title: "30" });
      }
    });
    for (const threshold of (data.thresholds || []).filter(item => item.pane === "indicator")) {
      paneSeries[0]?.createPriceLine({ price: threshold.value, color: "#8c9999", lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: threshold.title });
    }
    priceChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (chartSyncing || !range || !indicatorChart) return;
      chartSyncing = true;
      indicatorChart.timeScale().setVisibleLogicalRange(range);
      chartSyncing = false;
    });
    indicatorChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (chartSyncing || !range || !priceChart) return;
      chartSyncing = true;
      priceChart.timeScale().setVisibleLogicalRange(range);
      chartSyncing = false;
    });
    seriesEntries.push(...paneEntries);
  }
  renderChartLegend(seriesEntries, data.zones || [], data.events || []);
  updateChartOhlc(null, null, null, candleData.at(-1));
  priceChart.subscribeCrosshairMove(param => {
    if (chartCrosshairSyncing) return;
    const time = param.time;
    const selected = time === undefined ? null : param.seriesData.get(candles);
    const selectedVolume = time === undefined ? null : param.seriesData.get(volumeSeries);
    updateChartOhlc(selected, selectedVolume, time, candleData.at(-1));
    updateChartLegendValues(seriesEntries, param.seriesData, time);
    if (!indicatorChart || !paneEntries.length) return;
    const value = time === undefined ? undefined : paneEntries[0].valuesByTime.get(time);
    chartCrosshairSyncing = true;
    if (value === undefined) indicatorChart.clearCrosshairPosition();
    else indicatorChart.setCrosshairPosition(value, time, paneSeries[0]);
    chartCrosshairSyncing = false;
  });
  if (indicatorChart && paneEntries.length) {
    indicatorChart.subscribeCrosshairMove(param => {
      if (chartCrosshairSyncing) return;
      const time = param.time;
      const selected = time === undefined ? null : candlesByTime.get(time);
      updateChartOhlc(selected, selected ? { value: selected.volume } : null, time, candleData.at(-1));
      updateChartLegendValues(seriesEntries, param.seriesData, time);
      chartCrosshairSyncing = true;
      if (!selected) priceChart.clearCrosshairPosition();
      else priceChart.setCrosshairPosition(selected.close, time, candles);
      chartCrosshairSyncing = false;
    });
  }
  priceChart.timeScale().fitContent();
  if (priorRange) {
    try { priceChart.timeScale().setVisibleLogicalRange(priorRange); } catch { /* Data window changed while refreshing. */ }
  } else if (candleData.length > 120) {
    priceChart.timeScale().setVisibleLogicalRange({ from: candleData.length - 120, to: candleData.length + 6 });
  }
  chartResizeObserver = new ResizeObserver(() => {
    if (priceChart) priceChart.resize(Math.max(320, mainHolder.clientWidth), mainChartHeight());
    if (indicatorChart) indicatorChart.resize(Math.max(320, indicatorHolder.clientWidth), lowerChartHeight());
  });
  chartResizeObserver.observe(mainHolder);
  if (hasIndicatorPane) chartResizeObserver.observe(indicatorHolder);
}

async function refreshChart({ preserveRange = false } = {}) {
  if (!chartState) return;
  if (chartRequestBusy) {
    pendingChartRefresh = true;
    pendingChartPreserveRange ||= preserveRange;
    return;
  }
  const requestState = { ...chartState };
  chartRequestBusy = true;
  $("#chartMessage").classList.add("hidden");
  try {
    const response = await fetch("/api/chart", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestState),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "????????");
    if (chartState.instId !== requestState.instId
      || chartState.timeframe !== requestState.timeframe
      || chartState.mode !== requestState.mode
      || JSON.stringify(chartState.chartIndicators || []) !== JSON.stringify(requestState.chartIndicators || [])) {
      pendingChartRefresh = true;
      pendingChartPreserveRange ||= preserveRange;
      return;
    }
    chartData = data;
    $("#chartStatus").classList.add("hidden");
    $("#chartTitle").textContent = data.symbol;
    $("#chartSubtitle").textContent = `${data.timeframe} ? ${data.candles.length} ? K ? ? ${chartState.mode === "live" ? "????" : "????"}`;
    $("#chartQuote").textContent = `?? ${formatNumber(data.livePrice, data.pricePrecision)}????? ${formatNumber(data.closePrice, data.pricePrecision)}`;
    $("#chartTradeLink").href = `/trade?${new URLSearchParams({ instId: data.instId })}`;
    document.querySelectorAll("[data-chart-mode]").forEach(button => button.classList.toggle("active", button.dataset.chartMode === chartState.mode));
    updateChartReadout(data);
    updateChartLevelReadout(data);
    drawChart(data, preserveRange);
  } catch (error) {
    if (priceChart) {
      $("#chartStatus").textContent = `?????? ? ${error.message}`;
      $("#chartStatus").classList.remove("hidden");
    } else {
      $("#chartMessage").textContent = error.message;
      $("#chartMessage").classList.remove("hidden");
    }
  } finally {
    chartRequestBusy = false;
    if (pendingChartRefresh) {
      const nextPreserveRange = pendingChartPreserveRange;
      pendingChartRefresh = false;
      pendingChartPreserveRange = false;
      queueMicrotask(() => refreshChart({ preserveRange: nextPreserveRange }));
    }
  }
}

function openChart(button) {
  const timeframe = chartTimeframes.includes(button.dataset.chartTimeframe) ? button.dataset.chartTimeframe : "1h";
  const source = button.dataset.chartSource;
  const state = {
    instId: button.dataset.instId,
    timeframe,
    mode: source === "ranking" ? "live" : activeMode,
    conditions: source === "screen" ? (lastPayload?.conditions || []) : [],
    chartIndicators: readChartIndicators(),
  };
  const query = new URLSearchParams({
    instId: state.instId,
    symbol: button.dataset.symbol || "???",
    timeframe: state.timeframe,
    mode: state.mode,
    conditions: JSON.stringify(state.conditions),
    chartIndicators: JSON.stringify(state.chartIndicators),
  });
  window.open(`/chart?${query}`, "_blank", "noopener,noreferrer");
}

function startStandaloneChart() {
  const query = new URLSearchParams(window.location.search);
  let chartConditions = [];
  try {
    chartConditions = JSON.parse(query.get("conditions") || "[]");
    if (!Array.isArray(chartConditions)) chartConditions = [];
  } catch {
    chartConditions = [];
  }
  const timeframe = chartTimeframes.includes(query.get("timeframe")) ? query.get("timeframe") : "1h";
  chartState = {
    instId: query.get("instId") || "",
    timeframe,
    mode: query.get("mode") === "live" ? "live" : "close",
    conditions: chartConditions,
    chartIndicators: (() => { try { const value = JSON.parse(query.get("chartIndicators") || "[]"); return Array.isArray(value) ? value : []; } catch { return []; } })(),
  };
  applyChartIndicators(chartState.chartIndicators);
  $("#chartTimeframe").value = timeframe;
  $("#chartTitle").textContent = query.get("symbol") || "???";
  $("#chartSubtitle").textContent = "???????";
  $("#chartQuote").textContent = "";
  $("#chartStatus").classList.add("hidden");
  $("#chartMessage").classList.add("hidden");
  $("#chartTradeLink").href = `/trade?${new URLSearchParams({ instId: chartState.instId })}`;
  document.title = `${query.get("symbol") || "???"} ? Deepcoin ??`;
  updateChartReadout({ conditions: chartConditions });
  $("#chartDialog").showModal();
  window.requestAnimationFrame(() => refreshChart());
  clearInterval(chartRefreshTimer);
  chartRefreshTimer = setInterval(() => refreshChart({ preserveRange: true }), 15000);
}

function startCarouselPage() {
  const query = new URLSearchParams(window.location.search);
  const nextButton = $("#carouselNext");
  const existingPreviousButton = $("#carouselPrevious");
  if (nextButton && existingPreviousButton) {
    existingPreviousButton.classList.remove("carousel-previous-fallback");
    nextButton.parentElement.insertBefore(existingPreviousButton, nextButton);
  }
  if (nextButton && !$("#carouselPrevious")) {
    const previousButton = document.createElement("button");
    previousButton.id = "carouselPrevious";
    previousButton.type = "button";
    previousButton.textContent = "???";
    nextButton.parentElement.insertBefore(previousButton, nextButton);
  }
  const startWithQuery = resolvedQuery => {
    carouselItems = (resolvedQuery.get("instIds") || "").split(",").map(item => item.trim()).filter(Boolean);
    if (!carouselItems.length) {
      $("#chartMessage").textContent = "?????????";
      $("#chartMessage").classList.remove("hidden");
      return;
    }
    startCarouselWithResolvedQuery(resolvedQuery);
  };
  const token = query.get("carouselToken");
  if (token) {
    requestJson(`/api/carousel/session?token=${encodeURIComponent(token)}`, {}, { timeoutMs: 10000, retries: 1 })
      .then(data => startWithQuery(new URLSearchParams(data.query || "")))
      .catch(error => {
        $("#chartMessage").textContent = error.message;
        $("#chartMessage").classList.remove("hidden");
      });
    return;
  }
  startWithQuery(query);
}

function startCarouselWithResolvedQuery(query) {
  if (!carouselItems.length) {
    $("#chartMessage").textContent = "?????????";
    $("#chartMessage").classList.remove("hidden");
    return;
  }
  carouselIndex = 0;
  carouselPaused = false;
  carouselIntervalMs = Math.max(5000, Math.min(3600000, (Number(query.get("interval")) || 15) * 1000));
  let chartConditions = [];
  try { chartConditions = JSON.parse(query.get("conditions") || "[]"); } catch { chartConditions = []; }
  chartState = {
    instId: carouselItems[0], timeframe: chartTimeframes.includes(query.get("timeframe")) ? query.get("timeframe") : "1h",
    mode: query.get("mode") === "live" ? "live" : "close", conditions: chartConditions,
    chartIndicators: (() => { try { const value = JSON.parse(query.get("chartIndicators") || "[]"); return Array.isArray(value) ? value : []; } catch { return []; } })(),
  };
  applyChartIndicators(chartState.chartIndicators);
  const rankRefreshSeconds = Math.max(0, Math.min(3600, Number(query.get("rankRefreshSeconds")) || 0));
  if (rankRefreshSeconds > 0) {
    carouselRankConfig = {
      quote: query.get("rankQuote") || query.get("quote") || "USDT",
      timeframe: chartTimeframes.includes(query.get("rankTimeframe")) ? query.get("rankTimeframe") : chartState.timeframe,
      direction: query.get("rankDirection") === "asc" ? "asc" : "desc",
      maxSymbols: Math.max(1, Math.min(1000, Number(query.get("rankMaxSymbols")) || 100)),
      symbols: query.get("rankSymbols") || "",
      refreshSeconds: rankRefreshSeconds,
    };
    const firstDelay = Math.max(1, Math.min(rankRefreshSeconds, Number(query.get("rankFirstRefreshSeconds")) || rankRefreshSeconds));
    scheduleCarouselRankingRefresh(firstDelay);
    $("#carouselRankStatus").textContent = `?? ${carouselRankConfig.timeframe} ? ${rankRefreshSeconds} ???`;
  } else {
    carouselRankConfig = null;
    $("#carouselRankStatus").textContent = "????";
  }
  $("#chartTimeframe").value = chartState.timeframe;
  $("#chartTradeLink").href = `/trade?${new URLSearchParams({ instId: chartState.instId })}`;
  $("#chartDialog").showModal();
  $("#carouselOverlay").classList.remove("hidden");
  $("#carouselPosition").textContent = `?? 1 / ${carouselItems.length}`;
  document.title = "Deepcoin ????";
  window.requestAnimationFrame(() => refreshChart());
  clearInterval(chartRefreshTimer);
  chartRefreshTimer = setInterval(() => refreshChart({ preserveRange: true }), 15000);
  scheduleNextCarousel();
}

function scheduleCarouselRankingRefresh(delaySeconds) {
  clearTimeout(carouselRankRefreshTimer);
  if (!carouselRankConfig || carouselPaused) return;
  carouselRankRefreshTimer = setTimeout(refreshCarouselRanking, Math.max(1, delaySeconds) * 1000);
}

async function refreshCarouselRanking() {
  if (!carouselRankConfig || carouselPaused || carouselRankBusy) return;
  carouselRankBusy = true;
  const config = carouselRankConfig;
  $("#carouselRankStatus").textContent = "?????";
  try {
    const query = new URLSearchParams({
      quote: config.quote,
      timeframe: config.timeframe,
      direction: config.direction,
      maxSymbols: String(config.maxSymbols),
      symbols: config.symbols,
      cacheSeconds: String(config.refreshSeconds),
    });
    const response = await requestWithRetry(`/api/price-change?${query}`, {}, {
      timeoutMs: 180000,
      retries: 1,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "?????????");
    const nextItems = (data.results || []).map(item => item.instId).filter(Boolean);
    if (!nextItems.length) throw new Error("???????????????????");
    const currentId = chartState?.instId;
    const currentIndex = carouselIndex;
    carouselItems = nextItems;
    const preservedIndex = currentId ? carouselItems.indexOf(currentId) : -1;
    if (preservedIndex >= 0) {
      carouselIndex = preservedIndex;
    } else {
      carouselIndex = Math.min(currentIndex, carouselItems.length - 1);
      chartState.instId = carouselItems[carouselIndex];
      await refreshChart({ preserveRange: true });
    }
    $("#carouselPosition").textContent = `?? ${carouselIndex + 1} / ${carouselItems.length}`;
    $("#carouselRankStatus").textContent = `????? ${new Date(data.updatedAt || Date.now()).toLocaleTimeString("zh-CN")}`;
  } catch (error) {
    $("#carouselRankStatus").textContent = `?????? ? ${error.message}`;
  } finally {
    carouselRankBusy = false;
    if (carouselRankConfig && !carouselPaused) {
      scheduleCarouselRankingRefresh(config.refreshSeconds);
    }
  }
}

function scheduleNextCarousel() {
  clearTimeout(carouselTimer);
  if (carouselPaused || carouselItems.length < 2) return;
  carouselTimer = setTimeout(async () => {
    carouselIndex = (carouselIndex + 1) % carouselItems.length;
    chartState.instId = carouselItems[carouselIndex];
    $("#carouselPosition").textContent = `?? ${carouselIndex + 1} / ${carouselItems.length}`;
    await refreshChart();
    scheduleNextCarousel();
  }, carouselIntervalMs);
}

async function moveCarousel(step) {
  if (!carouselItems.length || !chartState) return;
  clearTimeout(carouselTimer);
  carouselIndex = (carouselIndex + step + carouselItems.length) % carouselItems.length;
  chartState.instId = carouselItems[carouselIndex];
  $("#carouselPosition").textContent = `?? ${carouselIndex + 1} / ${carouselItems.length}`;
  await refreshChart();
  scheduleNextCarousel();
}

function stopCarouselPage() {
  clearTimeout(carouselTimer);
  clearTimeout(carouselRankRefreshTimer);
  clearTimeout(carouselRankStartTimer);
  carouselTimer = null;
  carouselRankRefreshTimer = null;
  carouselRankStartTimer = null;
  carouselPaused = true;
  carouselRankConfig = null;
  $("#carouselOverlay")?.classList.add("hidden");
  if (document.documentElement.classList.contains("carousel-page")) window.close();
}

async function runScreen({ silent = false } = {}) {
  const button = $("#screenButton");
  const refreshButton = $("#refreshButton");
  const message = $("#message");
  if (screenBusy) return;
  const conditionList = readConditions();
  const validationError = validateClientConditions(conditionList);
  if (validationError) {
    message.textContent = validationError;
    message.classList.remove("hidden");
    return;
  }
  const payload = { quote: $("#quote").value, timeframe: $("#timeframe").value, maxSymbols: Number($("#maxSymbols").value) || 50, symbols: $("#symbols").value, conditions: conditionList };
  lastPayload = payload;
  screenBusy = true;
  button.disabled = true;
  refreshButton.disabled = true;
  button.setAttribute("aria-busy", "true");
  if (!silent) {
    button.innerHTML = `<span class="spinner"></span> ??????`;
    button.title = "???????????????";
    message.classList.add("hidden");
  } else {
    button.innerHTML = `<span class="spinner"></span> ?????`;
    button.title = "???????????????";
  }
  try {
    const response = await requestWithRetry("/api/screen", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    }, { timeoutMs: 300000, retries: 1 });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "????");
    message.classList.add("hidden");
    lastData = data;
    renderResults(data, activeMode);
    void loadMarketAudit();
    if (data.unavailableSymbols?.length) {
      message.textContent = `${data.unavailableSymbols.join("?")} ????????????????????????????????????????`;
      message.classList.remove("hidden");
    }
  } catch (error) {
    message.textContent = error.name === "TypeError" ? "?????????????? http://127.0.0.1:8000 ??????????" : error.message;
    message.classList.remove("hidden");
  } finally {
    screenBusy = false;
    button.disabled = false;
    refreshButton.disabled = false;
    button.removeAttribute("aria-busy");
    button.innerHTML = `<span class="button-icon">?</span>????`;
    button.title = "";
  }
}

function setMode(mode) {
  activeMode = mode;
  document.querySelectorAll(".result-tab").forEach(button => button.classList.toggle("active", button.dataset.mode === mode));
  if (lastData) renderResults(lastData, mode);
}

async function checkHealth() {
  const status = $(".live-status");
  try {
    const response = await fetch("/api/health");
    if (!response.ok) throw new Error("????????");
    const health = await response.json();
    const connected = health.exchangeConnected === true;
    status.classList.toggle("has-error", !connected && health.marketDirectoryStatus === "error");
    $("#healthText").textContent = connected
      ? "Deepcoin ???"
      : health.marketDirectoryStatus === "error" ? "Deepcoin ????" : "???? Deepcoin";
    status.title = connected ? "Deepcoin ????????????" : (health.marketDirectoryError || "?????? Deepcoin ????");
  } catch (error) {
    status.classList.add("has-error");
    $("#healthText").textContent = "??????";
    status.title = error.message || "??????????";
  }
}

async function checkForUpdates() {
  const button = $("#updateCheckButton");
  const status = $("#updateStatus");
  if (!button || !status || updateBusy) return;
  updateBusy = true;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  status.textContent = "?????";
  try {
    const data = await requestJson("/api/update/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }, { timeoutMs: 20000, retries: 0 });
    const current = data.currentVersion || "??";
    const latest = data.latestVersion || current;
    if (!data.updateAvailable) {
      status.textContent = `???? ${current}?????`;
    } else if (data.scheduled && data.updateStatus === "downloading") {
      status.textContent = `?? ${latest}??????????`;
    } else if (data.scheduled) {
      status.textContent = `?? ${latest}???????`;
    } else if (data.packaged) {
      status.textContent = `?? ${latest}???????`;
    } else {
      status.textContent = `?? ${latest}?????????????`;
    }
  } catch (error) {
    status.textContent = `?????${error.message}`;
  } finally {
    updateBusy = false;
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

$("#addCondition").addEventListener("click", () => addCondition());
$("#clearConditions").addEventListener("click", () => { conditions.innerHTML = ""; addCondition(); });
document.querySelectorAll("[data-preset]").forEach(button => button.addEventListener("click", () => {
  const timeframe = $("#timeframe").value;
  const presets = {
    trend: [
      { timeframe, indicator: "ema", period: 20, operator: ">", valueType: "indicator", valueIndicator: "ema", valuePeriod: 60 },
    ],
    "price-ma": [
      { timeframe, indicator: "price", period: 20, operator: ">", valueType: "indicator", valueIndicator: "ema", valuePeriod: 20 },
    ],
    "price-near-ema": [
      { timeframe, indicator: "price", operator: "within_percent", valueType: "indicator", valueIndicator: "ema", valuePeriod: 20, value: 0.5 },
    ],
    rsi: [
      { timeframe, indicator: "rsi", period: 14, operator: ">", valueType: "number", value: 50 },
    ],
    band: [
      { timeframe, indicator: "price", period: 20, operator: "<", valueType: "indicator", valueIndicator: "bb_lower", valuePeriod: 20 },
    ],
    "key-support": [
      { timeframe, indicator: "key_levels", keyLevelSignal: "high_quality_support", keyLevelThreshold: 70 },
    ],
    "key-hammer": [
      { timeframe, indicator: "key_levels", keyLevelSignal: "support_hammer_rejection", keyLevelThreshold: 60, keyLevelLookbackBars: 24 },
    ],
    "key-inverted-hammer": [
      { timeframe, indicator: "key_levels", keyLevelSignal: "resistance_inverted_hammer_rejection", keyLevelThreshold: 60, keyLevelLookbackBars: 24 },
    ],
    "key-touch": [
      { timeframe, indicator: "key_levels", keyLevelSignal: "support_touch" },
    ],
    "key-break": [
      { timeframe, indicator: "key_levels", keyLevelSignal: "resistance_break" },
    ],
  };
  conditions.innerHTML = "";
  (presets[button.dataset.preset] || []).forEach(addCondition);
}));
$("#screenButton").addEventListener("click", runScreen);
$("#refreshButton").addEventListener("click", runScreen);
$("#updateCheckButton")?.addEventListener("click", checkForUpdates);
$("#rankButton").addEventListener("click", runRanking);
$("#rankingRefreshSeconds")?.addEventListener("change", scheduleRankingRefresh);
$("#carouselStart")?.addEventListener("click", () => startCarouselFromPanel());
$("#rankCarouselButton")?.addEventListener("click", () => startCarouselFromPanel("ranking"));
$("#carouselSource")?.addEventListener("change", updateCarouselHint);
$("#carouselMode")?.addEventListener("change", updateCarouselHint);
$("#carouselPause")?.addEventListener("click", event => {
  carouselPaused = !carouselPaused;
  event.currentTarget.textContent = carouselPaused ? "??" : "??";
  if (carouselPaused) {
    clearTimeout(carouselRankRefreshTimer);
    clearTimeout(carouselRankStartTimer);
  } else if (carouselRankConfig) {
    scheduleCarouselRankingRefresh(carouselRankConfig.refreshSeconds);
  }
  scheduleNextCarousel();
});
$("#carouselNext")?.addEventListener("click", async () => {
  if (!carouselItems.length || !chartState) return;
  carouselIndex = (carouselIndex + 1) % carouselItems.length;
  chartState.instId = carouselItems[carouselIndex];
  $("#carouselPosition").textContent = `?? ${carouselIndex + 1} / ${carouselItems.length}`;
  await refreshChart();
  scheduleNextCarousel();
});
$("#carouselPrevious")?.addEventListener("click", () => moveCarousel(-1));
$("#carouselExit")?.addEventListener("click", stopCarouselPage);
$("#auditRefresh").addEventListener("click", () => loadMarketAudit(true));
$("#marketAuditSearch").addEventListener("input", renderAuditEntries);
document.addEventListener("click", event => {
  const chartButton = event.target.closest(".chart-open-button");
  if (chartButton) {
    openChart(chartButton);
    return;
  }
  const modeButton = event.target.closest("[data-chart-mode]");
  if (modeButton && chartState && chartData) {
    chartState.mode = modeButton.dataset.chartMode;
    document.querySelectorAll("[data-chart-mode]").forEach(button => button.classList.toggle("active", button === modeButton));
    $("#chartSubtitle").textContent = `${chartState.timeframe} ? ${chartData.candles.length} ? K ? ? ${chartState.mode === "live" ? "????" : "????"}`;
    updateChartReadout(chartData);
    drawChart(chartData, true);
  }
});
$("#chartClose").addEventListener("click", () => {
  if (document.documentElement.classList.contains("chart-page")) window.close();
  else $("#chartDialog").close();
});
$("#chartDialog").addEventListener("cancel", event => {
  if (document.documentElement.classList.contains("chart-page")) {
    event.preventDefault();
    window.close();
  }
});
$("#chartDialog").addEventListener("close", () => {
  clearInterval(chartRefreshTimer);
  chartResizeObserver?.disconnect();
  chartResizeObserver = null;
  if (priceChart) priceChart.remove();
  if (indicatorChart) indicatorChart.remove();
  priceChart = null;
  indicatorChart = null;
  chartData = null;
});
$("#chartTimeframe").addEventListener("change", () => {
  if (!chartState) return;
  chartState.timeframe = $("#chartTimeframe").value;
  refreshChart();
});
["chartIndicatorEma", "chartIndicatorMa", "chartIndicatorBb", "chartIndicatorEmaPeriod", "chartIndicatorMaPeriod", "chartIndicatorBbPeriod", "chartIndicatorBbDeviation"].forEach(id => {
  $("#" + id)?.addEventListener("change", () => {
    if (!chartState) return;
    chartState.chartIndicators = readChartIndicators();
    refreshChart({ preserveRange: true });
  });
});
$("#timeframe").addEventListener("change", () => {
  document.querySelectorAll(".condition-timeframe").forEach(select => { select.value = $("#timeframe").value; });
});
document.querySelectorAll(".result-tab").forEach(button => button.addEventListener("click", () => setMode(button.dataset.mode)));
addCondition({ timeframe: "1h", indicator: "price", operator: ">", valueType: "number", value: 0 });
setupCollapsiblePanels();
if (document.documentElement.classList.contains("chart-page")) {
  startStandaloneChart();
} else if (document.documentElement.classList.contains("carousel-page")) {
  startCarouselPage();
} else {
  checkHealth();
  loadMarketAudit();
  loadCarouselDisplays();
  setInterval(checkHealth, 5000);
  setInterval(loadMarketAudit, 15000);
  setInterval(() => {
    if ($("#autoRefresh").checked && lastPayload && !screenBusy) runScreen({ silent: true });
  }, 60000);
}
