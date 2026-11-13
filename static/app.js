const $ = (selector, root = document) => root.querySelector(selector);
const conditions = $("#conditions");
const template = $("#conditionTemplate");
let lastPayload = null;
let lastData = null;
let activeMode = "close";
let screenBusy = false;
let currentAudit = null;
let lastRankingData = null;
let auditBusy = false;

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
    ? `核验${data.status === "error" ? "失败，15 秒后自动重试" : "更新中"} · 上次成功 ${checked} · 暂停更新结果及跳转`
    : `已确认 ${data.availableMarkets || 0} 个 · 官网未列出 ${data.excludedMarkets || 0} 个 · 待核验 ${data.pendingMarkets || 0} 个 · ${checked}${data.refreshing ? " · 正在更新" : ""}`;
  $("#marketAuditSummary").textContent = `核验明细与最近变更${data.pendingMarkets ? `（${data.pendingMarkets} 个待核验）` : ""}`;
  $("#marketAuditHint").textContent = data.errors?.length ? data.errors.join("；") : "后台每 60 秒核对；异常或清单不同步时每 15 秒重试。官网未列出不等同永久下架，后续确认后会自动恢复。";
  const change = data.changes?.at(-1);
  $("#marketAuditChanges").textContent = change ? (change.initial ? `本次启动初始化确认 ${change.added.length} 个合约。` : `最近范围变更：新增确认 ${change.added.length} 个${change.added.length ? `（${change.added.slice(0, 8).join("、")}）` : ""}；移出确认范围 ${change.removed.length} 个${change.removed.length ? `（${change.removed.slice(0, 8).join("、")}）` : ""}。`) : "等待首次核验。";
  renderAuditEntries();
}

async function loadMarketAudit(force = false) {
  if (auditBusy) return;
  auditBusy = true;
  const button = $("#auditRefresh");
  button.disabled = true;
  if (force) button.textContent = "核验中…";
  try {
    const response = await fetch("/api/market-audit", { method: force ? "POST" : "GET" });
    const data = await response.json();
    const previousCheck = currentAudit?.checkedAt;
    renderAudit(data);
    if (lastData) renderResults(lastData, activeMode);
    if (lastRankingData) renderRanking(lastRankingData);
    if (data.status === "ready" && !data.stale && previousCheck && previousCheck !== data.checkedAt) {
      if (lastData && $("#autoRefresh").checked && !screenBusy) runScreen({ silent: true });
      if (lastRankingData && !$("#rankButton").disabled) runRanking();
    }
  } catch {
    if (currentAudit) renderAudit({ ...currentAudit, status: "error", stale: true, errors: ["无法获取本地核验状态，请确认服务仍在运行。"] });
    $("#marketAuditStatus").textContent = "无法获取核验状态，请检查本地服务";
    if (lastData) renderResults(lastData, activeMode);
    if (lastRankingData) renderRanking(lastRankingData);
  } finally {
    auditBusy = false;
    button.disabled = false;
    button.textContent = "立即核验";
  }
}

function verifiedRows(items) {
  if (!currentAudit) return items;
  if (currentAudit.stale || currentAudit.status !== "ready") return [];
  const names = new Set(currentAudit.verifiedSymbols || []);
  return items.filter(item => names.has(item.symbol));
}

function addCondition(defaults = {}) {
  const row = template.content.firstElementChild.cloneNode(true);
  $(".condition-indicator", row).value = defaults.indicator || "ema";
  $(".condition-timeframe", row).value = defaults.timeframe || $("#timeframe").value;
  $(".condition-operator", row).value = defaults.operator || ">";
  $(".condition-value-type", row).value = defaults.valueType || "number";
  $(".condition-value", row).value = defaults.value ?? "";
  $(".condition-period", row).value = defaults.period || 20;
  $(".condition-value-indicator", row).value = defaults.valueIndicator || "ema";
  $(".condition-value-period", row).value = defaults.valuePeriod || 60;
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
  const indicator = $(".condition-indicator", row).value;
  const type = $(".condition-value-type", row).value;
  $(".left-period-field", row).classList.toggle("hidden", ["price", "change24h", "volume24h"].includes(indicator));
  $(".numeric-value-field", row).classList.toggle("hidden", type === "indicator");
  $(".right-indicator-field", row).classList.toggle("hidden", type !== "indicator");
  $(".right-period-field", row).classList.toggle("hidden", type !== "indicator");
  const names = { price: "最新价", change24h: "24h涨跌幅", volume24h: "24h成交额", ma: "MA", ema: "EMA", rsi: "RSI", bb_upper: "布林上轨", bb_middle: "布林中轨", bb_lower: "布林下轨", bb_width: "布林带宽" };
  const operators = { ">": ">", ">=": "≥", "<": "<", "<=": "≤", "=": "=", "!=": "≠" };
  const left = `${names[indicator] || indicator}${["price", "change24h", "volume24h"].includes(indicator) ? "" : $(".condition-period", row).value}`;
  let right = type === "indicator" ? `${names[$(".condition-value-indicator", row).value] || $(".condition-value-indicator", row).value}${$(".condition-value-period", row).value}` : $(".condition-value", row).value;
  $(".condition-summary", row).textContent = `实际条件：${left} ${operators[$(".condition-operator", row).value] || $(".condition-operator", row).value} ${right || "未填写"}`;
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
    valueIndicator: $(".condition-value-indicator", row).value,
    valuePeriod: Number($(".condition-value-period", row).value) || 60,
  }));
}

function validateClientConditions(conditionList) {
  for (let index = 0; index < conditionList.length; index += 1) {
    const condition = conditionList[index];
    if (condition.valueType !== "indicator") continue;
    if (condition.indicator !== condition.valueIndicator || condition.period !== condition.valuePeriod) continue;
    if ([">", "<", "!=", "=", ">=", "<="].includes(condition.operator)) {
      const names = { price: "最新价", ma: "MA", ema: "EMA", rsi: "RSI", bb_upper: "布林上轨", bb_middle: "布林中轨", bb_lower: "布林下轨" };
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

function renderTradeLink(item) {
  const symbol = String(item.symbol || "").trim().toUpperCase();
  const expectedUrl = `https://www.deepcoin.com/swap/zh/${encodeURIComponent(item.webSymbol || symbol)}`;
  if (item.webTradeStatus !== "available" || item.tradeUrl !== expectedUrl) {
    const wrapper = document.createElement("span");
    wrapper.className = "trade-pair-unavailable";
    const name = document.createElement("strong");
    name.textContent = symbol;
    wrapper.appendChild(name);
    const note = document.createElement("span");
    note.className = "trade-link-note";
    note.textContent = item.webCatalogStatus === "unavailable" ? "官网清单核验失败" : "官网直达未确认";
    note.title = item.webTradeReason || "暂未确认官网入口，已暂停跳转，避免误入 BTCUSDT。请重新筛选以获取最新入口状态。";
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
  const items = verifiedRows(data.results || []);
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
    return `<tr><td class="rank-number">${item.rank}</td><td>${renderTradeLink(item)}<span class="pair-sub">${item.quote} 永续</span></td><td class="mono">${formatNumber(item.price, 8)}</td><td class="mono">${formatNumber(item.periodStartPrice, 8)}</td><td class="mono ${changeClass}">${item.changePercent >= 0 ? "+" : ""}${formatNumber(item.changePercent, 2)}%</td><td class="mono ${closeClass}">${item.closeChangePercent >= 0 ? "+" : ""}${formatNumber(item.closeChangePercent, 2)}%</td><td class="mono">${formatVolume(item.volume24h)}</td></tr>`;
  }).join("");
}

async function runRanking() {
  const button = $("#rankButton");
  button.disabled = true;
  button.textContent = "排序中";
  try {
    const query = new URLSearchParams({ quote: $("#quote").value, timeframe: $("#rankingTimeframe").value, direction: $("#rankingDirection").value, maxSymbols: String(Number($("#maxSymbols").value) || 100), symbols: $("#symbols").value });
    const response = await fetch(`/api/price-change?${query.toString()}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "涨跌幅排序失败");
    $("#message").classList.add("hidden");
    lastRankingData = data;
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
    button.disabled = false;
    button.textContent = "更新排序";
  }
}

function renderResults(data, mode = activeMode) {
  const results = verifiedRows(mode === "live" ? (data.liveResults || []) : (data.closeResults || data.results || []));
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
    const hint = waiting ? "上次核验记录已保留，确认后恢复结果" : data.unavailableSymbols?.length ? "指定合约未通过官网核验，请展开顶部核验明细" : "可以放宽阈值，或增加扫描数量";
    body.innerHTML = `<tr class="empty-row"><td colspan="8"><div class="empty-state"><div class="empty-mark">∅</div><strong>${waiting ? "等待官网清单核验" : "没有符合条件的交易对"}</strong><span>${hint}</span></div></td></tr>`;
    return;
  }
  body.innerHTML = results.map(item => {
    const changeClass = item.change24h >= 0 ? "positive" : "negative";
    return `<tr><td>${renderTradeLink(item)}<span class="pair-sub">${item.quote} 永续</span></td><td class="mono">${formatNumber(item.price, 8)}</td><td class="mono ${changeClass}">${item.change24h >= 0 ? "+" : ""}${formatNumber(item.change24h, 2)}%</td><td class="mono">${formatVolume(item.volume24h)}</td><td class="mono">${formatNumber(item.ema20, 8)}</td><td class="mono">${formatNumber(item.ma20, 8)}</td><td><span class="band-value">${formatNumber(item.bbLower, 6)} <i>—</i> ${formatNumber(item.bbUpper, 6)}</span><span class="pair-sub">宽度 ${formatNumber(item.bbWidth, 2)}%</span></td><td><span class="data-ok">${item.timeframes?.join(" / ") || "—"}</span><span class="pair-sub">${item.candles || 0} 根 K 线</span></td></tr>`;
  }).join("");
}

async function runScreen({ silent = false } = {}) {
  if (screenBusy) return;
  const button = $("#screenButton");
  const message = $("#message");
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
  if (!silent) {
    button.disabled = true;
    button.innerHTML = `<span class="spinner"></span> 正在扫描行情`;
    message.classList.add("hidden");
  }
  try {
    const response = await fetch("/api/screen", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "筛选失败");
    message.classList.add("hidden");
    lastData = data;
    await loadMarketAudit();
    renderResults(data, activeMode);
    if (data.unavailableSymbols?.length) {
      message.textContent = `${data.unavailableSymbols.join("、")} 未通过当前官网交易清单核验，请展开“核验明细”查看原因；未将它们当作条件不匹配。`;
      message.classList.remove("hidden");
    }
  } catch (error) {
    message.textContent = error.name === "TypeError" ? "无法连接本地筛选服务，请确认 http://127.0.0.1:8000 仍在运行后刷新页面。" : error.message;
    message.classList.remove("hidden");
  } finally {
    screenBusy = false;
    if (!silent) {
      button.disabled = false;
      button.innerHTML = `<span class="button-icon">⌕</span>开始筛选`;
    }
  }
}

function setMode(mode) {
  activeMode = mode;
  document.querySelectorAll(".result-tab").forEach(button => button.classList.toggle("active", button.dataset.mode === mode));
  if (lastData) renderResults(lastData, mode);
}

async function checkHealth() {
  try { const response = await fetch("/api/health"); if (!response.ok) throw new Error(); $("#healthText").textContent = "Deepcoin 已连接"; } catch { $("#healthText").textContent = "本地服务已启动"; }
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
    rsi: [
      { timeframe, indicator: "rsi", period: 14, operator: ">", valueType: "number", value: 50 },
    ],
    band: [
      { timeframe, indicator: "price", period: 20, operator: "<", valueType: "indicator", valueIndicator: "bb_lower", valuePeriod: 20 },
    ],
  };
  conditions.innerHTML = "";
  (presets[button.dataset.preset] || []).forEach(addCondition);
}));
$("#screenButton").addEventListener("click", runScreen);
$("#refreshButton").addEventListener("click", runScreen);
$("#rankButton").addEventListener("click", runRanking);
$("#auditRefresh").addEventListener("click", () => loadMarketAudit(true));
$("#marketAuditSearch").addEventListener("input", renderAuditEntries);
$("#timeframe").addEventListener("change", () => {
  document.querySelectorAll(".condition-timeframe").forEach(select => { select.value = $("#timeframe").value; });
});
document.querySelectorAll(".result-tab").forEach(button => button.addEventListener("click", () => setMode(button.dataset.mode)));
addCondition({ timeframe: "1h", indicator: "price", operator: ">", valueType: "number", value: 0 });
setupCollapsiblePanels();
checkHealth();
loadMarketAudit();
setInterval(loadMarketAudit, 5000);
setInterval(() => {
  if ($("#autoRefresh").checked && lastPayload && !screenBusy) runScreen({ silent: true });
}, 10000);
