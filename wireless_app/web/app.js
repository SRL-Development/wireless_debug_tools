/* WirelessNetOptimizer - 前端逻辑（无外部依赖） */
(function () {
"use strict";
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const W = 1000, H = 700;
let CAT = { records: [], stats: {} }, STATS = {};
let TOAST_T = null;
const CH_24 = [1, 6, 11], CH_5 = [36, 44, 149, 157];
let LIVE_STATE = { clients: [], events: [], interferers: [], kpi: {}, aps: [], paused: false, step: 0, ts: "", ssid_stats: [] };
let IF_STATE = { spectrum_24: [], spectrum_5: [], conflicts: [], channel_plan: [], interferers: [], summary: {}, library: [] };
let LIVE_TIMER = null, LIVE_SEL = null;
const LIVE_FILTER = { q: "", status: "", ap: "", band: "", ssid: "" };
let SSID_STATE = { ssids: [], active_id: "", active: null };
let SSID_EDIT_ID = "", SSID_STATS = [], AUTO_TIMER = null;

function esc(s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
function toast(msg, ms) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("on");
  clearTimeout(TOAST_T); TOAST_T = setTimeout(() => t.classList.remove("on"), ms || 2600);
}
async function api(path, body) {
  const o = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, o);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
  return j;
}
function go(p) {
  $$("nav button").forEach(b => b.classList.toggle("on", b.dataset.page === p));
  $$(".page").forEach(s => s.classList.toggle("on", s.id === "page-" + p));
  window.scrollTo(0, 0);
  if (p === "heat") resizeStage();
  if (p === "roam" && !ROAM.data) {
    api("/api/roam/state").then(r => { renderRoam(r); if (r.running) { startRoamTimer(); syncRoamButtons(true); } }).catch(() => {});
  }
  if (p === "survey") {
    if (!SURVEY_STATE.data) svLoad();
    if (HS.floor === "sketch") skSyncToSurvey(false);   // 草图改动同步给勘测打点画布
  }
  if (p === "audit" && !AUDIT_STATE.data) audLoad(false);
  if (p === "local" && !LOCAL_STATE.init) { LOCAL_STATE.init = true; initLocalDiag(); }
  if (p === "air" && !AIR_STATE.last) {
    api("/api/air/last").then(r => {
      if (r && r.ok) { AIR_STATE.last = r; renderAir(r);
        $("#airstatus").innerHTML = '<span class="muted">已载入上次扫描结果（' + (r.age_sec || 0) + ' 秒前）。点「开始扫描」重新采集。</span>'; }
    }).catch(() => {});
  }
}
$$("nav button").forEach(b => b.addEventListener("click", () => go(b.dataset.page)));

/* ====================================================================
   SSID（全局分析维度）
==================================================================== */
function activeSSID() {
  return SSID_STATE.active || SSID_STATE.ssids.find(s => s.id === SSID_STATE.active_id) || null;
}
function ssidEffBand() {
  const s = activeSSID();
  return s && ["2.4", "5", "6"].indexOf(s.band) >= 0 ? s.band : HS.band;
}
function apInSSID(a) {
  const s = activeSSID();
  if (!s) return true;
  if (s.enabled === false) return false;
  const ids = s.ap_ids || [];
  return !ids.length || ids.map(String).indexOf(String(a.id)) >= 0;
}
function renderGlobalSSID() {
  const sel = $("#globals");
  sel.innerHTML = (SSID_STATE.ssids || []).map(s =>
    '<option value="' + esc(s.id) + '"' + (s.id === SSID_STATE.active_id ? " selected" : "") + '>' +
    esc(s.name) + (s.source === "local" ? "（本机）" : "") +
    (s.enabled === false ? "（停用）" : "") + '</option>').join("");
}
async function setActiveSSID(id) {
  try {
    SSID_STATE = await api("/api/ssid/select", { id });
    renderGlobalSSID(); renderSSIDPage(); applySSIDToUI(false);
    const s = activeSSID();
    toast("已切换分析对象：" + (s ? s.name : "-") + "（" + (s ? s.band : "-") + "）");
  } catch (e) { toast("切换 SSID 失败：" + e.message); renderGlobalSSID(); }
}
function applySSIDToUI(skipHeat) {
  const s = activeSSID(), nm = s ? s.name : "全部 SSID";
  const locked = s && ["2.4", "5", "6"].indexOf(s.band) >= 0 ? s.band : "";
  const hb = $("#hband");
  if (hb) {
    if (locked) { hb.value = locked; HS.band = locked; }
    else { HS.band = HS.userBand || "2.4"; hb.value = HS.band; }
    hb.disabled = !!locked;
    hb.title = locked ? "该 SSID 锁定 " + locked + " GHz，如需更改请在 SSID 配置页调整" : "";
  }
  $("#heatssid").textContent = nm;
  $("#ifssidpill").textContent = "SSID " + nm;
  $("#diagssidpill").textContent = "SSID " + nm;
  $("#hbandhint").textContent = locked ? "（已由 SSID 锁定 " + locked + " GHz）" : "（由当前选择决定）";
  const lf = $("#livessid"), want = (SSID_STATE.ssids || []).map(x => x.name);
  if (lf.options.length - 1 !== want.length) {
    lf.innerHTML = '<option value="">全部 SSID</option>' + want.map(v => '<option value="' + esc(v) + '">' + esc(v) + '</option>').join("");
  }
  LIVE_FILTER.ssid = s ? s.name : "";
  lf.value = LIVE_FILTER.ssid;
  renderAPList(); renderLive(); renderSSIDStats();
  refreshInterference();
  // 切换 SSID 是全局维度变更：无论「自动重算」开关状态，都立即按新 AP 集合 / 频段刷新热力图
  if (!skipHeat) runHeat();
}
async function initSSID() {
  try { SSID_STATE = await api("/api/ssid/list"); }
  catch (e) { toast("SSID 配置加载失败：" + e.message); }
  renderGlobalSSID();
  $("#globals").addEventListener("change", e => setActiveSSID(e.target.value));
  $("#gssidmanage").addEventListener("click", () => { go("ssid"); renderSSIDPage(); });
  $("#heatssidbtn").addEventListener("click", () => { go("ssid"); renderSSIDPage(); });
  $("#ssidnew").addEventListener("click", newSSIDForm);
  $("#ssidcancel").addEventListener("click", newSSIDForm);
  $("#ssidsave").addEventListener("click", saveSSIDForm);
  $("#ssiddelete").addEventListener("click", deleteSSIDForm);
  $("#ssidreset").addEventListener("click", async () => {
    if (!confirm("恢复默认 SSID 配置？当前自定义配置将被覆盖。")) return;
    try {
      SSID_STATE = await api("/api/ssid/reset", {});
      renderGlobalSSID(); renderSSIDPage(); newSSIDForm(); applySSIDToUI(false);
      toast("已恢复默认 SSID 配置");
    } catch (e) { toast("恢复失败：" + e.message); }
  });
  newSSIDForm();
  renderSSIDPage();
  applySSIDToUI(true);
}

/* --------------------------------------------------------------------
   本机无线连接：Windows netsh 只读，断网可用，自动把当前 SSID 设为分析对象
-------------------------------------------------------------------- */
let WLAN_INFO = null, WLAN_BOUND = false;
function wlanCell(k, v) {
  return '<div class="metric"><span class="muted">' + esc(k) + '</span><b>' +
    esc(v == null || v === "" ? "-" : v) + '</b></div>';
}
function renderLocalWlan(info) {
  WLAN_INFO = info || null;
  const c = info && info.connected;
  $("#wlanauto").checked = SSID_STATE.auto_local !== false;
  $("#wlanprofiles").innerHTML = '<option value="">选择一个已保存配置…</option>' +
    ((info && info.profiles) || []).map(n => '<option value="' + esc(n) + '">' + esc(n) + '</option>').join("");
  if (c) {
    $("#wlanstate").innerHTML = '<span class="tag t-ok">已连接</span> ' + esc(info.checked_at || "");
    $("#wlaninfo").innerHTML =
      wlanCell("SSID", c.ssid) +
      wlanCell("生效频段", c.band === "双频" ? (c.band_text || "双频") : c.band + " GHz") +
      wlanCell("信道", c.channel == null ? "-" : c.channel) +
      wlanCell("信号", (c.signal_pct == null ? "-" : c.signal_pct + "%") +
        (c.rssi_dbm != null ? " / " + c.rssi_dbm + " dBm" : "")) +
      wlanCell("BSSID", c.bssid) +
      wlanCell("无线电类型", c.radio) +
      wlanCell("认证 / 加密", (c.auth || "-") + (c.cipher ? " / " + c.cipher : "")) +
      wlanCell("网卡 / 速率", (c.description || c.name || "-") +
        (c.rx_rate ? " · " + c.rx_rate + " Mbps" : ""));
    $("#wlanhint").textContent = "本机当前连接 " + c.ssid + "；点击「用本机当前 SSID 分析」即可切换。";
  } else {
    $("#wlanstate").innerHTML = '<span class="tag t-pend">未连接</span>';
    $("#wlaninfo").innerHTML =
      wlanCell("状态", (info && info.error) || "本机未连接 Wi-Fi") +
      wlanCell("提示", "可从下方已保存配置里选一个 SSID");
    $("#wlanhint").textContent = (info && info.error) || "未检测到无线连接，已保留当前分析对象。";
  }
}
async function useLocalSSID(name, quiet) {
  try {
    const r = await api("/api/wlan/use", name ? { name: name } : {});
    SSID_STATE = r.state;
    renderGlobalSSID(); renderSSIDPage(); newSSIDForm(); applySSIDToUI(false);
    // 现场终端池在 initLive() 时按当时的 SSID 列表生成；采用本机 SSID 后需要重建，
    // 否则「现场连接」按新 SSID 筛选会是一张空表。
    try {
      await api("/api/live/config", { aps: HS.aps, ppm: HS.ppm, n_clients: 36 });
      await refreshLive();
      await refreshInterference();
    } catch (e) { /* 现场引擎未就绪时由定时器自动补齐 */ }
    toast((quiet ? "已自动采用本机当前 SSID：" : "已采用本机 SSID：") +
      r.ssid.name + "（" + fmtBand(r.ssid.band) + "）", quiet ? 4200 : 2600);
    $("#wlanhint").textContent = "当前分析对象：" + r.ssid.name + "（来源：本机 netsh 只读）";
    return true;
  } catch (e) {
    if (!quiet) toast("采用失败：" + e.message);
    $("#wlanhint").textContent = e.message;
    return false;
  }
}
async function initLocalWlan(auto) {
  if (!WLAN_BOUND) {
    WLAN_BOUND = true;
    $("#wlanrefresh").addEventListener("click", () => initLocalWlan(false));
    $("#wlanuse").addEventListener("click", () => useLocalSSID(""));
    $("#wlanaddprofile").addEventListener("click", () => {
      const n = $("#wlanprofiles").value;
      if (!n) { toast("请先选择一个已保存配置"); return; }
      useLocalSSID(n);
    });
    $("#wlanauto").addEventListener("change", async e => {
      try { SSID_STATE = await api("/api/wlan/auto", { enabled: e.target.checked }); }
      catch (x) { e.target.checked = !e.target.checked; toast("设置失败：" + x.message); }
    });
  }
  let info;
  try { info = await api("/api/wlan/local"); }
  catch (e) { info = { error: e.message, profiles: [], interfaces: [], connected: null }; }
  renderLocalWlan(info);
  if (auto && info.connected && info.connected.ssid && SSID_STATE.auto_local !== false) {
    const cur = activeSSID();
    if (!cur || cur.name !== info.connected.ssid || cur.source !== "local") {
      await useLocalSSID(info.connected.ssid, true);
    } else {
      $("#wlanhint").textContent = "当前分析对象已是本机 SSID " + cur.name + "。";
    }
  }
}
function fmtBand(b) { return ["2.4", "5", "6"].indexOf(b) >= 0 ? b + " GHz" : (b || "双频"); }
function renderSSIDKpis() {
  const list = SSID_STATE.ssids || [], cur = activeSSID();
  const bound = cur ? (cur.ap_ids || []).length : 0;
  const clients = cur ? (SSID_STATS.find(x => x.name === cur.name) || {}).clients || 0 : 0;
  $("#ssidkpis").innerHTML = kpiCards([
    ["SSID 总数", list.length, "可自定义新增", ""],
    ["当前分析对象", cur ? cur.name : "-", cur ? cur.purpose || "-" : "-", "color:var(--pri)"],
    ["生效 AP", cur && bound ? bound : HS.aps.length, cur && bound ? "按绑定 AP 计算" : "所有 AP 广播", ""],
    ["当前 SSID 终端", clients, "来自现场连接模块", ""],
    ["生效频段", cur ? fmtBand(cur.band) : "-", cur && ["2.4", "5", "6"].indexOf(cur.band) >= 0 ? "由 SSID 锁定传播参数" : "跟随热力图选择", ""],
    ["接入门限", (cur ? cur.min_rssi : -70) + " dBm", "覆盖达标率判定线", ""],
  ]);
}
function renderSSIDPage() {
  renderSSIDKpis();
  const list = SSID_STATE.ssids || [], cur = activeSSID();
  $("#ssidcnt").textContent = list.length + " 个 · 点击卡片设为当前 / 点编辑修改";
  $("#ssidcards").innerHTML = list.map(s => {
    const on = s.id === SSID_STATE.active_id;
    const aps = (s.ap_ids || []).length ? s.ap_ids.length + " 台绑定 AP" : "全部 AP";
    return '<div class="ssidcard' + (on ? " on" : "") + (s.enabled === false ? " off" : "") + '" data-ssiduse="' + esc(s.id) + '">' +
      '<div class="row" style="justify-content:space-between"><span class="nm">' + esc(s.name) + '</span>' +
      '<span>' + (on ? '<span class="tag t-ok">当前分析对象</span> ' : '') +
      (s.enabled === false ? '<span class="tag t-bad">停用</span>' : '') + '</span></div>' +
      '<div class="small muted" style="margin-top:3px">' + esc(s.purpose || "-") + ' · ' + esc(s.security || "-") + '</div>' +
      '<div class="ssidmeta"><span class="tag t-cn">' + esc(fmtBand(s.band)) + '</span>' +
      '<span class="tag t-ph">VLAN ' + esc(s.vlan) + '</span>' +
      '<span class="tag t-ph">' + esc(aps) + '</span>' +
      '<span class="tag t-ph">门限 ' + esc(s.min_rssi) + ' dBm</span>' +
      (s.broadcast === false ? '<span class="tag t-pend">隐藏 SSID</span>' : '') + '</div>' +
      (s.note ? '<div class="tiny muted" style="margin-top:6px">' + esc(s.note) + '</div>' : '') +
      '<div class="row" style="margin-top:8px;gap:6px"><button class="btn ghost sm" data-ssidedit="' + esc(s.id) + '">编辑</button>' +
      (on ? '' : '<button class="btn ghost sm" data-ssidpick="' + esc(s.id) + '">设为当前</button>') + '</div></div>';
  }).join("") || '<div class="tiny muted">暂无 SSID</div>';
  $$("#ssidcards [data-ssidedit]").forEach(b => b.addEventListener("click", e => {
    e.stopPropagation(); editSSIDForm(SSID_STATE.ssids.find(s => s.id === b.dataset.ssidedit));
  }));
  $$("#ssidcards [data-ssidpick]").forEach(b => b.addEventListener("click", e => {
    e.stopPropagation(); setActiveSSID(b.dataset.ssidpick);
  }));
  $$("#ssidcards [data-ssiduse]").forEach(d => d.addEventListener("dblclick", () => setActiveSSID(d.dataset.ssiduse)));
  renderSSIDStats();
}
function renderSSIDStats() {
  const tb = $("#ssidstatstbl tbody"); if (!tb) return;
  tb.innerHTML = SSID_STATS.length ? SSID_STATS.map(s =>
    '<tr><td><b>' + esc(s.name) + '</b>' + (s.id === SSID_STATE.active_id ? ' <span class="tag t-ok">当前</span>' : '') + '</td>' +
    '<td class="tiny">' + esc(s.purpose || "-") + '</td><td>' + esc(fmtBand(s.band)) + '</td><td>' + esc(s.vlan) + '</td>' +
    '<td>' + ((s.ap_ids || []).length ? s.ap_ids.length + " 台" : "全部 AP") + '</td>' +
    '<td>' + s.clients + '</td><td><span style="color:' + (s.healthy_pct < 60 ? "var(--bad)" : "var(--ok)") + '">' + s.healthy_pct + '%</span></td>' +
    '<td>' + (s.avg_rssi == null ? "-" : s.avg_rssi + " dBm") + '</td><td>' + (s.avg_snr == null ? "-" : s.avg_snr + " dB") + '</td>' +
    '<td>' + esc(s.min_rssi) + ' dBm</td></tr>').join("")
    : '<tr><td colspan="10" style="text-align:center;padding:22px;color:#8b97a6">等待现场连接数据…</td></tr>';
}
function renderAPChecks(ids) {
  const box = $("#sf_aps"); if (!box) return;
  const set = (ids || []).map(String);
  box.innerHTML = HS.aps.length ? HS.aps.map(a =>
    '<label><input type="checkbox" value="' + a.id + '"' + (set.indexOf(String(a.id)) >= 0 ? " checked" : "") + '>AP-' + a.id + '</label>').join("")
    : '<span class="tiny muted">当前图纸没有 AP，可在热力图页添加</span>';
}
function newSSIDForm() {
  SSID_EDIT_ID = "";
  $("#sf_name").value = ""; $("#sf_purpose").value = ""; $("#sf_note").value = "";
  $("#sf_band").value = "双频"; $("#sf_security").value = "WPA2/3-Enterprise · 802.1X";
  $("#sf_vlan").value = 20; $("#sf_minrssi").value = -70; $("#sf_maxclients").value = 60;
  $("#sf_broadcast").checked = true; $("#sf_enabled").checked = true;
  $("#ssideditstate").textContent = "新建"; $("#ssiddelete").disabled = true;
  renderAPChecks([]);
}
function editSSIDForm(s) {
  if (!s) return;
  SSID_EDIT_ID = s.id;
  $("#sf_name").value = s.name || ""; $("#sf_purpose").value = s.purpose || ""; $("#sf_note").value = s.note || "";
  $("#sf_band").value = s.band || "双频"; $("#sf_security").value = s.security || "";
  $("#sf_vlan").value = s.vlan || 0; $("#sf_minrssi").value = s.min_rssi || -70; $("#sf_maxclients").value = s.max_clients || 60;
  $("#sf_broadcast").checked = s.broadcast !== false; $("#sf_enabled").checked = s.enabled !== false;
  $("#ssideditstate").textContent = "编辑 " + s.name; $("#ssiddelete").disabled = false;
  renderAPChecks(s.ap_ids || []);
  go("ssid");
  $("#sf_name").focus();
}
async function rebuildLivePool() {
  try {
    await api("/api/live/config", { aps: HS.aps, ppm: HS.ppm, n_clients: 36 });
    await refreshLive();
    await refreshInterference();
  } catch (e) { /* 现场引擎未就绪时忽略：定时器会补齐 */ }
}
async function saveSSIDForm() {
  const item = {
    id: SSID_EDIT_ID || undefined, name: $("#sf_name").value.trim(), purpose: $("#sf_purpose").value.trim(),
    band: $("#sf_band").value, security: $("#sf_security").value.trim(), vlan: +$("#sf_vlan").value || 0,
    min_rssi: +$("#sf_minrssi").value || -70, max_clients: +$("#sf_maxclients").value || 60,
    broadcast: $("#sf_broadcast").checked, enabled: $("#sf_enabled").checked,
    note: $("#sf_note").value.trim(),
    ap_ids: $$("#sf_aps input:checked").map(x => +x.value),
  };
  if (!item.name) { toast("请填写 SSID 名称"); return; }
  try {
    SSID_STATE = await api("/api/ssid/save", { ssid: item });
    renderGlobalSSID(); renderSSIDPage(); newSSIDForm();
    await rebuildLivePool();          // 重建终端池，新 SSID 才会有现场数据
    applySSIDToUI(false);
    const st = (SSID_STATS || []).find(x => x.name === item.name);
    toast("已保存并应用：" + item.name + (st ? "（现场终端 " + st.clients + " 台）" :
      "（该 SSID 暂无模拟终端，可在「现场连接」页重置池）"), 3600);
  } catch (e) { toast("保存失败：" + e.message); }
}
async function deleteSSIDForm() {
  if (!SSID_EDIT_ID || !confirm("删除该 SSID？相关终端会在下次重置现场时重新归类。")) return;
  try {
    SSID_STATE = await api("/api/ssid/delete", { id: SSID_EDIT_ID });
    renderGlobalSSID(); renderSSIDPage(); newSSIDForm();
    await rebuildLivePool();
    applySSIDToUI(false);
    toast("已删除 SSID");
  } catch (e) { toast("删除失败：" + e.message); }
}

/* ====================================================================
   概览
==================================================================== */
function bar(label, val, total, color) {
  const pct = total ? Math.round(val / total * 100) : 0;
  return '<div style="margin-bottom:7px"><div style="display:flex;justify-content:space-between;font-size:12px"><span>' + esc(label) + '</span><b>' + val + '</b></div>' +
    '<div class="bar"><i style="width:' + pct + '%;background:' + color + '"></i></div></div>';
}
function renderDash() {
  const s = STATS || {};
  const k = [
    ["型号总数", s.total, "逐 SKU 记录", ""],
    ["覆盖厂商", s.vendors, "国内外 + 历史品牌", ""],
    ["老旧 / EOL", s.legacy_total, "含历史品牌 " + (s.historic_brand || 0), "color:#b2531c"],
    ["Wi-Fi 7", s.wifi7, "新一代型号", "color:#12855b"],
    ["Wi-Fi 4 及更早", s.wifi4_or_older, "存量老设备", "color:#b2531c"],
    ["AP / 控制器", (s.ap || 0) + " / " + (s.controller || 0), "云管理 " + (s.management_plane || 0), ""],
  ];
  $("#kpis").innerHTML = k.map(x => '<div class="kpi"><div class="v" style="' + x[3] + '">' + x[1] + '</div><div class="k">' + x[0] + '</div><div class="d">' + x[2] + '</div></div>').join("");
  const lc = s.by_lifecycle || {}, cap = s.by_capability || {}, vs = s.by_vendor || {};
  $("#lc").innerHTML = Object.entries(lc).sort((a, b) => b[1] - a[1]).map(([a, b]) => bar(a, b, s.total, a.indexOf("EOL") >= 0 || a.indexOf("历史") >= 0 ? "#d98b45" : "#1f6feb")).join("") || "-";
  $("#cap").innerHTML = Object.entries(cap).sort().map(([a, b]) => bar(a, b, s.total, a[0] === "A" ? "#12855b" : a[0] === "B" ? "#d9a441" : "#9aa5b1")).join("") || "-";
  $("#vend").innerHTML = Object.entries(vs).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([a, b]) => bar(a, b, s.total, "#5b8def")).join("") || "-";
}

/* ====================================================================
   型号库
==================================================================== */
const PAGE = 40;
let CVIEW = [], CPAGE = 1;
function uniq(key) { return Array.from(new Set(CAT.records.map(r => r[key]).filter(Boolean))).sort(); }
function initCatalog() {
  ["Wi-Fi 7", "Wi-Fi 6E", "Wi-Fi 6", "Wi-Fi 5", "Wi-Fi 4", "Wi-Fi 3", "Wi-Fi 2", "Wi-Fi 1", "混合"].forEach(g => $("#cgen").add(new Option(g, g)));
  Object.keys(STATS.by_lifecycle || {}).forEach(x => $("#clc").add(new Option(x, x)));
  Object.keys(STATS.by_capability || {}).sort().forEach(x => $("#ccap").add(new Option(x, x)));
  (STATS.vendor_list || uniq("vendor")).forEach(v => $("#cvendor").add(new Option(v, v)));
  ["cq", "corigin", "ccat", "cvendor", "cgen", "clc", "ccap", "cstatus"].forEach(id => {
    $("#" + id).addEventListener("input", () => { CPAGE = 1; filterCatalog(); });
    $("#" + id).addEventListener("change", () => { CPAGE = 1; filterCatalog(); });
  });
  $("#creset").addEventListener("click", () => {
    ["cq", "corigin", "ccat", "cvendor", "cgen", "clc", "ccap", "cstatus"].forEach(id => $("#" + id).value = "");
    CPAGE = 1; filterCatalog();
  });
  $("#cprev").addEventListener("click", () => { if (CPAGE > 1) { CPAGE--; drawCatalog(); } });
  $("#cnext").addEventListener("click", () => { if (CPAGE * PAGE < CVIEW.length) { CPAGE++; drawCatalog(); } });
  $("#expcsv").addEventListener("click", exportCatalog);
  filterCatalog();
}
function filterCatalog() {
  const q = $("#cq").value.trim().toLowerCase();
  const f = { origin: $("#corigin").value, category: $("#ccat").value, vendor: $("#cvendor").value, wifi_gen: $("#cgen").value, lifecycle: $("#clc").value, capability_tier: $("#ccap").value, verify_status: $("#cstatus").value };
  CVIEW = CAT.records.filter(r => {
    for (const k in f) if (f[k] && r[k] !== f[k]) return false;
    if (q) {
      const s = [r.models, r.vendor, r.family, r.adapter_id, r.note, r.mgmt_examples, r.min_version].join(" ").toLowerCase();
      if (s.indexOf(q) < 0) return false;
    }
    return true;
  });
  CPAGE = 1; drawCatalog();
}
function lcTag(s) {
  if (!s) return "";
  if (s.indexOf("EOL") >= 0 || s.indexOf("历史") >= 0) return '<span class="tag t-eol">' + esc(s) + '</span>';
  if (s.indexOf("存量") >= 0) return '<span class="tag t-pend">' + esc(s) + '</span>';
  return '<span class="tag t-ok">' + esc(s) + '</span>';
}
function stTag(s) { return '<span class="tag ' + (s === "已核实" ? "t-ok" : s === "待核对" ? "t-pend" : "t-ph") + '">' + esc(s) + '</span>'; }
function drawCatalog() {
  const total = CVIEW.length, pages = Math.max(1, Math.ceil(total / PAGE));
  if (CPAGE > pages) CPAGE = pages;
  const rows = CVIEW.slice((CPAGE - 1) * PAGE, CPAGE * PAGE);
  $("#ccount").textContent = "显示 " + total + " 条 / 共 " + CAT.records.length + " 条";
  $("#cpage").textContent = "第 " + CPAGE + " / " + pages + " 页";
  $("#ctbl tbody").innerHTML = rows.length ? rows.map((r, i) => {
    const idx = (CPAGE - 1) * PAGE + i;
    return '<tr data-i="' + idx + '"><td class="model">' + esc(r.models) + '</td><td>' + esc(r.vendor) + '</td><td>' +
      '<span class="tag ' + (r.origin === "国内" ? "t-cn" : r.origin === "国外" ? "t-ov" : "t-ph") + '">' + esc(r.origin) + '</span></td><td>' + esc(r.category) + '</td><td>' + esc(r.wifi_gen) + '</td><td>' + lcTag(r.lifecycle) + '</td><td>' + esc(r.capability_tier) + '</td><td>' + esc(r.plane_type) + '</td><td><code>' + esc(r.adapter_id) + '</code></td><td>' + stTag(r.verify_status) + '</td></tr>';
  }).join("") : '<tr><td colspan="10" style="text-align:center;padding:30px;color:#8b97a6">没有匹配的型号，试试放宽筛选条件</td></tr>';
  $$("#ctbl tbody tr[data-i]").forEach(tr => tr.addEventListener("click", () => showDetail(CVIEW[+tr.dataset.i])));
}
function showDetail(r) {
  if (!r) return;
  const rows = [
    ["型号", r.models], ["厂商 / 产地", r.vendor + " · " + r.origin], ["系列", r.family],
    ["类别 / 角色", r.category + " · " + r.device_role], ["Wi-Fi 世代", r.wifi_gen], ["频段", r.bands],
    ["形态", r.form_factor], ["生命周期", r.lifecycle], ["能力层级", r.capability_tier],
    ["管理平面", r.plane_type], ["控制器示例", r.mgmt_examples], ["最低版本", r.min_version],
    ["适配器 ID", r.adapter_id], ["核实状态", r.verify_status], ["核对日期", r.verified_date], ["备注", r.note],
  ];
  let advice = "按该型号元数据选择适配器；未收录或能力层级 C 的型号降级为通用 SNMP v2c + 手工参数。";
  if (r.capability_tier === "C-降级") advice = "降级路径：仅取基础 SNMP v2c 指标，客户端级数据依赖现场实测打点；不参与 k/v/r 与 6 GHz 图层。";
  else if (r.capability_tier === "B-受限") advice = "受限路径：可取射频与关联数，但缺部分高级遥测；热力图预测参数需用实测打点校正。";
  else if (r.capability_tier === "A-完整") advice = "完整路径：可用控制器 API + SNMP 取射频、客户端与漫游遥测，支持优化仿真与 A/B 复测。";
  if (r.lifecycle && (r.lifecycle.indexOf("EOL") >= 0 || r.lifecycle.indexOf("历史") >= 0)) advice += "　⚠ 该型号已停产/退市，须核对固件末版限制并纳入替换优先级。";
  $("#cdetail").innerHTML = '<h3>型号详情</h3><div style="font:600 15px Consolas,monospace;color:#143a70;margin-bottom:10px">' + esc(r.models) + '</div>' +
    rows.map(x => '<div class="metric"><span class="muted">' + x[0] + '</span><span style="text-align:right;max-width:60%;font-size:12px">' + esc(x[1]) + '</span></div>').join("") +
    '<div class="note tiny" style="margin-top:11px"><b>使用建议：</b>' + esc(advice) + '</div>';
}
function exportCatalog() {
  const cols = ["models", "vendor", "origin", "family", "category", "wifi_gen", "bands", "lifecycle", "capability_tier", "plane_type", "mgmt_examples", "min_version", "adapter_id", "verify_status", "verified_date", "note"];
  const q = v => '"' + String(v == null ? "" : v).replace(/"/g, '""') + '"';
  const csv = "\ufeff" + [cols.join(",")].concat(CVIEW.map(r => cols.map(c => q(r[c])).join(","))).join("\r\n");
  dl(new Blob([csv], { type: "text/csv;charset=utf-8" }), "型号库筛选结果.csv");
  toast("已导出 " + CVIEW.length + " 条记录");
}
function dl(blob, name) {
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1500);
}

/* ====================================================================
   热力图
==================================================================== */
let HS = { aps: [], walls: [], samples: [], ppm: 10, band: "2.4", userBand: "2.4", noise: -95, grid: "m",
  floor: "demo", tool: "select", heat: null, drag: null, nextId: 1, dirty: false };
/* SK = 现场草图的矢量数据。房间 / 隔墙 / 门洞 / 标注都是可编辑对象，
   渲染到 bg 层，墙体同时映射进 HS.walls 参与传播模型计算。 */
let SK = skDefault();
let SK_SYNC_T = null;
const bg = $("#bg"), heat = $("#heat"), edit = $("#edit");
const bgc = bg.getContext("2d"), hc = heat.getContext("2d"), ec = edit.getContext("2d");
[bg, heat, edit].forEach(c => { c.width = W; c.height = H; });

function resizeStage() {
  const box = $("#stage"); const w = box.clientWidth || 900;
  box.style.height = Math.round(w * H / W) + "px";
}
window.addEventListener("resize", resizeStage);

/* 改动自动重算：所有 AP / 墙 / 打点 / 参数变更统一走 markDirty；
   拖动 AP 时先给轻量预览，避免「AP 动了但热力图没反应」。 */
function markDirty() {
  HS.dirty = true;
  if ($("#hauto") && $("#hauto").checked) {
    clearTimeout(AUTO_TIMER);
    AUTO_TIMER = setTimeout(() => { runHeat(); }, 280);
  } else if (HS.heat) {
    $("#hstatus").textContent = "参数已改动（自动重算已关闭）· 点「计算热力图」更新";
  }
}
function previewHeat() {
  const band = ssidEffBand();
  const p = { "2.4": [46, 3.0], "5": [52, 3.2], "6": [54, 3.3] }[band] || [46, 3.0];
  const aps = HS.aps.filter(a => a.enabled !== false && apInSSID(a) &&
    !(band !== "2.4" && String(a.band || "2.4") === "2.4"));
  const cols = 64, rows = 45;
  const c = document.createElement("canvas"); c.width = cols; c.height = rows;
  const cx = c.getContext("2d"), im = cx.createImageData(cols, rows);
  const ppm = Math.max(HS.ppm, .5);
  for (let r = 0; r < rows; r++) {
    const py = (r + .5) * H / rows;
    for (let cc = 0; cc < cols; cc++) {
      const px = (cc + .5) * W / cols;
      let best = -110;
      for (let k = 0; k < aps.length; k++) {
        const a = aps[k];
        const d = Math.max(Math.hypot(px - a.x, py - a.y) / ppm, 1);
        const v = ((+a.tx_power || 20) + (+a.gain || 3)) - (p[0] + 10 * p[1] * Math.log10(d));
        if (v > best) best = v;
      }
      const col = colorOf(best), i = (r * cols + cc) * 4;
      im.data[i] = col[0]; im.data[i + 1] = col[1]; im.data[i + 2] = col[2]; im.data[i + 3] = 218;
    }
  }
  cx.putImageData(im, 0, 0);
  hc.clearRect(0, 0, W, H); hc.imageSmoothingEnabled = true; hc.imageSmoothingQuality = "high";
  hc.drawImage(c, 0, 0, W, H);
  $("#hstatus").textContent = "拖动预览（轻量模型）· 松开鼠标后按完整模型自动重算";
}

const WCOLOR = { concrete: "#6b7280", brick: "#9a8378", drywall: "#c3c9d2", glass: "#7fb2d9", wood: "#b39a7d", metal: "#475569", door: "#c3c9d2" };
/* demo 与 sketch 都是程序绘制的矢量底图，参数变化时可重绘；
   uploaded 是用户图片，只在上传 / 加载项目时绘制一次。 */
function drawFloorIfDemo() { if (HS.floor === "demo" || HS.floor === "sketch") drawFloor(); }
function initHeat() {
  resizeStage();
  buildDemo();
  $("#tools").addEventListener("click", e => {
    const b = e.target.closest("button[data-tool]"); if (!b) return;
    if (SK.tool !== "off") skSetTool("off");   // 常规工具与草图工具互斥
    HS.tool = b.dataset.tool;
    $$("#tools button").forEach(x => x.classList.toggle("on", x === b));
    const hints = { select: "拖动 AP 可移动位置；点击空白处取消选择。", ap: "在画布上点击放置 AP，右侧可改型号与功率。", wall: "按住鼠标拖出一段墙，松开完成；材质在左侧选择。", sample: "在走测位置点击，按左侧填写的 RSSI 录入实测点。", ruler: "沿已知长度的参照物（如 10 m 走廊）拖一条线，然后输入实际米数。", erase: "点击 AP / 实测点 / 墙体可删除。" };
    $("#toolhint").textContent = hints[HS.tool];
  });
  edit.addEventListener("mousedown", onDown);
  edit.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
  edit.addEventListener("mousemove", e => { const p = pt(e); $("#hcoord").textContent = Math.round(p.x) + ", " + Math.round(p.y) + " px"; });
  ["lheat", "lsinr", "lconf", "loverlap"].forEach(id => $("#" + id).addEventListener("change", () => {
    if (id === "lsinr" && $("#lsinr").checked) {
      $("#happlyif").checked = true;
      if (HS.heat) { runHeat(); return; }
    }
    renderHeat();
  }));
  ["lsmooth", "lcontour", "ldeadline"].forEach(id => $("#" + id).addEventListener("change", renderHeat));
  $("#halpha").addEventListener("input", e => {
    const v = Math.max(10, Math.min(100, +e.target.value || 72));
    heat.style.opacity = String(v / 100);
    $("#halphaval").textContent = v + "%";
  });
  edit.addEventListener("mouseleave", () => { if (SK.hover) { SK.hover = null; drawEdit(); } });
  ["laps", "lwalls", "lsamples", "llive"].forEach(id => $("#" + id).addEventListener("change", drawEdit));
  $("#hppm").addEventListener("change", e => { HS.ppm = Math.max(.5, +e.target.value || 10); e.target.value = HS.ppm; drawFloorIfDemo(); markDirty(); });
  $("#hband").addEventListener("change", e => { HS.band = e.target.value; HS.userBand = HS.band; markDirty(); });
  $("#hnoise").addEventListener("change", e => { HS.noise = +e.target.value || -95; markDirty(); });
  $("#hgrid").addEventListener("change", e => { HS.grid = e.target.value; markDirty(); });
  $("#happlyif").addEventListener("change", markDirty);
  $("#hresid").addEventListener("change", markDirty);
  $("#hfile").addEventListener("change", uploadFloor);
  $("#hdemo").addEventListener("click", () => { buildDemo(); toast("已载入示例楼层：6 台 AP / 12 个实测点"); });
  $("#hrun").addEventListener("click", runHeat);
  $("#haddap").addEventListener("click", () => { addAP(W / 2, H / 2); renderAPList(); drawEdit(); markDirty(); toast("已在画布中心添加 AP，可拖动定位"); });
  $("#hclearsamp").addEventListener("click", () => { HS.samples = []; drawEdit(); renderAPList(); markDirty(); });
  $("#hclearheat").addEventListener("click", () => { HS.heat = null; hc.clearRect(0, 0, W, H); $("#hmetrics").innerHTML = '<div class="tiny muted">已清除热力图。</div>'; $("#hstatus").textContent = "就绪"; });
  $("#hclearall").addEventListener("click", () => {
    if (!confirm("清空所有 AP、墙体与实测点？")) return;
    HS.aps = []; HS.walls = []; HS.samples = []; HS.heat = null;
    hc.clearRect(0, 0, W, H); drawEdit(); renderAPList(); $("#hmetrics").innerHTML = '<div class="tiny muted">画布已清空。</div>';
    HS.dirty = true;
  });
  $("#hexp").addEventListener("click", exportPNG);
  $("#hsave").addEventListener("click", saveProject);
  $("#hload").addEventListener("click", loadProject);
  /* --- 现场草图 --- */
  $("#hsketch").addEventListener("click", () => skEnter($("#sktpl").value));
  $("#skcont").addEventListener("click", skResume);
  $("#sktools").addEventListener("click", e => {
    const b = e.target.closest("button[data-sktool]"); if (!b) return;
    if (HS.floor !== "sketch") return toast("请先点「绘制草图」新建草图");
    skSetTool(b.dataset.sktool);
  });
  $("#skmaterial").addEventListener("change", e => skApplyMaterial(e.target.value));
  $("#skwidthm").addEventListener("change", skApplyDim);
  $("#skdimapply").addEventListener("click", skApplyDim);
  $("#skgrid").addEventListener("change", e => { SK.showGrid = e.target.checked; drawFloor(); });
  $("#sksnap").addEventListener("change", e => { SK.snap = e.target.checked; });
  $("#skortho").addEventListener("change", e => { SK.ortho = e.target.checked; });
  $("#skgridsize").addEventListener("change", e => { SK.grid = +e.target.value || 20; drawFloor(); });
  $("#skundo").addEventListener("click", skUndo);
  $("#skredo").addEventListener("click", skRedo);
  $("#skrenamebtn").addEventListener("click", skRename);
  $("#skdel").addEventListener("click", skDeleteSel);
  $("#skclear").addEventListener("click", skClearAll);
  $("#sksync").addEventListener("click", () => { if (HS.floor !== "sketch") return toast("请先绘制草图"); skSyncToSurvey(true); });
  $("#skexp").addEventListener("click", skExportPNG);
  edit.addEventListener("dblclick", e => {
    if (HS.floor !== "sketch" || SK.tool !== "pick") return;
    const h = skHit(pt(e)); if (!h || (h.type !== "room" && h.type !== "label")) return;
    SK.sel = { type: h.type, id: h.obj.id }; skShowSel();
    const txt = prompt("名称：", h.obj.label || h.obj.text || "");
    if (txt && txt.trim()) {
      skSnapshot();
      if (h.type === "label") h.obj.text = txt.trim().slice(0, 24); else h.obj.label = txt.trim().slice(0, 24);
      skChanged();
    }
  });
  renderAPList();
}

/* --- 示例楼层 --- */
let SEED = 20261109;
function rnd() { SEED = (SEED * 1103515245 + 12345) & 0x7fffffff; return SEED / 0x7fffffff; }
function buildDemo() {
  SEED = 20261109;
  const walls = [];
  const add = (x1, y1, x2, y2, m) => walls.push({ x1, y1, x2, y2, material: m || "drywall" });
  // 外墙
  add(40, 40, 960, 40, "concrete"); add(960, 40, 960, 660, "concrete");
  add(960, 660, 40, 660, "concrete"); add(40, 660, 40, 40, "concrete");
  // 上排竖隔墙
  [[240, "drywall"], [440, "concrete"], [680, "drywall"]].forEach(([x, m]) => add(x, 40, x, 310, m));
  // 下排竖隔墙
  [[260, "drywall"], [520, "concrete"], [760, "drywall"]].forEach(([x, m]) => add(x, 390, x, 660, m));
  // 走廊两侧墙（留门洞）
  function withDoors(y, doors) {
    let cur = 40;
    doors.forEach(cx => { add(cur, y, cx - 32, y, "drywall"); cur = cx + 32; });
    add(cur, y, 960, y, "drywall");
  }
  withDoors(310, [140, 340, 560, 820]);
  withDoors(390, [150, 390, 640, 860]);
  HS.walls = walls;
  HS.aps = []; HS.samples = [];
  const defs = [
    [180, 350, "CW9176I"], [400, 350, "5760-10"], [640, 350, "RG-AP9220"], [860, 350, "FAP-431G"],
    [140, 160, "WA7539"], [560, 160, "MR57"], [860, 160, "AP-755"],
    [170, 520, "U7 Pro"], [620, 520, "AP47"], [880, 520, "R770"],
  ];
  defs.forEach(([x, y, m]) => addAP(x, y, m));
  // 模拟走测：以同一传播模型为底，叠加名义墙体损耗与 ±2.5 dB 测量噪声，
  // 使「实测残差校正」有真实可解释的作用，而不是随机涂抹。
  function sampleModel(x, y) {
    let best = -110;
    HS.aps.forEach(a => {
      const d = Math.max(Math.hypot(x - a.x, y - a.y) / HS.ppm, 1);
      const v = (a.tx_power + a.gain) - (46 + 30 * Math.log10(d)) - 5;
      if (v > best) best = v;
    });
    return best;
  }
  const spots = [
    [110, 110], [180, 240], [320, 130], [400, 250], [530, 120], [600, 250],
    [780, 130], [880, 240], [130, 500], [220, 610], [380, 470], [430, 610],
    [600, 450], [690, 590], [820, 470], [910, 600], [120, 350], [340, 350],
    [520, 350], [740, 350], [920, 350],
  ];
  spots.forEach(([x, y]) => HS.samples.push({ x, y, rssi: Math.round(sampleModel(x, y) + (rnd() - .5) * 5) }));
  HS.ppm = 10; HS.floor = "demo"; HS.heat = null; HS.dirty = true;
  $("#hppm").value = 10; $("#floorinfo").textContent = "示例楼层";
  drawFloor(); hc.clearRect(0, 0, W, H); drawEdit(); renderAPList();
  $("#hmetrics").innerHTML = '<div class="tiny muted">示例楼层已就绪（6 AP / ' + HS.walls.length + ' 段墙 / ' + HS.samples.length + ' 个实测点）。点「计算热力图」。</div>';
  $("#hstatus").textContent = "就绪 · 点「计算热力图」开始";
  $("#stagehint").style.display = "none";
}
function drawFloor() {
  if (HS.floor === "sketch") { drawSketch(); return; }
  if (HS.floor === "uploaded") return;   // 图片底图由 uploadFloor / loadProject 绘制
  drawDemoFloor();
}
function drawDemoFloor() {
  const c = bgc; c.clearRect(0, 0, W, H);
  c.fillStyle = "#fcfdff"; c.fillRect(0, 0, W, H);
  c.fillStyle = "#f2f6fb"; c.fillRect(40, 40, 920, 620);
  c.fillStyle = "#eef4fb"; c.fillRect(40, 310, 920, 80);
  // 房间底色
  c.fillStyle = "#f8fafc";
  [[41, 41, 239, 309], [241, 41, 439, 309], [441, 41, 679, 309], [681, 41, 959, 309],
   [41, 391, 259, 659], [261, 391, 519, 659], [521, 391, 759, 659], [761, 391, 959, 659]].forEach(r => c.fillRect(r[0], r[1], r[2] - r[0], r[3] - r[1]));
  // 墙体
  HS.walls.forEach(w => {
    c.strokeStyle = WCOLOR[w.material] || "#c3c9d2";
    c.lineWidth = w.material === "concrete" ? 6 : w.material === "metal" ? 7 : 3;
    c.beginPath(); c.moveTo(w.x1, w.y1); c.lineTo(w.x2, w.y2); c.stroke();
  });
  // 标注
  c.fillStyle = "#94a3b8"; c.font = "600 13px 'Microsoft YaHei',sans-serif"; c.textAlign = "center";
  const rooms = [["会议室 A", 140, 175], ["开放办公区", 340, 175], ["机房", 560, 175], ["财务室", 820, 175],
    ["培训室", 150, 525], ["研发区", 390, 525], ["茶水间", 640, 525], ["档案室", 860, 525]];
  rooms.forEach(([t, x, y]) => c.fillText(t, x, y));
  c.font = "12px 'Microsoft YaHei',sans-serif"; c.fillStyle = "#a9b4c2"; c.fillText("走　廊 / CORRIDOR", 500, 355);
  c.textAlign = "left"; c.font = "11px Consolas,monospace"; c.fillStyle = "#b0bac7";
  c.fillText("示例楼层 · 100 m × 70 m（示意）", 42, 28);
  c.fillText("N ↑", 928, 28);
  // 比例尺
  c.strokeStyle = "#64748b"; c.lineWidth = 2; c.beginPath(); c.moveTo(50, 680); c.lineTo(50 + 10 * HS.ppm, 680); c.stroke();
  c.beginPath(); c.moveTo(50, 675); c.lineTo(50, 685); c.moveTo(50 + 10 * HS.ppm, 675); c.lineTo(50 + 10 * HS.ppm, 685); c.stroke();
  c.fillStyle = "#64748b"; c.font = "11px Consolas,monospace"; c.fillText("10 m", 50 + 10 * HS.ppm + 8, 684);
}

/* ====================================================================
   现场草图：不导入图纸，直接在画布上勾出房间 / 隔墙 / 门洞 / 标注
   · SK 是矢量数据（可编辑、可撤销、随项目保存），渲染到 bg 层
   · 区域边界 + 显式隔墙统一生成「参与传播的墙」，共边自动去重，
     门洞按实际宽度把墙拆成两段，信号可以从开口穿过
   · 草图同时导出 PNG 到 /api/floorplan，供「现场勘测」页点击打点；
     两页都是 1000×700 坐标系，落点与 AP 坐标严格对齐
   ==================================================================== */
function skDefault() {
  return {
    name: "现场草图", rooms: [], walls: [], doors: [], labels: [],
    grid: 20, snap: true, showGrid: true, ortho: true,
    width_m: 40, door_w_m: 0.9, material: "drywall",
    tool: "off", nextId: 1, sel: null, drag: null, hover: null, snapHint: null, undo: [], redo: []
  };
}
function skSerialize() {
  return { name: SK.name, rooms: SK.rooms, walls: SK.walls, doors: SK.doors, labels: SK.labels,
    grid: SK.grid, snap: SK.snap, showGrid: SK.showGrid, ortho: SK.ortho,
    width_m: SK.width_m, door_w_m: SK.door_w_m, material: SK.material, nextId: SK.nextId };
}
function skRestore(o) {
  const d = skDefault();
  if (o) Object.keys(d).forEach(k => { if (o[k] !== undefined && ["tool", "sel", "drag", "undo", "redo"].indexOf(k) < 0) d[k] = o[k]; });
  d.nextId = Math.max(1, ...[].concat(d.rooms, d.walls, d.doors, d.labels).map(x => (x && x.id) || 0)) + 1;
  SK = d;
}
function skSnapStr() { return JSON.stringify({ rooms: SK.rooms, walls: SK.walls, doors: SK.doors, labels: SK.labels }); }
function skSnapshot() { SK.undo.push(skSnapStr()); if (SK.undo.length > 40) SK.undo.shift(); SK.redo = []; }
function skLoadSnap(s) { const o = JSON.parse(s); SK.rooms = o.rooms; SK.walls = o.walls; SK.doors = o.doors; SK.labels = o.labels; SK.sel = null; }
function skUndo() {
  if (!SK.undo.length) return toast("没有可撤销的操作");
  SK.redo.push(skSnapStr()); skLoadSnap(SK.undo.pop()); skShowSel(); skChanged();
}
function skRedo() {
  if (!SK.redo.length) return toast("没有可重做的操作");
  SK.undo.push(skSnapStr()); skLoadSnap(SK.redo.pop()); skShowSel(); skChanged();
}
const SK_HINT = {
  room: "草图 · 拖拽画矩形房间：自动生成 4 面墙；共边自动去重，选中房间可改名 / 改材质。",
  wall: "草图 · 拖拽画一段隔墙；按住 Shift 或勾选「正交」强制水平 / 垂直。",
  door: "草图 · 在墙上点一下开 0.9 m 门洞，传播模型按开口把墙拆段，信号可穿过。",
  label: "草图 · 点击画布输入区域名称，如「会议室 A」「弱电间」。",
  pick: "草图 · 点选房间 / 隔墙 / 标注后，可改材质、改名或删除。",
  erase: "草图 · 点击房间、隔墙、门洞或标注即可删除，可撤销。"
};
function skSetTool(t) {
  SK.tool = t;
  $$("#sktools button").forEach(b => b.classList.toggle("on", b.dataset.sktool === t));
  if (t === "off") {
    $$("#tools button").forEach(b => b.classList.toggle("on", b.dataset.tool === HS.tool));
    $("#toolhint").textContent = "草图已保存并同步，可用「放置 AP」「录入实测点」继续布点与勘测。";
  } else {
    $$("#tools button").forEach(b => b.classList.remove("on"));
    HS.tool = "select";
    $("#toolhint").textContent = SK_HINT[t] || "";
  }
  SK.drag = null; SK.hover = null; SK.snapHint = null; drawEdit();
}

/* --- 快速布局模板 --- */
function skTemplate(kind) {
  const rooms = [], walls = [], doors = [], labels = [];
  const WL = (x1, y1, x2, y2, m) => walls.push({ x1, y1, x2, y2, material: m || "concrete" });
  const RM = (x1, y1, x2, y2, label, m) => rooms.push({ x1, y1, x2, y2, label: label || "", material: m || "drywall" });
  const OUT = () => { WL(40, 40, 960, 40); WL(960, 40, 960, 660); WL(960, 660, 40, 660); WL(40, 660, 40, 40); };
  if (kind === "corridor") {
    OUT();
    WL(40, 290, 960, 290, "drywall"); WL(40, 410, 960, 410, "drywall");
    RM(40, 40, 340, 290, "会议室 A"); RM(340, 40, 640, 290, "开放办公区"); RM(640, 40, 960, 290, "机房", "concrete");
    RM(40, 410, 340, 660, "培训室"); RM(340, 410, 640, 660, "研发区"); RM(640, 410, 960, 660, "档案室");
    labels.push({ x: 500, y: 355, text: "走　廊 / CORRIDOR" });
    [[190, 290], [490, 290], [800, 290], [190, 410], [490, 410], [800, 410]].forEach(([x, y]) => doors.push({ x, y }));
  } else if (kind === "office") {
    OUT();
    WL(420, 40, 420, 300, "glass"); WL(40, 300, 420, 300, "glass");
    WL(760, 40, 760, 260, "concrete"); WL(760, 260, 960, 260, "concrete");
    WL(40, 420, 960, 420, "drywall"); WL(560, 420, 560, 660, "drywall");
    RM(40, 40, 420, 300, "会议室"); RM(420, 40, 760, 260, "开放办公区");
    RM(760, 40, 960, 260, "弱电间", "concrete"); RM(420, 260, 760, 420, "协作区");
    RM(40, 420, 560, 660, "工位区 A"); RM(560, 420, 960, 660, "工位区 B");
    doors.push({ x: 230, y: 300 }, { x: 860, y: 260 }, { x: 300, y: 420 }, { x: 760, y: 420 });
  } else if (kind === "lshape") {
    WL(40, 40, 620, 40); WL(620, 40, 620, 340); WL(620, 340, 960, 340);
    WL(960, 340, 960, 660); WL(960, 660, 40, 660); WL(40, 660, 40, 40);
    WL(40, 340, 620, 340, "drywall"); WL(340, 340, 340, 660, "drywall");
    doors.push({ x: 300, y: 340 }, { x: 340, y: 500 });
    labels.push({ x: 300, y: 190, text: "主办公区" }, { x: 180, y: 500, text: "会议室" },
      { x: 470, y: 500, text: "库房" }, { x: 790, y: 500, text: "扩展区" });
  } else if (kind === "single") {
    OUT(); RM(40, 40, 960, 660, "开放区域", "drywall");
  } else {
    OUT(); labels.push({ x: 500, y: 350, text: "空白草图 · 用「画房间」开始勾现场" });
  }
  let id = 1;
  [].concat(rooms, walls, doors, labels).forEach(o => { o.id = id++; });
  return { rooms, walls, doors, labels };
}
function skWallKey(w) {
  const a = Math.round(w.x1) + "," + Math.round(w.y1), b = Math.round(w.x2) + "," + Math.round(w.y2);
  return a < b ? a + "|" + b : b + "|" + a;
}
function skAxis(w) { return Math.abs(w.y1 - w.y2) < 1 ? "h" : Math.abs(w.x1 - w.x2) < 1 ? "v" : ""; }
function skInterval(w) { return skAxis(w) === "h" ? [Math.min(w.x1, w.x2), Math.max(w.x1, w.x2)] : [Math.min(w.y1, w.y2), Math.max(w.y1, w.y2)]; }
function skCollinear(a, b) {
  const aa = skAxis(a), bb = skAxis(b);
  if (!aa || aa !== bb) return false;
  return aa === "h" ? Math.abs(a.y1 - b.y1) < 1 : Math.abs(a.x1 - b.x1) < 1;
}
/* 完全重合的共边只保留一条；被共线长段（外墙 / 走廊墙）完整覆盖的短段也去掉，
   避免相邻房间共用墙被重复计入衰减。 */
function skDedupeWalls(list) {
  const seen = {}, base = [];
  list.forEach(w => { const k = skWallKey(w); if (!seen[k]) { seen[k] = 1; base.push(w); } });
  const out = base.filter((w, i) => {
    const A = skInterval(w);
    return !base.some((o, j) => {
      if (i === j || !skCollinear(w, o)) return false;
      const B = skInterval(o);
      return B[0] <= A[0] + 1 && B[1] >= A[1] - 1 && (B[1] - B[0]) > (A[1] - A[0]) + 1;
    });
  });
  return out.length ? out : base;
}
/* 区域边界 + 显式隔墙 → 参与传播模型与渲染的完整墙表 */
function skAllWalls() {
  const all = [];
  SK.rooms.forEach(r => {
    const x1 = Math.min(r.x1, r.x2), y1 = Math.min(r.y1, r.y2), x2 = Math.max(r.x1, r.x2), y2 = Math.max(r.y1, r.y2);
    const m = r.material || "drywall";
    [[x1, y1, x2, y1], [x2, y1, x2, y2], [x2, y2, x1, y2], [x1, y2, x1, y1]].forEach(v =>
      all.push({ x1: v[0], y1: v[1], x2: v[2], y2: v[3], material: m }));
  });
  SK.walls.forEach(w => all.push(Object.assign({}, w)));
  return skDedupeWalls(all);
}
function skSplitByDoors(w, doors) {
  const dx = w.x2 - w.x1, dy = w.y2 - w.y1, L = Math.hypot(dx, dy);
  if (L < 2) return [];
  const half = Math.max(5, (SK.door_w_m || .9) * HS.ppm / 2);
  const cuts = [];
  (doors || []).forEach(d => {
    const t = ((d.x - w.x1) * dx + (d.y - w.y1) * dy) / (L * L);
    const px = w.x1 + t * dx, py = w.y1 + t * dy;
    if (t > .02 && t < .98 && Math.hypot(d.x - px, d.y - py) < 12)
      cuts.push([Math.max(0, (t * L - half) / L), Math.min(1, (t * L + half) / L)]);
  });
  if (!cuts.length) return [w];
  cuts.sort((a, b) => a[0] - b[0]);
  const segs = [], put = (a, b) => {
    if (b - a > .012) segs.push({ x1: Math.round(w.x1 + a * dx), y1: Math.round(w.y1 + a * dy),
      x2: Math.round(w.x1 + b * dx), y2: Math.round(w.y1 + b * dy), material: w.material });
  };
  let cur = 0;
  cuts.forEach(c => { if (c[0] > cur) put(cur, c[0]); cur = Math.max(cur, c[1]); });
  if (cur < 1) put(cur, 1);
  return segs.length ? segs : [w];
}
function skHeatWalls() {
  const out = [];
  skAllWalls().forEach(w => skSplitByDoors(w, SK.doors).forEach(s => out.push(Object.assign({ sk: true }, s))));
  return out;
}
function skSyncWalls() { HS.walls = HS.walls.filter(w => !w.sk).concat(skHeatWalls()); }

/* --- 新建 / 继续编辑草图 --- */
function skEnter(kind) {
  if ((HS.aps && HS.aps.length) || (HS.samples && HS.samples.length) || (HS.walls && HS.walls.length)) {
    if (!confirm("新建草图会清空当前 AP、墙体与实测打点（已保存的项目不受影响），继续？")) return;
  }
  const t = skTemplate(kind || "blank");
  const nm = ({ blank: "空白草图", single: "单间草图", corridor: "走廊 + 房间", office: "办公区草图", lshape: "L 形草图" })[kind] || "现场草图";
  SK = skDefault(); SK.name = nm;
  SK.rooms = t.rooms; SK.walls = t.walls; SK.doors = t.doors; SK.labels = t.labels;
  SK.nextId = Math.max(1, ...[].concat(SK.rooms, SK.walls, SK.doors, SK.labels).map(x => x.id || 0)) + 1;
  HS.floor = "sketch"; HS.aps = []; HS.samples = []; HS.walls = []; HS.heat = null;
  HS.ppm = Math.round(920 / (SK.width_m || 40) * 100) / 100;
  $("#hppm").value = HS.ppm; hc.clearRect(0, 0, W, H);
  $("#floorinfo").textContent = "绘制草图 · " + nm;
  $("#stagehint").style.display = "none";
  const p = $("#skpanel"); if (p) p.open = true;
  const wn = $("#skwidthm"); if (wn) wn.value = SK.width_m;
  const mn = $("#skmaterial"); if (mn) mn.value = SK.material;
  skSetTool("room");
  skSyncWalls(); drawFloor(); drawEdit(); renderAPList(); skUpdateInfo(); skApplyDimText(); markDirty();
  skSyncToSurvey(false);
  toast("已进入草图模式：拖拽画房间，画好后点「完成 / 返回布点」");
}
function skResume() {
  if (!SK.walls.length && !SK.rooms.length) return skEnter("blank");
  HS.floor = "sketch";
  $("#floorinfo").textContent = "绘制草图 · " + SK.name;
  const p = $("#skpanel"); if (p) p.open = true;
  const wn = $("#skwidthm"); if (wn) wn.value = SK.width_m;
  skSetTool("room"); skSyncWalls(); drawFloor(); drawEdit(); renderAPList();
  skUpdateInfo(); skApplyDimText(); skSyncToSurvey(false);
  toast("已回到草图编辑，可继续增删房间与隔墙");
}
function skApplyDimText() {
  const el = $("#skdimhint");
  if (el) el.innerHTML = "外框 920 px ≈ <b>" + SK.width_m + " m</b> 宽 · 620 px ≈ <b>" + (620 / HS.ppm).toFixed(1) +
    " m</b> 高 · 比例尺 <b>" + HS.ppm + " px/m</b>（可用「比例尺标定」精修）";
}
function skApplyDim() {
  const m = Math.max(2, +$("#skwidthm").value || 40);
  SK.width_m = m; HS.ppm = Math.round(920 / m * 100) / 100;
  $("#hppm").value = HS.ppm;
  skApplyDimText(); skSyncWalls(); drawFloor(); drawEdit(); markDirty(); skSyncToSurvey(false);
  toast("比例尺已按实际宽度更新：" + HS.ppm + " px/m");
}
function skUpdateInfo() {
  const el = $("#skinfo");
  if (el) el.textContent = SK.rooms.length + " 区域 / " + SK.walls.length + " 隔墙 / " + SK.doors.length + " 门洞 / " + SK.labels.length + " 标注";
}
function skChanged() {
  skSyncWalls(); drawFloor(); drawEdit(); renderAPList(); skUpdateInfo(); skShowSel(); markDirty();
  clearTimeout(SK_SYNC_T);
  SK_SYNC_T = setTimeout(() => skSyncToSurvey(false), 1000);
}

/* --- 草图交互 --- */
function skClamp(p) { return { x: Math.max(0, Math.min(W, p.x)), y: Math.max(0, Math.min(H, p.y)) }; }
function skEndpoints() {
  const out = [], seen = new Set();
  skAllWalls().forEach(w => [[w.x1, w.y1], [w.x2, w.y2]].forEach(([x, y]) => {
    const k = Math.round(x / 3) + ":" + Math.round(y / 3);
    if (!seen.has(k)) { seen.add(k); out.push({ x, y }); }
  }));
  return out;
}
function skSnapPt(p) {
  const g = SK.snap ? Math.max(5, +SK.grid || 20) : 0;
  const q0 = skClamp(p);
  let q = g ? { x: Math.round(q0.x / g) * g, y: Math.round(q0.y / g) * g } : { x: Math.round(q0.x), y: Math.round(q0.y) };
  SK.snapHint = null;
  /* 端点吸附优先于网格：新墙能精确接到已有墙/房间的角上，避免差几个像素形成缝隙 */
  if (SK.snap) {
    let best = null, bd = 13;
    skEndpoints().forEach(e => { const d = Math.hypot(q0.x - e.x, q0.y - e.y); if (d < bd) { bd = d; best = e; } });
    if (best) { q = { x: best.x, y: best.y }; SK.snapHint = { x: best.x, y: best.y }; }
  }
  return q;
}
function skOrtho(a, b) { return Math.abs(b.x - a.x) >= Math.abs(b.y - a.y) ? { x: b.x, y: a.y } : { x: a.x, y: b.y }; }
function skHit(p) {
  for (let i = SK.doors.length - 1; i >= 0; i--) { const d = SK.doors[i]; if (Math.hypot(p.x - d.x, p.y - d.y) < 14) return { type: "door", obj: d, i }; }
  for (let i = SK.labels.length - 1; i >= 0; i--) { const l = SK.labels[i]; if (Math.abs(p.x - l.x) < 90 && Math.abs(p.y - l.y) < 15) return { type: "label", obj: l, i }; }
  for (let i = SK.walls.length - 1; i >= 0; i--) { if (distToSeg(p, SK.walls[i]) < 9) return { type: "wall", obj: SK.walls[i], i }; }
  for (let i = SK.rooms.length - 1; i >= 0; i--) {
    const r = SK.rooms[i];
    if (p.x >= Math.min(r.x1, r.x2) && p.x <= Math.max(r.x1, r.x2) && p.y >= Math.min(r.y1, r.y2) && p.y <= Math.max(r.y1, r.y2))
      return { type: "room", obj: r, i };
  }
  return null;
}
function skNearestWall(p) {
  let best = -1, bd = 13;
  skAllWalls().forEach((w, i) => { const d = distToSeg(p, w); if (d < bd) { bd = d; best = i; } });
  return best;
}
function skRemove(h) {
  const arr = { door: SK.doors, label: SK.labels, wall: SK.walls, room: SK.rooms }[h.type];
  if (arr) arr.splice(h.i, 1);
}
function skDown(e, p) {
  const q = skSnapPt(p), t = SK.tool;
  SK.drag = (t === "room" || t === "wall") ? { start: q, cur: q, shift: e.shiftKey } : null;
  SK.snapHint = null;
  if (t === "room" || t === "wall") { drawEdit(); return; }
  if (t === "door") {
    const walls = skAllWalls(), wi = skNearestWall(p);
    if (wi < 0) return toast("请在墙上点击开门洞");
    skSnapshot();
    const w = walls[wi], dx = w.x2 - w.x1, dy = w.y2 - w.y1, L = dx * dx + dy * dy;
    let k = L ? ((p.x - w.x1) * dx + (p.y - w.y1) * dy) / L : 0; k = Math.max(.06, Math.min(.94, k));
    SK.doors.push({ id: SK.nextId++, x: Math.round(w.x1 + k * dx), y: Math.round(w.y1 + k * dy) });
    skChanged(); toast("已开 " + SK.door_w_m + " m 门洞，传播模型按开口拆墙");
    return;
  }
  if (t === "label") {
    const txt = prompt("区域 / 标注文字：", "区域 " + (SK.labels.length + 1));
    if (txt && txt.trim()) { skSnapshot(); SK.labels.push({ id: SK.nextId++, x: q.x, y: q.y, text: txt.trim().slice(0, 24) }); skChanged(); }
    return;
  }
  if (t === "pick") { const h = skHit(p); SK.sel = h ? { type: h.type, id: h.obj.id } : null; skShowSel(); drawEdit(); return; }
  if (t === "erase") { const h = skHit(p); if (!h) return; skSnapshot(); skRemove(h); SK.sel = null; skChanged(); toast("已删除草图元素"); return; }
}
function skMove(e, p) {
  if (!SK.drag || (SK.tool !== "room" && SK.tool !== "wall")) return;
  let q = skSnapPt(p);
  if (SK.tool === "wall" && (SK.ortho || SK.drag.shift || e.shiftKey)) q = skOrtho(SK.drag.start, q);
  SK.drag.cur = skClamp(q); scheduleCanvas(false);
}
function skUp(e, p) {
  if (!SK.drag) return;
  const d = SK.drag; SK.drag = null; SK.hover = null; SK.snapHint = null;
  if (SK.tool !== "room" && SK.tool !== "wall") { drawEdit(); return; }
  /* 用拖拽预览里的 cur（已吸附 / 正交）作为终点：修复旧版「预览吸附、松手却按原始鼠标点落笔」，
     以及画房间时终点完全不吸附网格的问题。 */
  const a = d.start, b = skClamp(d.cur || p);
  if (Math.hypot(b.x - a.x, b.y - a.y) < 12) { drawEdit(); return; }
  skSnapshot();
  if (SK.tool === "wall") {
    SK.walls.push({ id: SK.nextId++, x1: a.x, y1: a.y, x2: b.x, y2: b.y, material: $("#skmaterial").value });
  } else {
    const x1 = Math.min(a.x, b.x), y1 = Math.min(a.y, b.y), x2 = Math.max(a.x, b.x), y2 = Math.max(a.y, b.y);
    SK.rooms.push({ id: SK.nextId++, x1, y1, x2, y2, label: "区域 " + (SK.rooms.length + 1), material: $("#skmaterial").value });
  }
  skChanged(); drawEdit();
}
function skSelObj() {
  if (!SK.sel) return null;
  const arr = { room: SK.rooms, wall: SK.walls, door: SK.doors, label: SK.labels }[SK.sel.type];
  return (arr && arr.find(x => x.id === SK.sel.id)) || null;
}
function skShowSel() {
  const el = $("#sksel"), o = skSelObj();
  if (el) el.textContent = !o ? "未选中对象" : "已选中：" +
    ({ room: "区域", wall: "隔墙", door: "门洞", label: "标注" })[SK.sel.type] + " #" + o.id + " · " + (o.label || o.text || "-");
  const m = $("#skmaterial"); if (m && o && (SK.sel.type === "wall" || SK.sel.type === "room") && o.material) m.value = o.material;
  const n = $("#skrename"); if (n) n.value = o ? (o.label || o.text || "") : "";
}
function skApplyMaterial(v) {
  SK.material = v;
  const o = skSelObj(); if (!o || (SK.sel.type !== "wall" && SK.sel.type !== "room")) return;
  skSnapshot(); o.material = v; skChanged();
}
function skRename() {
  const o = skSelObj(); if (!o) return toast("请先用「选择」工具点选一个区域或标注");
  const v = $("#skrename").value.trim(); if (!v) return;
  skSnapshot(); if (SK.sel.type === "label") o.text = v.slice(0, 24); else o.label = v.slice(0, 24);
  skChanged();
}
function skDeleteSel() {
  if (!SK.sel) return toast("请先用「选择」工具点选对象");
  const arr = { room: SK.rooms, wall: SK.walls, door: SK.doors, label: SK.labels }[SK.sel.type];
  const i = arr.findIndex(x => x.id === SK.sel.id);
  if (i < 0) return;
  skSnapshot(); arr.splice(i, 1); SK.sel = null; skChanged(); toast("已删除选中对象");
}
function skClearAll() {
  if (!confirm("清空草图上的全部区域、隔墙、门洞与标注？")) return;
  skSnapshot(); SK.rooms = []; SK.walls = []; SK.doors = []; SK.labels = []; SK.sel = null; skChanged();
}

/* --- 草图渲染（bg 层） --- */
function drawSketch() {
  const c = bgc; c.clearRect(0, 0, W, H);
  c.fillStyle = "#fdfefe"; c.fillRect(0, 0, W, H);
  if (SK.baseImageObj) {           // 图片识别候选墙时，把原图淡显作对照底稿
    c.globalAlpha = .26; c.drawImage(SK.baseImageObj, 0, 0, W, H); c.globalAlpha = 1;
  }
  if (SK.showGrid) {
    const g = Math.max(10, +SK.grid || 20);
    for (let x = 0; x <= W; x += g) { c.strokeStyle = x % (g * 5) === 0 ? "#dce5f1" : "#eff4fa"; c.lineWidth = 1; c.beginPath(); c.moveTo(x + .5, 0); c.lineTo(x + .5, H); c.stroke(); }
    for (let y = 0; y <= H; y += g) { c.strokeStyle = y % (g * 5) === 0 ? "#dce5f1" : "#eff4fa"; c.beginPath(); c.moveTo(0, y + .5); c.lineTo(W, y + .5); c.stroke(); }
  }
  SK.rooms.forEach(r => {
    const x = Math.min(r.x1, r.x2), y = Math.min(r.y1, r.y2), w = Math.abs(r.x2 - r.x1), h = Math.abs(r.y2 - r.y1);
    c.fillStyle = "#f2f7fd"; c.fillRect(x, y, w, h);
  });
  const walls = skAllWalls();
  walls.forEach(w => {
    c.strokeStyle = WCOLOR[w.material] || "#c3c9d2";
    c.lineWidth = w.material === "concrete" ? 6 : w.material === "metal" ? 7 : 3;
    c.lineCap = "round"; c.beginPath(); c.moveTo(w.x1, w.y1); c.lineTo(w.x2, w.y2); c.stroke();
  });
  // 门洞：擦掉墙线 + 门扇弧线，表示信号可穿过的开口
  const half = Math.max(5, (SK.door_w_m || .9) * HS.ppm / 2);
  SK.doors.forEach(d => {
    let best = null, bd = 1e9;
    walls.forEach(w => { const q = distToSeg(d, w); if (q < bd) { bd = q; best = w; } });
    const ang = best ? Math.atan2(best.y2 - best.y1, best.x2 - best.x1) : 0;
    c.save(); c.translate(d.x, d.y); c.rotate(ang);
    c.fillStyle = "#fdfefe"; c.fillRect(-half, -6, half * 2, 12);
    c.strokeStyle = "#94a3b8"; c.lineWidth = 1.4; c.setLineDash([3, 3]);
    c.beginPath(); c.arc(-half, 0, half * 2, -Math.PI / 2, 0); c.stroke();
    c.setLineDash([]); c.beginPath(); c.moveTo(-half, 0); c.lineTo(-half, -half * 1.6); c.stroke();
    c.restore();
  });
  c.textAlign = "center"; c.textBaseline = "middle";
  SK.rooms.forEach(r => {
    if (!r.label) return;
    const x = (r.x1 + r.x2) / 2, y = (r.y1 + r.y2) / 2;
    c.font = "600 13px 'Microsoft YaHei',sans-serif"; c.fillStyle = "#8b9aad"; c.fillText(r.label, x, y);
  });
  SK.labels.forEach(l => { c.font = "600 13px 'Microsoft YaHei',sans-serif"; c.fillStyle = "#7f8ea3"; c.fillText(l.text, l.x, l.y); });
  c.textBaseline = "alphabetic"; c.textAlign = "left";
  c.font = "600 12px 'Microsoft YaHei',sans-serif"; c.fillStyle = "#94a3b8";
  c.fillText("现场草图 · " + SK.name + " · 约 " + SK.width_m + " m × " + (620 / HS.ppm).toFixed(1) + " m（示意，非测绘）", 42, 26);
  c.textAlign = "right"; c.fillText("N ↑", 958, 26); c.textAlign = "left";
  const meters = HS.ppm > 30 ? 5 : 10, px = meters * HS.ppm;
  c.strokeStyle = "#64748b"; c.lineWidth = 2;
  c.beginPath(); c.moveTo(50, 680); c.lineTo(50 + px, 680);
  c.moveTo(50, 675); c.lineTo(50, 685); c.moveTo(50 + px, 675); c.lineTo(50 + px, 685); c.stroke();
  c.font = "11px Consolas,monospace"; c.fillStyle = "#64748b"; c.fillText(meters + " m", 50 + px + 8, 684);
}

/* --- 与「现场勘测」页共用图纸、导出、项目持久化 --- */
async function skSyncToSurvey(showToast) {
  if (HS.floor !== "sketch") return;
  try {
    await api("/api/floorplan", { name: (SK.name || "现场草图") + ".png", data: bg.toDataURL("image/png") });
    if (typeof SURVEY_STATE !== "undefined" && SURVEY_STATE) {
      const im = new Image();
      im.onload = () => { SURVEY_STATE.floor = im; if (typeof svDraw === "function") svDraw(); };
      im.src = "/api/floorplan?t=" + Date.now();
    }
    const el = $("#svfloorname"); if (el) el.textContent = "· " + (SK.name || "现场草图") + "（绘制草图）";
    if (showToast) toast("草图已同步到「现场勘测」页，可直接点击打点");
  } catch (e) { if (showToast) toast("同步失败：" + e.message); }
}
function skExportPNG() {
  if (HS.floor !== "sketch") return toast("请先新建草图");
  bg.toBlob(b => { dl(b, "现场草图_" + new Date().toISOString().slice(0, 10) + ".png"); toast("草图 PNG 已导出"); });
}

/* ====================================================================
   ASCII DXF 导入（批次 3）：读 CAD 图纸的线段 → 草图墙 → 人工修墙
   · 支持 LINE / LWPOLYLINE / POLYLINE+VERTEX / ARC / CIRCLE
   · 只读 ENTITIES 段，按包围盒等比铺进 1000×700 坐标系（Y 轴翻转）
   · 图层名推断初始材质，导入后仍可用「草图编辑」逐段改材质 / 删除 / 开门洞
   · 不支持二进制 DXF、块参照 INSERT、字型 / 填充、三维实体；也不自动识别门洞
   ==================================================================== */
const DXF_UNITS = { 0: 0.001, 1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1,
  8: 0.0000254, 9: 0.0000254, 10: 0.9144, 14: 0.1 };

function dxfRecords(text) {
  const lines = String(text).replace(/\r\n?/g, "\n").split("\n");
  const recs = []; let cur = null;
  for (let i = 0; i + 1 < lines.length; i += 2) {
    const code = parseInt(lines[i].trim(), 10);
    if (isNaN(code)) continue;
    const val = lines[i + 1].trim();
    if (code === 0) { cur = { type: val.toUpperCase(), data: [] }; recs.push(cur); }
    else if (cur) cur.data.push([code, val]);
  }
  return recs;
}
function dxfFirst(d, c, def) { const x = d.find(p => p[0] === c); return x ? x[1] : def; }
function dxfNum(v) { const f = parseFloat(v); return isFinite(f) ? f : 0; }
function dxfMaterial(layer) {
  const s = String(layer || "").toUpperCase();
  if (/WALL|墙|CONC|承重|SHEAR|STRUCT|COLUMN/.test(s)) return "concrete";
  if (/GLASS|玻璃|WINDOW|窗|CURTAIN/.test(s)) return "glass";
  if (/DOOR|门|WOOD|木/.test(s)) return "wood";
  if (/METAL|金属|SHIELD|屏蔽/.test(s)) return "metal";
  if (/BRICK|砖/.test(s)) return "brick";
  return "drywall";
}
function dxfParse(text) {
  const recs = dxfRecords(text), segs = [], layers = {}, errors = [];
  let inEnt = false, insunits = 4, poly = [], polyLayer = "0", polyClosed = false, skipped = 0;
  function push(x1, y1, x2, y2, layer) {
    if (![x1, y1, x2, y2].every(isFinite) || Math.hypot(x2 - x1, y2 - y1) < 1e-7) return;
    const k = layer || "0"; layers[k] = (layers[k] || 0) + 1;
    segs.push({ x1, y1, x2, y2, layer: k });
  }
  function flushPoly() {
    for (let i = 1; i < poly.length; i++) push(poly[i - 1][0], poly[i - 1][1], poly[i][0], poly[i][1], polyLayer);
    if (polyClosed && poly.length > 2) push(poly[poly.length - 1][0], poly[poly.length - 1][1], poly[0][0], poly[0][1], polyLayer);
    poly = []; polyClosed = false;
  }
  for (const r of recs) {
    if (r.type === "SECTION") {
      inEnt = String(dxfFirst(r.data, 2, "")).toUpperCase() === "ENTITIES";
      for (let i = 0; i < r.data.length - 1; i++) {
        if (r.data[i][0] === 9 && String(r.data[i][1]).toUpperCase() === "$INSUNITS") {
          const u = parseInt(r.data[i + 1][1], 10); if (!isNaN(u)) insunits = u;
        }
      }
      continue;
    }
    if (r.type === "ENDSEC") { if (inEnt) flushPoly(); inEnt = false; continue; }
    if (r.type === "EOF") break;
    if (!inEnt) continue;
    const d = r.data, layer = String(dxfFirst(d, 8, "0") || "0");
    if (r.type === "POLYLINE") {
      flushPoly(); poly = []; polyLayer = layer;
      polyClosed = !!(parseInt(dxfFirst(d, 70, "0"), 10) & 1);
    } else if (r.type === "VERTEX") {
      poly.push([dxfNum(dxfFirst(d, 10, 0)), dxfNum(dxfFirst(d, 20, 0))]);
    } else if (r.type === "SEQEND") {
      flushPoly();
    } else if (r.type === "LINE") {
      push(dxfNum(dxfFirst(d, 10, 0)), dxfNum(dxfFirst(d, 20, 0)),
        dxfNum(dxfFirst(d, 11, 0)), dxfNum(dxfFirst(d, 21, 0)), layer);
    } else if (r.type === "LWPOLYLINE") {
      const xs = d.filter(p => p[0] === 10), ys = d.filter(p => p[0] === 20), pts = [];
      for (let i = 0; i < Math.min(xs.length, ys.length); i++) pts.push([dxfNum(xs[i][1]), dxfNum(ys[i][1])]);
      for (let i = 1; i < pts.length; i++) push(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1], layer);
      if ((parseInt(dxfFirst(d, 70, "0"), 10) & 1) && pts.length > 2)
        push(pts[pts.length - 1][0], pts[pts.length - 1][1], pts[0][0], pts[0][1], layer);
    } else if (r.type === "CIRCLE" || r.type === "ARC") {
      // CAD 里的圆通常是柱 / 设备 / 家具符号，门弧也不该当墙；只有明确画在墙图层上的圆才保留
      const up = String(layer).toUpperCase();
      const wallLayer = /WALL|墙|CONC|承重|PARTITION|隔墙/.test(up);
      const symbolLayer = /DOOR|门|FURN|家具|COLUMN|柱|EQUIP|设备|SYMBOL|图框/.test(up);
      if ((r.type === "CIRCLE" && !wallLayer) || (r.type === "ARC" && symbolLayer)) {
        skipped++; continue;
      }
      const cx = dxfNum(dxfFirst(d, 10, 0)), cy = dxfNum(dxfFirst(d, 20, 0)), rad = dxfNum(dxfFirst(d, 40, 0));
      let a0 = 0, a1 = 360;
      if (r.type === "ARC") { a0 = dxfNum(dxfFirst(d, 50, 0)); a1 = dxfNum(dxfFirst(d, 51, 360)); if (a1 <= a0) a1 += 360; }
      if (rad > 0) {
        const steps = Math.max(8, Math.min(48, Math.ceil((a1 - a0) / 15)));
        let px = 0, py = 0;
        for (let i = 0; i <= steps; i++) {
          const a = (a0 + (a1 - a0) * i / steps) * Math.PI / 180;
          const x = cx + rad * Math.cos(a), y = cy + rad * Math.sin(a);
          if (i) push(px, py, x, y, layer);
          px = x; py = y;
        }
      }
    }
  }
  flushPoly();
  if (!segs.length) {
    errors.push("未找到 LINE / LWPOLYLINE / POLYLINE / ARC / CIRCLE 线段（二进制 DXF 与块参照 INSERT 暂不支持）");
    return { segments: [], layers, errors, width_m: 40 };
  }
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  segs.forEach(s => {
    minx = Math.min(minx, s.x1, s.x2); maxx = Math.max(maxx, s.x1, s.x2);
    miny = Math.min(miny, s.y1, s.y2); maxy = Math.max(maxy, s.y1, s.y2);
  });
  const bw = Math.max(1e-6, maxx - minx), bh = Math.max(1e-6, maxy - miny);
  const unit = DXF_UNITS[insunits] !== undefined ? DXF_UNITS[insunits] : 0.001;
  let width_m = bw * unit;
  if (!isFinite(width_m) || width_m < 2 || width_m > 2000) width_m = 40;
  const scale = Math.min(920 / bw, 620 / bh);
  const ox = (1000 - bw * scale) / 2 - minx * scale;
  const oy = (700 - bh * scale) / 2 - miny * scale;
  const mapped = segs.slice(0, 1500).map(s => ({
    x1: Math.round((s.x1 * scale + ox) * 10) / 10,
    y1: Math.round((700 - (s.y1 * scale + oy)) * 10) / 10,
    x2: Math.round((s.x2 * scale + ox) * 10) / 10,
    y2: Math.round((700 - (s.y2 * scale + oy)) * 10) / 10,
    layer: s.layer
  }));
  if (segs.length > 1500) errors.push("线段超过 1500 条，仅导入前 1500 条；建议先在 CAD 里清理标注与填充图层");
  return { segments: mapped, layers, errors, skipped, width_m: Math.round(width_m * 10) / 10,
    unit_name: (insunits === 6 ? "m" : insunits === 4 ? "mm" : insunits === 1 ? "in" : "DXF 单位"),
    bbox: { minx, miny, maxx, maxy } };
}
function dxfApply(text, filename) {
  const r = dxfParse(text);
  if (!r.segments.length) { toast("DXF 导入失败：" + (r.errors[0] || "无线段")); return; }
  if ((HS.aps && HS.aps.length) || (HS.samples && HS.samples.length) || (HS.walls && HS.walls.length)) {
    if (!confirm("导入 DXF 会替换当前 AP、墙体与实测打点（已保存的项目不受影响），继续？")) return;
  }
  SK = skDefault();
  SK.name = "DXF · " + String(filename || "图纸").replace(/\.dxf$/i, "").slice(0, 24);
  SK.width_m = r.width_m;
  SK.walls = r.segments.map(s => ({ id: SK.nextId++, x1: s.x1, y1: s.y1, x2: s.x2, y2: s.y2,
    material: dxfMaterial(s.layer), layer: s.layer }));
  SK.labels = [{ id: SK.nextId++, x: 500, y: 30,
    text: "DXF 导入 · " + Object.keys(r.layers).length + " 个图层 · 墙体材质为推断值，请人工核对" }];
  HS.floor = "sketch"; HS.aps = []; HS.samples = []; HS.walls = []; HS.heat = null;
  HS.ppm = Math.round(920 / (SK.width_m || 40) * 100) / 100;
  $("#hppm").value = HS.ppm; hc.clearRect(0, 0, W, H);
  $("#floorinfo").textContent = "DXF 草图 · " + SK.name;
  $("#stagehint").style.display = "none";
  const p = $("#skpanel"); if (p) p.open = true;
  const wn = $("#skwidthm"); if (wn) wn.value = SK.width_m;
  const mn = $("#skmaterial"); if (mn) mn.value = SK.material;
  skSetTool("pick");
  skSyncWalls(); drawFloor(); drawEdit(); renderAPList(); skUpdateInfo(); skApplyDimText(); markDirty();
  skSyncToSurvey(false);
  const info = $("#dxfinfo");
  if (info) info.innerHTML = "已导入 <b>" + SK.walls.length + "</b> 段墙 · " + Object.keys(r.layers).length +
    " 图层 · 外框约 <b>" + SK.width_m + " m</b>（" + esc(r.unit_name || "DXF 单位") + "）" +
    (r.skipped ? " · 已跳过 " + r.skipped + " 个柱 / 设备 / 门弧符号（非墙体）" : "") +
    (r.errors.length ? "<br><span style='color:var(--warn)'>" + esc(r.errors.join("；")) + "</span>" : "");
  toast("DXF 已导入 " + SK.walls.length + " 段墙；请用「选择 / 改名」逐段核对材质，或删除标注线与开门洞");
}
const dxfInput = $("#hdxffile");
if (dxfInput) dxfInput.addEventListener("change", e => {
  const f = e.target.files[0]; if (!f) return;
  const rd = new FileReader();
  rd.onload = () => { try { dxfApply(String(rd.result), f.name); } catch (err) { toast("DXF 解析异常：" + err.message); } };
  rd.onerror = () => toast("DXF 文件读取失败");
  rd.readAsText(f, "utf-8");
  e.target.value = "";
});

/* ====================================================================
   图片识别候选墙（v0.13）：灰度 + 水平/垂直连续暗像素游程
   · 纯 Canvas 像素运算，零依赖、零联网、不做 AI 语义识别
   · 输出的是「候选直线段」，必须人工用草图工具核对材质 / 删标注线 / 开门洞
   · 不支持斜墙、门弧、填充、文字标注的语义区分，也不自动识别房间
   ==================================================================== */
function imgWallScan() {
  let data;
  try { data = bgc.getImageData(0, 0, W, H).data; }
  catch (e) { return { segs: [], error: "无法读取画布像素：" + e.message }; }
  const lum = new Uint8Array(W * H);
  let sum = 0;
  for (let i = 0, p = 0; i < lum.length; i++, p += 4) {
    const v = (data[p] * .299 + data[p + 1] * .587 + data[p + 2] * .114) | 0;
    lum[i] = v; sum += v;
  }
  const avg = sum / lum.length;
  const thr = Math.max(55, Math.min(190, avg * .70));
  const dark = i => lum[i] < thr;
  const H_MIN = 70, V_MIN = 50, MAX_RAW = 3500;
  const hSegs = [], vSegs = [];
  for (let y = 4; y < H - 4 && hSegs.length < MAX_RAW; y++) {
    let st = -1;
    for (let x = 0; x <= W; x++) {
      const on = x < W && dark(y * W + x);
      if (on && st < 0) st = x;
      else if (!on && st >= 0) {
        const len = x - st;
        if (len >= H_MIN && len <= W - 6) hSegs.push({ y, x1: st, x2: x - 1, len });
        st = -1;
      }
    }
  }
  for (let x = 4; x < W - 4 && vSegs.length < MAX_RAW; x++) {
    let st = -1;
    for (let y = 0; y <= H; y++) {
      const on = y < H && dark(y * W + x);
      if (on && st < 0) st = y;
      else if (!on && st >= 0) {
        const len = y - st;
        if (len >= V_MIN && len <= H - 6) vSegs.push({ x, y1: st, y2: y - 1, len });
        st = -1;
      }
    }
  }
  function merge(list, vertical) {
    list.sort((a, b) => (vertical ? a.x - b.x : a.y - b.y) || b.len - a.len);
    const out = [];
    for (const s of list) {
      let hit = null;
      for (let i = out.length - 1; i >= Math.max(0, out.length - 60); i--) {
        const o = out[i];
        if ((vertical ? Math.abs(o.x - s.x) : Math.abs(o.y - s.y)) > 3) continue;
        const a1 = vertical ? o.y1 : o.x1, a2 = vertical ? o.y2 : o.x2;
        const b1 = vertical ? s.y1 : s.x1, b2 = vertical ? s.y2 : s.x2;
        if (Math.min(a2, b2) - Math.max(a1, b1) > Math.min(a2 - a1, b2 - b1) * .55) { hit = o; break; }
      }
      if (hit) {
        if (vertical) { hit.y1 = Math.min(hit.y1, s.y1); hit.y2 = Math.max(hit.y2, s.y2); }
        else { hit.x1 = Math.min(hit.x1, s.x1); hit.x2 = Math.max(hit.x2, s.x2); }
        hit.len = Math.max(hit.len, s.len);
      } else out.push(Object.assign({}, s));
    }
    return out;
  }
  const segs = [];
  merge(hSegs, false).sort((a, b) => b.len - a.len).slice(0, 120).forEach(s =>
    segs.push({ x1: s.x1, y1: s.y, x2: s.x2, y2: s.y, material: "drywall", layer: "image-H" }));
  merge(vSegs, true).sort((a, b) => b.len - a.len).slice(0, 120).forEach(s =>
    segs.push({ x1: s.x, y1: s.y1, x2: s.x, y2: s.y2, material: "drywall", layer: "image-V" }));
  const uniq = [];
  segs.forEach(s => {
    if (!uniq.some(o => Math.abs(o.x1 - s.x1) < 4 && Math.abs(o.y1 - s.y1) < 4 &&
      Math.abs(o.x2 - s.x2) < 4 && Math.abs(o.y2 - s.y2) < 4)) uniq.push(s);
  });
  return { segs: uniq.slice(0, 180), threshold: Math.round(thr), avg: Math.round(avg),
    raw: hSegs.length + vSegs.length };
}
function imgApplyWalls() {
  const snapshot = bg.toDataURL("image/png");
  const r = imgWallScan();
  const info = $("#wallinfo");
  if (r.error || !r.segs.length) {
    if (info) info.innerHTML = '<span style="color:var(--warn)">未能从当前底图提取到候选墙：' +
      esc(r.error || "暗色连续直线太少。请换对比度更高的 CAD 风格平面图，或用「绘制草图 / 导入 DXF」") + "</span>";
    return;
  }
  if ((HS.aps && HS.aps.length) || (HS.samples && HS.samples.length) || (HS.walls && HS.walls.length)) {
    if (!confirm("识别候选墙会替换当前 AP、墙体与实测打点（已保存的项目不受影响），继续？")) return;
  }
  SK = skDefault();
  SK.name = "图片候选墙 · 需人工修墙";
  SK.walls = r.segs.map(s => ({ id: SK.nextId++, x1: s.x1, y1: s.y1, x2: s.x2, y2: s.y2,
    material: s.material, layer: s.layer }));
  SK.labels = [{ id: SK.nextId++, x: 500, y: 26,
    text: "图片自动识别 · 候选墙 " + SK.walls.length + " 段（材质均为石膏板推断值，请逐段核对）" }];
  HS.floor = "sketch"; HS.aps = []; HS.samples = []; HS.walls = []; HS.heat = null;
  HS.ppm = Math.round(920 / (SK.width_m || 40) * 100) / 100;
  $("#hppm").value = HS.ppm; hc.clearRect(0, 0, W, H);
  $("#floorinfo").textContent = "图片候选墙 · " + SK.walls.length + " 段";
  $("#stagehint").style.display = "none";
  const p = $("#skpanel"); if (p) p.open = true;
  skSetTool("pick");
  skSyncWalls(); drawFloor(); drawEdit(); renderAPList(); skUpdateInfo(); skApplyDimText(); markDirty();
  skSyncToSurvey(false);
  const im = new Image();
  im.onload = () => { SK.baseImageObj = im; drawFloor(); skSyncToSurvey(false); };
  im.src = snapshot;
  if (info) info.innerHTML = "已提取 <b>" + SK.walls.length + "</b> 段候选墙 · 灰度阈值 " + r.threshold +
    "（平均亮度 " + r.avg + "）· 原始游程 " + r.raw + " 条。<br><span style='color:var(--warn)'>" +
    "这是几何候选线段，<b>不是 AI 识别成果</b>：请用「选择 / 改名」核对材质、删掉标注线与图框、在墙上开门洞，再放 AP 计算。</span>";
  toast("已提取 " + SK.walls.length + " 段候选墙，请人工修墙后再计算");
}
const imgWallBtn = $("#himgwalls");
if (imgWallBtn) imgWallBtn.addEventListener("click", imgApplyWalls);

/* --- AP --- */
const DEFAULT_AP_MODELS = ["CW9176I", "CW9178I", "5760-10", "6760-X1", "WA7539", "RG-AP9220",
  "FAP-431G", "AP-755", "AP47", "R770", "U7 Pro", "MR57",
  "AP6010DN-AGN", "WA2620-AGN", "AP-105", "MR16", "FAP-220B", "自定义 / 未收录型号"];
function findModel(model) {
  if (!model) return null;
  const rs = CAT.records;
  let r = rs.find(x => x.models === model);
  if (r) return r;
  const key = String(model).replace(/^[^ ]+\s+/, "").trim().toLowerCase();
  if (!key) return null;
  r = rs.find(x => x.models.toLowerCase() === key);
  if (r) return r;
  return rs.find(x => x.models.toLowerCase().indexOf(key) >= 0 || key.indexOf(x.models.toLowerCase()) >= 0) || null;
}
function apMeta(a) {
  const r = findModel(a.model);
  if (!r) return { text: "未收录 · 通用 SNMP v2c 降级", cls: "t-pend" };
  return { text: r.vendor + " · " + r.wifi_gen + " · " + r.lifecycle + " · " + r.capability_tier, cls: (r.lifecycle.indexOf("EOL") >= 0 || r.lifecycle.indexOf("历史") >= 0) ? "t-eol" : "t-ok" };
}
function inferParams(model) {
  const r = findModel(model);
  let tx = 20, gain = 4, only24 = false;
  if (r) {
    const g = r.wifi_gen || "", lc = r.lifecycle || "";
    if (["Wi-Fi 4", "Wi-Fi 3", "Wi-Fi 2", "Wi-Fi 1"].indexOf(g) >= 0) { tx = 14; gain = 2; only24 = true; }
    else if (g === "Wi-Fi 5") { tx = 17; gain = 3; }
    const b = String(r.bands || "");
    if (b.indexOf("2.4") >= 0 && b.indexOf("5") < 0 && b.indexOf("6") < 0) only24 = true;
    if (lc.indexOf("EOL") >= 0 || lc.indexOf("历史") >= 0) { tx = Math.min(tx, 14); gain = Math.min(gain, 2); }
  } else { tx = 17; gain = 3; }
  return { tx_power: tx, gain: gain, band: only24 ? "2.4" : "5" };
}
function addAP(x, y, model) {
  const m = model || DEFAULT_AP_MODELS[0], p = inferParams(m);
  const pool = HS.band === "2.4" ? CH_24 : CH_5;
  HS.aps.push({ id: HS.nextId++, x: Math.round(x), y: Math.round(y), model: m,
    tx_power: p.tx_power, gain: p.gain, band: p.band, enabled: true,
    channel: pool[HS.aps.length % pool.length] });
}
function renderAPList() {
  $("#apcount").textContent = HS.aps.length + " 台 / " + HS.samples.length + " 打点";
  const dl = $("#apmodels");
  if (!dl) {
    const d = document.createElement("datalist"); d.id = "apmodels";
    d.innerHTML = CAT.records.filter(r => r.category === "AP").map(r => '<option value="' + esc(r.models) + '">').join("");
    document.body.appendChild(d);
  }
  $("#aplist").innerHTML = HS.aps.length ? HS.aps.map((a, i) =>
    '<div style="border:1px solid var(--line2);border-radius:7px;padding:7px;margin-bottom:7px">' +
    '<div class="row" style="justify-content:space-between;margin-bottom:5px"><span><i class="chip" style="background:' + apColor(i) + ';display:inline-block;margin-right:5px"></i><b class="small">AP-' + a.id + '</b></span>' +
    '<button class="btn ghost sm" data-del="' + a.id + '" style="padding:1px 7px">×</button></div>' +
    '<input type="text" list="apmodels" data-f="model" data-id="' + a.id + '" value="' + esc(a.model) + '" style="font-size:11px;padding:4px 6px;margin-bottom:4px" title="可从型号库搜索选择">' +
    '<div class="tiny muted" style="margin-bottom:4px">(' + a.x + ', ' + a.y + ') · <span class="tag ' + (apInSSID(a) ? 't-ok' : 't-ph') + '">' + (apInSSID(a) ? '本 SSID' : '不广播本 SSID') + '</span></div>' +
    '<div class="tiny" style="margin-bottom:5px"><span class="tag ' + apMeta(a).cls + '">' + esc(apMeta(a).text) + '</span></div>' +
    '<div class="row tiny" style="gap:5px"><span>功率</span><input type="number" data-f="tx_power" data-id="' + a.id + '" value="' + a.tx_power + '" style="width:52px;padding:3px 5px;font-size:11px">' +
    '<span>增益</span><input type="number" data-f="gain" data-id="' + a.id + '" value="' + a.gain + '" style="width:46px;padding:3px 5px;font-size:11px">' +
    '<span>信道</span><input type="number" data-f="channel" data-id="' + a.id + '" value="' + (a.channel || 1) + '" style="width:48px;padding:3px 5px;font-size:11px">' +
    '<label style="display:flex;align-items:center;gap:3px;cursor:pointer"><input type="checkbox" data-f="only24" data-id="' + a.id + '" ' + (a.band === "2.4" ? "checked" : "") + ' style="width:auto">仅2.4G</label></div></div>').join("") : '<div class="tiny muted">暂无 AP，用「放置 AP」工具或下方按钮添加。</div>';
  $$("#aplist [data-del]").forEach(b => b.addEventListener("click", () => {
    HS.aps = HS.aps.filter(a => a.id !== +b.dataset.del); drawEdit(); renderAPList(); markDirty();
  }));
  $$("#aplist [data-f]").forEach(el => el.addEventListener("change", () => {
    const a = HS.aps.find(x => x.id === +el.dataset.id); if (!a) return;
    const f = el.dataset.f;
    if (f === "model") { a.model = el.value; const p = inferParams(a.model); a.tx_power = p.tx_power; a.gain = p.gain; a.band = p.band; renderAPList(); }
    else if (f === "only24") a.band = el.checked ? "2.4" : "5";
    else a[f] = +el.value;
    markDirty(); drawEdit();
  }));
}
function apColor(i) { const p = ["#1f6feb", "#12855b", "#b4740a", "#8b5cf6", "#c7372f", "#0891b2", "#db2777", "#65a30d"]; return p[i % p.length]; }

/* --- 交互 --- */
function pt(e) { const r = edit.getBoundingClientRect(); return { x: (e.clientX - r.left) / r.width * W, y: (e.clientY - r.top) / r.height * H }; }
function near(p, arr, r) { let bi = -1, bd = r * r; arr.forEach((o, i) => { const d = (o.x - p.x) ** 2 + (o.y - p.y) ** 2; if (d < bd) { bd = d; bi = i; } }); return bi; }
function distToSeg(p, w) {
  const dx = w.x2 - w.x1, dy = w.y2 - w.y1, L = dx * dx + dy * dy;
  let t = L ? ((p.x - w.x1) * dx + (p.y - w.y1) * dy) / L : 0; t = Math.max(0, Math.min(1, t));
  return Math.hypot(p.x - (w.x1 + t * dx), p.y - (w.y1 + t * dy));
}
/* mousemove 合并到一帧：拖动 AP / 画墙 / 门洞 hover 不再每个事件都全量重绘三个图层 */
let CANVAS_RAF = 0, PENDING_PREVIEW = false;
function scheduleCanvas(preview) {
  PENDING_PREVIEW = PENDING_PREVIEW || !!preview;
  if (CANVAS_RAF) return;
  CANVAS_RAF = requestAnimationFrame(() => {
    CANVAS_RAF = 0;
    drawEdit();
    if (PENDING_PREVIEW) previewHeat();
    PENDING_PREVIEW = false;
  });
}
/* 画布内的深色小标签：实时尺寸 / 门洞宽度 / 吸附提示 */
function drawMeasureTag(c, x, y, text, accent) {
  c.save();
  c.font = "600 11px Consolas,monospace";
  const w = c.measureText(text).width + 12;
  const X = Math.max(4, Math.min(W - w - 4, x + 12)), Y = Math.max(4, Math.min(H - 24, y - 27));
  c.fillStyle = "rgba(15,23,42,.88)";
  c.beginPath();
  if (c.roundRect) c.roundRect(X, Y, w, 20, 5); else c.rect(X, Y, w, 20);
  c.fill();
  c.fillStyle = accent || "#fff"; c.textAlign = "left"; c.textBaseline = "middle";
  c.fillText(text, X + 6, Y + 10);
  c.restore();
}
function onDown(e) {
  const p = pt(e);
  if (HS.floor === "sketch" && SK.tool !== "off") { skDown(e, p); return; }
  HS.drag = { start: p, cur: p };
  if (HS.tool === "select") {
    const i = near(p, HS.aps, 22);
    HS.drag.ap = i >= 0 ? HS.aps[i].id : null;
  } else if (HS.tool === "ap") {
    addAP(p.x, p.y); renderAPList(); drawEdit(); markDirty();
  } else if (HS.tool === "sample") {
    const s = activeSSID();
    HS.samples.push({ x: Math.round(p.x), y: Math.round(p.y), rssi: +$("#srssi").value || -62, ssid: s ? s.name : "" });
    drawEdit(); renderAPList(); markDirty();
  } else if (HS.tool === "erase") {
    let done = false;
    let i = near(p, HS.aps, 20); if (i >= 0) { HS.aps.splice(i, 1); done = true; }
    if (!done) { i = near(p, HS.samples, 16); if (i >= 0) { HS.samples.splice(i, 1); done = true; } }
    if (!done) { i = HS.walls.findIndex(w => distToSeg(p, w) < 10); if (i >= 0) { HS.walls.splice(i, 1); done = true; } }
    if (done) { drawFloorIfDemo(); drawEdit(); renderAPList(); markDirty(); toast("已删除元素"); }
  }
}
function onMove(e) {
  if (HS.floor === "sketch" && SK.tool !== "off" && SK.drag) { skMove(e, pt(e)); return; }
  /* 门洞工具：鼠标悬停即高亮目标墙并预览开口宽度 */
  if (HS.floor === "sketch" && SK.tool === "door" && !SK.drag) {
    SK.hover = pt(e); scheduleCanvas(false); return;
  }
  if (!HS.drag) return;
  HS.drag.cur = pt(e);
  const moving = HS.tool === "select" && !!HS.drag.ap;
  if (moving) {
    const a = HS.aps.find(x => x.id === HS.drag.ap);
    if (a) { a.x = Math.round(HS.drag.cur.x); a.y = Math.round(HS.drag.cur.y); HS.dirty = true; HS.drag.moved = true; }
  }
  // 拖 AP 时同步刷新热力图（轻量预览），一帧内合并重绘
  scheduleCanvas(moving);
}
function onUp(e) {
  if (HS.floor === "sketch" && SK.tool !== "off" && SK.drag) { skUp(e, pt(e)); return; }
  if (!HS.drag) return;
  const d = HS.drag; HS.drag = null;
  const len = Math.hypot(d.cur.x - d.start.x, d.cur.y - d.start.y);
  let changed = !!d.moved;
  if (HS.tool === "wall" && len > 8) {
    HS.walls.push({ x1: Math.round(d.start.x), y1: Math.round(d.start.y), x2: Math.round(d.cur.x), y2: Math.round(d.cur.y), material: $("#wmaterial").value, user: true });
    changed = true; toast("已添加墙体（" + $("#wmaterial").selectedOptions[0].text.split(" · ")[0] + "）");
  } else if (HS.tool === "ruler" && len > 20) {
    const m = parseFloat(prompt("这条线的实际长度是多少米？", "10"));
    if (m > 0) {
      HS.ppm = Math.round(len / m * 100) / 100;
      $("#hppm").value = HS.ppm; drawFloorIfDemo(); changed = true;
      toast("比例尺已标定：" + HS.ppm.toFixed(2) + " 像素/米");
    }
  }
  if (d.ap) renderAPList();
  drawEdit();
  if (changed) markDirty();   // 松手后按完整模型（含墙体/实测残差/干扰）自动重算
}
function drawEdit() {
  ec.clearRect(0, 0, W, H);
  /* 草图模式：选中框与拖拽预览画在编辑层，底图无需每次重绘 */
  if (HS.floor === "sketch") {
    const o = skSelObj();
    if (o && SK.sel) {
      ec.strokeStyle = "#1f6feb"; ec.lineWidth = 2; ec.setLineDash([5, 4]);
      if (SK.sel.type === "room") ec.strokeRect(Math.min(o.x1, o.x2) - 4, Math.min(o.y1, o.y2) - 4, Math.abs(o.x2 - o.x1) + 8, Math.abs(o.y2 - o.y1) + 8);
      else if (SK.sel.type === "wall") { ec.beginPath(); ec.moveTo(o.x1, o.y1); ec.lineTo(o.x2, o.y2); ec.stroke(); }
      else ec.strokeRect(o.x - 13, o.y - 13, 26, 26);
      ec.setLineDash([]);
    }
    if (SK.drag && (SK.tool === "room" || SK.tool === "wall")) {
      const a = SK.drag.start, b = SK.drag.cur;
      ec.strokeStyle = "#1f6feb"; ec.lineWidth = 2; ec.setLineDash([6, 4]);
      if (SK.tool === "room") ec.strokeRect(Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y));
      else { ec.beginPath(); ec.moveTo(a.x, a.y); ec.lineTo(b.x, b.y); ec.stroke(); }
      ec.setLineDash([]);
      const ppm = Math.max(HS.ppm, .5);
      const txt = SK.tool === "room"
        ? (Math.abs(b.x - a.x) / ppm).toFixed(1) + " × " + (Math.abs(b.y - a.y) / ppm).toFixed(1) + " m"
        : (Math.hypot(b.x - a.x, b.y - a.y) / ppm).toFixed(1) + " m · " +
          Math.round(Math.atan2(b.y - a.y, b.x - a.x) * 180 / Math.PI) + "°";
      drawMeasureTag(ec, b.x, b.y, txt);
    }
    if (SK.snapHint) {
      const s = SK.snapHint;
      ec.strokeStyle = "#f59e0b"; ec.lineWidth = 2;
      ec.beginPath(); ec.arc(s.x, s.y, 7, 0, 7); ec.stroke();
      ec.beginPath(); ec.moveTo(s.x - 11, s.y); ec.lineTo(s.x + 11, s.y);
      ec.moveTo(s.x, s.y - 11); ec.lineTo(s.x, s.y + 11); ec.stroke();
    }
    if (SK.tool === "door" && SK.hover) {
      const walls = skAllWalls(), wi = skNearestWall(SK.hover);
      if (wi >= 0) {
        const w = walls[wi], dx = w.x2 - w.x1, dy = w.y2 - w.y1, L = dx * dx + dy * dy;
        let k = L ? ((SK.hover.x - w.x1) * dx + (SK.hover.y - w.y1) * dy) / L : 0;
        k = Math.max(.06, Math.min(.94, k));
        const px = w.x1 + k * dx, py = w.y1 + k * dy;
        const len = Math.hypot(dx, dy) || 1, ux = dx / len, uy = dy / len;
        const half = Math.max(5, (SK.door_w_m || .9) * HS.ppm / 2);
        ec.save();
        ec.strokeStyle = "#1f6feb"; ec.lineWidth = 4; ec.globalAlpha = .7;
        ec.beginPath(); ec.moveTo(w.x1, w.y1); ec.lineTo(w.x2, w.y2); ec.stroke();
        ec.globalAlpha = 1; ec.strokeStyle = "#f59e0b"; ec.lineWidth = 2.5; ec.setLineDash([4, 3]);
        ec.beginPath(); ec.moveTo(px - ux * half, py - uy * half); ec.lineTo(px + ux * half, py + uy * half); ec.stroke();
        ec.setLineDash([]); ec.restore();
        drawMeasureTag(ec, px, py, "门洞 " + (SK.door_w_m || .9) + " m", "#fbbf24");
      }
    }
  }
  if ($("#lwalls").checked) {
    // 用户新增的墙（示例墙已画在底图）
    ec.lineWidth = 2; ec.setLineDash([6, 4]);
    HS.walls.forEach(w => { if (w.user) { ec.strokeStyle = "#ef6c5a"; ec.beginPath(); ec.moveTo(w.x1, w.y1); ec.lineTo(w.x2, w.y2); ec.stroke(); } });
    ec.setLineDash([]);
  }
  if (HS.drag && ["wall", "ruler"].indexOf(HS.tool) >= 0) {
    ec.strokeStyle = HS.tool === "ruler" ? "#1f6feb" : "#ef6c5a"; ec.lineWidth = 2.5; ec.setLineDash([7, 5]);
    ec.beginPath(); ec.moveTo(HS.drag.start.x, HS.drag.start.y); ec.lineTo(HS.drag.cur.x, HS.drag.cur.y); ec.stroke(); ec.setLineDash([]);
    const a = HS.drag.start, b = HS.drag.cur, ppm = Math.max(HS.ppm, .5);
    const len = Math.hypot(b.x - a.x, b.y - a.y);
    drawMeasureTag(ec, b.x, b.y, HS.tool === "ruler"
      ? (len / ppm).toFixed(1) + " m（松手后输入实际长度）"
      : (len / ppm).toFixed(1) + " m · " + Math.round(Math.atan2(b.y - a.y, b.x - a.x) * 180 / Math.PI) + "°");
  }
  if ($("#lsamples").checked) HS.samples.forEach(s => {
    ec.fillStyle = "rgba(255,255,255,.92)"; ec.strokeStyle = "#334155"; ec.lineWidth = 1.5;
    ec.beginPath(); ec.rect(s.x - 5, s.y - 5, 10, 10); ec.fill(); ec.stroke();
    ec.fillStyle = "#334155"; ec.font = "10px Consolas,monospace"; ec.textAlign = "left"; ec.fillText(s.rssi, s.x + 8, s.y + 3);
  });
  if ($("#laps").checked) HS.aps.forEach((a, i) => {
    const col = apColor(i);
    ec.beginPath(); ec.arc(a.x, a.y, 13, 0, 7); ec.fillStyle = "rgba(255,255,255,.94)"; ec.fill();
    ec.lineWidth = 3; ec.strokeStyle = col; ec.stroke();
    ec.beginPath(); ec.arc(a.x, a.y, 5, 0, 7); ec.fillStyle = col; ec.fill();
    ec.font = "bold 11px Consolas,monospace"; ec.fillStyle = "#1b2430"; ec.textAlign = "center";
    ec.fillText("AP-" + a.id, a.x, a.y - 19);
    if (a.enabled === false) { ec.strokeStyle = "#c7372f"; ec.beginPath(); ec.moveTo(a.x - 10, a.y - 10); ec.lineTo(a.x + 10, a.y + 10); ec.stroke(); }
  });
  if ($("#llive").checked && LIVE_STATE.clients.length) {
    LIVE_STATE.clients.forEach(c => {
      ec.beginPath(); ec.arc(c.x, c.y, 4.2, 0, 7);
      ec.fillStyle = statusColor(c.status); ec.fill();
      ec.strokeStyle = "rgba(255,255,255,.9)"; ec.lineWidth = 1; ec.stroke();
    });
  }
  ec.textAlign = "left";
}

/* --- 热力图计算与渲染 --- */
const STOPS = [[-45, [26, 152, 80]], [-60, [102, 189, 99]], [-67, [214, 224, 110]], [-70, [254, 224, 139]], [-78, [252, 141, 89]], [-85, [213, 62, 79]], [-100, [140, 35, 66]]];
function colorOf(v) {
  if (v >= STOPS[0][0]) return STOPS[0][1];
  for (let i = 0; i < STOPS.length - 1; i++) {
    const [a, ca] = STOPS[i], [b, cb] = STOPS[i + 1];
    if (v >= b) {
      const t = Math.max(0, Math.min(1, (a - v) / (a - b)));
      return [0, 1, 2].map(k => Math.round(ca[k] + (cb[k] - ca[k]) * t));
    }
  }
  return STOPS[STOPS.length - 1][1];
}
const SINR_STOPS = [[35, [26, 152, 80]], [25, [166, 213, 100]], [20, [254, 224, 139]], [14, [252, 141, 89]], [8, [213, 62, 79]], [0, [140, 35, 66]]];
function colorOfSinr(v) {
  if (v >= SINR_STOPS[0][0]) return SINR_STOPS[0][1];
  for (let i = 0; i < SINR_STOPS.length - 1; i++) {
    const [a, ca] = SINR_STOPS[i], [b, cb] = SINR_STOPS[i + 1];
    if (v >= b) {
      const t = Math.max(0, Math.min(1, (a - v) / (a - b)));
      return [0, 1, 2].map(k => Math.round(ca[k] + (cb[k] - ca[k]) * t));
    }
  }
  return SINR_STOPS[SINR_STOPS.length - 1][1];
}
async function runHeat() {
  // 刚导入 DXF / 新建草图时还没有 AP：不要画一张全红「弱信号」层盖住底图，
  // 人工修墙阶段需要看清墙线；放上第一台 AP 后自动重算会正常出图。
  if (!(HS.aps || []).some(a => a.enabled !== false)) {
    HS.heat = null; hc.clearRect(0, 0, W, H);
    $("#hstatus").textContent = "图纸已就绪 · 请先在画布上放置至少 1 台 AP，再计算热力图";
    return;
  }
  const btn = $("#hrun"); btn.disabled = true; btn.innerHTML = '<span class="loading"></span>计算中…';
  $("#hstatus").textContent = "正在计算传播模型与实测残差校正…";
  const g = { s: [80, 56], m: [120, 84], l: [160, 112] }[HS.grid];
  try {
    const r = await api("/api/heatmap", {
      width: W, height: H, cols: g[0], rows: g[1], ppm: HS.ppm, band: HS.band, noise: HS.noise,
      aps: HS.aps, walls: HS.walls, samples: HS.samples,
      ssid_id: SSID_STATE.active_id,
      use_residuals: $("#hresid").checked,
      interferers: $("#happlyif").checked ? LIVE_STATE.interferers : [],
      // 「现场勘测」页校准并应用后，预测热力图改用实测拟合出的传播参数
      pl0: (SURVEY_STATE.calib && SURVEY_STATE.calib.applied) ? SURVEY_STATE.calib.pl0_fit : null,
      path_n: (SURVEY_STATE.calib && SURVEY_STATE.calib.applied) ? SURVEY_STATE.calib.n_fit : null,
    });
    HS.heat = r; HS.dirty = false; renderHeat(); renderMetrics(r.stats);
    $("#hgridinfo").textContent = r.stats.grid;
    $("#hstatus").textContent = "完成 · " + (r.stats.ssid || "-") + " · " + r.stats.grid + " 网格 · " + r.stats.model;
  } catch (e) { toast("计算失败：" + e.message); $("#hstatus").textContent = "计算失败"; }
  finally { btn.disabled = false; btn.textContent = "计算热力图"; }
}
/* ---------------- 色标 / 图例 / 等值线 ---------------- */
/* 色标随图层切换：RSSI 用 dBm 刻度，SINR 用 dB 刻度；覆盖门限取当前 SSID 的 min_rssi，
   不再写死 -67。图例、等值线、导出 PNG 三处共用同一份元数据。 */
function heatLayerMeta() {
  const sinr = !!($("#lsinr") && $("#lsinr").checked && HS.heat && HS.heat.sinr && HS.heat.sinr.length);
  const thr = (HS.heat && HS.heat.stats && HS.heat.stats.coverage_threshold != null)
    ? HS.heat.stats.coverage_threshold : -67;
  if (sinr) return { sinr: true, min: 0, max: 35, thr: 20, unit: "dB", fn: colorOfSinr,
    left: "0", right: "35", thrText: "20 dB" };
  return { sinr: false, min: -100, max: -45, thr: thr, unit: "dBm", fn: colorOf,
    left: "-100", right: "-45", thrText: thr + " dBm" };
}
function renderLegend() {
  const m = heatLayerMeta(), el = $("#legend");
  if (el) {
    const N = 32, out = [];
    for (let i = 0; i < N; i++) {
      const v = m.max - (m.max - m.min) * i / (N - 1);
      out.push('<i style="background:rgb(' + m.fn(v).join(",") + ')"></i>');
    }
    el.innerHTML = out.join("");
  }
  const mark = $("#legendmark");
  if (mark) {
    const t = Math.max(0, Math.min(1, (m.max - m.thr) / (m.max - m.min)));
    mark.style.left = (t * 100).toFixed(1) + "%";
    mark.title = (m.sinr ? "SINR 参考线 " : "覆盖门限 ") + m.thrText;
  }
  const lbl = $("#legendlbl");
  if (lbl) lbl.innerHTML = m.sinr
    ? '<div class="legend-row"><span>0 dB · 差</span><span>35 dB · 优</span></div><div class="legend-thr">▼ 参考线 20 dB</div>'
    : '<div class="legend-row"><span>-100 dBm · 盲区</span><span>-45 dBm · 极好</span></div>' +
      '<div class="legend-thr">▼ 覆盖门限 ' + esc(m.thrText) + '</div>';
}
/* marching-squares：在网格数值上求等值线。格点按后端采样位置 (c+.5)*cell_w 对齐，
   这样等值线与色块中心一致，不会整体偏移半格。 */
function contourSegments(vals, level) {
  const r = HS.heat, cw = W / r.cols, ch = H / r.rows, segs = [];
  const at = (c, rr) => vals[Math.max(0, Math.min(r.rows - 1, rr)) * r.cols + Math.max(0, Math.min(r.cols - 1, c))];
  for (let rr = 0; rr < r.rows - 1; rr++) for (let c = 0; c < r.cols - 1; c++) {
    const x = (c + .5) * cw, y = (rr + .5) * ch;
    const v = [at(c, rr), at(c + 1, rr), at(c + 1, rr + 1), at(c, rr + 1)];
    const P = [[x, y], [x + cw, y], [x + cw, y + ch], [x, y + ch]], pts = [];
    const edge = (a, b, pa, pb) => {
      if ((v[a] < level) !== (v[b] < level)) {
        const t = (level - v[a]) / ((v[b] - v[a]) || 1e-6);
        pts.push([pa[0] + (pb[0] - pa[0]) * t, pa[1] + (pb[1] - pa[1]) * t]);
      }
    };
    edge(0, 1, P[0], P[1]); edge(1, 2, P[1], P[2]); edge(2, 3, P[2], P[3]); edge(3, 0, P[3], P[0]);
    if (pts.length === 2) segs.push([pts[0], pts[1]]);
    else if (pts.length === 4) segs.push([pts[0], pts[1]], [pts[2], pts[3]]);
  }
  return segs;
}
function strokeContour(vals, level, color, dash, label) {
  const segs = contourSegments(vals, level);
  if (!segs.length) return 0;
  hc.save();
  hc.strokeStyle = color; hc.lineWidth = 2; hc.setLineDash(dash); hc.lineJoin = "round";
  hc.beginPath();
  segs.forEach(s => { hc.moveTo(s[0][0], s[0][1]); hc.lineTo(s[1][0], s[1][1]); });
  hc.stroke(); hc.setLineDash([]);
  if (label) {
    const s = segs[Math.floor(segs.length / 3)] || segs[0];
    const x = (s[0][0] + s[1][0]) / 2, y = (s[0][1] + s[1][1]) / 2;
    hc.font = "600 11px Consolas,monospace";
    const tw = hc.measureText(label).width;
    hc.fillStyle = "rgba(255,255,255,.9)"; hc.fillRect(x + 6, y - 15, tw + 10, 17);
    hc.fillStyle = color; hc.textAlign = "left"; hc.fillText(label, x + 11, y - 3);
  }
  hc.restore();
  return segs.length;
}
/* 导出 PNG 用的内嵌色标，保证脱离软件后仍能读数 */
function drawExportLegend(x, X, Y) {
  const m = heatLayerMeta(), w = 296, h = 56;
  x.save();
  x.fillStyle = "rgba(255,255,255,.94)"; x.strokeStyle = "rgba(30,41,59,.2)"; x.lineWidth = 1;
  x.beginPath();
  if (x.roundRect) x.roundRect(X, Y, w, h, 8); else x.rect(X, Y, w, h);
  x.fill(); x.stroke();
  const N = 24, bw = (w - 24) / N;
  for (let i = 0; i < N; i++) {
    const v = m.max - (m.max - m.min) * i / (N - 1), c = m.fn(v);
    x.fillStyle = "rgb(" + c.join(",") + ")";
    x.fillRect(X + 12 + i * bw, Y + 12, bw + 1, 12);
  }
  x.font = "10px Consolas,monospace"; x.fillStyle = "#334155"; x.textAlign = "left";
  x.fillText(m.left + " " + m.unit, X + 12, Y + 38);
  x.textAlign = "right"; x.fillText(m.right + " " + m.unit, X + w - 12, Y + 38);
  x.textAlign = "center"; x.fillStyle = "#b45309";
  x.fillText((m.sinr ? "参考线 " : "覆盖门限 ") + m.thrText, X + w / 2, Y + 38);
  x.textAlign = "left"; x.fillStyle = "#64748b";
  x.fillText("色标按当前图层生成 · 等值线为模型网格插值", X + 12, Y + 51);
  x.restore();
}
/* smooth=true 时先在「数值域」做双线性插值到 2× 网格再映射颜色。
   旧做法是把低分辨率格子先着色、再由浏览器放大插值 RGB，色带交界会出现明显阶梯。 */
function gridImage(values, fn, smooth) {
  const r = HS.heat;
  if (!smooth) {
    const c = document.createElement("canvas"); c.width = r.cols; c.height = r.rows;
    const cx = c.getContext("2d"), im = cx.createImageData(r.cols, r.rows);
    for (let i = 0; i < values.length; i++) {
      const p = fn(values[i], i);
      if (!p) continue;
      im.data[i * 4] = p[0]; im.data[i * 4 + 1] = p[1]; im.data[i * 4 + 2] = p[2]; im.data[i * 4 + 3] = p[3] === undefined ? 255 : p[3];
    }
    cx.putImageData(im, 0, 0); return c;
  }
  const S = 2, cw = r.cols * S, ch = r.rows * S;
  const c = document.createElement("canvas"); c.width = cw; c.height = ch;
  const cx = c.getContext("2d"), im = cx.createImageData(cw, ch);
  const get = (x, y) => values[Math.max(0, Math.min(r.rows - 1, y)) * r.cols + Math.max(0, Math.min(r.cols - 1, x))];
  for (let yy = 0; yy < ch; yy++) {
    const gy = (yy + .5) / S - .5, y0 = Math.floor(gy), fy = gy - y0;
    for (let xx = 0; xx < cw; xx++) {
      const gx = (xx + .5) / S - .5, x0 = Math.floor(gx), fx = gx - x0;
      const v = (get(x0, y0) * (1 - fx) + get(x0 + 1, y0) * fx) * (1 - fy) +
                (get(x0, y0 + 1) * (1 - fx) + get(x0 + 1, y0 + 1) * fx) * fy;
      const p = fn(v, yy * cw + xx), i = (yy * cw + xx) * 4;
      if (!p) continue;
      im.data[i] = p[0]; im.data[i + 1] = p[1]; im.data[i + 2] = p[2]; im.data[i + 3] = p[3] === undefined ? 255 : p[3];
    }
  }
  cx.putImageData(im, 0, 0); return c;
}
function renderHeat() {
  hc.clearRect(0, 0, W, H);
  if (!HS.heat) return;
  const r = HS.heat;
  hc.imageSmoothingEnabled = true; hc.imageSmoothingQuality = "high";
  const useSinr = $("#lsinr").checked && r.sinr && r.sinr.length;
  const smooth = !$("#lsmooth") || $("#lsmooth").checked;
  if ($("#lheat").checked || useSinr) {
    const arr = useSinr ? r.sinr : r.rssi;
    const img = gridImage(arr, v => { const c = useSinr ? colorOfSinr(v) : colorOf(v); return [c[0], c[1], c[2], 218]; }, smooth);
    hc.globalAlpha = 1; hc.drawImage(img, 0, 0, W, H);
  }
  if ($("#loverlap").checked) {
    const img = gridImage(r.overlap, v => v > 0.45 ? [138, 63, 200, Math.round((v - .45) / .55 * 165)] : null);
    hc.drawImage(img, 0, 0, W, H);
  }
  if ($("#lconf").checked) {
    const img = gridImage(r.conf, (v, i) => {
      if (v > 0.55) return null;
      const x = i % r.cols, y = Math.floor(i / r.cols);
      return (x + y * 2) % 5 < 2 ? [55, 65, 81, 105] : null;
    });
    hc.drawImage(img, 0, 0, W, H);
  }
  const m = heatLayerMeta();
  if ($("#lcontour") && $("#lcontour").checked) {
    strokeContour(useSinr ? r.sinr : r.rssi, m.thr, m.sinr ? "#0f5132" : "#12447e", [7, 5],
      (m.sinr ? "SINR ≥ " : "覆盖 ≥ ") + m.thrText);
  }
  if ($("#ldeadline") && $("#ldeadline").checked && !m.sinr) {
    strokeContour(r.rssi, -80, "#8b1a1a", [3, 4], "盲区 < -80 dBm");
  }
  renderLegend();
}
function renderMetrics(s) {
  const m = (l, v, c) => '<div class="metric"><span class="muted">' + l + '</span><b style="' + (c || "") + '">' + v + '</b></div>';
  $("#hmetrics").innerHTML =
    m("覆盖达标率 (≥ " + (s.coverage_threshold == null ? -67 : s.coverage_threshold) + " dBm)", s.coverage_pct + "%", "color:" + (s.coverage_pct >= 90 ? "var(--ok)" : s.coverage_pct >= 70 ? "var(--warn)" : "var(--bad)")) +
    m("当前 SSID", esc(s.ssid || "-"), "color:var(--pri);font-size:13px") +
    m("盲区占比 (< -80 dBm)", s.deadzone_pct + "%", "color:" + (s.deadzone_pct > 15 ? "var(--bad)" : "")) +
    m("低置信区域", s.low_conf_pct + "%", "color:var(--warn)") +
    m("平均 RSSI", (s.avg_rssi == null ? "-" : s.avg_rssi + " dBm")) +
    (s.avg_sinr != null ? m("平均 SINR（含干扰）", s.avg_sinr + " dB", "color:" + (s.avg_sinr < 20 ? "var(--bad)" : "var(--ok)")) : "") +
    (s.low_sinr_pct != null ? m("低 SINR 区域 (<20 dB)", s.low_sinr_pct + "%", "color:var(--warn)") : "") +
    (s.interferer_count ? m("干扰抬升底噪", "+" + s.avg_noise_rise_db + " dB", "color:var(--warn)") : "") +
    m("留一交叉验证 RMSE", (s.holdout_rmse_db == null ? "打点不足" : s.holdout_rmse_db + " dB"), "color:" + (s.holdout_rmse_db != null && s.holdout_rmse_db <= 5 ? "var(--ok)" : "")) +
    '<div class="tiny muted" style="margin-top:9px;line-height:1.8">SSID ' + esc(s.ssid || "-") + '（' + esc(s.ssid_band || "双频") + '）· 生效 AP ' + (s.ssid_ap_total == null ? s.ap_count : s.ssid_ap_total) + ' 台<br>' +
    '参与计算 AP ' + s.ap_count + ' 台 · 墙体 ' + s.wall_count + ' 段 · 实测点 ' + s.sample_count + ' 个 · 干扰源 ' + s.interferer_count + ' 个<br>' +
    s.band + ' GHz · 噪声底 ' + s.noise_dbm + ' dBm · ' + s.ppm + ' px/m<br>' + esc(s.model) + '</div>' +
    (s.holdout_rmse_db != null ? '<div class="note tiny" style="margin-top:8px">RMSE ≤ 5 dB 为方案 P0 验收目标；当前值反映模型对本组打点的拟合程度。</div>' : '') +
    (s.residual_outliers ? '<div class="note tiny" style="margin-top:8px;color:var(--warn)">注意：' + s.residual_outliers +
      ' 个实测点与当前 AP 布局不一致（残差中位数 ' + s.residual_median_db +
      ' dB），已自动降权，不会在 AP 附近挖出假盲区。移动 AP 后建议重新走测打点，或点「清除打点」后重算。</div>' : '') + '';
}
function exportPNG() {
  const c = document.createElement("canvas"); c.width = W; c.height = H;
  const x = c.getContext("2d"); x.fillStyle = "#fff"; x.fillRect(0, 0, W, H);
  x.drawImage(bg, 0, 0);
  /* heat 层在屏幕上带 CSS 不透明度，导出时按同一系数合成，避免图片比屏幕更浓 */
  const op = parseFloat(heat.style.opacity);
  x.save(); x.globalAlpha = isFinite(op) ? op : .72; x.drawImage(heat, 0, 0); x.restore();
  x.drawImage(edit, 0, 0);
  x.fillStyle = "rgba(255,255,255,.93)"; x.fillRect(10, 10, 440, 32);
  x.fillStyle = "#1b2430"; x.font = "bold 13px 'Microsoft YaHei'";
  x.fillText("WirelessNetOptimizer · " + (HS.heat ? HS.heat.stats.band + " GHz 覆盖热力图" : "平面图 / AP 布点"), 20, 30);
  const st = HS.heat && HS.heat.stats;
  x.fillStyle = "rgba(255,255,255,.9)"; x.fillRect(10, H - 34, 570, 24);
  x.font = "10px Consolas,monospace"; x.fillStyle = "#475569";
  x.fillText(st ? (st.grid + " 网格 · " + st.ppm + " px/m · " + st.model + " · " + new Date().toLocaleString())
    : ("平面图 / AP 布点 · " + new Date().toLocaleString()), 18, H - 18);
  drawExportLegend(x, W - 308, H - 74);
  c.toBlob(b => { dl(b, "无线热力图_" + new Date().toISOString().slice(0, 10) + ".png"); toast("已导出 PNG（含色标与口径）"); });
}
function uploadFloor(e) {
  const f = e.target.files[0]; if (!f) return;
  const rd = new FileReader();
  rd.onload = async () => {
    try {
      await api("/api/floorplan", { name: f.name, data: rd.result });
      const img = new Image();
      img.onload = () => {
        const c = bgc; c.clearRect(0, 0, W, H); c.fillStyle = "#fff"; c.fillRect(0, 0, W, H);
        const s = Math.min(W / img.width, H / img.height);
        const w = img.width * s, h = img.height * s;
        c.drawImage(img, (W - w) / 2, (H - h) / 2, w, h);
        HS.floor = "uploaded"; $("#floorinfo").textContent = f.name.slice(0, 22);
        HS.walls = []; HS.aps = []; HS.samples = []; HS.heat = null; hc.clearRect(0, 0, W, H);
        drawEdit(); renderAPList(); HS.dirty = true;
        toast("图纸已载入，请用「比例尺标定」重新标定并绘制墙体");
      };
      img.src = rd.result;
    } catch (err) { toast("图纸加载失败：" + err.message); }
  };
  rd.readAsDataURL(f);
  e.target.value = "";
}
async function saveProject() {
  const name = prompt("项目名称：", "园区A座-3F-" + new Date().toISOString().slice(0, 10));
  if (!name) return;
  try {
    await api("/api/project/save", { name, payload: { aps: HS.aps, walls: HS.walls, samples: HS.samples, ppm: HS.ppm, band: HS.userBand || HS.band, noise: HS.noise, floor: HS.floor, sketch: HS.floor === "sketch" ? skSerialize() : null, ssid_id: SSID_STATE.active_id, saved_at: new Date().toISOString() } });
    toast("项目已保存：" + name);
  } catch (e) { toast("保存失败：" + e.message); }
}
async function loadProject() {
  try {
    const l = await api("/api/project/list");
    if (!l.projects.length) return toast("还没有保存的项目");
    const name = prompt("输入要加载的项目名：\n\n" + l.projects.join("\n"), l.projects[0]);
    if (!name) return;
    const p = await api("/api/project/load?name=" + encodeURIComponent(name));
    HS.aps = p.aps || []; HS.walls = p.walls || []; HS.samples = p.samples || [];
    HS.ppm = p.ppm || 10; HS.userBand = p.band || "2.4"; HS.noise = p.noise || -95; HS.floor = p.floor || "demo";
    if (p.ssid_id && p.ssid_id !== SSID_STATE.active_id) {
      try { SSID_STATE = await api("/api/ssid/select", { id: p.ssid_id }); renderGlobalSSID(); renderSSIDPage(); }
      catch (e) { /* 项目里的 SSID 已被删除时沿用当前选择 */ }
    }
    applySSIDToUI(true);   // 由 SSID 决定是否锁定频段，避免项目频段与分析对象冲突
    HS.nextId = Math.max(0, ...HS.aps.map(a => a.id)) + 1; HS.heat = null;
    $("#hppm").value = HS.ppm; $("#hnoise").value = HS.noise;
    if (p.sketch) { skRestore(p.sketch); HS.floor = "sketch"; }
    $("#floorinfo").textContent = HS.floor === "sketch" ? ("绘制草图 · " + SK.name)
      : HS.floor === "demo" ? "示例楼层" : "已上传图纸";
    if (HS.floor === "sketch") {
      const wn = $("#skwidthm"); if (wn) wn.value = SK.width_m;
      skSetTool("off"); skApplyDimText(); skUpdateInfo();
      skSyncWalls(); drawFloor(); skSyncToSurvey(false);
    } else if (HS.floor === "demo") drawFloor();
    else { const im = new Image(); im.onload = () => { bgc.drawImage(im, 0, 0, W, H); }; im.src = "/api/floorplan?" + Date.now(); }
    hc.clearRect(0, 0, W, H); drawEdit(); renderAPList(); markDirty();
    toast("已加载项目：" + name);
  } catch (e) { toast("加载失败：" + e.message); }
}

/* ====================================================================
   无线干扰分析 / 无线连接现场状态
==================================================================== */
function statusCls(s) {
  if (s === "正常") return "st-ok";
  if (s === "弱信号" || s === "低速率" || s === "频繁漫游") return "st-warn";
  if (s === "认证异常" || s === "受干扰" || s === "高重传") return "st-bad";
  return "st-gray";
}
function statusColor(s) {
  return { "正常": "#12a06a", "弱信号": "#e0912f", "低速率": "#d9a441", "频繁漫游": "#8b5cf6",
    "认证异常": "#c7372f", "受干扰": "#c7372f", "高重传": "#ef6c5a" }[s] || "#94a3b8";
}
function fmtUptime(s) {
  s = Math.max(0, Math.floor(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), q = s % 60;
  return h ? h + "h " + m + "m" : m ? m + "m " + q + "s" : q + "s";
}
function rssiColor(v) { return v >= -60 ? "var(--ok)" : v >= -70 ? "var(--warn)" : "var(--bad)"; }
function rssiBar(v) {
  const pct = Math.max(2, Math.min(100, (v + 95) / 60 * 100));
  return '<div class="rbar"><i style="width:' + pct.toFixed(0) + '%;background:' + (v >= -60 ? "#12855b" : v >= -70 ? "#d9a441" : "#c7372f") + '"></i></div>';
}
function kpiCards(list) {
  return list.map(x => '<div class="kpi"><div class="v" style="' + (x[3] || "") + '">' + x[1] + '</div><div class="k">' + x[0] + '</div><div class="d">' + x[2] + '</div></div>').join("");
}

async function initLive() {
  bindLiveUI();
  try {
    await api("/api/live/config", { aps: HS.aps, ppm: HS.ppm, n_clients: 36 });
    await refreshLive();
    await refreshInterference();
    LIVE_TIMER = setInterval(async () => {
      try { await refreshLive(); } catch (e) { /* 服务重启中跳过本次 */ }
    }, 2000);
  } catch (e) { toast("现场引擎初始化失败：" + e.message); }
}
async function refreshLive() {
  const s = await api("/api/live/snapshot");
  LIVE_STATE = { clients: s.clients || [], events: s.events || [], interferers: s.interferers || [],
    kpi: s.kpi || {}, aps: s.aps || [], paused: !!s.paused, step: s.step || 0, ts: s.ts || "",
    ssid_stats: s.ssid_stats || [] };
  SSID_STATS = s.ssid_stats || [];
  renderLive(); renderSSIDStats(); renderSSIDKpis();
  if ($("#llive").checked) drawEdit();
}
function renderLive() {
  const k = LIVE_STATE.kpi || {};
  const abnormal = (k.weak || 0) + (k.interfered || 0) + (k.low_rate || 0) + (k.auth_fail || 0);
  $("#livekpis").innerHTML = kpiCards([
    ["在线 / 总数", (k.online || 0) + " / " + (k.total || 0), "认证通过的终端"],
    ["连接健康率", (k.healthy_pct || 0) + "%", "状态正常占比", (k.healthy_pct || 0) < 75 ? "color:var(--warn)" : "color:var(--ok)"],
    ["平均 RSSI", (k.avg_rssi == null ? "-" : k.avg_rssi) + " dBm", "在线终端均值"],
    ["平均 SNR", (k.avg_snr == null ? "-" : k.avg_snr) + " dB", "已含干扰抬升"],
    ["平均重传率", (k.avg_retry == null ? "-" : k.avg_retry) + "%", "> 20% 需排查", (k.avg_retry || 0) > 20 ? "color:var(--bad)" : ""],
    ["异常终端", abnormal, "弱信号/干扰/低速/认证", abnormal > 6 ? "color:var(--bad)" : ""],
  ]);
  $("#liveclock").innerHTML = '<span class="dot" style="' + (LIVE_STATE.paused ? "background:#b4740a;box-shadow:0 0 0 3px #fff4dd" : "") + '"></span>' +
    (LIVE_STATE.paused ? "已暂停" : "实时 " + LIVE_STATE.ts + " · 步进 " + LIVE_STATE.step);
  const apsel = $("#liveap"), want = LIVE_STATE.aps.map(a => "AP-" + a.id);
  if (apsel.options.length - 1 !== want.length) {
    apsel.innerHTML = '<option value="">全部 AP</option>' + want.map(v => '<option>' + v + '</option>').join("");
  }
  const q = LIVE_FILTER.q.toLowerCase();
  const rows = LIVE_STATE.clients.filter(c => {
    if (LIVE_FILTER.status && c.status !== LIVE_FILTER.status) return false;
    if (LIVE_FILTER.ap && c.ap_name !== LIVE_FILTER.ap) return false;
    if (LIVE_FILTER.band && c.band !== LIVE_FILTER.band) return false;
    if (LIVE_FILTER.ssid && c.ssid !== LIVE_FILTER.ssid) return false;
    if (q && [c.name, c.mac, c.ip, c.ap_name, c.ssid].join(" ").toLowerCase().indexOf(q) < 0) return false;
    return true;
  });
  $("#livecount").textContent = "显示 " + rows.length + " / " + LIVE_STATE.clients.length + " 台 · 点击行看详情";
  $("#livetbl tbody").innerHTML = rows.length ? rows.map(c =>
    '<tr data-id="' + c.id + '" class="' + (LIVE_SEL === c.id ? "sel" : "") + '">' +
    '<td><span class="st ' + statusCls(c.status) + '">' + esc(c.status) + '</span></td>' +
    '<td><b>' + esc(c.name) + '</b></td>' +
    '<td class="tiny muted"><code>' + esc(c.mac) + '</code><br>' + esc(c.ip) + '</td>' +
    '<td>' + esc(c.ap_name) + '</td>' +
    '<td class="tiny"><b>' + esc(c.ssid || "-") + '</b><br><span class="muted">VLAN ' + esc(c.vlan == null ? "-" : c.vlan) + '</span></td>' +
    '<td class="tiny">' + esc(c.band) + ' G / ch' + esc(c.channel) + '</td>' +
    '<td><span class="rssi" style="color:' + rssiColor(c.rssi) + '">' + c.rssi + '</span>' + rssiBar(c.rssi) + '</td>' +
    '<td class="rssi" style="color:' + (c.snr < 18 ? "var(--bad)" : "var(--tx)") + '">' + c.snr + '</td>' +
    '<td class="tiny">' + c.rate + ' Mbps<br><span class="muted">MCS ' + c.mcs + '</span></td>' +
    '<td class="rssi" style="color:' + (c.retry > 25 ? "var(--bad)" : "var(--tx)") + '">' + c.retry + '%</td>' +
    '<td class="tiny">' + c.rx_mbps + ' / ' + c.tx_mbps + '</td>' +
    '<td class="tiny muted">' + fmtUptime(c.uptime_s) + '</td>' +
    '<td class="tiny muted">' + esc(c.last_event) + '</td></tr>').join("")
    : '<tr><td colspan="13" style="text-align:center;padding:26px;color:#8b97a6">没有匹配的终端</td></tr>';
  $("#liveevcnt").textContent = LIVE_STATE.events.length + " 条";
  $("#liveevents").innerHTML = LIVE_STATE.events.slice(0, 25).map(e =>
    '<div class="ev"><span class="et">' + esc(e.ts) + '</span><i class="lv lv-' + esc(e.level) + '"></i>' +
    '<div class="eb">' + esc(e.text) + (e.client ? ' <span class="tiny muted">· ' + esc(e.client) + '</span>' : '') + '</div></div>').join("")
    || '<div class="tiny muted">暂无事件</div>';
  if (LIVE_SEL) {
    const c = LIVE_STATE.clients.find(x => x.id === LIVE_SEL);
    if (c) renderLiveDetail(c); else LIVE_SEL = null;
  }
}
function renderLiveDetail(c) {
  const rows = [
    ["状态", c.status], ["MAC / IP", c.mac + " / " + c.ip], ["关联 AP", c.ap_name],
    ["SSID / VLAN", (c.ssid || "-") + " / VLAN " + (c.vlan == null ? "-" : c.vlan)],
    ["频段 / 信道", c.band + " GHz / ch" + c.channel], ["RSSI / SNR", c.rssi + " dBm / " + c.snr + " dB"],
    ["协商速率", c.rate + " Mbps（MCS " + c.mcs + "）"], ["重传率", c.retry + "%"],
    ["实时流量 ↓/↑", c.rx_mbps + " / " + c.tx_mbps + " Mbps"], ["认证状态", c.auth],
    ["在线时长", fmtUptime(c.uptime_s)], ["漫游次数", c.roam_count], ["最近事件", c.last_event],
  ];
  $("#livecdetail").innerHTML = '<div style="font-weight:650;margin-bottom:9px">' + esc(c.name) +
    ' <span class="st ' + statusCls(c.status) + '">' + esc(c.status) + '</span></div>' +
    rows.map(x => '<div class="metric"><span class="muted">' + x[0] + '</span><span style="text-align:right;font-size:12px">' + esc(x[1]) + '</span></div>').join("") +
    '<button class="btn sm" data-cdiag="1" style="margin-top:11px;width:100%">用该终端的证据诊断</button>';
}
function diagFromClient(id) {
  const c = LIVE_STATE.clients.find(x => x.id === id); if (!c) return;
  const s = IF_STATE.summary || {}, cf = IF_STATE.conflicts || [];
  applyPreset({
    symptom: c.status === "受干扰" ? "网速慢" : c.status === "弱信号" ? "局部无信号" :
      c.status === "认证异常" ? "认证失败" : c.status === "频繁漫游" ? "漫游掉线" : "时断时续",
    rssi: c.rssi, snr: c.snr, noise: -95, channel_util: s.max_util_pct || 40,
    client_count: LIVE_STATE.kpi.total || 18, ap_count: LIVE_STATE.aps.length || 6,
    cochannel_aps: Math.min(9, cf.length), overlap_pct: Math.min(80, cf.length * 8),
    retry_rate: c.retry, auth_fail: c.auth === "已认证" ? 0 : 35, roam_fail: c.roam_count * 8,
    ap_tx_power: 20, dfs_event: 0, band: c.band, ap_gen: "Wi-Fi 6", legacy: false,
  });
  go("diag"); runDiag(); toast("已用 " + c.name + " 的现场证据推理");
}
function pushLiveToDiag() {
  const k = LIVE_STATE.kpi || {}, s = IF_STATE.summary || {}, cf = IF_STATE.conflicts || [];
  applyPreset({
    symptom: (k.interfered || 0) > 3 ? "网速慢" : (k.weak || 0) > 3 ? "局部无信号" :
      (k.auth_fail || 0) > 1 ? "认证失败" : "时断时续",
    rssi: k.avg_rssi == null ? -62 : k.avg_rssi, snr: k.avg_snr == null ? 28 : k.avg_snr,
    noise: -95, channel_util: s.max_util_pct || 40,
    client_count: k.total || 18, ap_count: LIVE_STATE.aps.length || 6,
    cochannel_aps: Math.min(9, cf.length), overlap_pct: Math.min(80, cf.length * 8),
    retry_rate: k.avg_retry == null ? 8 : k.avg_retry,
    auth_fail: k.total ? Math.round(100 * (k.auth_fail || 0) / k.total) : 0,
    roam_fail: k.total ? Math.round(100 * (k.roam || 0) / k.total) : 0,
    ap_tx_power: 20, dfs_event: 0, band: "2.4", ap_gen: "Wi-Fi 6", legacy: false,
  });
  go("diag"); runDiag(); toast("已用现场快照的聚合证据推理");
}

async function refreshInterference() {
  try {
    IF_STATE = await api("/api/interference", { aps: HS.aps, ppm: HS.ppm, band: HS.band, ssid_id: SSID_STATE.active_id });
    renderInterference();
  } catch (e) { toast("干扰分析失败：" + e.message); }
}
function renderInterference() {
  const s = IF_STATE.summary || {}, sp = IF_STATE.spectrum_24 || [], sp5 = IF_STATE.spectrum_5 || [];
  const cf = IF_STATE.conflicts || [], plan = IF_STATE.channel_plan || [], ifs = IF_STATE.interferers || [];
  $("#ifkpis").innerHTML = kpiCards([
    ["干扰源", s.interferer_count || 0, "现场识别到的非 Wi-Fi / 邻区源"],
    ["高严重度", s.high_severity || 0, "高占空比且靠近 AP", (s.high_severity || 0) > 0 ? "color:var(--bad)" : ""],
    ["同信道冲突对", s.conflict_pairs || 0, "AP 间距 < 32 m"],
    ["最忙信道", "ch" + (s.worst_channel || "-"), "利用率 " + (s.max_util_pct || 0) + "%", (s.max_util_pct || 0) > 75 ? "color:var(--bad)" : ""],
    ["建议变更", s.suggested_changes || 0, "2.4G 非重叠信道重规划"],
    ["5G 信道组", sp5.length, "36-64 / 100-140 / 149-165"],
  ]);
  $("#ifspectip").textContent = "最忙 ch" + (s.worst_channel || "-") + " 达 " + (s.max_util_pct || 0) + "%";
  drawSpectrum(sp);
  $("#spec5").innerHTML = sp5.map(g =>
    '<div class="sp5"><span class="nm">' + esc(g.group) + '</span>' +
    '<div class="mini"><i style="width:' + g.util_pct + '%;background:' + (g.util_pct > 70 ? "#c7372f" : "#1f6feb") + '"></i></div>' +
    '<span class="vv">' + g.ap_count + ' AP · ' + g.util_pct + '% · ' + g.interference_dbm + ' dBm</span></div>').join("");
  $("#ifconfcnt").textContent = cf.length + " 组";
  $("#ifconflicts").innerHTML = cf.length ? cf.slice(0, 12).map(c =>
    '<div class="ci"><div class="ct">' + esc(c.a) + ' ↔ ' + esc(c.b) + ' · ch' + esc(c.channel) +
    ' <span class="tag ' + (c.severity === "高" ? "t-bad" : "t-pend") + '">' + esc(c.severity) + '</span> ' +
    '<span class="tiny muted">' + c.distance_m + ' m</span></div><div class="cd">' + esc(c.advice) + '</div></div>').join("")
    : '<div class="tiny muted">未发现 32 m 内的同信道 AP 对。</div>';
  $("#ifplan").innerHTML = plan.length ? plan.map(p =>
    '<div class="ci"><div class="ct">' + esc(p.ap) + ' · ch' + esc(p.old) + ' → <span style="color:var(--pri)">ch' + esc(p.new) + '</span></div>' +
    '<div class="cd">' + esc(p.reason) + '　<span class="muted">' + esc(p.model) + '</span></div></div>').join("")
    : '<div class="tiny muted">当前 2.4G 信道分配已无更优解。</div>';
  const kindSel = $("#ifkind");
  if ((IF_STATE.library || []).length && !kindSel.options.length) {
    kindSel.innerHTML = IF_STATE.library.map(t => '<option value="' + esc(t) + '">' + esc(t) + '</option>').join("");
  }
  $("#iftbl tbody").innerHTML = ifs.length ? ifs.map(i =>
    '<tr><td><b>' + esc(i.kind) + '</b></td><td>' + esc(i.band) + ' GHz</td><td>' + i.center + ' MHz / ' + i.width + ' MHz</td>' +
    '<td>' + Math.round(i.duty * 100) + '%</td><td>' + i.power + ' dBm</td>' +
    '<td><span class="tag ' + (i.severity === "高" ? "t-bad" : i.severity === "中" ? "t-pend" : "t-ok") + '">' + esc(i.severity) + '</span></td>' +
    '<td>' + i.nearest_ap_m + ' m</td><td class="tiny">' + (((i.affected_channels || []).join(", ")) || "-") + '</td>' +
    '<td class="tiny muted" style="max-width:270px">' + esc(i.advice) + '</td>' +
    '<td><button class="btn ghost sm" data-ifdel="' + i.id + '" style="padding:1px 8px">×</button></td></tr>').join("")
    : '<tr><td colspan="10" style="text-align:center;padding:22px;color:#8b97a6">暂无干扰源</td></tr>';
  $$("#iftbl [data-ifdel]").forEach(b => b.addEventListener("click", async () => {
    try {
      await api("/api/interference/remove", { id: +b.dataset.ifdel });
      await refreshInterference(); await refreshLive();
      markDirty();
      toast("已移除干扰源并重算");
    } catch (e) { toast("移除失败：" + e.message); }
  }));
}
function drawSpectrum(sp) {
  const c = $("#spec24"); if (!c) return;
  const w = 900, h = 220; c.width = w; c.height = h;
  const x = c.getContext("2d");
  x.fillStyle = "#fbfcfe"; x.fillRect(0, 0, w, h);
  const L = 38, R = 12, T = 14, B = 32, gh = h - T - B, sc = gh / 100;
  [0, 25, 50, 75, 100].forEach(p => {
    const yy = h - B - gh * p / 100;
    x.strokeStyle = "#e9edf3"; x.lineWidth = 1;
    x.beginPath(); x.moveTo(L, yy); x.lineTo(w - R, yy); x.stroke();
    x.fillStyle = "#9aa5b1"; x.font = "10px Consolas,monospace"; x.textAlign = "right"; x.fillText(p + "%", L - 6, yy + 3);
  });
  const bw = (w - L - R) / 14;
  (sp || []).forEach((s, i) => {
    const bx = L + i * bw + 5, wid = bw - 10;
    const base = 8 * sc, ap = Math.min(s.ap_count * 14, 100) * sc;
    const ifp = Math.max(0, s.util_pct * sc - base - ap);
    x.fillStyle = "#cbd5e1"; x.fillRect(bx, h - B - base, wid, base);
    x.fillStyle = "#1f6feb"; x.fillRect(bx, h - B - base - ap, wid, ap);
    x.fillStyle = "#e07a3f"; x.fillRect(bx, h - B - base - ap - ifp, wid, ifp);
    if (s.non_overlap) {
      x.strokeStyle = "#12855b"; x.lineWidth = 2;
      x.strokeRect(bx - 2, h - B - s.util_pct * sc - 2, wid + 4, s.util_pct * sc + 4);
    }
    x.textAlign = "center"; x.font = (s.util_pct > 70 ? "bold " : "") + "10px Consolas,monospace";
    x.fillStyle = s.util_pct > 70 ? "#c7372f" : "#5b6878";
    x.fillText(s.util_pct + "%", bx + wid / 2, h - B - s.util_pct * sc - 5);
    x.font = "11px Consolas,monospace"; x.fillStyle = s.non_overlap ? "#12855b" : "#7b8794";
    x.fillText(s.ch, bx + wid / 2, h - 11);
  });
  x.textAlign = "left";
}
function applyChannelPlan() {
  const plan = IF_STATE.channel_plan || [];
  let n = 0;
  plan.forEach(p => {
    const id = +String(p.ap).replace("AP-", "");
    const a = HS.aps.find(x => x.id === id);
    if (a && +a.channel !== +p.new) { a.channel = +p.new; n++; }
  });
  if (n) {
    renderAPList(); markDirty(); refreshInterference();
    toast("已应用 " + n + " 条信道变更，自动重算已触发");
  } else toast("建议清单为空，或当前信道已是最优分配");
}
function bindLiveUI() {
  $("#ifrefresh").addEventListener("click", async () => { await refreshInterference(); toast("干扰分析已刷新"); });
  $("#ifapplyplan").addEventListener("click", applyChannelPlan);
  $("#iftoheat").addEventListener("click", () => {
    $("#happlyif").checked = true; $("#lsinr").checked = true; go("heat"); runHeat();
    toast("已切到 SINR 图层并按现场干扰重算");
  });
  $("#ifadd").addEventListener("click", async () => {
    try {
      await api("/api/interference/add", { kind: $("#ifkind").value, x: +$("#ifx").value || 500, y: +$("#ify").value || 350 });
      await refreshInterference(); await refreshLive();
      markDirty();
      toast("干扰源已加入现场，频谱与热力图已重算");
    } catch (e) { toast("添加失败：" + e.message); }
  });
  $("#livepause").addEventListener("click", async () => {
    try {
      const p = !LIVE_STATE.paused;
      await api("/api/live/pause", { paused: p });
      LIVE_STATE.paused = p; $("#livepause").textContent = p ? "继续" : "暂停";
      await refreshLive();
    } catch (e) { toast("操作失败：" + e.message); }
  });
  $("#livereset").addEventListener("click", async () => {
    try {
      await api("/api/live/reset", { aps: HS.aps, ppm: HS.ppm, n_clients: 36 });
      LIVE_SEL = null; await refreshLive(); await refreshInterference();
      toast("现场数据已按当前 AP 重置");
    } catch (e) { toast("重置失败：" + e.message); }
  });
  $("#livetoheat").addEventListener("click", () => { $("#llive").checked = true; go("heat"); drawEdit(); toast("现场终端已标到图纸上"); });
  $("#livetodiag").addEventListener("click", pushLiveToDiag);
  const fmap = { liveq: "q", livestatus: "status", liveap: "ap", liveband: "band", livessid: "ssid" };
  Object.keys(fmap).forEach(id => {
    const el = $("#" + id);
    const upd = () => { LIVE_FILTER[fmap[id]] = el.value; renderLive(); };
    el.addEventListener("input", upd); el.addEventListener("change", upd);
  });
  $("#liveclear").addEventListener("click", () => {
    ["liveq", "livestatus", "liveap", "liveband"].forEach(id => $("#" + id).value = "");
    Object.keys(LIVE_FILTER).forEach(k => LIVE_FILTER[k] = ""); renderLive();
  });
  $("#livetbl tbody").addEventListener("click", e => {
    const tr = e.target.closest("tr[data-id]"); if (!tr) return;
    LIVE_SEL = +tr.dataset.id; renderLive();
    const c = LIVE_STATE.clients.find(x => x.id === LIVE_SEL); if (c) renderLiveDetail(c);
  });
  $("#livecdetail").addEventListener("click", e => { if (e.target.closest("[data-cdiag]")) diagFromClient(LIVE_SEL); });
}

/* ====================================================================
   故障排查
==================================================================== */
const FIELDS = [
  { k: "rssi", l: "终端 RSSI (dBm)", d: -62 }, { k: "snr", l: "SNR (dB)", d: 28 }, { k: "noise", l: "噪声底 (dBm)", d: -95 },
  { k: "channel_util", l: "信道利用率 (%)", d: 40 }, { k: "client_count", l: "单 AP 客户端数", d: 18 }, { k: "ap_count", l: "覆盖区 AP 数", d: 6 },
  { k: "cochannel_aps", l: "同信道可见 AP 数", d: 1 }, { k: "overlap_pct", l: "强重叠区域 (%)", d: 10 }, { k: "retry_rate", l: "重传率 (%)", d: 8 },
  { k: "dhcp_fail", l: "DHCP 失败率 (%)", d: 0 }, { k: "auth_fail", l: "认证失败率 (%)", d: 0 }, { k: "roam_fail", l: "漫游失败率 (%)", d: 0 },
  { k: "ap_tx_power", l: "AP 发射功率 (dBm)", d: 20 }, { k: "dfs_event", l: "近 24h DFS 事件", d: 0 },
  { k: "band", l: "主要频段", t: "select", o: ["2.4", "5", "6"], d: "2.4" },
  { k: "ap_gen", l: "AP 世代", t: "select", o: ["Wi-Fi 7", "Wi-Fi 6E", "Wi-Fi 6", "Wi-Fi 5", "Wi-Fi 4", "Wi-Fi 3"], d: "Wi-Fi 6" },
  { k: "legacy", l: "老旧 / EOL 设备", t: "select", o: ["false", "true"], d: "false" },
];
const PRESETS = {
  good: { rssi: -58, snr: 32, channel_util: 30, client_count: 12, cochannel_aps: 1, overlap_pct: 6, retry_rate: 4, dhcp_fail: 0, auth_fail: 0, roam_fail: 0, ap_tx_power: 17, dfs_event: 0, band: "5", ap_gen: "Wi-Fi 6", legacy: false, symptom: "网速慢" },
  weak: { symptom: "局部无信号", rssi: -81, snr: 13, channel_util: 22, client_count: 4, cochannel_aps: 0, overlap_pct: 2, retry_rate: 24, ap_gen: "Wi-Fi 5", legacy: false },
  congest: { symptom: "网速慢", rssi: -57, snr: 30, channel_util: 84, client_count: 56, ap_count: 2, cochannel_aps: 5, overlap_pct: 46, retry_rate: 29, band: "2.4", ap_tx_power: 26, legacy: false },
  roam: { symptom: "漫游掉线", rssi: -59, snr: 29, channel_util: 45, client_count: 20, cochannel_aps: 2, overlap_pct: 20, retry_rate: 12, auth_fail: 8, roam_fail: 24 },
  legacy: { symptom: "时断时续", rssi: -73, snr: 20, channel_util: 68, client_count: 32, cochannel_aps: 3, overlap_pct: 30, retry_rate: 27, band: "2.4", ap_gen: "Wi-Fi 4", legacy: true },
};
function initDiag() {
  $("#evgrid").innerHTML = FIELDS.map(f => {
    const id = "ev_" + f.k;
    const ctrl = f.t === "select"
      ? '<select id="' + id + '">' + f.o.map(o => '<option value="' + o + '">' + (f.k === "legacy" ? (o === "true" ? "是（EOL / Wi-Fi 4 及更早）" : "否") : o) + '</option>').join("") + '</select>'
      : '<input type="number" id="' + id + '" value="' + f.d + '" step="1">';
    return '<div class="field" style="margin-bottom:0"><label class="f" for="' + id + '">' + f.l + '</label>' + ctrl + '</div>';
  }).join("");
  $("#dreset").addEventListener("click", () => applyPreset({}));
  $("#drun").addEventListener("click", runDiag);
  $$("[data-preset]").forEach(b => b.addEventListener("click", () => {
    applyPreset(PRESETS[b.dataset.preset] || {}); toast("已填入预设：" + b.textContent.replace("填入：", ""));
  }));
  applyPreset({});
}
function applyPreset(p) {
  FIELDS.forEach(f => {
    const v = p[f.k] !== undefined ? p[f.k] : f.d;
    $("#ev_" + f.k).value = (f.k === "legacy") ? String(v) : v;
  });
  if (p.symptom) $("#dsymptom").value = p.symptom;
}
function collectEvidence() {
  const e = {};
  FIELDS.forEach(f => {
    const v = $("#ev_" + f.k).value;
    e[f.k] = f.k === "legacy" ? (v === "true") : (f.t === "select" ? v : (+v || 0));
  });
  e.symptom = $("#dsymptom").value;
  e.ssid_id = SSID_STATE.active_id;   // 诊断结论附带当前分析对象，便于按 SSID 核查认证/VLAN
  return e;
}
async function runDiag() {
  const btn = $("#drun"); btn.disabled = true; btn.textContent = "推理中…";
  try {
    const r = await api("/api/diagnose", collectEvidence());
    $("#dhit").textContent = r.all.length + " 条规则命中 · 症状「" + r.symptom + "」· 分析对象 SSID " + (r.ssid || "-");
    $("#dresult").innerHTML = r.top.length ? r.top.map((h, i) =>
      '<div class="rc ' + (i === 0 ? "top" : "") + '"><div class="rh"><span class="rank">' + (i + 1) + '</span><span class="rn">' + esc(h.name) + '</span>' +
      '<span class="tag ' + (i === 0 ? "t-cn" : "t-ph") + '">' + h.confidence + '%</span><span class="tiny muted">' + esc(h.id) + '</span></div>' +
      '<p><span class="rl">依据　</span>' + esc(h.evidence) + '</p>' +
      '<p><span class="rl">建议　</span>' + esc(h.action) + '</p>' +
      '<p><span class="rl">复测　</span>' + esc(h.retest) + '</p>' +
      '<div class="bar"><i style="width:' + Math.min(100, h.confidence) + '%"></i></div></div>').join("") +
      '<div class="warn tiny" style="margin-top:10px">' + esc(r.note) + '</div>'
      : '<div class="note">当前证据未命中任何规则，各项指标处于健康范围。建议补充现场走测数据，或检查症状是否与无线侧无关（如终端、有线侧、应用服务器）。</div>' +
        '<div class="warn tiny" style="margin-top:10px">' + esc(r.note) + '</div>';
  } catch (e) { toast("推理失败：" + e.message); }
  finally { btn.disabled = false; btn.textContent = "开始推理"; }
}

/* ====================================================================
   WLC 诊断（离线知识包：运行时零外网请求）
==================================================================== */
let WLC_KB = null;
const WLC_DEMOS = {
  cisco: `2026-11-10 09:12:31.004 show clock
2026-11-10 09:12:32.118 %DOT1X-5-SUCCESS: Client aaaa.bbbb.cccc authenticated, WLAN Corp-Staff
2026-11-10 09:12:33.221 %WNCD-6-CLIENT_DELETE: Client aaaa.bbbb.cccc deleted,
   reason CO_CLIENT_DELETE_REASON_CLIENT_DHCP_FAILURE, WLAN Corp-Staff,
   Policy Profile pp-corp, VLAN 20, AP AP-3F-07
2026-11-10 09:12:33.222 %DHCP-6-NO_OFFER: no DHCP Offer for aaaa.bbbb.cccc on VLAN 20
2026-11-10 09:12:34.010 show logging profile wireless filter mac aaaa.bbbb.cccc to-file always-on-case01`,
  fortinet: `FortiGate-400F # diagnose wireless-controller wlac -c wtp
-------------------------------WTP 3----------------------------
id=FP221FTF23001187  ip=10.20.31.87:5246  state=offline
image download progress: 0
last failure : 8 -- Control message maximal retransmission limit reached
last failure param: N/A
last failure time: Tue Nov 10 08:41:12 2026
-------------------------------WTP 7----------------------------
id=FP431FTF23004512  state=offline
last failure : 8 -- Control message maximal retransmission limit reached`,
  dot11: `2026-11-10 10:02:11.334 MR36_3F events type=disassociation radio='0' vap='1'
   client_mac='46:C4:0C:8a:11:2f' band='2' channel='1' reason='23'
   auth_neg_failed='1' is_8021x='1' identity='anonymous@apple.openroaming.net'
2026-11-10 10:02:12.011 events type=deauth reason=15 4-Way Handshake timeout client_mac='46:C4:0C:8a:11:2f'`,
  ruckus: `POST https://sz.example.local:8443/wsg/api/public/v10_0/wlans
Response 400
{"errorCode": 152, "message": "Operation is not allowed because this is a standby cluster"}
SmartZone 6.0 / API version v10_0 / serviceTicket expired`,
  h3c: `<AC> display aaa online-fail-record
 User name: staff01
 MAC address: 3c-22-fb-11-09-aa
 Online failure reason: Device received Add Mobile failure response from upstream
 code=2052
<AC> display logbuffer | include 2052
%WLAN/6/WLAN_CLIENT_ONLINE_FAIL: code=2052 domain system authorization lan-access none`
};
const WLC_GROUP_CN = {
  cisco_9800_client_delete: "Cisco 9800", ruckus_sz_api: "Ruckus API",
  ieee80211_reason: "802.11 reason", fortigate_wtp_failure: "FortiGate WTP",
  h3c_community: "H3C 社区"
};
const WLC_SEV_CN = { critical: "紧急", high: "高", medium: "中", low: "低", info: "方法" };

function wlcCopy(txt, btn) {
  const done = () => { const o = btn.textContent; btn.textContent = "已复制"; setTimeout(() => btn.textContent = o, 1400); };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(txt).then(done).catch(() => wlcCopyFallback(txt, done));
  } else wlcCopyFallback(txt, done);
}
function wlcCopyFallback(txt, done) {
  const t = document.createElement("textarea"); t.value = txt; document.body.appendChild(t);
  t.select(); try { document.execCommand("copy"); done(); } catch (e) { toast("复制失败，请手动选中文本"); }
  document.body.removeChild(t);
}
function wlcKpi(label, val, sub) {
  return '<div class="wlc-kpi"><div class="k">' + esc(label) + '</div><div class="v">' + esc(val) +
    '</div><div class="s">' + esc(sub || "") + '</div></div>';
}
function renderWLCKpis() {
  const c = WLC_KB.counts || {};
  const codes = (c.cisco_client_delete_reasons || 0) + (c.ruckus_api_codes || 0) +
    (c.ieee80211_reason_codes || 0) + (c.fortigate_wtp_failures || 0) + (c.h3c_community_codes || 0);
  $("#wlckpis").innerHTML = [
    wlcKpi("离线状态", "已内置", "运行时零外网请求"),
    wlcKpi("官方码表", codes, "Cisco 270 条全量"),
    wlcKpi("诊断剧本", c.playbooks || 0, (c.playbook_steps || 0) + " 个处置步骤"),
    wlcKpi("官方文献", c.doc_entries || 0, (c.doc_vendors || 0) + " 家厂商 · 点右侧「浏览全部」"),
    wlcKpi("命令速查", c.commands || 0, "含风险标注"),
    wlcKpi("话术模板", c.phrase_templates || 0, "三口径 + 通用模板"),
    wlcKpi("处置 SOP", c.sop_entries || 0, "取证 / 分诊 / 变更 / 工单")
  ].join("");
}
function wlcDocHtml(d, open) {
  return '<details class="wlc-doc"' + (open ? " open" : "") + '><summary>' + esc(d.vendor) + ' · ' + esc(d.title) +
    ' <span class="tiny muted">' + esc(d.version) + '</span></summary>' +
    '<div class="wlc-docbody"><div class="small">' + esc(d.summary_zh) + '</div>' +
    '<div class="tiny muted" style="margin-top:7px">来源：' + esc(d.kind) + ' · 核对 ' + esc(d.captured) +
    ' · ' + esc(d.verify_status) + '</div>' +
    '<div class="tiny" style="margin-top:5px;word-break:break-all">官方链接（仅展示，程序不会自动请求）：<code>' + esc(d.url) + '</code> ' +
    '<button class="btn ghost sm wlccopylink" data-url="' + esc(d.url) + '">复制链接</button> ' +
    '<button class="btn ghost sm wlcgolib" data-did="' + esc(d.id) + '">在文献库中打开 ↗</button></div>' +
    '<div class="tiny muted" style="margin-top:5px">' + esc(d.license_note) + '</div></div></details>';
}
function renderWLCDocs(list) {
  const arr = list || WLC_KB.docs || [];
  $("#wlcdoccnt").textContent = "显示 " + arr.length + " 篇 / 内置 " + ((WLC_KB.counts || {}).doc_entries || 0) + " 篇";
  $("#wlcdocs").innerHTML = arr.length ? arr.map((d, i) => wlcDocHtml(d, i === 0)).join("") : '<div class="muted small">暂无</div>';
  $$("#wlcdocs .wlccopylink").forEach(b => b.addEventListener("click", () => wlcCopy(b.dataset.url, b)));
  $$("#wlcdocs .wlcgolib").forEach(b => b.addEventListener("click", () => {
    go("lib");
    $("#libq").value = b.dataset.did;
    renderLib();
  }));
}
function wlcCodeRow(r) {
  const name = r.code + (r.name && r.name !== r.code ? " · " + r.name : "");
  const official = r.explanation ? (r.explanation + (r.action ? " ｜ Action: " + r.action : ""))
    : (r.zh || "") + (r.detail ? " ｜ " + r.detail : "");
  const hint = r.hint_zh || r.action || r.zh || "";
  const sev = r.severity_zh || (r.group === "h3c_community" ? "社区资料" : WLC_GROUP_CN[r.group] || "");
  return '<tr><td><code>' + esc(name) + '</code><div class="tiny muted">' + esc(WLC_GROUP_CN[r.group] || r.group) +
    '</div></td><td>' + esc(sev) + '</td><td class="small">' + esc(official) +
    '</td><td class="small">' + esc(hint) + '</td></tr>';
}
function renderWLCCodeTable(j) {
  $("#wlccodecnt").textContent = "命中 " + j.total + " 条" + (j.truncated ? "（已截断）" : "");
  $("#wlccodetbl tbody").innerHTML = j.records.length ? j.records.map(wlcCodeRow).join("")
    : '<tr><td colspan="4" class="muted small">没有匹配的码，换个关键词试试。</td></tr>';
}
function renderWLCCmdTable(list) {
  $("#wlccmdcnt").textContent = "共 " + list.length + " 条";
  $("#wlccmdtbl tbody").innerHTML = list.length ? list.map(c =>
    '<tr><td>' + esc(c.vendor) + '</td><td>' + esc(c.scene) + '</td><td><code>' + esc(c.command) +
    '</code></td><td class="small">' + esc(c.purpose) + '</td><td class="tiny">' + esc(c.risk) + '</td></tr>').join("")
    : '<tr><td colspan="5" class="muted small">没有匹配的命令。</td></tr>';
}
function renderWLCPhraseTemplates() {
  $("#wlcphrases").innerHTML = (WLC_KB.phrase_templates || []).map(t =>
    '<div class="wlc-phrase"><div class="h">' + esc(t.scene) + '<span class="tiny muted"> · ' + esc(t.audience) +
    '</span><button class="btn ghost sm wlcphrase-copy">复制</button></div><div class="b">' + esc(t.text) + '</div></div>').join("");
  $$("#wlcphrases .wlcphrase-copy").forEach(b => b.addEventListener("click", () =>
    wlcCopy(b.closest(".wlc-phrase").querySelector(".b").textContent, b)));
}
function wlcPhraseCard(title, text, tag) {
  if (!text) return "";
  return '<div class="wlc-say"><div class="h">' + esc(title) + '<span class="tiny muted"> · ' + esc(tag) +
    '</span><button class="btn ghost sm wlcsay-copy">复制</button></div><div class="b">' + esc(text) + '</div></div>';
}
function renderWLCResult(r) {
  $("#wlcresmeta").textContent = "命中 " + r.matched_code_count + " 个码 / " + r.playbook_hit_count + " 个剧本";
  let h = '<div class="note" style="margin-bottom:11px"><b>结论：</b>' + esc(r.summary) + '</div>';
  if (r.matched_codes.length) {
    h += '<h4 class="wlc-h4">命中的官方错误码 / 原因码</h4><div class="tblwrap" style="max-height:230px"><table><thead><tr><th>码</th><th>判定</th><th>官方原文 / 含义</th><th>中文处置提示</th></tr></thead><tbody>' +
      r.matched_codes.slice(0, 10).map(wlcCodeRow).join("") + '</tbody></table></div>';
    if (r.matched_codes.length > 10) h += '<div class="tiny muted" style="margin-top:5px">另有 ' + (r.matched_codes.length - 10) + ' 条，点击「错误码速查」按关键词查看。</div>';
  }
  const ps = r.top_playbooks || [];
  if (ps.length) {
    const t = ps[0];
    h += '<h4 class="wlc-h4">首选处置剧本 · ' + esc(t.title) + ' <span class="tag t-b">' + esc(WLC_SEV_CN[t.severity] || t.severity) + '</span></h4>';
    h += '<div class="small" style="margin-bottom:9px"><b>现场症状：</b>' + esc(t.symptom) + '<br><b>定位结论：</b>' + esc(t.conclusion) + '</div>';
    h += '<h4 class="wlc-h4">三套沟通话术（可直接复制使用）</h4>';
    h += wlcPhraseCard("对业务用户", r.phrases.to_user, "不含技术黑话") +
      wlcPhraseCard("对运维工程师", r.phrases.to_engineer, "证据链与定位方向") +
      wlcPhraseCard("对厂商工单", r.phrases.to_vendor, "含需附材料");
    h += '<h4 class="wlc-h4">分步处置方法</h4><ol class="wlc-steps">' + (t.steps || []).map(s =>
      '<li><b>' + esc(s.action) + '</b>' +
      (s.command ? '<div class="wlc-cmd"><code>' + esc(s.command) + '</code></div>' : "") +
      '<div class="tiny muted">验证：' + esc(s.verify) + '</div></li>').join("") + '</ol>';
    if (t.escalate) h += '<div class="warn tiny" style="margin-top:9px"><b>何时升级 / 开 case：</b>' + esc(t.escalate) + '</div>';
    if (t.rollback) h += '<div class="note tiny" style="margin-top:7px"><b>回滚：</b>' + esc(t.rollback) + '</div>';
    if (ps.length > 1) h += '<details class="wlc-doc" style="margin-top:11px"><summary>其他候选剧本（' + (ps.length - 1) + ' 条）</summary><div class="wlc-docbody">' +
      ps.slice(1).map(p => '<div class="small" style="padding:5px 0"><b>' + esc(p.title) + '</b> · 匹配分 ' + esc(p.score) +
        '<div class="tiny muted">' + esc(p.conclusion) + '</div></div>').join("") + '</div></details>';
  }
  h += '<div class="warn tiny" style="margin-top:11px">' + esc(r.note) + '</div>';
  $("#wlcresult").innerHTML = h;
  $$("#wlcresult .wlcsay-copy").forEach(b => b.addEventListener("click", () =>
    wlcCopy(b.closest(".wlc-say").querySelector(".b").textContent, b)));
  renderWLCDocs(r.docs && r.docs.length ? r.docs : WLC_KB.docs);
}
async function runWLCAnalyze() {
  const txt = $("#wlctext").value.trim();
  if (!txt) { toast("请先粘贴日志或点示例按钮"); return; }
  const btn = $("#wlcrun"); btn.disabled = true; btn.textContent = "分析中…";
  try {
    const r = await api("/api/wlc/analyze", { text: txt, vendor: $("#wlcvendor").value });
    renderWLCResult(r);
    toast("离线分析完成：命中 " + r.matched_code_count + " 个错误码");
  } catch (e) { toast("分析失败：" + e.message); }
  finally { btn.disabled = false; btn.textContent = "开始分析"; }
}
async function searchWLCCodes(q) {
  try {
    const j = await api("/api/wlc/codes?q=" + encodeURIComponent(q || "") +
      "&group=" + encodeURIComponent($("#wlccodegroup").value) + "&limit=120");
    renderWLCCodeTable(j);
  } catch (e) { toast("码表查询失败：" + e.message); }
}
async function filterWLCCommands(q) {
  try {
    const j = await api("/api/wlc/commands?q=" + encodeURIComponent(q || ""));
    renderWLCCmdTable(j.records || []);
  } catch (e) { toast("命令过滤失败：" + e.message); }
}
async function initWLC() {
  WLC_KB = await api("/api/wlc/kb");
  renderWLCKpis(); renderWLCDocs(); renderWLCCmdTable(WLC_KB.commands || []); renderWLCPhraseTemplates();
  $("#wlclicense").innerHTML = '<b>离线与版权说明：</b>' + esc(WLC_KB.offline_notice) + '<br>' +
    esc(WLC_KB.license_notice) + '<br><b>版本差异提示：</b>' + esc(WLC_KB.sources_note);
  $("#wlcoffline").textContent = "离线内置 · v" + esc(WLC_KB.schema_version || "2.0");
  $("#wlcrun").addEventListener("click", runWLCAnalyze);
  $("#wlcclear").addEventListener("click", () => {
    $("#wlctext").value = ""; $("#wlcresult").innerHTML = '<div class="muted small">已清空，可重新粘贴日志。</div>';
    $("#wlcresmeta").textContent = ""; renderWLCDocs();
  });
  $$("[data-wlcdemo]").forEach(b => b.addEventListener("click", () => {
    $("#wlctext").value = WLC_DEMOS[b.dataset.wlcdemo] || "";
    const v = { cisco: "Cisco", fortinet: "Fortinet", ruckus: "RUCKUS", h3c: "H3C", dot11: "通用" }[b.dataset.wlcdemo];
    $("#wlcvendor").value = v || "";
    toast("已填入示例，点「开始分析」查看离线诊断");
  }));
  $("#wlccodebtn").addEventListener("click", () => searchWLCCodes($("#wlccodeq").value));
  $("#wlccodeq").addEventListener("keydown", e => { if (e.key === "Enter") searchWLCCodes(e.target.value); });
  $("#wlccodegroup").addEventListener("change", () => searchWLCCodes($("#wlccodeq").value));
  $$("[data-wlcquick]").forEach(b => b.addEventListener("click", () => {
    $("#wlccodeq").value = b.dataset.wlcquick; searchWLCCodes(b.dataset.wlcquick);
  }));
  $("#wlccmdbtn").addEventListener("click", () => filterWLCCommands($("#wlccmdq").value));
  $("#wlccmdq").addEventListener("keydown", e => { if (e.key === "Enter") filterWLCCommands(e.target.value); });
  $("#wlcopenlib").addEventListener("click", () => go("lib"));
  await searchWLCCodes("");
}

/* ====================================================================
   官方文档库（125 篇离线内置文献 · 本地检索）
==================================================================== */
const LIB_STATE = { docs: [], sops: [], facets: {}, counts: {} };
const LIB_STATUS_CN = {
  "public-verified": "官方已核实",
  "needs-field-check": "需现场核对",
  "community": "社区资料"
};
const LIB_QUICKS = ["DHCP", "证书", "DTLS", "DFS", "抓包", "AP 上线", "认证", "License", "漫游", "信道", "RADIUS", "配置同步", "话术", "SOP"];

function libHay(d) {
  return [d.id, d.vendor, d.title, d.version, d.url, d.kind, d.summary_zh,
          d.topic, d.scene, d.keywords, d.commands_zh, d.action_zh, d.verify_status]
    .map(x => String(x || "")).join(" ").toLowerCase();
}
function libBadge(d) {
  const st = d.verify_status || "public-verified";
  const cls = st === "public-verified" ? "ok" : (st === "community" ? "warn" : "check");
  return '<span class="lib-badge ' + cls + '">' + esc(LIB_STATUS_CN[st] || st) + '</span>';
}
function libDocHtml(d, open) {
  const meta = [d.vendor, d.topic || "未分类", d.version].filter(Boolean).map(esc).join(" · ");
  const cmds = (d.commands_zh || "").split(";").map(s => s.trim()).filter(Boolean);
  return '<details class="lib-doc"' + (open ? " open" : "") + '><summary>' +
    '<span class="lib-t">' + esc(d.title) + '</span>' + libBadge(d) +
    '<div class="lib-meta">' + meta + ' · <code>' + esc(d.id) + '</code></div></summary>' +
    '<div class="lib-body">' +
    '<div class="lib-sum">' + esc(d.summary_zh || "") + '</div>' +
    (d.scene ? '<div class="lib-row"><b>适用场景</b><span>' + esc(d.scene) + '</span></div>' : "") +
    (d.action_zh ? '<div class="lib-row"><b>处置要点</b><span>' + esc(d.action_zh) + '</span></div>' : "") +
    (d.keywords ? '<div class="lib-row"><b>关键词</b><span>' + esc(d.keywords) + '</span></div>' : "") +
    (cmds.length ? '<div class="lib-row"><b>关键命令</b><span>' + cmds.map(c =>
      '<button class="lib-cmd" data-cmd="' + esc(c) + '" title="点击复制">' + esc(c) + '</button>').join("") + '</span></div>' : "") +
    '<div class="lib-row"><b>官方链接</b><span style="word-break:break-all"><code>' + esc(d.url) + '</code> ' +
      '<button class="btn ghost sm libcopylink" data-url="' + esc(d.url) + '">复制</button></span></div>' +
    '<div class="lib-foot">' + esc(d.kind || "") + ' · 核对 ' + esc(d.captured || "") +
      ' · 索引与中文摘要已内置，不内置全文 · 版权归原作者</div>' +
    '</div></details>';
}
function libSopHtml(s, open) {
  return '<details class="lib-doc lib-sop"' + (open ? " open" : "") + '><summary>' +
    '<span class="lib-t">' + esc(s.id) + ' · ' + esc(s.title) + '</span>' +
    '<div class="lib-meta">' + esc(s.category || "") + ' · ' + (s.steps || []).length + ' 步</div></summary>' +
    '<div class="lib-body"><ol class="lib-steps">' + (s.steps || []).map(x => '<li>' + esc(x) + '</li>').join("") + '</ol>' +
    (s.note ? '<div class="lib-row"><b>提示</b><span>' + esc(s.note) + '</span></div>' : "") +
    '<div class="lib-row"><b>依据</b><span>' + (s.ref_docs || []).map(r =>
      '<button class="lib-ref" data-did="' + esc(r.id) + '">' + esc(r.title) + '</button>').join("") + '</span></div>' +
    '</div></details>';
}
function renderLibKpis() {
  const c = LIB_STATE.counts || {};
  const f = LIB_STATE.facets || {};
  $("#libkpis").innerHTML = [
    wlcKpi("内置文献", c.doc_entries || LIB_STATE.docs.length, "索引 + 中文提炼"),
    wlcKpi("覆盖厂商", c.doc_vendors || Object.keys(f.vendors || {}).length, "含 IETF / IEEE 标准"),
    wlcKpi("专题分类", c.doc_topics || Object.keys(f.topics || {}).length, "按故障域切片"),
    wlcKpi("官方已核实", c.doc_verified || 0, "带版本与出处"),
    wlcKpi("需现场核对", c.doc_needs_check || 0, "不得自动决策"),
    wlcKpi("社区资料", c.doc_community || 0, "单独标注"),
    wlcKpi("处置 SOP", (LIB_STATE.sops || []).length, "标准处置流程")
  ].join("");
}
function renderLib() {
  const q = ($("#libq").value || "").trim().toLowerCase();
  const vendor = $("#libvendor").value, topic = $("#libtopic").value, status = $("#libstatus").value;
  const terms = q.split(/\s+/).filter(Boolean);
  let arr = LIB_STATE.docs.filter(d => {
    if (vendor && d.vendor !== vendor) return false;
    if (topic && (d.topic || "未分类") !== topic) return false;
    if (status && (d.verify_status || "public-verified") !== status) return false;
    if (terms.length) {
      const hay = libHay(d);
      if (!terms.every(t => hay.indexOf(t) >= 0)) return false;
    }
    return true;
  });
  if (terms.length) {
    arr.sort((a, b) => {
      const score = d => (String(d.title || "").toLowerCase().indexOf(terms[0]) >= 0 ? 2 : 0) +
                         (String(d.id || "").toLowerCase() === terms[0] ? 3 : 0);
      return score(b) - score(a);
    });
  }
  $("#libresultcnt").textContent = "命中 " + arr.length + " / " + LIB_STATE.docs.length + " 篇";
  $("#libcount").textContent = arr.length === LIB_STATE.docs.length ? "全部文献" : "已筛选";
  $("#liblist").innerHTML = arr.length
    ? arr.map((d, i) => libDocHtml(d, i < 2)).join("")
    : '<div class="muted small" style="padding:14px">没有匹配的文献。试试更短的关键词，或点「重置筛选」。</div>';
  $$("#liblist .lib-cmd").forEach(b => b.addEventListener("click", () => wlcCopy(b.dataset.cmd, b)));
  $$("#liblist .libcopylink").forEach(b => b.addEventListener("click", () => wlcCopy(b.dataset.url, b)));
}
function renderLibSops() {
  $("#libsopcnt").textContent = "共 " + (LIB_STATE.sops || []).length + " 套";
  $("#libsops").innerHTML = (LIB_STATE.sops || []).map((s, i) => libSopHtml(s, i === 0)).join("");
  $$("#libsops .lib-ref").forEach(b => b.addEventListener("click", () => {
    $("#libvendor").value = ""; $("#libtopic").value = ""; $("#libstatus").value = "";
    $("#libq").value = b.dataset.did; renderLib();
    $("#liblist").scrollIntoView({ behavior: "smooth", block: "start" });
  }));
}
function fillLibSelect(el, items, allLabel) {
  el.innerHTML = '<option value="">' + allLabel + '</option>' +
    Object.keys(items).sort((a, b) => items[b] - items[a] || a.localeCompare(b, "zh"))
      .map(k => '<option value="' + esc(k) + '">' + esc(k) + '（' + items[k] + '）</option>').join("");
}
async function initLib() {
  const [d, s] = await Promise.all([api("/api/wlc/docs?limit=400"), api("/api/wlc/sop")]);
  LIB_STATE.docs = d.records || [];
  LIB_STATE.facets = d.facets || {};
  LIB_STATE.sops = s.records || [];
  LIB_STATE.counts = (WLC_KB && WLC_KB.counts) || {};
  const f = LIB_STATE.facets;
  fillLibSelect($("#libvendor"), f.vendors || {}, "全部厂商");
  fillLibSelect($("#libtopic"), f.topics || {}, "全部专题");
  $("#libquick").innerHTML = LIB_QUICKS.map(k =>
    '<button class="btn ghost sm" data-libquick="' + esc(k) + '">' + esc(k) + '</button>').join("");
  renderLibKpis(); renderLibSops(); renderLib();
  $("#liboffline").textContent = "离线内置 · " + LIB_STATE.docs.length + " 篇";
  $("#libsearch").addEventListener("click", renderLib);
  $("#libq").addEventListener("keydown", e => { if (e.key === "Enter") renderLib(); });
  ["#libvendor", "#libtopic", "#libstatus"].forEach(sel => $(sel).addEventListener("change", renderLib));
  $$("[data-libquick]").forEach(b => b.addEventListener("click", () => {
    $("#libq").value = b.dataset.libquick; renderLib();
  }));
  $("#libreset").addEventListener("click", () => {
    $("#libq").value = ""; $("#libvendor").value = ""; $("#libtopic").value = ""; $("#libstatus").value = "";
    renderLib(); toast("已重置筛选");
  });
}


/* ====================================================================
   空口扫描：真实扫描本空间 SSID（含隐藏）+ 无线质量与干扰评估
==================================================================== */
let AIR_STATE = { last: null, selected: new Set(), loading: false };
function initAir() {
  $("#airscan").addEventListener("click", () => runAirScan(true));
  $("#airlast").addEventListener("click", () => runAirScan(false));
  $("#airimport").addEventListener("click", importAirSSID);
  $("#airall").addEventListener("change", e => {
    const on = e.target.checked;
    const rows = AIR_STATE.last ? (AIR_STATE.last.analysis.ssids || []) : [];
    AIR_STATE.selected = new Set();
    if (on) rows.forEach((s, i) => { if (s.ssid) AIR_STATE.selected.add(s.ssid); });
    $$("#airssidtbl input[data-airssid]").forEach(c => { c.checked = on && !!c.dataset.airssid; });
    syncAirImportBtn();
  });
  const ep = $("#airpoint");
  if (ep) ep.addEventListener("keydown", e => { if (e.key === "Enter") runAirScan(true); });
  initHistory();
}
function airLevelColor(level) {
  return { "极低": "var(--ok)", "低": "var(--ok)", "中": "var(--warn)",
           "高": "var(--bad)", "极高": "var(--bad)" }[level] || "var(--tx3)";
}
function airGradeColor(g) {
  return { "优": "var(--ok)", "良": "var(--ok)", "中": "var(--warn)",
           "差": "var(--bad)", "很差": "var(--bad)" }[g] || "var(--tx2)";
}
async function runAirScan(force) {
  if (AIR_STATE.loading) return;
  AIR_STATE.loading = true;
  const btn = $("#airscan");
  $("#airstatus").innerHTML = '<span style="color:var(--warn)">' +
    (force ? "正在扫描空口，约 4-8 秒…（网卡会短暂切换信道，可能瞬断）" : "读取上次结果…") + '</span>';
  btn.disabled = true; btn.textContent = "扫描中…";
  try {
    const point = ($("#airpoint").value || "").trim();
    const url = force
      ? "/api/air/scan?wait=5&point=" + encodeURIComponent(point)
      : "/api/air/last";
    const r = await api(url);
    if (!r.ok) throw new Error(r.error || "扫描失败");
    AIR_STATE.last = r;
    AIR_STATE.selected = new Set();
    renderAir(r);
    $("#airstatus").innerHTML = '<span style="color:var(--ok)">扫描完成 · ' + esc(r.scanned_at || "") +
      ' · 采集点：' + esc(r.point || "本机所在位置") + ' · 发现 ' + r.count + ' 个 BSS' +
      (r.cached ? '（缓存 ' + (r.age_sec || 0) + ' 秒前）' : '') + '</span>';
  } catch (e) {
    $("#airstatus").innerHTML = '<span style="color:var(--bad)">' + esc(e.message) +
      '　常见原因：无线网卡被禁用 / 未连接 Wi-Fi / 无线服务未启动。有线环境无法扫描空口。</span>';
  } finally {
    AIR_STATE.loading = false;
    btn.disabled = false; btn.textContent = "开始扫描";
  }
}
function renderAir(r) {
  const a = r.analysis || {};
  const k = a.rollup || {};
  const grade = k.overall_grade || "-";
  $("#airkpis").innerHTML = [
    ["总评分", (k.overall_score != null ? k.overall_score : "-"), grade + " · 覆盖 " + (k.coverage_score || 0) +
      " / 干扰 " + (k.interference_score || 0), "color:" + airGradeColor(grade)],
    ["发现 BSS / 射频", (k.bss_total || 0) + " / " + (k.radio_total || 0),
      "SSID " + (k.ssid_total || 0) + " 个（不含隐藏）", ""],
    ["隐藏 SSID 的 BSS", k.hidden_bss || 0, "未广播名称，已按 BSSID 列出", k.hidden_bss ? "color:var(--warn)" : ""],
    ["2.4G / 5G 射频", (k.radios_24 || 0) + " / " + (k.radios_5 || 0),
      "2.4G " + (k.bss_24 || 0) + " 个 BSS · 5G " + (k.bss_5 || 0) + " 个", ""],
    ["最强信号", (k.best_rssi != null ? k.best_rssi + " dBm" : "-"), k.strongest_ssid || "-", ""],
    ["最差信道", k.worst_channel ? (k.worst_channel.band + "G ch" + k.worst_channel.channel) : "-",
      k.worst_channel ? "干扰指数 " + k.worst_channel.index : "-", "color:var(--warn)"],
    ["开放 / 未加密", k.open_bss || 0, "个 BSS", k.open_bss ? "color:var(--bad)" : ""],
    ["平均干扰指数", (k.avg_channel_index != null ? k.avg_channel_index : "-"), "0-100，越低越好", ""],
  ].map(x => '<div class="metric"><span>' + esc(x[0]) + '</span><b style="' + x[3] + '">' + esc(String(x[1])) +
    '</b><span class="tiny muted">' + esc(x[2]) + '</span></div>').join("");

  // 评分条
  const bars = [
    ["覆盖（本采集点最强信号）", k.coverage_score, 35],
    ["干扰（信道干扰指数反算）", k.interference_score, 35],
    ["安全（加密强度）", k.security_score, 15],
    ["密度（2.4G 射频数）", k.density_score, 15],
  ].map(([nm, v, w]) => '<div style="margin-bottom:9px"><div class="row" style="justify-content:space-between;font-size:12px">' +
    '<span>' + esc(nm) + '</span><b>' + (v == null ? "-" : v) + ' / 100</b></div>' +
    '<div class="bar"><i style="width:' + (v || 0) + '%;background:' + ((v || 0) >= 70 ? "var(--ok)" : (v || 0) >= 45 ? "var(--warn)" : "var(--bad)") + '"></i></div>' +
    '<div class="tiny muted">权重 ' + w + '%</div></div>').join("");
  $("#airscore").innerHTML = '<div style="display:flex;align-items:baseline;gap:10px;margin-bottom:12px">' +
    '<b style="font-size:34px;line-height:1;color:' + airGradeColor(grade) + '">' + esc(String(k.overall_score != null ? k.overall_score : "-")) + '</b>' +
    '<span style="color:' + airGradeColor(grade) + ';font-weight:600">' + esc(grade) + '</span>' +
    '<span class="tiny muted">满分 100，≥85 优 / ≥70 良 / ≥55 中 / ≥40 差</span></div>' + bars;

  // 问题
  const iss = a.issues || [];
  $("#airissues").innerHTML = iss.length ? iss.map(i => {
    const c = i.severity === "high" ? "var(--bad)" : i.severity === "medium" ? "var(--warn)" : "var(--tx2)";
    const bg = i.severity === "high" ? "var(--bad-bg)" : i.severity === "medium" ? "var(--warn-bg)" : "var(--gray-bg)";
    return '<div style="border-left:3px solid ' + c + ';background:' + bg + ';padding:8px 10px;border-radius:6px;margin-bottom:8px">' +
      '<div style="font-size:12.5px;font-weight:600;color:' + c + '">' + esc(i.title) + '</div>' +
      '<div class="tiny" style="color:var(--tx2);margin-top:2px;line-height:1.65">' + esc(i.detail) + '</div></div>';
  }).join("") : '<div class="tiny muted">未发现明显问题（仍建议换 2-3 个点位复测）。</div>';

  // SSID 表
  const rows = a.ssids || [];
  $("#airssidcnt").textContent = "共 " + rows.length + " 项 · 勾选可直接导入为分析对象";
  $("#airssidtbl tbody").innerHTML = rows.length ? rows.map(s => {
    const ch = (s.channels || []).length ? (s.bands || []).join("/") + "G ch" + s.channels.join(",") : "-";
    const util = s.utilization_pct == null ? "-" : s.utilization_pct + "%";
    const sec = s.security || "-";
    const secColor = s.security_score === 0 ? "var(--bad)" : s.security_score >= 95 ? "var(--ok)" : "var(--tx2)";
    return '<tr>' +
      '<td><input type="checkbox" data-airssid="' + esc(s.ssid || "") + '" style="width:auto"' +
      (s.ssid ? "" : " disabled") + '></td>' +
      '<td><b>' + esc(s.label) + '</b>' + (s.hidden ? ' <span class="tag t-pend">隐藏</span>' : '') +
      (s.bss_count > 1 ? ' <span class="tag t-ph">' + s.bss_count + ' 个 BSS</span>' : '') + '</td>' +
      '<td class="tiny">' + esc(ch) + '<br>' + esc((s.widths || []).join("/") + " MHz") + '</td>' +
      '<td>' + s.rssi_best + ' dBm</td>' +
      '<td>' + s.snr_db + ' dB</td>' +
      '<td><b style="color:' + (s.sinr_db >= 20 ? "var(--ok)" : s.sinr_db >= 10 ? "var(--warn)" : "var(--bad)") + '">' + s.sinr_db + ' dB</b></td>' +
      '<td>' + s.co_channel + '</td>' +
      '<td>' + esc(util) + '</td>' +
      '<td class="tiny" style="color:' + secColor + '">' + esc(sec) + '</td>' +
      '<td><b style="color:' + airGradeColor(s.quality_grade) + '">' + s.quality_score + '</b> <span class="tiny">' + esc(s.quality_grade) + '</span></td>' +
      '</tr>';
  }).join("") : '<tr><td colspan="10" style="text-align:center;padding:22px;color:#8b97a6">未发现任何 BSS</td></tr>';
  $$("#airssidtbl input[data-airssid]").forEach(c => c.addEventListener("change", () => {
    if (c.checked) AIR_STATE.selected.add(c.dataset.airssid);
    else AIR_STATE.selected.delete(c.dataset.airssid);
    syncAirImportBtn();
  }));
  syncAirImportBtn();

  // 信道
  const chans = (a.channels || []).filter(c => c.bss_on_channel || c.radios_on_channel);
  const byBand = { "2.4": [], "5": [], "6": [] };
  chans.forEach(c => { (byBand[c.band] = byBand[c.band] || []).push(c); });
  $("#airchannels").innerHTML = Object.keys(byBand).filter(b => byBand[b].length).map(band => {
    const list = byBand[band].sort((x, y) => x.channel - y.channel);
    const rowsHtml = list.map(c => {
      const col = airLevelColor(c.level);
      const util = c.utilization_pct == null ? "未上报" : c.utilization_pct + "%";
      return '<div class="row" style="gap:10px;align-items:center;padding:4px 0;border-bottom:1px solid var(--line2)">' +
        '<span style="width:74px;font-size:12.5px"><b>ch ' + c.channel + '</b></span>' +
        '<div style="flex:1;min-width:90px"><div class="bar" style="margin:0"><i style="width:' + c.interference_index + '%;background:' + col + '"></i></div></div>' +
        '<span style="width:56px;text-align:right;font-size:12px;color:' + col + ';font-weight:600">' + c.interference_index + '</span>' +
        '<span style="width:52px;font-size:11.5px;color:' + col + '">' + esc(c.level) + '</span>' +
        '<span style="width:120px;font-size:11.5px;color:var(--tx2)">' + c.radios_on_channel + ' 射频 / ' + c.bss_on_channel + ' BSS</span>' +
        '<span style="width:96px;font-size:11.5px;color:var(--tx2)">利用率 ' + esc(util) + '</span>' +
        '<span style="width:74px;font-size:11.5px;color:var(--tx2)">' + (c.strongest_rssi == null ? "-" : c.strongest_rssi + " dBm") + '</span>' +
        '</div>';
    }).join("");
    return '<div style="margin-bottom:14px"><div class="row" style="justify-content:space-between;margin-bottom:4px">' +
      '<b style="font-size:13px">' + band + ' GHz</b><span class="tiny muted">干扰指数 0-100（射频数 45% + 重叠对 20% + 利用率 35%）</span></div>' +
      rowsHtml + '</div>';
  }).join("") || '<div class="tiny muted">无</div>';

  // 建议
  const rec = a.recommend || {};
  const recHtml = Object.keys(rec).map(band => '<div class="tiny" style="margin-top:5px"><b>' + band +
    ' GHz 建议：</b>' + rec[band].map(x => "ch" + x.channel + "（指数 " + x.interference_index + "）").join(" · ") + '</div>').join("");
  if (recHtml) $("#airchannels").innerHTML += '<div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--line)">' + recHtml + '</div>';

  // 射频
  const radios = (a.radios || []).slice().sort((x, y) => (y.rssi || -999) - (x.rssi || -999));
  $("#airradiocnt").textContent = "共 " + radios.length + " 个射频";
  $("#airradiotbl tbody").innerHTML = radios.length ? radios.map(x =>
    '<tr><td><code>' + esc(x.key) + '</code></td><td>' + esc(String(x.channel || "-")) + '</td>' +
    '<td>' + (x.width_mhz || 20) + ' MHz</td><td>' + (x.rssi == null ? "-" : x.rssi + " dBm") + '</td>' +
    '<td>' + (x.utilization_pct == null ? "-" : x.utilization_pct + "%") + '</td>' +
    '<td class="tiny">' + esc((x.ssids || []).join(" / ") || "（隐藏）") + '</td></tr>').join("")
    : '<tr><td colspan="6" style="text-align:center;padding:18px;color:#8b97a6">无</td></tr>';

  $("#airassume").innerHTML = (a.assumptions || []).map(x => "· " + esc(x)).join("<br>");
  renderCapMatrix(r);
  if (HIST_STATE.data) histLoad();   // 首次加载由 initHistory 负责，扫描后刷新趋势
}
function syncAirImportBtn() {
  const n = AIR_STATE.selected.size;
  const b = $("#airimport");
  b.disabled = n === 0;
  b.textContent = n ? "导入勾选 SSID（" + n + "）" : "导入勾选 SSID";
}
async function importAirSSID() {
  const names = [...AIR_STATE.selected];
  if (!names.length) return;
  try {
    const r = await api("/api/air/import", { names });
    SSID_STATE = r.state;
    renderGlobalSSID(); renderSSIDPage();
    toast("已导入 " + r.created.length + " 个 SSID" + (r.existed.length ? "，" + r.existed.length + " 个已存在" : ""));
    AIR_STATE.selected = new Set();
    $$("#airssidtbl input[data-airssid]").forEach(c => { c.checked = false; });
    $("#airall").checked = false;
    syncAirImportBtn();
    await rebuildLivePool();
  } catch (e) { toast("导入失败：" + e.message); }
}

/* ====================================================================
   移动漫游监测：当前关联 / 漫游时间线 / 信号轨迹 / 信道拥挤
==================================================================== */
let ROAM = { timer: null, data: null, busy: false, scanning: false };

const ROAM_KIND_COLOR = { roam: "#1f6feb", "band-roam": "#6550c4", "ssid-switch": "#b4740a",
                          disconnect: "#c7372f", connect: "#12855b" };
function sigLabel(r) {
  if (r == null) return ["无信号", "var(--tx3)"];
  if (r >= -55) return ["极佳", "var(--ok)"];
  if (r >= -62) return ["良好", "var(--ok)"];
  if (r >= -67) return ["可用", "var(--ok)"];
  if (r >= -75) return ["偏弱", "var(--warn)"];
  return ["边缘", "var(--bad)"];
}
function sigHex(r) {
  if (r == null) return "#8b97a6";
  if (r >= -67) return "#12855b";
  if (r >= -75) return "#b4740a";
  return "#c7372f";
}
function roamColor(idx) {
  if (idx == null) return "var(--tx3)";
  if (idx < 25) return "var(--ok)";
  if (idx < 55) return "var(--warn)";
  return "var(--bad)";
}
function durText(s) {
  if (s == null) return "-";
  s = Math.round(s);
  if (s < 60) return s + " 秒";
  const m = Math.floor(s / 60), r = s % 60;
  return m + " 分 " + (r < 10 ? "0" : "") + r + " 秒";
}
function initRoam() {
  $("#roamstart").addEventListener("click", roamStart);
  $("#roamstop").addEventListener("click", roamStop);
  $("#roamrefresh").addEventListener("click", roamRefresh);
  $("#roamexport").addEventListener("click", () => {
    if (!ROAM.data || !ROAM.data.event_total) { toast("还没有可导出的漫游记录"); return; }
    window.location.href = "/api/roam/export";
  });
  api("/api/roam/state").then(s => {
    renderRoam(s);
    if (s.running) { startRoamTimer(); syncRoamButtons(true); }
  }).catch(() => {});
}
function syncRoamButtons(running) {
  $("#roamstart").disabled = !!running;
  $("#roamstop").disabled = !running;
  $("#roamstart").textContent = running ? "监测中…" : "开始监测";
}
function startRoamTimer() { if (!ROAM.timer) ROAM.timer = setInterval(roamTick, 1000); }
function stopRoamTimer() { if (ROAM.timer) { clearInterval(ROAM.timer); ROAM.timer = null; } }
async function roamTick() {
  if (ROAM.busy) return;
  ROAM.busy = true;
  try {
    const s = await api("/api/roam/state");
    renderRoam(s);
    if (!s.running) { stopRoamTimer(); syncRoamButtons(false); }
  } catch (e) { /* 轮询失败不打断，下一秒重试 */ } finally { ROAM.busy = false; }
}
async function roamStart() {
  const btn = $("#roamstart");
  btn.disabled = true; btn.textContent = "启动中…";
  try {
    const r = await api("/api/roam/start", {
      point: ($("#roampoint").value || "").trim(),
      poll: parseFloat($("#roampoll").value || "1"),
      air_every: parseFloat($("#roamair").value || "20"),
    });
    if (!r.ok) throw new Error(r.error || "启动失败");
    renderRoam(r.state || {});
    syncRoamButtons(true);
    startRoamTimer();
    toast("已开始监测，拿着笔记本沿路线走即可");
  } catch (e) {
    toast("启动失败：" + e.message, 3600);
    syncRoamButtons(false);
  }
}
async function roamStop() {
  try {
    const s = await api("/api/roam/stop", {});
    renderRoam(s);
    stopRoamTimer(); syncRoamButtons(false);
    toast("已停止，共记录 " + (s.stats ? s.stats.roams : 0) + " 次漫游，可导出 CSV 留档");
  } catch (e) { toast("停止失败：" + e.message); }
}
async function roamRefresh() {
  if (ROAM.scanning) return;
  ROAM.scanning = true;
  const b = $("#roamrefresh"); const old = b.textContent;
  b.disabled = true; b.textContent = "扫描中…";
  $("#roamstatus").innerHTML = '<span style="color:var(--warn)">正在做一次主动空口扫描（约 4-8 秒，网卡会短暂切信道）…</span>';
  try {
    const s = await api("/api/roam/scan", {});
    if (s.ok === false) throw new Error(s.error || "扫描失败");
    renderRoam(s);
  } catch (e) { toast("刷新周边失败：" + e.message, 3600); }
  finally { ROAM.scanning = false; b.disabled = false; b.textContent = old; }
}
function renderRoam(s) {
  if (!s || typeof s !== "object") return;
  ROAM.data = s;
  const cur = s.current || null, st = s.stats || {}, air = s.air || {};
  $("#roamnowts").textContent = s.started_ts ? "监测起点 " + s.started_ts : "";
  // ---- 状态行 ----
  const bits = [];
  if (s.running) bits.push('<span style="color:var(--ok)">● 监测中</span>');
  else if (s.started_at) bits.push('<span style="color:var(--tx3)">● 已停止</span>');
  else bits.push('<span style="color:var(--tx3)">● 未开始</span>');
  bits.push("采集点：" + esc(s.point || "-"));
  if (s.elapsed_sec) bits.push("已采 " + durText(s.elapsed_sec));
  bits.push("采样间隔 " + (s.poll || 1) + " 秒 · 已采 " + (s.polls || 0) + " 次");
  if (air.age_sec != null) bits.push("周边 " + air.age_sec + " 秒前(" + (air.mode === "active" ? "主动扫描" : "系统缓存") + "，" + (air.count || 0) + " 个 BSS)");
  if (s.error) bits.push('<span style="color:var(--warn)">读取异常：' + esc(s.error) + "</span>");
  if (s.air_error) bits.push('<span style="color:var(--warn)">周边异常：' + esc(s.air_error) + "</span>");
  $("#roamstatus").innerHTML = bits.join(" · ");

  // ---- KPI ----
  $("#roamkpis").innerHTML = [
    ["漫游次数", st.roams || 0, "跨频段 " + (st.band_roams || 0) + " · 跨 SSID " + (st.ssid_switches || 0),
     (st.roams ? "" : "color:var(--tx3)")],
    ["平均驻留", st.dwell_avg != null ? durText(st.dwell_avg) : "-",
     st.dwell_min != null ? ("最短 " + durText(st.dwell_min) + " / 最长 " + durText(st.dwell_max)) : "每段停留多久", ""],
    ["最长切换空窗", st.handoff_max != null ? st.handoff_max + " 秒" : "-",
     "观测值 ≤ 采样间隔，非空口时延", st.handoff_max > 3 ? "color:var(--warn)" : ""],
    ["掉线次数", st.disconnects || 0, "切换后弱信号 " + (st.weak_handoffs || 0) + " 次",
     st.disconnects ? "color:var(--bad)" : ""],
  ].map(x => '<div class="metric"><span>' + esc(x[0]) + '</span><b style="' + x[3] + '">' + esc(String(x[1])) +
    '</b><span class="tiny muted">' + esc(x[2]) + "</span></div>").join("");

  // ---- 当前关联 ----
  if (!cur) {
    const pre = !s.started_at;
    $("#roamnow").innerHTML = pre
      ? '<div class="note">还没有开始监测。点右上角「开始监测」，程序会立刻读取本机当前关联（' +
        'SSID / BSSID / 信道 / 信号），之后每秒刷新一次；拿着笔记本沿路线走，漫游会自动记录。</div>'
      : '<div class="warn">监测中但读不到本机关联：可能已走出覆盖、Wi-Fi 被关闭、或网卡切到了有线。' +
        '恢复关联后这里会自动更新，掉线区间会记录在时间线里。</div>';
  } else {
    const [lbl, col] = sigLabel(cur.rssi);
    const pct = cur.signal_pct != null ? cur.signal_pct : (cur.rssi != null ? Math.max(0, Math.min(100, Math.round((cur.rssi + 100) * 2))) : 0);
    $("#roamnow").innerHTML =
      '<div class="row" style="justify-content:space-between;align-items:flex-start;gap:14px">' +
        '<div style="min-width:0">' +
          '<div style="font:650 20px Consolas,monospace;color:#143a70;word-break:break-all">' + esc(cur.ssid || "-") + "</div>" +
          '<div class="tiny muted" style="margin-top:3px">BSSID ' + esc(cur.bssid || "-") + " · " + esc(cur.interface || "-") +
            " · " + esc(cur.auth || "-") + (cur.cipher ? " / " + esc(cur.cipher) : "") + "</div>" +
          '<div class="row" style="gap:6px;margin-top:7px">' +
            '<span class="tag t-cn">' + esc(cur.band ? cur.band + " GHz" : "-") + " · ch" + esc(String(cur.channel != null ? cur.channel : "-")) + "</span>" +
            '<span class="tag t-ph">' + esc(cur.radio || "-") + "</span>" +
            (s.dwell_sec != null ? '<span class="tag t-pend">已停留 ' + durText(s.dwell_sec) + "</span>" : "") +
          "</div>" +
        "</div>" +
        '<div style="text-align:right;flex:none">' +
          '<div style="font:680 34px/1.05 -apple-system,Segoe UI;color:' + col + '">' + (cur.rssi != null ? cur.rssi : "-") + "</div>" +
          '<div class="tiny" style="color:' + col + '">dBm · ' + lbl + "</div>" +
          '<div class="tiny muted">' + (cur.rssi_source === "netsh dBm" ? "netsh 实测" : "百分比换算") + "</div>" +
        "</div>" +
      "</div>" +
      '<div class="bar" style="height:7px"><i style="width:' + pct + "%;background:" + col + '"></i></div>' +
      '<div class="grid g2" style="margin-top:10px;gap:0 18px">' +
        [["信号百分比", (cur.signal_pct != null ? cur.signal_pct + "%" : "-")],
         ["接收速率", (cur.rx_rate || "-") + " Mbps"],
         ["发送速率", (cur.tx_rate || "-") + " Mbps"],
         ["配置文件", (cur.profile || "-")]
        ].map(x => '<div class="metric"><span>' + esc(x[0]) + "</span><b>" + esc(String(x[1])) + "</b></div>").join("") +
      "</div>";
  }

  // ---- 当前信道拥挤 ----
  const c = s.channel || {};
  if (!c.channel) {
    $("#roamchannow").innerHTML = '<div class="tiny muted">未关联，无法判定当前信道。</div>';
    $("#roamchantag").textContent = "";
  } else {
    const col = roamColor(c.index);
    $("#roamchantag").textContent = air.scanned_at ? "数据时刻 " + air.scanned_at : "";
    $("#roamchannow").innerHTML =
      '<div class="row" style="justify-content:space-between;align-items:flex-end;gap:12px">' +
        '<div><div style="font:650 18px Consolas,monospace;color:#143a70">' + esc(c.band) + " GHz · ch" + esc(String(c.channel)) + "</div>" +
          '<div class="tiny muted" style="margin-top:2px">' +
            (c.measured ? (esc(String(c.radios)) + " 个射频 / " + esc(String(c.bss)) + " 个 BSS · 利用率 " +
              (c.utilization_pct != null ? esc(String(c.utilization_pct)) + "%" : "未上报") +
              (c.rank ? " · 本频段拥挤排名 " + c.rank + "/" + c.rank_total : ""))
              : "该信道暂无周边数据（系统缓存未覆盖），可点「刷新周边」补一次扫描") +
          "</div></div>" +
        '<div style="text-align:right"><div style="font:680 30px/1.05 -apple-system,Segoe UI;color:' + col + '">' +
          (c.index != null ? c.index : "-") + '</div><div class="tiny" style="color:' + col + '">拥挤指数 · ' + esc(c.level || "-") + "</div></div>" +
      "</div>" +
      '<div class="bar" style="height:7px;margin-top:9px"><i style="width:' + (c.index || 0) + "%;background:" + col + '"></i></div>';
  }

  // ---- 判定 ----
  const v = s.verdict || {};
  $("#roamverdict").innerHTML =
    '<div class="row" style="justify-content:space-between;align-items:flex-end;gap:12px">' +
      '<div><div style="font:680 32px/1.05 -apple-system,Segoe UI;color:' + airGradeColor(v.grade) + '">' + (v.score != null ? v.score : "-") +
        '</div><div class="tiny muted">满分 100 · 覆盖与漫游体验综合</div></div>' +
      '<span class="tag ' + (v.grade === "优" || v.grade === "良" ? "t-ok" : v.grade === "中" ? "t-pend" : "t-bad") + '">' + esc(v.grade || "-") + "</span>" +
    "</div>" +
    '<div class="bar" style="height:7px;margin-top:10px"><i style="width:' + (v.score || 0) + "%;background:" + airGradeColor(v.grade) + '"></i></div>' +
    '<div class="small" style="margin-top:10px;color:var(--tx2)">' + esc(v.text || "开始监测后给出判定") + "</div>";

  // ---- 问题清单 ----
  const issues = s.issues || [];
  $("#roamissues").innerHTML = issues.length ? issues.map(i => {
    const col = i.severity === "high" ? "var(--bad)" : i.severity === "medium" ? "var(--warn)" : "var(--tx2)";
    return '<div style="border-left:3px solid ' + col + ';padding:2px 0 2px 10px;margin-bottom:10px">' +
      '<div style="font-weight:600;font-size:12.5px;color:' + col + '">' + esc(i.title) + "</div>" +
      '<div class="tiny muted" style="margin-top:3px;line-height:1.65">' + esc(i.detail) + "</div></div>";
  }).join("") : '<div class="tiny muted">暂无异常。继续沿路线走，切换点会自动出现在时间线里。</div>';

  drawRoamChart(s.samples || [], s.events || [], cur);
  renderRoamEvents(s);
  renderRoamChannels(s);
  renderRoamNeighbors(s);
}
function renderRoamEvents(s) {
  const evs = s.events || [];
  $("#roamevcnt").textContent = s.event_total ? "共 " + s.event_total + " 条，显示最近 " + evs.length + " 条" : "";
  if (!evs.length) {
    $("#roamevents").innerHTML = '<div class="tiny muted" style="padding:10px 0">还没有切换记录。' +
      '从当前位置往另一个 AP 覆盖区走，BSSID 一变就会出现在这里。</div>';
    return;
  }
  $("#roamevents").innerHTML = evs.map(e => {
    const col = ROAM_KIND_COLOR[e.kind] || "var(--tx3)";
    const a = e.from || {}, b = e.to || {};
    const fm = a.bssid ? esc(a.bssid) + " <span class=\"tiny muted\">ch" + esc(String(a.channel)) + (a.rssi != null ? " · " + a.rssi + "dBm" : "") + "</span>" : "—";
    const to = b.bssid ? esc(b.bssid) + " <span class=\"tiny muted\">ch" + esc(String(b.channel)) + (b.rssi != null ? " · " + b.rssi + "dBm" : "") + "</span>" : "—";
    const meta = [];
    if (e.dwell_sec != null) meta.push("驻留 " + durText(e.dwell_sec));
    if (e.gap_sec) meta.push("空窗 " + e.gap_sec + " 秒");
    if (e.rssi_delta != null) meta.push("信号 " + (e.rssi_delta > 0 ? "+" : "") + e.rssi_delta + " dB");
    return '<div class="ev"><span class="lv" style="background:' + col + '"></span>' +
      '<span class="et">' + esc(e.ts) + "</span>" +
      '<div class="eb"><b style="color:' + col + '">' + esc(e.label) + "</b>" +
        '<span class="tiny muted"> ' + fm + " → " + to + "</span>" +
        (meta.length ? '<div class="tiny muted">' + meta.join(" · ") + "</div>" : "") +
        (e.scan_window ? '<div class="tiny" style="color:var(--warn)">⚠ 落在主动扫描窗口内，可能是扫描伪影，判读时请剔除</div>' : "") +
      "</div></div>";
  }).join("");
}
function renderRoamChannels(s) {
  const air = s.air || {}, c = s.channel || {};
  const band = c.band || (s.current || {}).band || "5";
  let list = (air.channels || []).filter(x => x.band === band).sort((a, b) => a.channel - b.channel);
  if (band === "2.4") list = list.filter(x => x.bss_on_channel || x.radios_on_channel || [1, 6, 11].indexOf(x.channel) >= 0);
  $("#roamchanscope").textContent = list.length ? band + " GHz · " + list.length + " 条信道" : "暂无数据";
  if (!list.length) {
    $("#roamchannels").innerHTML = '<div class="tiny muted">还没有周边 BSS 数据。点「刷新周边」做一次主动扫描。</div>';
    return;
  }
  const max = Math.max(100, ...list.map(x => x.interference_index || 0));
  $("#roamchannels").innerHTML = list.map(x => {
    const mine = x.channel === c.channel && x.band === band;
    const col = roamColor(x.interference_index);
    return '<div style="display:grid;grid-template-columns:58px 1fr 108px;gap:9px;align-items:center;margin-bottom:5px">' +
      '<span style="font:600 11.5px Consolas,monospace;color:' + (mine ? "#143a70" : "var(--tx3)") + '">' +
        (mine ? "▲ " : "") + "ch" + x.channel + "</span>" +
      '<span style="height:9px;background:var(--gray-bg);border-radius:4px;overflow:hidden">' +
        '<i style="display:block;height:100%;width:' + Math.round((x.interference_index || 0) / max * 100) + "%;background:" + col +
        (mine ? ";box-shadow:0 0 0 1.5px #143a70 inset" : "") + '"></i></span>' +
      '<span class="tiny muted" style="text-align:right">' + (x.interference_index || 0) + " · " + (x.radios_on_channel || 0) + " 射频" +
        (x.utilization_pct != null ? " · " + x.utilization_pct + "%" : "") + "</span>" +
    "</div>";
  }).join("") + '<div class="tiny muted" style="margin-top:8px">▲ 为当前所在信道；条形越长表示该信道射频越多、重叠越重、越拥挤。</div>';
}
function renderRoamNeighbors(s) {
  const nb = s.neighbors || [], cur = s.current;
  const tbody = $("#roamneigh").querySelector("tbody");
  if (!nb.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:16px;color:#8b97a6">' +
      (cur ? "同 SSID 未发现其他 BSSID（可能是单 AP 组网，或周边缓存未覆盖）" : "等待采集") + "</td></tr>";
    return;
  }
  tbody.innerHTML = nb.map(n => {
    const d = n.delta;
    const tag = d == null ? '<span class="tag t-ph">未知</span>'
      : d >= 8 ? '<span class="tag t-ok">更近，应切换</span>'
      : d >= 3 ? '<span class="tag t-pend">略强</span>'
      : d <= -8 ? '<span class="tag t-ph">更远</span>' : '<span class="tag t-ph">相当</span>';
    return "<tr><td class='model'>" + esc(n.bssid) + "</td><td>ch" + esc(String(n.channel)) + "</td><td>" + esc(n.band) + "G</td>" +
      "<td>" + (n.rssi != null ? n.rssi + " dBm" : "-") + "</td>" +
      "<td>" + (d != null ? (d > 0 ? "+" : "") + d + " dB" : "-") + "</td><td>" + tag + "</td></tr>";
  }).join("");
}
function drawRoamChart(samples, events, cur) {
  const cv = $("#roamchart");
  if (!cv || !cv.getContext) return;
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(320, cv.clientWidth || 900), h = 240;
  cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  const padL = 46, padR = 12, padT = 14, padB = 24;
  const iw = w - padL - padR, ih = h - padT - padB;
  const yMin = -95, yMax = -30;
  const Y = v => padT + ih * (yMax - v) / (yMax - yMin);
  const now = samples.length ? samples[samples.length - 1].t : Date.now() / 1000;
  const span = Math.max(60, (samples.length ? now - samples[0].t : 60));
  const t0 = now - span;
  const X = t => padL + iw * Math.max(0, Math.min(1, (t - t0) / span));
  // 网格
  g.font = "10px Consolas,monospace"; g.textBaseline = "middle";
  for (let v = -90; v <= -30; v += 10) {
    g.strokeStyle = "#eef1f6"; g.lineWidth = 1;
    g.beginPath(); g.moveTo(padL, Y(v)); g.lineTo(w - padR, Y(v)); g.stroke();
    g.fillStyle = "#8b97a6"; g.textAlign = "right"; g.fillText(v, padL - 7, Y(v));
  }
  // 阈值线
  [[-67, "#b4740a", "高速率拐点 -67"], [-75, "#c7372f", "边缘 -75"]].forEach(([v, col, txt]) => {
    g.save(); g.setLineDash([5, 4]); g.strokeStyle = col; g.globalAlpha = .55;
    g.beginPath(); g.moveTo(padL, Y(v)); g.lineTo(w - padR, Y(v)); g.stroke(); g.restore();
    g.fillStyle = col; g.globalAlpha = .8; g.textAlign = "left";
    g.fillText(txt, padL + 6, Y(v) - 8); g.globalAlpha = 1;
  });
  // 漫游时刻竖线
  (events || []).forEach(e => {
    if (e.kind === "connect" || e.t < t0) return;
    const x = X(e.t);
    g.save(); g.setLineDash([3, 3]); g.strokeStyle = ROAM_KIND_COLOR[e.kind] || "#8b97a6";
    g.globalAlpha = .5; g.beginPath(); g.moveTo(x, padT); g.lineTo(x, padT + ih); g.stroke(); g.restore();
    if (e.rssi_delta != null) {
      g.fillStyle = ROAM_KIND_COLOR[e.kind] || "#8b97a6"; g.textAlign = "center"; g.font = "9px Consolas,monospace";
      g.fillText((e.rssi_delta > 0 ? "+" : "") + e.rssi_delta, x, padT + 8);
    }
  });
  // 主曲线（断点处断开）
  const pts = (samples || []).filter(p => p.t >= t0);
  let prev = null;
  g.lineWidth = 2; g.strokeStyle = "#1f6feb"; g.lineJoin = "round";
  pts.forEach(p => {
    if (p.rssi == null) { prev = null; return; }
    if (!prev || p.t - prev.t > Math.max(6, span / 40)) {
      g.beginPath(); g.moveTo(X(p.t), Y(p.rssi));
    } else {
      g.lineTo(X(p.t), Y(p.rssi)); g.stroke();
    }
    prev = p;
  });
  // 断点标记（未关联）
  (samples || []).forEach(p => {
    if (p.rssi != null || p.t < t0) return;
    g.fillStyle = "#c7372f"; g.beginPath(); g.arc(X(p.t), padT + ih - 3, 3, 0, 7); g.fill();
  });
  // 当前点
  if (cur && cur.rssi != null && pts.length) {
    const p = pts[pts.length - 1];
    g.fillStyle = sigHex(cur.rssi);
    g.beginPath(); g.arc(X(p.t), Y(p.rssi), 4, 0, 7); g.fill();
  }
  // 时间轴
  g.fillStyle = "#8b97a6"; g.textAlign = "center"; g.font = "10px Consolas,monospace";
  g.fillText(pts.length ? new Date(t0 * 1000).toLocaleTimeString("zh-CN", { hour12: false }) : "", padL + 18, h - 10);
  g.fillText(pts.length ? new Date(now * 1000).toLocaleTimeString("zh-CN", { hour12: false }) : "", w - padR - 22, h - 10);
  if (!pts.length) {
    g.fillStyle = "#8b97a6"; g.textAlign = "center"; g.font = "12px sans-serif";
    g.fillText("开始监测后，这里会画出每秒一次的信号轨迹", w / 2, padT + ih / 2);
  }
}

/* ====================================================================
   现场勘测（Ekahau Survey 式） · 智能审计（AirMagnet AirWISE 式）
==================================================================== */
const SV_W = 1000, SV_H = 700;
let SURVEY_STATE = { data: null, layer: "rssi", heat: null, floor: null, busy: false, calib: null };
let AUDIT_STATE = { data: null, filter: "all", busy: false };
const SV_STATUS = ["不达标", "边缘", "达标"];
const SV_STCOLOR = ["#d6453d", "#e0a52a", "#12a06d"];
const SV_TAGCLS = ["t-bad", "t-warn", "t-ok"];

function tile(label, val, sub) {
  return '<div class="stat-tile"><span>' + esc(label) + '</span><b>' + esc(val) + '</b><em>' + esc(sub || "") + '</em></div>';
}
function clamp01(v) { return Math.max(0, Math.min(1, v)); }
function fmtN(v, suffix) { return (v === null || v === undefined || v === "") ? "-" : (v + (suffix || "")); }

/* ---------------- 现场勘测 ---------------- */
function initSurvey() {
  $("#svnew").addEventListener("click", async () => {
    const name = $("#svsession").value.trim();
    if (!name) { toast("请先填写会话名称"); return; }
    try { const r = await api("/api/survey/session", { name }); SURVEY_STATE.data = r.state; svRender(r.state); toast("会话已切换：" + r.session); }
    catch (e) { toast("保存失败：" + e.message); }
  });
  $("#svprofile").addEventListener("change", async () => {
    try { const r = await api("/api/survey/profile", { key: $("#svprofile").value }); SURVEY_STATE.data = r.state; svRender(r.state); svHeat(); }
    catch (e) { toast("切换模板失败：" + e.message); }
  });
  $("#svlayer").addEventListener("change", () => { SURVEY_STATE.layer = $("#svlayer").value; svHeat(); });
  $("#svrefresh").addEventListener("change", () => {});
  $("#svscanhere").addEventListener("click", () => {
    const n = (SURVEY_STATE.data && SURVEY_STATE.data.points) ? SURVEY_STATE.data.points.length : 0;
    const x = 130 + (n % 5) * 185, y = 120 + Math.floor(n / 5) * 135;
    svAddPoint(x, y, "P" + String(n + 1).padStart(2, "0"));
  });
  $("#svclear").addEventListener("click", async () => {
    if (!confirm("清空当前会话的所有勘测点位？此操作不可撤销。")) return;
    try { const r = await api("/api/survey/clear", {}); SURVEY_STATE.data = r.state; svRender(r.state); svHeat(); toast("已清除 " + r.removed + " 个点位"); }
    catch (e) { toast("清空失败：" + e.message); }
  });
  $("#svcalib").addEventListener("click", svCalib);
  $("#svapply").addEventListener("click", svApply);
  $("#svreport").addEventListener("click", () => { const u = exReportUrl(false); if (u) window.open(u, "_blank"); });
  $("#svexp").addEventListener("click", () => window.open("/api/survey/export", "_blank"));
  $("#svfile").addEventListener("change", svFloorUpload);
  $("#svcanvas").addEventListener("click", e => {
    const r = e.currentTarget.getBoundingClientRect();
    const fx = (e.clientX - r.left) / r.width, fy = (e.clientY - r.top) / r.height;
    // 画布可能被滚动到视口外，或收到合成事件；越界点击直接忽略，避免生成负坐标点位
    if (fx < 0 || fy < 0 || fx > 1 || fy > 1) return;
    const x = Math.round(fx * SV_W), y = Math.round(fy * SV_H);
    const n = (SURVEY_STATE.data && SURVEY_STATE.data.points) ? SURVEY_STATE.data.points.length : 0;
    svAddPoint(x, y, "P" + String(n + 1).padStart(2, "0"));
  });
  svLoadFloor();
}
function svLoadFloor() {
  const img = new Image();
  img.onload = () => { SURVEY_STATE.floor = img; svDraw(); };
  img.onerror = () => { SURVEY_STATE.floor = null; svDraw(); };
  img.src = "/api/floorplan?t=" + Date.now();
}
async function svFloorUpload(e) {
  const f = e.target.files[0]; if (!f) return;
  const rd = new FileReader();
  rd.onload = async () => {
    try {
      await api("/api/floorplan", { name: f.name, data: rd.result });
      SURVEY_STATE.floor = null; svLoadFloor(); $("#svfloorname").textContent = "· " + f.name;
      toast("楼层图已载入，勘测页与热力图页共用这张图纸");
    } catch (err) { toast("图纸上传失败：" + err.message); }
  };
  rd.readAsDataURL(f); e.target.value = "";
}
async function svLoad() {
  try { const s = await api("/api/survey/state"); SURVEY_STATE.data = s; svRender(s); await svHeat(); }
  catch (e) { $("#svstatus").innerHTML = '<span style="color:var(--bad)">勘测模块加载失败：' + esc(e.message) + '</span>'; }
}
function svRender(s) {
  const ps = $("#svprofile"), ls = $("#svlayer");
  if (ps.options.length !== s.profiles.length) {
    ps.innerHTML = s.profiles.map(p => '<option value="' + esc(p.key) + '">' + esc(p.icon || "") + " " + esc(p.name) + "</option>").join("");
  }
  if (!ls.options.length) {
    ls.innerHTML = Object.keys(s.layers).map(k => '<option value="' + k + '">' + esc(s.layers[k].name) + "</option>").join("");
  }
  ps.value = s.profile.key; ls.value = SURVEY_STATE.layer;
  if (!$("#svsession").value) $("#svsession").value = s.session || "";
  const ev = s.eval, st = ev.stats, pr = s.profile;
  $("#svkpis").innerHTML = [
    tile("勘测点位", ev.total, s.session || "未命名会话"),
    tile("达标率", ev.pass_rate === null ? "-" : ev.pass_rate + "%", ev.pass + " 达标 / " + ev.edge + " 边缘 / " + ev.fail + " 不达标"),
    tile("最弱信号", fmtN(st.rssi_min, " dBm"), "验收线 " + pr.min_rssi + " dBm"),
    tile("平均干扰指数", fmtN(st.index_avg), "上限 " + pr.max_interference_index),
  ].join("");
  $("#svproftag").textContent = "· " + pr.name;
  $("#svprofileinfo").innerHTML = "<b>" + esc(pr.basis) + "</b><br>" + esc(pr.desc) +
    "　|　RSSI ≥ " + pr.min_rssi + " dBm，SINR ≥ " + pr.min_sinr + " dB，干扰指数 ≤ " +
    pr.max_interference_index + "，利用率 ≤ " + pr.max_utilization_pct + "%，可见射频 ≥ " + pr.min_ap_count + " 个。";
  const total = ev.total || 1;
  $("#svcompliance").innerHTML =
    '<div class="svrow"><b>点位达标率</b><b style="font-size:17px;color:' +
    (ev.pass_rate >= 90 ? "#12855b" : ev.pass_rate >= 70 ? "#b4740a" : "#c7372f") + '">' +
    (ev.pass_rate === null ? "-" : ev.pass_rate + "%") + "</b></div>" +
    '<div class="svbar"><i class="ok" style="width:' + (100 * ev.pass / total) + '%"></i>' +
    '<i class="edge" style="width:' + (100 * ev.edge / total) + '%"></i>' +
    '<i class="bad" style="width:' + (100 * ev.fail / total) + '%"></i></div>' +
    '<div class="tiny muted">绿 达标 / 黄 边缘 / 红 不达标</div>' +
    '<div class="svrow" style="margin-top:9px"><span>区域达标率（插值）</span><b id="svarea">-</b></div>' +
    '<div class="svrow"><span>最弱 / 平均 RSSI</span><b>' + fmtN(st.rssi_min) + " / " + fmtN(st.rssi_avg) + " dBm</b></div>" +
    '<div class="svrow"><span>最低 / 平均 SINR</span><b>' + fmtN(st.sinr_min) + " / " + fmtN(st.sinr_avg) + " dB</b></div>" +
    '<div class="svrow"><span>最高 / 平均干扰指数</span><b>' + fmtN(st.index_max) + " / " + fmtN(st.index_avg) + "</b></div>";
  const bad = ev.items.filter(i => i.status === 0).slice(0, 5);
  $("#svadvice").innerHTML = (bad.length ? '<div class="tiny" style="margin:9px 0 5px"><b>不达标点位</b></div>' +
    bad.map(i => '<div class="svrow"><span>' + esc(i.label) + " · " + fmtN(i.rssi, " dBm") + '</span><span class="tiny" style="color:var(--bad);text-align:right">' + esc((i.reasons || []).slice(0, 2).join("；")) + "</span></div>").join("") : "") +
    (ev.advice || []).map(a => '<div class="note tiny" style="margin-top:7px">· ' + esc(a) + "</div>").join("");
  svRenderPoints(s);
  svRenderCalib(s.calibration);
  const air = s.air || {};
  $("#svstatus").innerHTML = air.available
    ? '<span style="color:var(--ok)">● 最近一次空口扫描 ' + esc(air.scanned_at || "-") + " · " + air.count + " 个 BSS" +
      (air.age_sec !== null && air.age_sec !== undefined ? "（" + air.age_sec + " 秒前）" : "") + "</span>　" +
      '<span class="muted">点位文件：' + esc(s.storage || "-") + "</span>"
    : ((s.points || []).length
        ? '<span class="muted">当前进程还没有新的空口快照；已有 ' + s.points.length + ' 个点位沿用 ' +
          esc((s.points[s.points.length - 1] || {}).scanned_at || "上次扫描") +
          ' 的扫描结果。继续打点时请勾选「每个点位重新扫描」。</span>'
        : '<span class="warn" style="display:block;padding:8px 10px">还没有空口数据。请先到「空口扫描」页扫描一次，或勾选「每个点位重新扫描」后直接打点。</span>');
  exSessions(s.sessions || []);
}
function svRenderPoints(s) {
  const byId = {}; (s.eval.items || []).forEach(i => { byId[i.id] = i; });
  const rows = (s.points || []).map(p => {
    const e = byId[p.id] || { status: 0, reasons: [] };
    return '<tr><td><b>' + esc(p.label) + '</b><div class="tiny muted">' + esc(p.source) + "</div></td>" +
      "<td>" + p.x + ", " + p.y + '</td><td class="tiny">' + esc(p.at_text) + "</td>" +
      '<td>' + esc(p.ssid || "-") + '<div class="tiny muted">' + esc(p.bssid || "-") + "</div></td>" +
      "<td><b>" + fmtN(p.rssi) + "</b></td><td>" + fmtN(p.sinr) + "</td>" +
      "<td>" + esc(fmtN(p.band, "G")) + " / ch" + fmtN(p.channel) + "</td>" +
      "<td>" + fmtN(p.interference_index) + "</td><td>" + fmtN(p.visible_radios) + "</td>" +
      '<td><span class="tag ' + SV_TAGCLS[e.status] + '">' + SV_STATUS[e.status] + "</span></td>" +
      '<td class="tiny">' + esc((e.reasons || []).join("；") || "—") + "</td>" +
      '<td><button class="btn ghost sm svdel" data-id="' + esc(p.id) + '">删除</button></td></tr>';
  });
  $("#svpointsbody").innerHTML = rows.length ? rows.join("") :
    '<tr><td colspan="12" style="text-align:center;padding:22px;color:#8b97a6">在上方图纸点击落点，或用「扫描并打点」开始采集</td></tr>';
  $$(".svdel").forEach(b => b.addEventListener("click", async () => {
    try { const r = await api("/api/survey/delete", { id: b.dataset.id }); SURVEY_STATE.data = r.state; svRender(r.state); svHeat(); }
    catch (e) { toast("删除失败：" + e.message); }
  }));
  $("#svpointcnt").textContent = "· " + (s.points || []).length + " 个点位 · 会话「" + (s.session || "未命名") + "」";
}
function svRenderCalib(c) {
  if (!c || !c.ok) { $("#svcalibout").innerHTML = '<span class="muted">尚未校准。' + esc((c && c.error) || "建议先采集 5 个以上点位。") + "</span>"; $("#svapply").disabled = true; return; }
  SURVEY_STATE.calib = c;
  $("#svapply").disabled = !!(c.applied);
  $("#svcalibout").innerHTML = '<div class="note tiny">路径损耗指数 <b>n ' + c.n_before + " → " + c.n_fit +
    '</b>　·　1 m 损耗 <b>PL0 ' + c.pl0_before + " → " + c.pl0_fit + " dB</b><br>" +
    "拟合 RMSE <b>" + c.rmse_before_db + " → " + c.rmse_after_db + " dB</b>（改善 " + c.improvement_db + " dB）　·　异常点 " + c.outlier_count + " 个<br>" +
    '<span class="muted">' + esc(c.guard || "") + "</span></div>";
}
async function svHeat() {
  try {
    const h = await api("/api/survey/heatmap?field=" + encodeURIComponent(SURVEY_STATE.layer) + "&cols=100&rows=70&width=1000&height=700");
    SURVEY_STATE.heat = h;
    const m = h.meta;
    $("#svlayerhint").textContent = m.name + " · " + m.desc;
    const area = $("#svarea");
    if (area && SURVEY_STATE.layer === "compliance") area.textContent = fmtN(m.area_pass_pct, "%") + " 达标 / " + fmtN(m.area_fail_pct, "%") + " 不达标";
    else if (area) area.textContent = "切到「需求达标」图层查看";
    svLegend(m); svDraw();
  } catch (e) { toast("图层计算失败：" + e.message); }
}
const SV_INVERT = ["interference", "utilization", "sinr_gap"];
function svColor(field, v, m) {
  if (v === null || v === undefined) return [240, 244, 249];
  if (field === "compliance") return v >= 1.5 ? [18, 160, 109] : v >= 0.5 ? [224, 165, 42] : [214, 69, 61];
  // 用图层自身的量纲做归一，新增图层不必再改配色分支
  const lo = (m && isFinite(m.min)) ? +m.min : 0, hi = (m && isFinite(m.max)) ? +m.max : 100;
  let t = clamp01((v - lo) / ((hi - lo) || 1));
  if (SV_INVERT.indexOf(field) >= 0) t = 1 - t;
  const l = [214, 69, 61], mid = [232, 183, 58], h = [18, 160, 109];
  const a = t < 0.5 ? l : mid, b = t < 0.5 ? mid : h, k = t < 0.5 ? t * 2 : (t - 0.5) * 2;
  return [0, 1, 2].map(i => Math.round(a[i] + (b[i] - a[i]) * k));
}
function svLegend(m) {
  const field = SURVEY_STATE.layer;
  let inner;
  if (field === "compliance") {
    inner = ['<span class="sw"><i style="background:#12a06d"></i>达标</span>',
      '<span class="sw"><i style="background:#e0a52a"></i>边缘</span>',
      '<span class="sw"><i style="background:#d6453d"></i>不达标</span>'].join("");
  } else {
    inner = '<span>差</span><span class="grad" style="background:linear-gradient(90deg,rgb(214,69,61),rgb(232,183,58),rgb(18,160,109))"></span><span>好</span>';
  }
  $("#svlegend").innerHTML = inner + '<span class="muted">' + esc(m.name || "") +
    " · 实测范围 " + fmtN(m.min_value) + " ~ " + fmtN(m.max_value) + " " + esc(m.unit) +
    " · " + m.point_count + " 个有效点</span>";
}
function svDraw() {
  const cv = $("#svcanvas"); if (!cv) return;
  const g = cv.getContext("2d");
  g.clearRect(0, 0, SV_W, SV_H); g.fillStyle = "#f8fafc"; g.fillRect(0, 0, SV_W, SV_H);
  if (SURVEY_STATE.floor && SURVEY_STATE.floor.complete) {
    const im = SURVEY_STATE.floor, s = Math.min(SV_W / im.width, SV_H / im.height);
    g.globalAlpha = 0.95; g.drawImage(im, (SV_W - im.width * s) / 2, (SV_H - im.height * s) / 2, im.width * s, im.height * s); g.globalAlpha = 1;
  } else {
    g.strokeStyle = "#e8edf5"; g.lineWidth = 1;
    for (let x = 0; x <= SV_W; x += 50) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, SV_H); g.stroke(); }
    for (let y = 0; y <= SV_H; y += 50) { g.beginPath(); g.moveTo(0, y); g.lineTo(SV_W, y); g.stroke(); }
    g.strokeStyle = "#cbd5e3"; g.lineWidth = 3; g.strokeRect(40, 40, SV_W - 80, SV_H - 80);
    g.setLineDash([9, 7]); g.lineWidth = 2; g.strokeStyle = "#d8e0ec";
    [[320, 40, 320, SV_H - 40], [660, 40, 660, 300], [40, 400, 660, 400]].forEach(v => {
      g.beginPath(); g.moveTo(v[0], v[1]); g.lineTo(v[2], v[3]); g.stroke();
    }); g.setLineDash([]);
    g.fillStyle = "#a3b0c2"; g.font = "14px sans-serif"; g.textAlign = "center";
    g.fillText("示例网格 · 上传真实楼层图后开始走测", SV_W / 2, SV_H / 2);
  }
  const h = SURVEY_STATE.heat, s = SURVEY_STATE.data;
  if (h && h.grid && h.meta.point_count) {
    const cw = SV_W / h.meta.cols, ch = SV_H / h.meta.rows;
    for (let r = 0; r < h.meta.rows; r++) for (let c = 0; c < h.meta.cols; c++) {
      const i = r * h.meta.cols + c, v = h.grid[i];
      if (v === null || v === undefined) continue;
      const col = svColor(SURVEY_STATE.layer, v, h.meta), a = 0.20 + 0.48 * (h.confidence[i] || 0);
      g.fillStyle = "rgba(" + col[0] + "," + col[1] + "," + col[2] + "," + a.toFixed(2) + ")";
      g.fillRect(c * cw, r * ch, cw + 1, ch + 1);
    }
  }
  // 走测路径：按点位采集顺序连线（点击序列，不是 GPS 轨迹）
  const pathOn = $("#svpath") && $("#svpath").checked;
  if (pathOn && s && (s.points || []).length > 1) {
    const pp = s.points;
    g.save(); g.setLineDash([7, 5]); g.lineWidth = 2; g.strokeStyle = "rgba(37,99,235,.8)";
    g.beginPath(); pp.forEach((p, i) => { const x = +p.x || 0, y = +p.y || 0; i ? g.lineTo(x, y) : g.moveTo(x, y); }); g.stroke();
    g.setLineDash([]);
    for (let i = 1; i < pp.length; i++) {
      const a = pp[i - 1], b = pp[i], dx = (+b.x || 0) - (+a.x || 0), dy = (+b.y || 0) - (+a.y || 0);
      const L = Math.hypot(dx, dy); if (L < 26) continue;
      g.save(); g.translate((+a.x || 0) + dx * 0.62, (+a.y || 0) + dy * 0.62); g.rotate(Math.atan2(dy, dx));
      g.fillStyle = "rgba(37,99,235,.9)"; g.beginPath();
      g.moveTo(7, 0); g.lineTo(-4, 4); g.lineTo(-4, -4); g.closePath(); g.fill(); g.restore();
    }
    g.restore();
  }
  const ev = (s && s.eval) || { items: [] };
  (ev.items || []).forEach((it, idx) => {
    const p = (s.points || []).find(x => x.id === it.id);
    const x = p ? p.x : 100 + (idx % 5) * 180, y = p ? p.y : 120 + Math.floor(idx / 5) * 135;
    g.beginPath(); g.arc(x, y, 11, 0, 7); g.fillStyle = "#fff"; g.fill();
    g.beginPath(); g.arc(x, y, 8, 0, 7); g.fillStyle = SV_STCOLOR[it.status]; g.fill();
    g.lineWidth = 2; g.strokeStyle = "#fff"; g.stroke();
    g.fillStyle = "#fff"; g.font = "bold 10px sans-serif"; g.textAlign = "center"; g.textBaseline = "middle";
    g.fillText(String(idx + 1), x, y + .5);
    g.textBaseline = "alphabetic"; g.font = "11px sans-serif"; g.textAlign = "center";
    g.fillStyle = "#1b2430"; g.fillText(it.label || "-", x, y - 16);
  });
}
async function svAddPoint(x, y, label) {
  if (SURVEY_STATE.busy) return;
  SURVEY_STATE.busy = true;
  $("#svstatus").innerHTML = '<span class="muted">正在采集点位…' + ($("#svrefresh").checked ? "（重新扫描约需 4 秒）" : "") + "</span>";
  try {
    // 会话名填了就直接生效，不必再点一次「保存为当前会话」
    const name = $("#svsession").value.trim();
    if (name && (!SURVEY_STATE.data || name !== SURVEY_STATE.data.session)) {
      await api("/api/survey/session", { name });
    }
    const r = await api("/api/survey/point", { x, y, label, refresh_air: $("#svrefresh").checked, wait: 4.0 });
    if (!r.ok) throw new Error(r.error || "采集失败");
    toast(r.point.rssi == null
      ? ("已采集 " + r.point.label + "，但该点没读到无线信号（可能走有线或 WLAN 服务未启动）")
      : ("已采集 " + r.point.label + " · " + r.point.rssi + " dBm · " + SV_STATUS[r.status]));
    await svLoad();
  } catch (e) { toast("打点失败：" + e.message); $("#svstatus").innerHTML = '<span style="color:var(--bad)">' + esc(e.message) + "</span>"; }
  finally { SURVEY_STATE.busy = false; }
}
async function svCalib() {
  const btn = $("#svcalib"); btn.disabled = true; btn.textContent = "校准中…";
  $("#svcalibout").innerHTML = '<span class="muted">正在用实测点搜索路径损耗指数…</span>';
  try {
    const r = await api("/api/survey/calibrate", { aps: HS.aps, walls: HS.walls, ppm: HS.ppm, band: HS.band });
    svRenderCalib(r);
    toast(r.ok ? "校准完成：n = " + r.n_fit : ("校准未完成：" + (r.error || "")));
  } catch (e) { toast("校准失败：" + e.message); $("#svcalibout").innerHTML = '<span style="color:var(--bad)">' + esc(e.message) + "</span>"; }
  finally { btn.disabled = false; btn.textContent = "用实测点校准"; }
}
async function svApply() {
  try {
    const r = await api("/api/survey/apply", {});
    SURVEY_STATE.calib = r.calibration; svRenderCalib(r.calibration);
    toast("已应用：到「热力图」页点「计算热力图」即可看到校准后的预测结果");
  } catch (e) { toast("应用失败：" + e.message); }
}

/* ====================================================================
   v0.12 · 高级能力（类别 ④ 纯软件 + 类别 ③ DXF 的界面联动）
   报告模板 / 多楼层合订 / 基线 diff / CSV 批量导入 / 审计规则 / 深色主题
   全部只与本地服务通信，不引入任何外部依赖
   ==================================================================== */
let EX_STATE = { data: null, logo: "", csv: "", selected: null, diff: null };

function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  const b = $("#themebtn");
  if (b) b.textContent = t === "dark" ? "☀ 浅色" : "◐ 深色";
}
function initTheme() {
  let t = "light";
  try { t = localStorage.getItem("wn_theme") === "dark" ? "dark" : "light"; } catch (e) { }
  applyTheme(t);
  const b = $("#themebtn");
  if (b) b.addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(next);
    try { localStorage.setItem("wn_theme", next); } catch (e) { }
  });
}

function exReportPatch() {
  const d = {
    title: $("#rptTitle").value.trim(), org: $("#rptOrg").value.trim(),
    project: $("#rptProject").value.trim(), footer: $("#rptFooter").value.trim(),
    engineer: $("#rptEngineer").value.trim(), reviewer: $("#rptReviewer").value.trim(),
    show_sign: $("#rptSign").checked, confidential: $("#rptConf").checked,
    show_boundary: $("#rptBoundary").checked
  };
  if (EX_STATE.logo) d.logo = EX_STATE.logo;   // 不重选就不覆盖已存 Logo
  return d;
}
function exFillReport(r) {
  $("#rptTitle").value = r.title || "无线现场勘测与验收报告";
  $("#rptOrg").value = r.org || ""; $("#rptProject").value = r.project || "";
  $("#rptFooter").value = r.footer || ""; $("#rptEngineer").value = r.engineer || "";
  $("#rptReviewer").value = r.reviewer || "";
  $("#rptSign").checked = r.show_sign !== false;
  $("#rptConf").checked = r.confidential !== false;
  $("#rptBoundary").checked = r.show_boundary !== false;
  $("#rptLogoHint").textContent = r.has_logo ? "已设置 Logo（重新选择可替换）" : "未设置 Logo";
}
function exSessions(list) {
  const box = $("#rptSessions"); if (!box) return;
  const sel = EX_STATE.selected;
  box.innerHTML = (list || []).length
    ? list.map((s, i) => '<label class="chk"><input type="checkbox" class="ex-sess" value="' + esc(s) + '"' +
      (sel ? (sel.indexOf(s) >= 0 ? " checked" : "") : (i === 0 ? " checked" : "")) + '>' + esc(s) + '</label>').join("")
    : '<span class="muted">还没有勘测会话，先在图纸上打点或登记楼层</span>';
}
function exSelectedSessions() { return $$("#rptSessions .ex-sess:checked").map(x => x.value); }
function exSnaps(list) {
  const a = $("#snapBefore"), b = $("#snapAfter"); if (!a || !b) return;
  const opts = (list || []).length
    ? list.map(s => '<option value="' + esc(s.name) + '">' + esc(s.name) + " · " + esc(s.at_text || "") + '</option>').join("")
    : '<option value="">（暂无基线快照）</option>';
  const va = a.value, vb = b.value;
  a.innerHTML = opts; b.innerHTML = opts;
  if (va) a.value = va; if (vb) b.value = vb;
  if ((list || []).length > 1) { a.value = list[list.length - 1].name; b.value = list[0].name; }
}
function exFloors(f) {
  const box = $("#floorList"); if (!box) return;
  const items = (f && f.items) || [];
  box.innerHTML = items.length
    ? "当前：<b>" + esc((f || {}).current || "未选择") + "</b>　|　" + items.slice(-8).map(x =>
      esc(x.name) + (x.width_m ? "（" + x.width_m + " m）" : "")).join(" · ")
    : "尚未登记楼层。登记后会自动切换为同名勘测会话。";
}
function exRules(d) {
  const box = $("#audrulelist"); if (!box) return;
  const cfg = d.rules || {}, dis = cfg.disabled || [], ov = cfg.severity || {};
  const def = {}; (d.catalog || []).forEach(r => { def[r.id] = r.severity; });
  EX_STATE.ruleDefault = def;
  box.innerHTML = (d.catalog || []).map(r =>
    '<div class="ruleitem"><label class="chk"><input type="checkbox" class="ex-rule-on" data-id="' + esc(r.id) + '"' +
    (dis.indexOf(r.id) < 0 ? " checked" : "") + '><b>' + esc(r.id) + '</b>　' + esc(r.title) +
    ' <span class="tiny muted">' + esc(r.group) + '</span></label><select class="ex-rule-sev" data-id="' + esc(r.id) + '">' +
    '<option value="">默认（' + esc(({ critical: "严重", high: "高", medium: "中", low: "低", info: "提示" })[r.severity] || r.severity) + '）</option>' +
    (d.severities || []).map(s => '<option value="' + esc(s.key) + '"' + (ov[r.id] === s.key ? " selected" : "") + '>' + esc(s.name) + '</option>').join("") +
    '</select></div>').join("");
  const nDis = dis.length, nOv = Object.keys(ov).length;
  const info = $("#audrulesinfo");
  if (info) info.textContent = nDis || nOv ? (nDis + " 条停用 / " + nOv + " 条改级") : "全部默认规则已启用";
}
function exRender(d) {
  EX_STATE.data = d;
  exFillReport(d.report || {});
  exSnaps(d.snapshots || []);
  exFloors(d.floors || {});
  exRules(d);
  exSessions((SURVEY_STATE.data && SURVEY_STATE.data.sessions) || []);
}
async function exLoad() {
  try { exRender(await api("/api/extras/state")); }
  catch (e) { const s = $("#rptStatus"); if (s) s.textContent = "设置加载失败：" + e.message; }
}
function exDiffHtml(d) {
  if (!d || !d.ok) return '<span style="color:var(--bad)">' + esc((d && d.error) || "对比失败") + "</span>";
  const rows = (d.metrics || []).map(m => {
    const delta = m.delta === null || m.delta === undefined ? "-" :
      '<b style="color:' + (m.flat ? "var(--tx3)" : m.better ? "var(--ok)" : "var(--bad)") + '">' +
      (m.delta > 0 ? "+" : "") + m.delta + " " + esc(m.unit || "") + "</b>";
    return "<tr><td>" + esc(m.label) + "</td><td>" + fmtN(m.before) + "</td><td>" + fmtN(m.after) +
      "</td><td>" + delta + "</td><td>" + (m.flat ? "持平" : m.better ? "改善" : "退化") + "</td></tr>";
  }).join("");
  return '<div class="note tiny"><b>' + esc(d.before.name) + " → " + esc(d.after.name) + "</b>　" +
    "新增问题 " + (d.added || []).length + " · 已消除 " + (d.removed || []).length +
    " · 严重度变化 " + (d.changed || []).length + " · 逐点配对 " + (d.point_rows || []).length + " 条</div>" +
    '<div class="tblwrap"><table><thead><tr><th>指标</th><th>优化前</th><th>优化后</th><th>变化</th><th>判读</th></tr></thead><tbody>' +
    rows + "</tbody></table></div>";
}
function exReportUrl(withDiff) {
  const q = [];
  const ss = exSelectedSessions();
  if (ss.length) q.push("sessions=" + encodeURIComponent(ss.join("|")));
  if (withDiff) {
    const a = $("#snapBefore").value, b = $("#snapAfter").value;
    if (a && b) q.push("diff=" + encodeURIComponent(a + "|" + b));
    else { toast("请先在「优化前后基线对比」里选择两份快照"); return null; }
  }
  return "/api/survey/report" + (q.length ? "?" + q.join("&") : "");
}
function initAdvanced() {
  initTheme();
  $("#rptLogo").addEventListener("change", e => {
    const f = e.target.files[0]; if (!f) return;
    const rd = new FileReader();
    rd.onload = () => {
      EX_STATE.logo = String(rd.result);
      $("#rptLogoHint").textContent = "已选择 " + f.name + "（点「保存模板」生效）";
    };
    rd.readAsDataURL(f); e.target.value = "";
  });
  $("#rptSave").addEventListener("click", async () => {
    try {
      const r = await api("/api/extras/state", { target: "report", data: exReportPatch() });
      if (!r.ok) throw new Error(r.error || "保存失败");
      EX_STATE.logo = ""; exRender(r.extras);
      $("#rptStatus").innerHTML = '<span style="color:var(--ok)">模板已保存，之后生成的报告会自动套用。</span>';
      toast("报告模板已保存");
    } catch (e) { $("#rptStatus").innerHTML = '<span style="color:var(--bad)">' + esc(e.message) + "</span>"; }
  });
  $("#rptCombined").addEventListener("click", () => { const u = exReportUrl(false); if (u) window.open(u, "_blank"); });
  $("#rptAll").addEventListener("click", () => {
    const all = $$("#rptSessions .ex-sess");
    const on = all.some(x => !x.checked);
    all.forEach(x => { x.checked = on; });
  });
  $("#rptWithDiff").addEventListener("click", () => { const u = exReportUrl(true); if (u) window.open(u, "_blank"); });
  $("#floorAdd").addEventListener("click", async () => {
    const name = $("#floorName").value.trim();
    if (!name) { toast("请填写楼层 / 会话名称"); return; }
    try {
      const r = await api("/api/extras/state", { target: "floors",
        data: { name, width_m: $("#floorWidth").value, note: $("#floorNote").value.trim() } });
      if (!r.ok) throw new Error(r.error || "登记失败");
      EX_STATE.data = r.extras; exFloors(r.extras.floors);
      const s = await api("/api/survey/session", { name });
      SURVEY_STATE.data = s.state; svRender(s.state); svHeat();
      $("#svsession").value = name; $("#floorName").value = ""; $("#floorNote").value = "";
      toast("已登记楼层「" + name + "」并切换为当前勘测会话");
    } catch (e) { toast("登记失败：" + e.message); }
  });
  $("#snapSave").addEventListener("click", async () => {
    try {
      const r = await api("/api/survey/snapshot", { action: "save", name: $("#snapName").value.trim() });
      if (!r.ok) throw new Error(r.error || "保存失败");
      if (r.extras) { EX_STATE.data = r.extras; exSnaps(r.extras.snapshots || []); }
      $("#snapName").value = "";
      $("#diffOut").innerHTML = '<span style="color:var(--ok)">已保存基线「' + esc(r.name) + '」：' +
        r.snapshot.total + " 个点位 · 达标率 " + fmtN(r.snapshot.pass_rate, "%") + " · 健康分 " + fmtN(r.snapshot.health) + "</span>";
      toast("基线已保存：" + r.name);
    } catch (e) { toast("保存基线失败：" + e.message); }
  });
  $("#snapDiff").addEventListener("click", async () => {
    try {
      const d = await api("/api/survey/snapshot", { action: "diff",
        before: $("#snapBefore").value, after: $("#snapAfter").value });
      EX_STATE.diff = d; $("#diffOut").innerHTML = exDiffHtml(d);
      if (d.ok) toast("对比完成：新增 " + (d.added || []).length + " / 消除 " + (d.removed || []).length + " 条问题");
    } catch (e) { toast("对比失败：" + e.message); }
  });
  $("#svcsvfile").addEventListener("change", e => {
    const f = e.target.files[0]; if (!f) return;
    const rd = new FileReader();
    rd.onload = () => { EX_STATE.csv = String(rd.result); $("#svcsvout").textContent = "已选择 " + f.name + "（" + EX_STATE.csv.length + " 字符），点「导入点位」生效"; };
    rd.readAsText(f, "utf-8"); e.target.value = "";
  });
  $("#svcsvimport").addEventListener("click", async () => {
    if (!EX_STATE.csv) { toast("请先选择 CSV 文件"); return; }
    try {
      const r = await api("/api/survey/import-csv", { csv: EX_STATE.csv });
      SURVEY_STATE.data = r.state; svRender(r.state); svHeat(); exSessions(r.state.sessions || []);
      $("#svcsvout").innerHTML = '<span style="color:var(--ok)">成功导入 ' + r.imported + " 个点位</span>" +
        (r.skipped ? "，跳过 " + r.skipped + " 行（缺坐标）" : "") +
        (r.errors && r.errors.length ? "<br><span style='color:var(--warn)'>" + esc(r.errors.slice(0, 3).join("；")) + "</span>" : "");
      toast("已导入 " + r.imported + " 个点位");
    } catch (e) { $("#svcsvout").innerHTML = '<span style="color:var(--bad)">' + esc(e.message) + "</span>"; }
  });
  $("#svpath").addEventListener("change", svDraw);
  $("#audrulesave").addEventListener("click", async () => {
    const disabled = $$(".ex-rule-on").filter(x => !x.checked).map(x => x.dataset.id);
    const severity = {};
    $$(".ex-rule-sev").forEach(s => {
      if (s.value && s.value !== ((EX_STATE.ruleDefault || {})[s.dataset.id])) severity[s.dataset.id] = s.value;
    });
    try {
      const r = await api("/api/extras/state", { target: "rules", data: { disabled, severity } });
      if (!r.ok) throw new Error(r.error || "保存失败");
      EX_STATE.data = r.extras; exRules(r.extras);
      $("#audrulestatus").innerHTML = '<span style="color:var(--ok)">已保存，正在重新审计…</span>';
      await audLoad(false);
      $("#audrulestatus").textContent = "已按新规则重新计算健康分与问题清单";
      toast("审计规则已更新");
    } catch (e) { $("#audrulestatus").innerHTML = '<span style="color:var(--bad)">' + esc(e.message) + "</span>"; }
  });
  $("#audrulereset").addEventListener("click", async () => {
    try {
      const r = await api("/api/extras/state", { target: "rules", data: { reset: true } });
      EX_STATE.data = r.extras; exRules(r.extras); await audLoad(false);
      toast("已恢复默认规则");
    } catch (e) { toast("重置失败：" + e.message); }
  });
  $("#bkfile").addEventListener("change", e => {
    const f = e.target.files[0]; if (!f) return;
    if (!confirm("恢复备份会覆盖同名用户数据（SSID / 勘测点位 / 图纸 / 报告模板 / 规则 / 扫描历史），继续？")) { e.target.value = ""; return; }
    const rd = new FileReader();
    rd.onload = async () => {
      $("#bkstatus").innerHTML = '<span class="loading"></span>正在恢复 ' + esc(f.name) + " …";
      try {
        const r = await api("/api/backup/import", { data: String(rd.result) });
        if (!r.ok) throw new Error(r.error || "恢复失败");
        $("#bkstatus").innerHTML = '<span style="color:var(--ok)">已恢复 ' + r.count + " 个文件：" +
          esc((r.restored || []).slice(0, 8).join("、")) + (r.count > 8 ? " …" : "") +
          "</span><br><b>请刷新页面</b>使内存状态重新加载。";
        toast("备份已恢复，请刷新页面");
      } catch (err) { $("#bkstatus").innerHTML = '<span style="color:var(--bad)">恢复失败：' + esc(err.message) + "</span>"; }
    };
    rd.readAsDataURL(f);
    e.target.value = "";
  });
  $("#rptdocx").addEventListener("click", () => {
    const u = exReportUrl(false);
    window.open(String(u).replace("/api/survey/report", "/api/report/docx"), "_blank");
    toast("正在生成离线 Word 报告");
  });
  exLoad();
}

/* ====================================================================
   v0.13 · 本机诊断 / AP 能力矩阵 / 扫描历史趋势 / 离线交付
   ==================================================================== */
let LOCAL_STATE = { data: null, busy: false };
let HIST_STATE = { data: null, metric: "overall_score" };

function localChip(sev) {
  const m = { high: ["严重", "var(--bad)"], medium: ["关注", "var(--warn)"],
    low: ["提示", "var(--pri)"], info: ["信息", "var(--tx3)"] }[sev] || ["信息", "var(--tx3)"];
  return '<span class="pill" style="color:' + m[1] + ';border-color:' + m[1] + '55">' + m[0] + "</span>";
}
function localKpi(label, val, sub, style) {
  return '<div class="metric"><span>' + esc(label) + '</span><b style="' + (style || "") + '">' +
    esc(String(val)) + '</b><span class="tiny muted">' + esc(sub) + "</span></div>";
}
function localRows(pairs) {
  return '<table class="clean"><tbody>' + pairs.map(p =>
    "<tr><td class='tiny muted' style='width:36%;white-space:nowrap'>" + esc(p[0]) +
    "</td><td style='font-size:12.5px'>" + esc(p[1] === undefined || p[1] === "" ? "-" : String(p[1])) + "</td></tr>").join("") +
    "</tbody></table>";
}
function localRender(d) {
  const drv = d.driver || {}, prof = d.profiles || [], net = d.network || [], rep = d.wlan_report || {};
  const validIp = net.filter(x => x.ip && !String(x.ip).startsWith("169.254"));
  const weak = prof.filter(p => p.weak);
  $("#localat").textContent = "· " + esc(d.checked_at || "");
  $("#localstatus").innerHTML = d.ok === false
    ? '<span style="color:var(--bad)">' + esc(d.error || "本机读取失败") + "</span>"
    : '<span class="muted">已读取：网卡驱动、' + prof.length + " 个已保存配置、" +
      net.length + " 个 IPv4 适配器、系统 WLAN 报告（" + (rep.available ? "已生成" : "未生成") + "）。全部为只读查询。</span>";
  $("#localKpis").innerHTML = [
    localKpi("无线网卡", drv.name || "未识别", drv.vendor || "netsh wlan show drivers"),
    localKpi("驱动版本", drv.version || "-", drv.date || "-"),
    localKpi("已保存配置", prof.length, weak.length ? weak.length + " 个弱加密" : "未读取明文密钥",
      weak.length ? "color:var(--warn)" : ""),
    localKpi("有效 IPv4 适配器", validIp.length, "共 " + net.length + " 个适配器"),
    localKpi("系统报告事件", rep.events ? rep.events.length : 0, rep.available ? "生成于 " + (rep.generated || "-") : "尚未生成"),
    localKpi("本机问题", (d.findings || []).length,
      ["高", "中", "低", "提示"].map((t, i) => t + " " +
        (d.findings || []).filter(f => ["high", "medium", "low", "info"][i] === f.severity).length).join(" · ")),
    localKpi("网卡无线电类型", (drv.radios || []).length, (drv.radios || []).join(" / ") || "未列出"),
    localKpi("承载网络", drv.hosted || "-", drv.fips ? "FIPS: " + drv.fips : "—")
  ].join("");

  $("#localDriver").innerHTML = drv.available
    ? localRows([["接口/驱动", drv.name], ["供应商", drv.vendor], ["版本", drv.version], ["日期", drv.date],
        ["无线电类型", (drv.radios || []).join(" / ")], ["承载网络", drv.hosted],
        ["认证与密码套件", (drv.auth || []).join("；")]]) +
      '<div class="note tiny" style="margin-top:9px">是否支持 6 GHz / Wi-Fi 7 以驱动报告为准；本工具不会因网卡不支持而伪造扫描结果。</div>'
    : '<div class="tiny muted">' + esc(drv.error || "未读取到无线网卡信息") + "</div>";

  $("#localProfiles").innerHTML = prof.length
    ? '<div class="tblwrap" style="max-height:250px;overflow:auto"><table><thead><tr><th>SSID / 配置名</th><th>认证</th><th>加密</th><th>判定</th></tr></thead><tbody>' +
      prof.map(p => "<tr><td>" + esc(p.name) + "</td><td>" + esc(p.auth || "-") + "</td><td>" + esc(p.cipher || "-") +
        "</td><td>" + (p.weak ? '<span style="color:var(--bad)">' + esc(p.weak_reason || "弱") + "</span>" :
          '<span class="muted">正常</span>') + "</td></tr>").join("") + "</tbody></table></div>" +
      '<div class="note tiny" style="margin-top:9px">只调用 <code>netsh wlan show profile name=X</code>（不带 key=clear），不读取、不显示任何明文无线密码。</div>'
    : '<div class="tiny muted">本机没有已保存的无线配置文件。</div>';

  $("#localNetwork").innerHTML = net.length
    ? '<div class="tblwrap" style="max-height:250px;overflow:auto"><table><thead><tr><th>适配器</th><th>IPv4 / 掩码</th><th>网关</th><th>DNS</th><th>DHCP</th></tr></thead><tbody>' +
      net.map(n => "<tr><td>" + esc(n.adapter || "-") + "</td><td><code>" + esc(n.ip || "-") + "</code> / " + esc(n.mask || "-") +
        "</td><td>" + esc(n.gateway || "-") + "</td><td class='tiny'>" + esc((n.dns || []).join(" / ") || "-") +
        "</td><td>" + esc(n.dhcp || "-") + "</td></tr>").join("") + "</tbody></table></div>"
    : '<div class="tiny muted">未解析到 IPv4 适配器。</div>';

  $("#localReport").innerHTML = rep.available
    ? localRows([["报告文件", rep.path], ["生成时间", rep.generated],
        ["断开记录", rep.disconnects], ["失败相关记录", rep.failures],
        ["常见原因", (rep.reasons || []).slice(0, 3).join("；")]]) +
      '<div class="tblwrap" style="max-height:180px;overflow:auto;margin-top:9px"><table><thead><tr><th>时间</th><th>类型</th><th>标识</th></tr></thead><tbody>' +
      (rep.events || []).slice(0, 20).map(e => "<tr><td class='tiny'>" + esc(e.time) + "</td><td>" + esc(e.kind) +
        "</td><td class='tiny'>" + esc(e.ssid || "-") + "</td></tr>").join("") + "</tbody></table></div>"
    : '<div class="tiny muted">' + esc(rep.error || "系统尚未生成 WLAN 报告") +
      '</div><div class="note tiny" style="margin-top:9px">点右上角「生成系统 WLAN 报告」会调用 Windows 自带的 <code>netsh wlan show wlanreport</code>，报告由系统写到 ProgramData，本工具只读取、不改写。</div>';

  $("#localFindings").innerHTML = (d.findings || []).length
    ? (d.findings || []).map(f => '<div class="card" style="margin:0 0 9px;padding:11px 13px;box-shadow:none;border:1px solid var(--line)">' +
        '<div class="row" style="gap:8px;align-items:center">' + localChip(f.severity) + "<b>" + esc(f.title) + "</b></div>" +
        '<div class="tiny muted" style="margin-top:5px">证据：' + esc(f.evidence) + "</div>" +
        '<div class="tiny" style="margin-top:4px">处置：' + esc(f.action) + "</div></div>").join("")
    : '<div class="tiny muted">无</div>';
  $("#localNotes").innerHTML = (d.notes || []).map(x => "· " + esc(x)).join("<br>") +
    "<br>· 本机诊断只回答「这台电脑自身配置有没有问题」；空口质量、干扰与漫游请以「空口扫描 / 现场勘测 / 移动漫游」的真实采集结论为准。";
}
async function localLoad(force) {
  if (LOCAL_STATE.busy) return;
  LOCAL_STATE.busy = true;
  $("#localstatus").innerHTML = '<span class="loading"></span>正在读取本机（网卡 / 配置 / 网络栈 / 系统报告）…';
  try {
    const d = await api("/api/local/diagnostics" + (force ? "?t=" + Date.now() : ""));
    LOCAL_STATE.data = d; localRender(d);
  } catch (e) {
    $("#localstatus").innerHTML = '<span style="color:var(--bad)">读取失败：' + esc(e.message) + "</span>";
  } finally { LOCAL_STATE.busy = false; }
}
function initLocalDiag() {
  $("#localrefresh").addEventListener("click", () => localLoad(true));
  $("#localwlanreport").addEventListener("click", async () => {
    $("#localstatus").innerHTML = '<span class="loading"></span>正在请 Windows 生成系统 WLAN 报告（可能需要十几秒）…';
    try {
      const r = await api("/api/local/wlanreport", { force: true });
      $("#localstatus").innerHTML = '<span class="muted">' + esc(r.message || "") + "</span>";
      if (r.report && r.report.available) { LOCAL_STATE.data.wlan_report = r.report; localRender(LOCAL_STATE.data); }
      toast(r.ok ? "系统 WLAN 报告已生成并解析" : "系统未生成报告，请检查权限");
    } catch (e) { $("#localstatus").innerHTML = '<span style="color:var(--bad)">' + esc(e.message) + "</span>"; }
  });
  localLoad(false);
}

/* ---------------- AP 能力矩阵 ---------------- */
function renderCapMatrix(r) {
  const bss = (r.bss || []).slice().sort((a, b) => (b.rssi || -999) - (a.rssi || -999));
  const caps = bss.map(b => b.cap || {});
  const cnt = k => caps.filter(c => c[k]).length;
  const known = caps.filter(c => c.pmf && c.pmf !== "n/a");
  $("#capkpis").innerHTML = [
    localKpi("解析出能力的 BSS", caps.filter(c => (c.flags || []).length).length, "共 " + bss.length + " 个 BSS"),
    localKpi("WPA3 / OWE", cnt("wpa3"), "SAE " + cnt("sae") + " · OWE " + cnt("owe")),
    localKpi("PMF 强制 / 可选", caps.filter(c => c.pmf === "required").length + " / " + caps.filter(c => c.pmf === "capable").length,
      "缺失 " + caps.filter(c => c.pmf === "none").length + " 个", "color:var(--warn)"),
    localKpi("802.11r / k / v", cnt("ft") + " / " + cnt("rm") + " / " + cnt("btm"), "按 Beacon IE 位推断")
  ].join("");
  const pmfText = { required: '<span style="color:var(--ok)">强制</span>', capable: '<span style="color:var(--warn)">可选</span>',
    none: '<span style="color:var(--bad)">未启用</span>', "n/a": '<span class="muted">开放网络</span>' };
  $("#captbl tbody").innerHTML = bss.length ? bss.map((b, i) => {
    const c = caps[i];
    const roam = [c.ft ? "11r" : "", c.rm ? "11k" : "", c.btm ? "11v" : ""].filter(Boolean).join(" · ") || '<span class="muted">未见</span>';
    return "<tr><td><b>" + esc(b.ssid || "（隐藏 SSID）") + "</b>" + (b.hidden ? ' <span class="pill">隐藏</span>' : "") +
      "<br><code class='tiny'>" + esc(b.bssid) + "</code></td>" +
      "<td>" + esc(String(b.band || "-")) + "G / ch" + esc(String(b.channel || "-")) + "<br><span class='tiny muted'>" +
      esc(String(b.freq_mhz || "-")) + " MHz</span></td>" +
      "<td>" + esc(b.phy || "-") + "<br><span class='tiny muted'>" + esc(String(b.width_mhz || 20)) + " MHz</span></td>" +
      "<td>" + esc(b.security || "-") + "<br><span class='tiny muted'>" + esc((c.ciphers || []).join(" / ") || "-") + "</span></td>" +
      "<td>" + (pmfText[c.pmf] || '<span class="muted">-</span>') + "</td>" +
      "<td class='tiny'>" + roam + "</td>" +
      "<td class='tiny'>" + ((c.flags || []).length ? (c.flags || []).map(f =>
        '<span class="pill" style="margin:1px 2px 1px 0;padding:1px 6px">' + esc(f) + "</span>").join("") : '<span class="muted">-</span>') + "</td>" +
      "<td class='tiny'>" + esc((c.vendor_names || []).slice(0, 2).join(" / ") || "-") +
      "<br><span class='muted'>DTIM " + esc(c.dtim_period == null ? "-" : c.dtim_period) +
      " · Color " + esc(c.bss_color == null ? "-" : c.bss_color) + "</span></td></tr>";
  }).join("") : '<tr><td colspan="8" style="text-align:center;padding:18px;color:#8b97a6">点「开始扫描」后显示</td></tr>';
  $("#capnote").textContent = (caps.find(c => c.note) || {}).note ||
    "能力由 Beacon / Probe 信息元素位推断，未做关联协商验证；厂商实现可能有差异。";
}

/* ---------------- 扫描历史趋势 ---------------- */
const HIST_LABELS = { overall_score: "区域评分", avg_channel_index: "平均干扰指数", radio_total: "可见射频数",
  avg_rssi: "平均 RSSI (dBm)", best_rssi: "最强 RSSI (dBm)", utilization_max: "最高信道利用率 (%)",
  current_rssi: "当前关联 RSSI (dBm)" };
function drawHistChart(items, metric) {
  const cv = $("#histchart"); if (!cv) return;
  const g = cv.getContext("2d"), w = cv.width, h = cv.height;
  const css = getComputedStyle(document.documentElement);
  const tx = (css.getPropertyValue("--tx2") || "#556170").trim();
  const line = (css.getPropertyValue("--line") || "#e5e9f1").trim();
  const bg = (css.getPropertyValue("--panel") || "#fff").trim();
  const blue = "#2563eb", red = "#d92d20";
  g.clearRect(0, 0, w, h); g.fillStyle = bg; g.fillRect(0, 0, w, h);
  const L = 58, R = 16, T = 18, B = 30, pw = w - L - R, ph = h - T - B;
  const vals = items.map(x => x[metric]).filter(v => typeof v === "number" && isFinite(v));
  if (!vals.length) {
    g.fillStyle = tx; g.font = "14px sans-serif";
    g.fillText("暂无历史数据：每次「开始扫描」会自动记录一行快照", L + 10, T + ph / 2);
    return;
  }
  let mn = Math.min.apply(null, vals), mx = Math.max.apply(null, vals);
  if (mx - mn < 1) { mx += 1; mn -= 1; }
  const pad = (mx - mn) * .12; mn -= pad; mx += pad;
  g.strokeStyle = line; g.lineWidth = 1; g.font = "11px sans-serif"; g.fillStyle = tx;
  for (let i = 0; i <= 4; i++) {
    const y = T + ph * i / 4, v = mx - (mx - mn) * i / 4;
    g.beginPath(); g.moveTo(L, y); g.lineTo(L + pw, y); g.stroke();
    g.fillText(v.toFixed(1), 6, y + 4);
  }
  const n = items.length;
  const X = i => L + (n === 1 ? pw / 2 : pw * i / (n - 1));
  const Y = v => T + ph * (1 - (v - mn) / (mx - mn));
  // 面积
  g.beginPath(); let started = false;
  items.forEach((it, i) => {
    const v = it[metric]; if (typeof v !== "number" || !isFinite(v)) return;
    if (!started) { g.moveTo(X(i), Y(v)); started = true; } else g.lineTo(X(i), Y(v));
  });
  g.strokeStyle = blue; g.lineWidth = 2; g.stroke();
  g.beginPath(); started = false; let lastX = L;
  items.forEach((it, i) => {
    const v = it[metric]; if (typeof v !== "number" || !isFinite(v)) return;
    if (!started) { g.moveTo(X(i), T + ph); g.lineTo(X(i), Y(v)); started = true; } else g.lineTo(X(i), Y(v));
    lastX = X(i);
  });
  g.lineTo(lastX, T + ph); g.closePath(); g.fillStyle = "rgba(37,99,235,.10)"; g.fill();
  items.forEach((it, i) => {
    const v = it[metric]; if (typeof v !== "number" || !isFinite(v)) return;
    g.fillStyle = blue; g.beginPath(); g.arc(X(i), Y(v), 2.6, 0, Math.PI * 2); g.fill();
  });
  // 均值线
  const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
  g.setLineDash([5, 4]); g.strokeStyle = red; g.beginPath();
  g.moveTo(L, Y(avg)); g.lineTo(L + pw, Y(avg)); g.stroke(); g.setLineDash([]);
  g.fillStyle = red; g.fillText("均值 " + avg.toFixed(1), L + pw - 92, Y(avg) - 6);
  // 时间轴
  g.fillStyle = tx;
  [0, Math.floor(n / 2), n - 1].filter((v, i, a) => a.indexOf(v) === i).forEach(i => {
    const t = String(items[i].at || "").slice(5, 16);
    g.fillText(t, Math.max(L, Math.min(L + pw - 70, X(i) - 28)), h - 8);
  });
  g.fillStyle = tx; g.fillText(HIST_LABELS[metric] || metric, L, T - 5);
}
async function histLoad() {
  try {
    const d = await api("/api/history/state?limit=400");
    HIST_STATE.data = d;
    const metric = $("#histMetric").value;
    drawHistChart(d.items || [], metric);
    $("#histinfo").textContent = "共 " + (d.total || 0) + " 条快照" +
      (d.span && d.span.from ? " · " + d.span.from + " ~ " + d.span.to : "");
    const its = d.items || [], last = its[its.length - 1] || {}, scores = its.map(x => x.overall_score).filter(v => typeof v === "number");
    $("#histkpis").innerHTML = [
      localKpi("快照总数", d.total || 0, "本地 JSONL，滚动保留最近 3000 条"),
      localKpi("最新评分", last.overall_score == null ? "-" : last.overall_score,
        "较首条 Δ " + ((d.delta || {}).score ?? "-")),
      localKpi("评分均值", scores.length ? (scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(1) : "-",
        "区间最低 " + (scores.length ? Math.min.apply(null, scores) : "-")),
      localKpi("关联变更", d.switches || 0, "跨快照的当前 SSID 变化次数")
    ].join("");
    const hrs = Object.keys(d.by_hour || {}).sort();
    $("#histhours").innerHTML = hrs.length
      ? "按小时聚合：" + hrs.map(h => h + ":00（" + d.by_hour[h].n + " 次 · 评分 " +
          d.by_hour[h].score + " · 干扰 " + d.by_hour[h].index + "）").join("　")
      : "尚无按时段聚合数据。";
  } catch (e) { $("#histinfo").textContent = "历史读取失败：" + e.message; }
}
function initHistory() {
  $("#histMetric").addEventListener("change", () => { if (HIST_STATE.data) drawHistChart(HIST_STATE.data.items || [], $("#histMetric").value); });
  $("#histrefresh").addEventListener("click", histLoad);
  $("#histexport").addEventListener("click", () => window.open("/api/history/export", "_blank"));
  $("#histclear").addEventListener("click", async () => {
    if (!confirm("清空所有扫描历史快照？此操作不可撤销（不影响勘测点位与项目）。")) return;
    try { await api("/api/history/clear", {}); await histLoad(); toast("扫描历史已清空"); }
    catch (e) { toast("清空失败：" + e.message); }
  });
  histLoad();
}

/* ---------------- 智能审计 ---------------- */
function initAudit() {
  $("#audrun").addEventListener("click", () => audLoad(false));
  $("#audrescan").addEventListener("click", () => audLoad(true));
  $("#audreport").addEventListener("click", () => { const u = exReportUrl(false); if (u) window.open(u, "_blank"); });
}
async function audLoad(refresh) {
  if (AUDIT_STATE.busy) return;
  AUDIT_STATE.busy = true;
  $("#audstatus").innerHTML = '<span class="muted">' + (refresh ? "正在重新扫描空口（约 5 秒）并生成审计结论…" : "正在汇总审计规则…") + "</span>";
  try {
    const d = await api(refresh ? "/api/audit/refresh" : "/api/audit/state", refresh ? {} : undefined);
    AUDIT_STATE.data = d; audRender(d);
  } catch (e) { $("#audstatus").innerHTML = '<span style="color:var(--bad)">审计失败：' + esc(e.message) + "</span>"; }
  finally { AUDIT_STATE.busy = false; }
}
function audGauge(health, grade) {
  const r = 52, C = 2 * Math.PI * r, v = Math.max(0, Math.min(100, health || 0));
  const col = v >= 85 ? "#12855b" : v >= 70 ? "#65a30d" : v >= 55 ? "#d69b26" : "#d6453d";
  return '<svg width="132" height="132" viewBox="0 0 132 132">' +
    '<circle cx="66" cy="66" r="' + r + '" fill="none" stroke="#eef1f6" stroke-width="12"/>' +
    '<circle cx="66" cy="66" r="' + r + '" fill="none" stroke="' + col + '" stroke-width="12" stroke-linecap="round" ' +
    'stroke-dasharray="' + (C * v / 100).toFixed(1) + " " + C.toFixed(1) + '" transform="rotate(-90 66 66)"/>' +
    '<text x="66" y="64" text-anchor="middle" font-size="30" font-weight="700" fill="#173a7a">' + (health === null ? "-" : v) + "</text>" +
    '<text x="66" y="86" text-anchor="middle" font-size="12" fill="#667085">' + esc(grade || "-") + "</text></svg>";
}
function audRender(d) {
  const a = d.audit || {}, c = a.counts || {}, roll = (d.air && d.air.rollup) || {}, sec = d.security || {}, sm = sec.summary || {};
  $("#audkpis").innerHTML = [
    tile("网络健康分", a.health === null ? "-" : a.health, a.grade || "待审计"),
    tile("待处置问题", a.finding_total === undefined ? "-" : a.finding_total, "严重 " + (c.critical || 0) + " / 高 " + (c.high || 0)),
    tile("空口 BSS / 射频", (roll.bss_total || 0) + " / " + (roll.radio_total || 0), "隐藏 " + (roll.hidden_bss || 0) + " · 开放 " + (roll.open_bss || 0)),
    tile("勘测达标率", d.eval && d.eval.pass_rate !== null ? d.eval.pass_rate + "%" : "-", (d.eval ? d.eval.total : 0) + " 个勘测点"),
  ].join("");
  $("#audstatus").innerHTML = a.ok
    ? '<span style="color:var(--ok)">● 审计完成 ' + esc(d.generated_at) + '</span>　<span class="muted">数据来自 ' + esc(d.air.scanned_at || "上次扫描") + " · " + (d.air.count || 0) + " 个 BSS · 会话「" + esc(d.session || "未命名") + "」</span>"
    : '<span class="warn" style="display:block;padding:9px 11px">' + esc(a.error || "暂无审计数据") + "　<button class=\"btn ghost sm\" onclick=\"go('air')\">去空口扫描</button></span>";
  $("#audmethod").innerHTML = "<b>判定方法：</b>" + esc(a.method || "规则库比对") +
    "　|　<b>首要处置：</b>" + esc(a.top_action || "-");
  $("#audgauge").innerHTML = '<div class="gauge">' + audGauge(a.health, a.grade) +
    '<div class="gt"><b>严重 ' + (c.critical || 0) + "</b> 条<br><b>高 " + (c.high || 0) + "</b> 条<br><b>中 " + (c.medium || 0) + "</b> 条<br><b>低 " + ((c.low || 0) + (c.info || 0)) + "</b> 条</div></div>";
  $("#audtop").innerHTML = a.top_action ? '<div class="note tiny"><b>建议先做：</b>' + esc(a.top_action) + "</div>" : "";
  const ks = [["区域综合评分", roll.overall_score, roll.overall_grade], ["最强信号", roll.best_rssi, "dBm"],
    ["平均信道干扰指数", roll.avg_channel_index, "/100"], ["2.4G / 5G 射频", (roll.radios_24 || 0) + " / " + (roll.radios_5 || 0), ""],
    ["开放 / 隐藏 BSS", (roll.open_bss || 0) + " / " + (roll.hidden_bss || 0), ""]];
  $("#audair").innerHTML = ks.map(k => '<div class="svrow"><span>' + esc(k[0]) + "</span><b>" + fmtN(k[1]) + " " + esc(k[2] || "") + "</b></div>").join("");
  audFilters(c); audFindings(a.findings || []);
  const cats = ["覆盖验收", "勘测完整性", "信道规划", "干扰", "容量", "合规 / 可用性", "安全", "配置", "漫游", "核查"];
  const hit = {}; (a.findings || []).forEach(x => { hit[x.category] = (hit[x.category] || 0) + 1; });
  $("#audcover").innerHTML = '<div style="font-size:11px;color:var(--tx3);font-weight:650;margin-bottom:7px">' +
    '本次规则覆盖（10 类）· 高亮 = 已触发</div>' +
    cats.map(k => '<span class="tag ' + (hit[k] ? "t-warn" : "t-ok") + '" style="margin:2px 4px 2px 0">' +
      esc(k) + (hit[k] ? " · " + hit[k] : "") + "</span>").join("") +
    '<div class="muted" style="margin-top:8px;line-height:1.7">规则只比对本次扫描可见的信息；未触发不等于该项一定合规，' +
    '例如没有客户端流量数据时无法判定实际吞吐与漫游时延。</div>';
  audPlan(d.plan || {}); audSec(sec);
}
function audFilters(c) {
  const items = [["all", "全部", 0], ["critical", "严重", c.critical || 0], ["high", "高", c.high || 0],
                 ["medium", "中", c.medium || 0], ["low", "低 / 提示", (c.low || 0) + (c.info || 0)]];
  $("#audfilters").innerHTML = items.map(i => '<button class="aud-fbtn' + (AUDIT_STATE.filter === i[0] ? " on" : "") +
    '" data-f="' + i[0] + '">' + esc(i[1]) + " " + i[2] + "</button>").join("");
  $$(".aud-fbtn").forEach(b => b.addEventListener("click", () => {
    AUDIT_STATE.filter = b.dataset.f;
    audFilters((AUDIT_STATE.data.audit || {}).counts || {});
    audFindings((AUDIT_STATE.data.audit || {}).findings || []);
  }));
}
function audFindings(list) {
  const f = AUDIT_STATE.filter;
  const shown = f === "all" ? list : list.filter(x => f === "low" ? (x.severity === "low" || x.severity === "info") : x.severity === f);
  $("#audfindcnt").textContent = "· 显示 " + shown.length + " / " + list.length + " 条";
  $("#audfindings").innerHTML = shown.length ? shown.map(x =>
    '<div class="finding ' + esc(x.severity) + '"><div class="fh"><span class="sev ' + esc(x.severity) + '">' + esc(x.severity_text) +
    '</span><span class="ft">' + esc(x.title) + '</span><span class="tiny muted">' + esc(x.id) + "</span></div>" +
    '<div class="meta">分类：' + esc(x.category) + "</div>" +
    '<span class="lbl">证据</span><ul>' + (x.evidence || []).map(e => "<li>" + esc(e) + "</li>").join("") + "</ul>" +
    '<span class="lbl">影响</span><div style="font-size:11.5px;color:var(--tx2)">' + esc(x.impact) + "</div>" +
    '<span class="lbl">处置建议</span><ul>' + (x.action || []).map(e => "<li>" + esc(e) + "</li>").join("") + "</ul>" +
    ((x.refs || []).length ? '<div class="refs">关联：' + esc(x.refs.join(" · ")) + "</div>" : "") + "</div>").join("")
    : '<div class="note tiny">' + (list.length ? "当前过滤条件下没有条目。" : "本次未触发任何审计规则，建议保存基线报告。") + "</div>";
}
function audPlan(plan) {
  const bands = plan.bands || [];
  $("#audplan").innerHTML = bands.length ? bands.map(b => {
    const w = b.worst;
    return '<div class="planband"><div class="ph"><span>' + esc(b.band) + ' GHz</span>' +
      '<span class="tag t-ok">建议首选 ch' + esc(b.target) + '</span>' +
      '<span class="tag ' + (b.avg_index >= 55 ? "t-bad" : b.avg_index >= 35 ? "t-warn" : "t-ok") + '">平均干扰指数 ' + esc(b.avg_index) + "</span>" +
      (w ? '<span class="tiny muted">最忙 ch' + esc(w.channel) + " · 指数 " + esc(w.interference_index) + "</span>" : "") + "</div>" +
      '<div class="pd">' + esc(b.advice) + "</div>" +
      '<div class="pk"><span>带宽：' + esc(b.width_advice) + "</span><span>功率：" + esc(b.power_advice) +
      "</span><span>占用信道 " + esc(b.busy_count) + " 个</span></div>" +
      '<div class="tblwrap" style="margin-top:9px;box-shadow:none"><table><thead><tr><th>信道</th><th>BSS</th><th>覆盖射频</th><th>活跃</th><th>利用率</th><th>干扰指数</th><th>等级</th></tr></thead><tbody>' +
      (b.channels || []).map(c => "<tr><td>ch" + esc(c.channel) + (c.channel === b.target ? ' <span class="tag t-ok">建议</span>' : "") +
        "</td><td>" + esc(c.bss_on_channel) + "</td><td>" + esc(c.radios_on_channel) + "</td><td>" + esc(c.active_radios) +
        "</td><td>" + fmtN(c.utilization_pct, "%") + "</td><td>" + esc(c.interference_index) + "</td><td>" + esc(c.level) + "</td></tr>").join("") +
      "</tbody></table></div></div>";
  }).join("") + '<div class="note tiny">' + esc(plan.assumption || "") +
    '<br><b>「覆盖射频」</b>按频率区间统计：40 / 80 MHz 的射频会同时覆盖多个 20 MHz 信道编号，' +
    '因此相邻行出现同一批射频属正常，不代表该信道上有独立 AP。</div>'
    : '<div class="note tiny">暂无空口数据，先在「空口扫描」页扫描一次。</div>';
}
function audSec(sec) {
  const sm = sec.summary || {};
  $("#audseckpis").innerHTML = [
    tile("BSS 总数", sm.total || 0, "本次扫描可见"),
    tile("开放 / 弱加密", (sm.open || 0) + " / " + (sm.weak || 0), "高危组合"),
    tile("未登记 SSID", sm.rogue || 0, "需现场核对，不等于非法 AP"),
    tile("随机私有 MAC", sm.random_mac || 0, "不能用于厂商判断"),
  ].join("");
  $("#audtwins").innerHTML = (sec.twins || []).length
    ? (sec.twins || []).map(t => '<div class="warn tiny" style="margin-bottom:9px"><b>疑似仿冒热点：' + esc(t.ssid) +
      "</b>　" + esc(t.detail) + "</div>").join("") : "";
  const rows = sec.rows || [];
  $("#audsecbody").innerHTML = rows.length ? rows.slice(0, 80).map(r =>
    "<tr><td>" + esc(r.ssid) + (r.hidden ? ' <span class="tag t-warn">隐藏</span>' : "") +
    '</td><td class="tiny">' + esc(r.bssid) + "</td><td>" + esc(r.vendor) +
    "</td><td>" + esc(r.band) + " / ch" + esc(r.channel) + "</td><td>" + fmtN(r.rssi) +
    "</td><td>" + esc(r.security) + '</td><td class="tiny">' +
    ((r.flags || []).length ? (r.flags || []).map(f => '<span class="tag ' + (r.severity === "critical" || r.severity === "high" ? "t-bad" : "t-warn") + '" style="margin:1px 2px 1px 0">' + esc(f) + "</span>").join("") : '<span class="muted">—</span>') +
    "</td></tr>").join("")
    : '<tr><td colspan="7" style="text-align:center;padding:22px;color:#8b97a6">点「刷新审计」读取空口数据</td></tr>';
}

/* ====================================================================
   启动
==================================================================== */
(async function boot() {
  try {
    const s = await api("/api/stats");
    STATS = s.stats || {}; $("#ver").textContent = "v" + s.version; $("#fver").textContent = "v" + s.version;
    $("#userdir").textContent = s.user_dir; $("#catver").textContent = s.catalog_version + " / " + s.generated_at;
    renderDash();
    CAT = await api("/api/catalog");
    initCatalog(); initHeat(); initDiag();
    await initSSID();          // 全局 SSID 维度：需在 initHeat 之后（编辑表单要读 HS.aps）
    await initLive();
    initAir();                // 空口扫描：真实 BSS 列表 + 干扰评估
    initRoam();               // 移动漫游：当前关联 + 漫游时间线 + 信道拥挤
    initSurvey();             // 现场勘测：实测打点 + 需求验收 + 模型校准 + 报告
    initAudit();              // 智能审计：问题清单 + 信道规划 + 安全合规
    initAdvanced();           // 报告模板 / 合订 / 基线 diff / CSV / 规则 / 主题
    await initWLC();          // WLC 离线知识包：码表 / 话术 / 处置步骤 / 文献索引
    await initLib();          // 官方文档库：125 篇离线文献 + SOP，检索在浏览器本地完成
    renderLegend();           // 色标按当前图层 / SSID 门限动态生成
    await initLocalWlan(true);  // 本机 Wi-Fi 只读：自动采用当前 SSID 并触发一次全局重算
    // 快捷入口：/?page=heat 直达热力图，/?demo=1 自动算出示例热力图（便于演示与验收）
    const qs = new URLSearchParams(location.search);
    if (qs.get("page")) go(qs.get("page"));
    if (qs.get("demo") === "1") { go("heat"); setTimeout(() => runHeat(), 250); }
  } catch (e) {
    document.body.insertAdjacentHTML("afterbegin", '<div style="padding:16px;background:#fdeceb;color:#c7372f">初始化失败：' + esc(e.message) + '　请刷新页面，或查看控制台窗口的错误信息。</div>');
  }
})();
})();
