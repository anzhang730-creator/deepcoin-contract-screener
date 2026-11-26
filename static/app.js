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
  ["support_exists", "存在支撑区域"], ["resistance_exists", "存在压力区域"],
  ["high_quality_support", "高质量支撑"], ["high_quality_resistance", "高质量压力"],
  ["support_hammer_rejection", "支撑锤型线拒绝"],
  ["resistance_inverted_hammer_rejection", "压力位长上影拒绝"],
  ["support_near", "价格接近支撑"], ["resistance_near", "价格接近压力"],
  ["support_touch", "收盘触碰支撑"], ["resistance_touch", "收盘触碰压力"],
  ["support_break", "收盘跌破支撑"], ["resistance_break", "收盘突破压力"],
  ["support_retest", "支撑回踩确认"], ["resistance_retest", "压力回踩确认"],
  ["price_above_vwap", "价格高于会话 VWAP"], ["price_below_vwap", "价格低于会话 VWAP"],
  ["price_above_previous_day_high", "价格高于前日高点"], ["price_below_previous_day_low", "价格低于前日低点"],
  ["price_above_previous_week_high", "价格高于前周高点"], ["price_below_previous_week_low", "价格低于前周低点"],
  ["price_above_day_open", "价格高于当日开盘"], ["price_below_day_open", "价格低于当日开盘"],
]);

function renderAuditEntries() {
  const query = $("#marketAuditSearch").value.trim().toUpperCase();
  const body = $("#marketAuditRows");
  body.replaceChildren();
  const states = { pending: "待核验", not_listed: "官网未列出", inactive: "非交易中" };
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
  const checked = data.checkedAt ? new Date(data.checkedAt).toLocaleTimeString("zh-CN") : "尚无成功记录";
  $(".market-audit").classList.toggle("has-error", failed);
  $("#marketAuditStatus").textContent = failed
    ? `核验${data.status === "error" ? "失败，15 秒后自动重试" : "更新中"} · 上次成功 ${checked} · 官网核验异常`
    : `已确认 ${data.availableMarkets || 0} 个 · 官网未列出 ${data.excludedMarkets || 0} 个 · 待核验 ${data.pendingMarkets || 0} 个 · ${checked}${data.refreshing ? " · 正在更新" : ""}`;
  if (failed && data.verifiedSymbols?.length) {
    $("#marketAuditStatus").textContent += " · 使用上次确认目录继续筛选，直达暂不可用";
  }
  $("#marketAuditSummary").textContent = `核验明细与最近变更${data.pendingMarkets ? `（${data.pendingMarkets} 个待核验）` : ""}`;
  $("#marketAuditHint").textContent = data.errors?.length ? data.errors.join("；") : "后台每 60 秒核对；异常或清单不同步时每 15 秒重试。官网未列出不等同永久下架，后续确认后会自动恢复。";
  const change = data.changes?.at(-1);
  $("#marketAuditChanges").textContent = change ? (change.initial ? `本次启动初始化确认 ${change.added.length} 个合约。` : `最近范围变更：新增确认 ${change.added.length} 个${change.added.length ? `（${change.added.slice(0, 8).join("、")}）` : ""}；移出确认范围 ${change.removed.length} 个${change.removed.length ? `（${change.removed.slice(0, 8).join("、")}）` : ""}。`) : "等待首次核验。";
  renderAuditEntries();
}

async function loadMarketAudit(force = false) {
  const button = $("#auditRefresh");
  if (auditBusy) {
    if (force) {
      auditForceQueued = true;
      button.disabled = true;
      button.textContent = "核验排队中";
    }
    return;
  }
  auditBusy = true;
  button.disabled = true;
  if (force) button.textContent = "核验中…";
  try {
    const data = await requestJson("/api/market-audit", { method: force ? "POST" : "GET" }, {
      timeoutMs: force ? 90000 : 15000,
      retries: force ? 0 : 1,
    });
    renderAudit(data);
    if (lastData) renderResults(lastData, activeMode);
    if (lastRankingData) renderRanking(lastRankingData);
  } catch {
    if (currentAudit) renderAudit({ ...currentAudit, status: "error", stale: true, errors: ["无法获取本地核验状态，请确认服务仍在运行。"] });
    $("#marketAuditStatus").textContent = "无法获取核验状态，请检查本地服务";
    if (lastData) renderResults(lastData, activeMode);
    if (lastRankingData) renderRanking(lastRankingData);
  } finally {
    auditBusy = false;
    const runQueuedRefresh = auditForceQueued;
    auditForceQueued = false;
    button.disabled = runQueuedRefresh;
    button.textContent = runQueuedRefresh ? "核验排队中" : "立即核验";
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
    // `market_trade_navigation()` exposes the final verified decision as
    // webTradeStatus; webListed is an internal directory field and is not
    // included in ranking rows.
    return items.filter(item => item?.webTradeStatus === "available");
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
    hint.textContent = selection.source === "ranking" ? "先更新涨跌幅排序" : "先完成一次筛选";
    return;
  }
  const label = selection.source === "ranking" ? `${selection.timeframe} 排序` : (selection.mode === "live" ? "实时筛选" : "收盘筛选");
  hint.textContent = `${selection.results.length} 个${label}结果可轮播`;
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
      state.childNodes[0].textContent = open ? "展开 " : "收起 ";
      $(".collapse-chevron", header).textContent = open ? "⌄" : "⌃";
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
      ? "最大距离 (ATR)" : signal === "support_hammer_rejection" ? "事件支撑质量下限"
        : signal === "resistance_inverted_hammer_rejection" ? "事件压力质量下限" : "质量分下限";
    for (const selector of [".left-period-field", ".condition-operator", ".condition-value-type", ".numeric-value-field", ".boolean-value-field", ".right-indicator-field", ".right-period-field"]) {
      $(selector, row).closest(".condition-field")?.classList.add("hidden");
    }
    const thresholdSummary = !needsThreshold ? ""
      : signal.endsWith("_near") ? `，最大距离 ${threshold.value || "未填写"} ATR`
        : signal === "support_hammer_rejection" ? `，事件支撑质量 ≥ ${threshold.value || "未填写"}`
          : signal === "resistance_inverted_hammer_rejection" ? `，事件压力质量 ≥ ${threshold.value || "未填写"}`
          : `，质量分 ≥ ${threshold.value || "未填写"}`;
    const lookbackSummary = isRejectionPattern
      ? `，回看最近 ${$(".condition-key-level-lookback", row).value} 根已收盘 K 线` : "";
    $(".condition-summary", row).textContent = `实际条件：关键位指标 · ${keyLevelSignalNames.get(signal) || signal}${thresholdSummary}${lookbackSummary}`;
    return;
  }
  for (const selector of [".condition-operator", ".condition-value-type", ".numeric-value-field", ".boolean-value-field", ".right-indicator-field", ".right-period-field"]) {
    $(selector, row).closest(".condition-field")?.classList.remove("hidden");
  }
  $(".left-period-field", row).classList.toggle("hidden", ["price", "change24h", "volume24h"].includes(indicator) || keyLevelMetrics.has(indicator));
  $(".numeric-value-field", row).classList.toggle("hidden", type !== "number" && !isProximity);
  $(".numeric-value-field", row).childNodes[0].textContent = isProximity ? "距离范围 (%)" : "数值";
  const valueInput = $(".condition-value", row);
  if (isProximity) {
    valueInput.min = "0";
    valueInput.max = "100";
    valueInput.placeholder = "例如 0.5";
  } else {
    valueInput.removeAttribute("min");
    valueInput.removeAttribute("max");
    valueInput.placeholder = "例如 50";
  }
  $(".boolean-value-field", row).classList.toggle("hidden", type !== "boolean");
  $(".right-indicator-field", row).classList.toggle("hidden", type !== "indicator");
  $(".right-period-field", row).classList.toggle("hidden", type !== "indicator");
  const names = {
    price: "最新价", change24h: "24h涨跌幅", volume24h: "24h成交额", ma: "MA", ema: "EMA", rsi: "RSI",
    bb_upper: "布林上轨", bb_middle: "布林中轨", bb_lower: "布林下轨", bb_width: "布林带宽",
    kl_support_exists: "存在支撑区域", kl_support_top: "支撑上沿", kl_support_bottom: "支撑下沿",
    kl_support_score: "支撑质量分", kl_support_touches: "支撑触碰次数", kl_support_confluence: "支撑合并次数",
    kl_support_htf: "支撑高周期共振", kl_support_retest_pending: "支撑等待回踩", kl_support_distance_atr: "距支撑 ATR",
    kl_resistance_exists: "存在压力区域", kl_resistance_top: "压力上沿", kl_resistance_bottom: "压力下沿",
    kl_resistance_score: "压力质量分", kl_resistance_touches: "压力触碰次数", kl_resistance_confluence: "压力合并次数",
    kl_resistance_htf: "压力高周期共振", kl_resistance_retest_pending: "压力等待回踩", kl_resistance_distance_atr: "距压力 ATR",
    kl_event_support_touch: "收盘触碰支撑", kl_event_support_hammer: "支撑锤型拒绝事件", kl_event_resistance_touch: "收盘触碰压力",
    kl_event_support_break: "收盘跌破支撑", kl_event_resistance_break: "收盘突破压力",
    kl_event_support_retest: "收盘确认支撑回踩", kl_event_resistance_retest: "收盘确认压力回踩",
    kl_previous_day_high: "前一日高点", kl_previous_day_low: "前一日低点",
    kl_previous_week_high: "前一周高点", kl_previous_week_low: "前一周低点",
    kl_day_open: "当日开盘价", kl_session_vwap: "会话 VWAP",
  };
  const operators = { ">": ">", ">=": "≥", "<": "<", "<=": "≤", "=": "=", "!=": "≠" };
  const noPeriod = ["price", "change24h", "volume24h"].includes(indicator) || keyLevelMetrics.has(indicator);
  const left = `${names[indicator] || indicator}${noPeriod ? "" : $(".condition-period", row).value}`;
  let right = type === "indicator"
    ? `${names[$(".condition-value-indicator", row).value] || $(".condition-value-indicator", row).value}${keyLevelMetrics.has($(".condition-value-indicator", row).value) ? "" : $(".condition-value-period", row).value}`
    : type === "boolean" ? $(".condition-boolean-value", row).selectedOptions[0].textContent : $(".condition-value", row).value;
  $(".condition-summary", row).textContent = isProximity
    ? `实际条件：${left} 距离 ${right || "未选择指标"} 不超过 ${valueInput.value || "未填写"}%`
    : `实际条件：${left} ${operators[operator.value] || operator.value} ${right || "未填写"}`;
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
        return `第 ${index + 1} 条距离条件需要选择最新价和价格尺度指标。`;
      }
      if (!condition.valueText || !Number.isFinite(condition.value) || condition.value < 0 || condition.value > 100) {
        return `第 ${index + 1} 条指标距离范围应在 0 到 100% 之间。`;
      }
      continue;
    }
    if (condition.indicator === "key_levels") {
      if (!keyLevelSignalNames.has(condition.keyLevelSignal)) return `第 ${index + 1} 条条件请选择关键位指标信号。`;
      if ((condition.keyLevelSignal.startsWith("high_quality_") || condition.keyLevelSignal.endsWith("_near")
          || ["support_hammer_rejection", "resistance_inverted_hammer_rejection"].includes(condition.keyLevelSignal))
          && (!condition.keyLevelThresholdText || !Number.isFinite(condition.keyLevelThreshold)
            || condition.keyLevelThreshold < 0 || condition.keyLevelThreshold > (condition.keyLevelSignal.endsWith("_near") ? 10 : 100))) {
        return `第 ${index + 1} 条关键位阈值超出范围。`;
      }
      if (["support_hammer_rejection", "resistance_inverted_hammer_rejection"].includes(condition.keyLevelSignal)
          && (!Number.isInteger(condition.keyLevelLookbackBars)
            || condition.keyLevelLookbackBars < 1 || condition.keyLevelLookbackBars > 100)) {
        return `第 ${index + 1} 条裸K形态回看根数应为 1 到 100 的整数。`;
      }
      continue;
    }
    if (keyLevelBooleanMetrics.has(condition.indicator)) {
      if (condition.valueType !== "boolean" || !["=", "!="].includes(condition.operator)) return `第 ${index + 1} 条关键位状态条件需要使用“状态”并选择等于或不等于。`;
      continue;
    }
    if (condition.valueType === "boolean") return `第 ${index + 1} 条条件需要先选择关键位状态指标。`;
    if (condition.valueType !== "indicator") continue;
    if (condition.indicator !== condition.valueIndicator || (!keyLevelMetrics.has(condition.indicator) && condition.period !== condition.valuePeriod)) continue;
    if ([">", "<", "!=", "=", ">=", "<="].includes(condition.operator)) {
      const names = { price: "最新价", ma: "MA", ema: "EMA", rsi: "RSI", bb_upper: "布林上轨", bb_middle: "布林中轨", bb_lower: "布林下轨", ...Object.fromEntries([...keyLevelMetrics].map(metric => [metric, metric])) };
      const label = `${names[condition.indicator] || condition.indicator}(${condition.period})`;
      if ([">", "<", "!="].includes(condition.operator)) return `第 ${index + 1} 条条件无效：${label} 与自身比较永远不会命中，请修改右侧指标周期。`;
      return `第 ${index + 1} 条条件恒为真：${label} 与自身相同，可以删除这条条件。`;
    }
  }
  return "";
}

function formatNumber(value, digits = 4) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: digits }).format(Number(value));
}

function formatVolume(value) {
  if (value === null || value === undefined) return "—";
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
  return `<button class="chart-open-button" type="button" data-inst-id="${escapeHtml(item.instId)}" data-symbol="${escapeHtml(item.symbol)}" data-chart-timeframe="${escapeHtml(timeframe || "1h")}" data-chart-source="${source}" title="查看 ${escapeHtml(item.symbol)} 本地 K 线与筛选指标">图表</button>`;
}

function renderKeyLevelSummary(levels) {
  const entries = Object.entries(levels || {});
  if (!entries.length) return "—";
  return `<div class="key-level-cell">${entries.map(([timeframe, values]) => {
    const support = Number(values.kl_support_exists) === 1
      ? `${formatNumber(values.kl_support_bottom, 8)} - ${formatNumber(values.kl_support_top, 8)} · ${formatNumber(values.kl_support_score, 0)}`
      : "—";
    const resistance = Number(values.kl_resistance_exists) === 1
      ? `${formatNumber(values.kl_resistance_bottom, 8)} - ${formatNumber(values.kl_resistance_top, 8)} · ${formatNumber(values.kl_resistance_score, 0)}`
      : "—";
    const refs = [
      ["昨高", values.kl_previous_day_high], ["昨低", values.kl_previous_day_low],
      ["周高", values.kl_previous_week_high], ["周低", values.kl_previous_week_low],
      ["开", values.kl_day_open], ["VWAP", values.kl_session_vwap],
    ].filter(([, value]) => value !== null && value !== undefined)
      .map(([label, value]) => `${label} ${formatNumber(value, 8)}`);
    return `<span><b>${timeframe}</b> 支 ${support} / 压 ${resistance}${refs.length ? `<small>${refs.join(" · ")}</small>` : ""}</span>`;
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
    note.textContent = auditUnavailable ? "官网清单更新中" : item.webCatalogStatus === "unavailable" ? "官网清单核验失败" : "官网直达未确认";
    note.title = auditUnavailable
      ? "保留最近一次已确认的筛选结果；官网清单核验完成前暂不开放直达。"
      : item.webTradeReason || "暂未确认官网入口，已暂停跳转，避免误入 BTCUSDT。请重新筛选以获取最新入口状态。";
    note.tabIndex = 0;
    wrapper.appendChild(note);
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "copy-trade-symbol";
    copy.dataset.symbol = symbol;
    copy.textContent = "复制名称";
    copy.title = `复制 ${symbol}，到 Deepcoin 官网搜索核对`;
    wrapper.appendChild(copy);
    return wrapper.outerHTML;
  }
  const link = document.createElement("a");
  link.className = "trade-pair-link";
  link.href = `/trade?${new URLSearchParams({ instId: item.instId })}`;
  link.dataset.tradeUrl = item.tradeUrl;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.title = `在新标签页打开 ${symbol} 的 Deepcoin 合约页面`;
  link.setAttribute("aria-label", `${symbol}，在新标签页打开 Deepcoin 合约页面`);
  const name = document.createElement("strong");
  name.textContent = symbol;
  const icon = document.createElement("span");
  icon.className = "trade-link-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = "↗";
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
    button.textContent = "已复制";
    setTimeout(() => { button.textContent = "复制名称"; }, 1800);
  } catch {
    window.prompt("请复制交易对名称，到 Deepcoin 官网搜索核对：", symbol);
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
  const directionLabel = data.direction === "asc" ? "跌幅" : "涨幅";
  const catalog = data.websiteCatalog || {};
  $("#rankMeta").textContent = `扫描 ${data.scanned}/${data.totalMarkets} 个官网合约${catalog.excludedMarkets ? ` · 官网清单排除 ${catalog.excludedMarkets}` : ""} · ${data.timeframe} · ${data.durationMs || 0}ms 更新`;
  if (catalog.errors?.length) {
    $("#message").textContent = `官网清单核验失败，已保留核验记录并暂停更新结果：${catalog.errors.join("；")}`;
    $("#message").classList.remove("hidden");
  }
  if (data.errors?.length) {
    $("#message").textContent = `部分涨跌幅数据获取失败：${data.errors.join("；")}`;
    $("#message").classList.remove("hidden");
  }
  if (!items.length) {
    const waiting = currentAudit && (currentAudit.stale || currentAudit.status !== "ready");
    body.innerHTML = `<tr class="empty-row"><td colspan="7"><div class="empty-state"><div class="empty-mark">∅</div><strong>${waiting ? "等待官网清单核验" : "没有可排序的行情数据"}</strong><span>${waiting ? "上次核验记录已保留，确认后恢复结果" : `失败 ${data.failed || 0} 个交易对`}</span></div></td></tr>`;
    return;
  }
  body.innerHTML = items.map((original, index) => {
    const item = { ...original, rank: index + 1 };
    const changeClass = item.changePercent >= 0 ? "positive" : "negative";
    const closeClass = item.closeChangePercent >= 0 ? "positive" : "negative";
    return `<tr><td class="rank-number">${item.rank}</td><td><div class="pair-actions">${renderTradeLink(item)}${renderChartButton(item, data.timeframe, "ranking")}</div><span class="pair-sub">${item.quote} 永续</span></td><td class="mono">${formatNumber(item.price, 8)}</td><td class="mono">${formatNumber(item.periodStartPrice, 8)}</td><td class="mono ${changeClass}">${item.changePercent >= 0 ? "+" : ""}${formatNumber(item.changePercent, 2)}%</td><td class="mono ${closeClass}">${item.closeChangePercent >= 0 ? "+" : ""}${formatNumber(item.closeChangePercent, 2)}%</td><td class="mono">${formatVolume(item.volume24h)}</td></tr>`;
  }).join("");
}

function scheduleRankingRefresh() {
  clearTimeout(rankingRefreshTimer);
  rankingRefreshTimer = null;
  rankingNextRefreshAt = 0;
  const seconds = Number($("#rankingRefreshSeconds").value) || 0;
  if (!seconds) {
    $("#rankAutoStatus").textContent = lastRankingData ? "自动重排已关闭" : "更新排序后按 60 秒自动重排";
    return;
  }
  if (!lastRankingData) {
    $("#rankAutoStatus").textContent = `首次更新排序后，每 ${seconds} 秒自动重排`;
    return;
  }
  rankingNextRefreshAt = Date.now() + seconds * 1000;
  $("#rankAutoStatus").textContent = `每 ${seconds} 秒自动重排，下次 ${new Date(rankingNextRefreshAt).toLocaleTimeString("zh-CN")}`;
  rankingRefreshTimer = setTimeout(() => runRanking({ automatic: true }), seconds * 1000);
}

async function runRanking({ automatic = false } = {}) {
  const button = $("#rankButton");
  if (rankingBusy) return;
  clearTimeout(rankingRefreshTimer);
  rankingRefreshTimer = null;
  rankingNextRefreshAt = 0;
  rankingBusy = true;
  if (automatic) $("#rankAutoStatus").textContent = "自动重排中，保留上一批结果";
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.textContent = "排序中";
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
    if (!response.ok) throw new Error(data.error || "涨跌幅排序失败");
    $("#message").classList.add("hidden");
    lastRankingData = data;
    lastRankingPayload = requestPayload;
    await loadMarketAudit();
    renderRanking(data);
    if (data.unavailableSymbols?.length) {
      $("#message").textContent = `${data.unavailableSymbols.join("、")} 未通过当前官网交易清单核验，请展开“核验明细”查看原因；未将它们当作条件不匹配。`;
      $("#message").classList.remove("hidden");
    }
  } catch (error) {
    $("#message").textContent = error.name === "TypeError" ? "无法连接本地筛选服务，请刷新页面。" : error.message;
    $("#message").classList.remove("hidden");
  } finally {
    rankingBusy = false;
    scheduleRankingRefresh();
    button.disabled = false;
    button.removeAttribute("aria-busy");
    button.textContent = "更新排序";
  }
}

function renderResults(data, mode = activeMode) {
  const results = verifiedRows(mode === "live" ? (data.liveResults || []) : (data.closeResults || data.results || []));
  updateCarouselHint();
  renderScreeningDiagnostics(data, mode);
  $("#resultCount").textContent = results.length;
  $("#priceHeader").textContent = mode === "live" ? "实时价" : "收盘价";
  const failedText = data.failedCandles ? ` · ${data.failedCandles} 组 K 线失败` : "";
  const performanceText = data.durationMs ? ` · ${data.durationMs}ms` : "";
  const catalog = data.websiteCatalog || {};
  const catalogText = catalog.excludedMarkets ? ` · 官网清单排除 ${catalog.excludedMarkets}` : "";
  $("#scanMeta").textContent = `扫描 ${data.scanned}/${data.totalMarkets} 个官网合约 · 周期 ${data.timeframes?.join(" / ") || data.timeframe}${catalogText}${failedText}${performanceText} · ${new Date(data.updatedAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })} 更新`;
  if (catalog.errors?.length) {
    $("#message").textContent = `官网清单核验失败，已保留核验记录并暂停更新结果：${catalog.errors.join("；")}`;
    $("#message").classList.remove("hidden");
  }
  if (data.candleErrors?.length) {
    $("#message").textContent = `部分 K 线获取失败：${data.candleErrors.join("；")}`;
    $("#message").classList.remove("hidden");
  }
  const body = $("#resultsBody");
  if (!results.length) {
    const waiting = currentAudit && (currentAudit.stale || currentAudit.status !== "ready");
    const diagnostic = data.screeningDiagnostics;
    const modeDiagnostic = diagnostic?.[mode];
    let hint = waiting ? "上次核验记录已保留，确认后恢复结果" : data.unavailableSymbols?.length ? "指定合约未通过官网核验，请展开顶部核验明细" : "可以放宽阈值，或增加扫描数量";
    if (!waiting && !data.unavailableSymbols?.length && diagnostic) {
      if (diagnostic.selectedMarkets > 0 && diagnostic.candleCandidates === 0) {
        hint = `${diagnostic.selectedMarkets} 个扫描对象全部被 24h 行情条件预筛排除`;
      } else if (modeDiagnostic?.missingData) {
        hint = `${modeDiagnostic.missingData} 个候选因必要行情或指标数据不足，无法判定；请展开扫描诊断`;
      } else if (modeDiagnostic?.conditionsNotMet) {
        hint = `${modeDiagnostic.conditionsNotMet} 个候选未通过全部条件；请展开扫描诊断查看各条件通过数`;
      } else if (diagnostic.selectedMarkets === 0) {
        hint = "当前扫描范围内没有可用合约";
      }
    }
    body.innerHTML = `<tr class="empty-row"><td colspan="9"><div class="empty-state"><div class="empty-mark">∅</div><strong>${waiting ? "等待官网清单核验" : "没有符合条件的交易对"}</strong><span>${hint}</span></div></td></tr>`;
    return;
  }
  body.innerHTML = results.map(item => {
    const changeClass = item.change24h >= 0 ? "positive" : "negative";
    const chartTimeframe = item.timeframes?.[0] || data.timeframe || "1h";
    return `<tr><td><div class="pair-actions">${renderTradeLink(item)}${renderChartButton(item, chartTimeframe, "screen")}</div><span class="pair-sub">${item.quote} 永续</span></td><td class="mono">${formatNumber(item.price, 8)}</td><td class="mono ${changeClass}">${item.change24h >= 0 ? "+" : ""}${formatNumber(item.change24h, 2)}%</td><td class="mono">${formatVolume(item.volume24h)}</td><td class="mono">${formatNumber(item.ema20, 8)}</td><td class="mono">${formatNumber(item.ma20, 8)}</td><td><span class="band-value">${formatNumber(item.bbLower, 6)} <i>—</i> ${formatNumber(item.bbUpper, 6)}</span><span class="pair-sub">宽度 ${formatNumber(item.bbWidth, 2)}%</span></td><td>${renderKeyLevelSummary(item.keyLevelsByTimeframe)}</td><td><span class="data-ok">${item.timeframes?.join(" / ") || "—"}</span><span class="pair-sub">${item.candles || 0} 根 K 线</span></td></tr>`;
  }).join("");
}

async function loadCarouselDisplays() {
  const select = $("#carouselDisplay");
  if (!select) return;
  try {
    const data = await requestJson("/api/displays", {}, { timeoutMs: 10000, retries: 1 });
    select.innerHTML = (data.displays || []).map(display =>
      `<option value="${display.index}">${escapeHtml(display.name || `显示器 ${display.index + 1}`)}${display.primary ? "（主）" : ""} · ${display.width || "窗口"}×${display.height || ""}</option>`
    ).join("");
    for (const [index, display] of (data.displays || []).entries()) {
      const option = select.options[index];
      if (option) option.textContent = `显示屏 ${Number(display.screenNumber) || Number(display.index) + 1}${display.primary ? "（主屏）" : ""} · ${display.width || "窗口"}×${display.height || ""}`;
    }
  } catch (error) {
    select.innerHTML = "<option value=\"0\">当前窗口（无法读取显示器）</option>";
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
  status.textContent = "正在准备轮播窗口…";
  try {
    const selection = carouselSelection();
    const { mode, results } = selection;
    if (!results.length) {
      const message = selection.source === "ranking"
        ? "当前没有通过官网合约自检的涨跌幅排行结果，请先更新排序并等待自检完成。"
        : "当前模式没有可轮播的筛选结果，请先完成筛选。";
      status.textContent = message;
      $("#message").textContent = message;
      $("#message").classList.remove("hidden");
      return;
    }
    const interval = Math.max(5, Math.min(3600, Number($("#carouselInterval").value) || 15));
    status.textContent = "正在启动轮播窗口…";
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
    status.textContent = `已启动 ${Number(data.startedCount) || results.length} 个交易对轮播（进程 ${data.pid}）${skipped ? `，已跳过 ${skipped} 个已下架合约` : ""}`;
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
    `扫描范围 ${selected} 个；24h 行情预筛排除 ${diagnostics.tickerFiltered || 0} 个；进入 K 线筛选 ${diagnostics.candleCandidates || 0} 个。` +
    `当前${mode === "live" ? "实时" : "收盘"}模式：通过 ${current.matched} 个、条件未满足 ${current.conditionsNotMet} 个、数据不足 ${current.missingData} 个。`;
  const conditionRows = (diagnostics.conditions || []).map(item => {
    const close = item.close || {};
    const live = item.live || {};
    return `<li>${escapeHtml(item.label)}：收盘通过 ${close.passed || 0} / 未满足 ${close.notMet || 0} / 缺数 ${close.missing || 0}；实时通过 ${live.passed || 0} / 未满足 ${live.notMet || 0} / 缺数 ${live.missing || 0}</li>`;
  });
  $("#screenDiagnosticsConditions").innerHTML = conditionRows.length
    ? conditionRows.join("")
    : "<li>未设置筛选条件</li>";
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
  ["support_exists", "存在支撑区域"], ["resistance_exists", "存在压力区域"],
  ["high_quality_support", "高质量支撑"], ["high_quality_resistance", "高质量压力"],
  ["support_hammer_rejection", "支撑锤型线拒绝"],
  ["resistance_inverted_hammer_rejection", "压力位长上影拒绝"],
  ["support_near", "价格接近支撑"], ["resistance_near", "价格接近压力"],
  ["support_touch", "收盘触碰支撑"], ["resistance_touch", "收盘触碰压力"],
  ["support_break", "收盘跌破支撑"], ["resistance_break", "收盘突破压力"],
  ["support_retest", "支撑回踩确认"], ["resistance_retest", "压力回踩确认"],
  ["price_above_vwap", "价格高于会话 VWAP"], ["price_below_vwap", "价格低于会话 VWAP"],
  ["price_above_previous_day_high", "价格高于前日高点"], ["price_below_previous_day_low", "价格低于前日低点"],
  ["price_above_previous_week_high", "价格高于前周高点"], ["price_below_previous_week_low", "价格低于前周低点"],
  ["price_above_day_open", "价格高于当日开盘"], ["price_below_day_open", "价格低于当日开盘"],
]);
const chartIndicatorNames = {
  price: "价格", change24h: "24h 涨跌幅", volume24h: "24h 成交额",
  ma: "MA", ema: "EMA", rsi: "RSI", bb_upper: "布林上轨", bb_middle: "布林中轨",
  bb_lower: "布林下轨", bb_width: "布林带宽", key_levels: "关键位指标",
};
const chartOperatorNames = { ">": ">", ">=": "≥", "<": "<", "<=": "≤", "=": "=", "!=": "≠" };
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
        throw new Error("服务返回了无法识别的数据，请刷新页面后重试。");
      }
      if (!response.ok) {
        const error = new Error(data.error || `请求失败（HTTP ${response.status}）`);
        error.retryable = [408, 429, 500, 502, 503, 504].includes(response.status);
        throw error;
      }
      return data;
    } catch (error) {
      lastError = error;
      const retryable = error.name === "TypeError" || error.retryable;
      if (!retryable || attempt >= retries) {
        if (error.name === "AbortError") {
          throw new Error(`请求等待超过 ${Math.round(timeoutMs / 1000)} 秒，服务可能仍在处理；可稍后查看结果或重试。`);
        }
        throw error;
      }
      await new Promise(resolve => setTimeout(resolve, 400 * (attempt + 1)));
    } finally {
      clearTimeout(timeout);
    }
  }
  throw lastError || new Error("请求失败");
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
      lastError = new Error(`请求失败（HTTP ${response.status}）`);
    } catch (error) {
      lastError = error;
      if (error.name === "AbortError") {
        throw new Error(`请求等待超过 ${Math.round(timeoutMs / 1000)} 秒，服务可能仍在处理；可稍后查看结果或重试。`);
      }
      if (error.name !== "TypeError") throw error;
      if (attempt >= retries) throw error;
    } finally {
      clearTimeout(timeout);
    }
    await new Promise(resolve => setTimeout(resolve, 400 * (attempt + 1)));
  }
  throw lastError || new Error("请求失败");
}

function chartIndicatorLabel(name, period) {
  const base = chartIndicatorNames[name] || name || "指标";
  return ["ma", "ema", "rsi", "bb_upper", "bb_middle", "bb_lower", "bb_width"].includes(name)
    ? `${base}${period || 20}` : base;
}

function chartConditionLabel(condition) {
  if (condition.indicator === "key_levels") {
    const signal = chartKeyLevelNames.get(condition.keyLevelSignal) || condition.keyLevelSignal;
    const threshold = condition.keyLevelThreshold;
    if (["support_hammer_rejection", "resistance_inverted_hammer_rejection"].includes(condition.keyLevelSignal)) {
      return `${signal} ≥ ${formatNumber(threshold, 1)} · 回看${condition.keyLevelLookbackBars || 24}根`;
    }
    if (condition.keyLevelSignal?.startsWith("high_quality_") && threshold !== undefined) return `${signal} ≥ ${formatNumber(threshold, 1)}`;
    if (condition.keyLevelSignal?.endsWith("_near") && threshold !== undefined) return `${signal} ≤ ${formatNumber(threshold, 2)} ATR`;
    return signal;
  }
  const left = chartIndicatorLabel(condition.indicator, condition.period);
  if (condition.valueType === "boolean") return `${left} ${chartOperatorNames[condition.operator] || condition.operator} ${condition.booleanValue ? "是" : "否"}`;
  const right = condition.valueType === "indicator"
    ? chartIndicatorLabel(condition.valueIndicator, condition.valuePeriod)
    : formatNumber(condition.value, 4);
  if (condition.operator === "within_percent") {
    return `${left} 距离 ${right} 不超过 ${formatNumber(condition.value, 2)}%`;
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
    label.textContent = "此圖表未附加篩選條件";
    target.appendChild(label);
    return;
  }
  rows.forEach(condition => {
    const chip = document.createElement("span");
    const pass = chartState.mode === "live" ? condition.livePass : condition.closePass;
    chip.className = `chart-condition-chip${condition.onChart ? (pass ? " is-pass" : " is-fail") : " is-muted"}`;
    chip.textContent = `${condition.timeframe} · ${chartConditionLabel(condition)}${condition.onChart ? ` · ${pass ? "符合" : "不符合"}` : " · 其他周期"}`;
    if (condition.onChart && condition.closeValue !== undefined) {
      chip.title = `收盘值 ${formatNumber(condition.closeValue, 8)} · 实时值 ${formatNumber(condition.liveValue, 8)}`;
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
  table.innerHTML = "<thead><tr><th>区域</th><th>价格范围</th><th>评分 / 触碰</th></tr></thead>";
  const body = document.createElement("tbody");
  const precision = data.pricePrecision ?? 8;
  for (const [kind, label, className] of [[-1, "压力", "resistance"], [1, "支撑", "support"]]) {
    const zone = nearest(kind);
    const row = document.createElement("tr");
    row.className = className;
    const region = document.createElement("th");
    region.textContent = zone?.awaitingRetest ? `${label}候选` : label;
    const range = document.createElement("td");
    range.textContent = zone ? `${formatNumber(zone.bottom, precision)} – ${formatNumber(zone.top, precision)}` : "—";
    const score = document.createElement("td");
    score.textContent = zone ? `${formatNumber(zone.score, 0)} / T${formatNumber(zone.touches, 0)}${zone.containsHtf ? " · HTF" : ""}` : "—";
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
  volumeEntry.append(volumeSwatch, document.createTextNode("成交量"));
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
    for (const [kind, label, color] of [[1, "支撑区域", "#26a69a"], [-1, "压力区域", "#ef5350"]]) {
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
    entry.textContent = "● 触碰　↓ / ↑ 突破或回踩";
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
  for (const [label, key] of [["开", "open"], ["高", "high"], ["低", "low"], ["收", "close"]]) {
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
  volumeName.textContent = "量";
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
    ["kl_previous_day_high", "前日高", "#8a9996"], ["kl_previous_day_low", "前日低", "#8a9996"],
    ["kl_previous_week_high", "前周高", "#a07e2e"], ["kl_previous_week_low", "前周低", "#a07e2e"],
    ["kl_day_open", "当日开盘", "#557eae"], ["kl_session_vwap", "会话 VWAP", "#865aa1"],
  ];
  for (const [key, title, color] of referenceLines) {
    const value = Number(features?.[key]);
    if (!Number.isFinite(value)) continue;
    candles.createPriceLine({ price: value, color, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true, title });
  }
  const livePrice = Number(data.livePrice);
  if (Number.isFinite(livePrice)) {
    candles.createPriceLine({ price: livePrice, color: "#f5a623", lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: "实时价" });
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
    if (!response.ok) throw new Error(data.error || "本地图表加载失败");
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
    $("#chartSubtitle").textContent = `${data.timeframe} · ${data.candles.length} 根 K 线 · ${chartState.mode === "live" ? "实时价格" : "收盘数据"}`;
    $("#chartQuote").textContent = `实时 ${formatNumber(data.livePrice, data.pricePrecision)}　·　收盘 ${formatNumber(data.closePrice, data.pricePrecision)}`;
    $("#chartTradeLink").href = `/trade?${new URLSearchParams({ instId: data.instId })}`;
    document.querySelectorAll("[data-chart-mode]").forEach(button => button.classList.toggle("active", button.dataset.chartMode === chartState.mode));
    updateChartReadout(data);
    updateChartLevelReadout(data);
    drawChart(data, preserveRange);
  } catch (error) {
    if (priceChart) {
      $("#chartStatus").textContent = `行情暂未更新 · ${error.message}`;
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
    symbol: button.dataset.symbol || "交易对",
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
  $("#chartTitle").textContent = query.get("symbol") || "交易对";
  $("#chartSubtitle").textContent = "正在加载行情…";
  $("#chartQuote").textContent = "";
  $("#chartStatus").classList.add("hidden");
  $("#chartMessage").classList.add("hidden");
  $("#chartTradeLink").href = `/trade?${new URLSearchParams({ instId: chartState.instId })}`;
  document.title = `${query.get("symbol") || "交易对"} · Deepcoin 图表`;
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
    previousButton.textContent = "上一个";
    nextButton.parentElement.insertBefore(previousButton, nextButton);
  }
  const startWithQuery = resolvedQuery => {
    carouselItems = (resolvedQuery.get("instIds") || "").split(",").map(item => item.trim()).filter(Boolean);
    if (!carouselItems.length) {
      $("#chartMessage").textContent = "没有可轮播的交易对";
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
    $("#chartMessage").textContent = "没有可轮播的交易对";
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
    $("#carouselRankStatus").textContent = `榜单 ${carouselRankConfig.timeframe} · ${rankRefreshSeconds} 秒重排`;
  } else {
    carouselRankConfig = null;
    $("#carouselRankStatus").textContent = "固定列表";
  }
  $("#chartTimeframe").value = chartState.timeframe;
  $("#chartTradeLink").href = `/trade?${new URLSearchParams({ instId: chartState.instId })}`;
  $("#chartDialog").showModal();
  $("#carouselOverlay").classList.remove("hidden");
  $("#carouselPosition").textContent = `轮播 1 / ${carouselItems.length}`;
  document.title = "Deepcoin 图表轮播";
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
  $("#carouselRankStatus").textContent = "榜单更新中";
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
    if (!response.ok) throw new Error(data.error || "涨跌幅榜单更新失败");
    const nextItems = (data.results || []).map(item => item.instId).filter(Boolean);
    if (!nextItems.length) throw new Error("本次没有可用榜单，继续使用上一批交易对");
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
    $("#carouselPosition").textContent = `轮播 ${carouselIndex + 1} / ${carouselItems.length}`;
    $("#carouselRankStatus").textContent = `榜单已更新 ${new Date(data.updatedAt || Date.now()).toLocaleTimeString("zh-CN")}`;
  } catch (error) {
    $("#carouselRankStatus").textContent = `沿用上次榜单 · ${error.message}`;
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
    $("#carouselPosition").textContent = `轮播 ${carouselIndex + 1} / ${carouselItems.length}`;
    await refreshChart();
    scheduleNextCarousel();
  }, carouselIntervalMs);
}

async function moveCarousel(step) {
  if (!carouselItems.length || !chartState) return;
  clearTimeout(carouselTimer);
  carouselIndex = (carouselIndex + step + carouselItems.length) % carouselItems.length;
  chartState.instId = carouselItems[carouselIndex];
  $("#carouselPosition").textContent = `轮播 ${carouselIndex + 1} / ${carouselItems.length}`;
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
    button.innerHTML = `<span class="spinner"></span> 正在扫描行情`;
    button.title = "正在获取行情并逐项检查筛选条件";
    message.classList.add("hidden");
  } else {
    button.innerHTML = `<span class="spinner"></span> 自动重筛中`;
    button.title = "自动筛选正在获取行情并检查条件";
  }
  try {
    const response = await requestWithRetry("/api/screen", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    }, { timeoutMs: 300000, retries: 1 });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "筛选失败");
    message.classList.add("hidden");
    lastData = data;
    renderResults(data, activeMode);
    void loadMarketAudit();
    if (data.unavailableSymbols?.length) {
      message.textContent = `${data.unavailableSymbols.join("、")} 未通过当前官网交易清单核验，请展开“核验明细”查看原因；未将它们当作条件不匹配。`;
      message.classList.remove("hidden");
    }
  } catch (error) {
    message.textContent = error.name === "TypeError" ? "无法连接本地筛选服务，请确认 http://127.0.0.1:8000 仍在运行后刷新页面。" : error.message;
    message.classList.remove("hidden");
  } finally {
    screenBusy = false;
    button.disabled = false;
    refreshButton.disabled = false;
    button.removeAttribute("aria-busy");
    button.innerHTML = `<span class="button-icon">⌕</span>开始筛选`;
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
    if (!response.ok) throw new Error("本地服务响应异常");
    const health = await response.json();
    const connected = health.exchangeConnected === true;
    status.classList.toggle("has-error", !connected && health.marketDirectoryStatus === "error");
    $("#healthText").textContent = connected
      ? "Deepcoin 已连接"
      : health.marketDirectoryStatus === "error" ? "Deepcoin 接口异常" : "正在连接 Deepcoin";
    status.title = connected ? "Deepcoin 行情与官网合约清单已核验" : (health.marketDirectoryError || "正在首次核验 Deepcoin 合约清单");
  } catch (error) {
    status.classList.add("has-error");
    $("#healthText").textContent = "本地服务异常";
    status.title = error.message || "无法连接本地筛选服务";
  }
}

async function checkForUpdates() {
  const button = $("#updateCheckButton");
  const status = $("#updateStatus");
  if (!button || !status || updateBusy) return;
  updateBusy = true;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  status.textContent = "正在检查…";
  try {
    const data = await requestJson("/api/update/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }, { timeoutMs: 20000, retries: 0 });
    const current = data.currentVersion || "未知";
    const latest = data.latestVersion || current;
    if (!data.updateAvailable) {
      status.textContent = `当前版本 ${current}，已是最新`;
    } else if (data.scheduled && data.updateStatus === "downloading") {
      status.textContent = `发现 ${latest}，已开始后台下载校验`;
    } else if (data.scheduled) {
      status.textContent = `发现 ${latest}，正在重启更新`;
    } else if (data.packaged) {
      status.textContent = `发现 ${latest}，更新未能安排`;
    } else {
      status.textContent = `发现 ${latest}；源码运行请使用发布包更新`;
    }
  } catch (error) {
    status.textContent = `检查失败：${error.message}`;
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
  event.currentTarget.textContent = carouselPaused ? "继续" : "暂停";
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
  $("#carouselPosition").textContent = `轮播 ${carouselIndex + 1} / ${carouselItems.length}`;
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
    $("#chartSubtitle").textContent = `${chartState.timeframe} · ${chartData.candles.length} 根 K 线 · ${chartState.mode === "live" ? "实时价格" : "收盘数据"}`;
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
