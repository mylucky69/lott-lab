"use strict";
const WEEKDAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"];
const STATUS = { preview: "Đang xem trước", locked: "Đã chốt", settled: "Đã có kết quả" };
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pad = (n) => String(n).padStart(2, "0");
const fmtInt = (n) => Number(n).toLocaleString("vi-VN");
const fmtMoney = (n) => n == null ? "—" : "$" + Number(n).toLocaleString("en-AU", { maximumFractionDigits: 2 });
const num = (x, d = 2) => Number(x).toLocaleString("vi-VN", { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (p, d = 1) => num(p * 100, d) + "%";
const SHORT = { balanced: "Phủ đều (đang dùng)", hot: "Số nóng", cold: "Số lạnh", random: "Ngẫu nhiên" };

function fmtDate(iso) {
  const d = new Date(iso + "T12:00:00");
  const wd = (d.getDay() + 6) % 7;
  return `${WEEKDAYS[wd]}, ${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()}`;
}
function fmtDateTime(iso) {
  if (!iso) return "—";
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-GB", { timeZone: "Australia/Sydney", hour: "2-digit", minute: "2-digit",
    day: "2-digit", month: "2-digit", hourCycle: "h23" }).formatToParts(new Date(iso)).map((p) => [p.type, p.value]));
  return `${parts.hour}:${parts.minute} ngày ${parts.day}/${parts.month} (giờ Sydney)`;
}

const state = { index: null, games: {}, current: null };

async function load() {
  const bust = "?t=" + Math.floor(Date.now() / 60000);
  try {
    state.index = await (await fetch("data/index.json" + bust)).json();
  } catch {
    $("#app").innerHTML = `<div class="card"><h2>Chưa có dữ liệu</h2><p class="muted">Hệ thống chưa chạy lần nào. Hãy chạy workflow “Cập nhật kết quả” trên GitHub (xem README).</p></div>`;
    $("#updated").textContent = "";
    return;
  }
  await Promise.all(state.index.games.map(async (id) => {
    try { state.games[id] = await (await fetch(`data/${id}.json` + bust)).json(); } catch { /* skip */ }
  }));
  $("#updated").innerHTML = `Kiểm tra lần cuối: ${fmtDateTime(state.index.checked_at)}`;
  const tabs = $("#tabs");
  tabs.innerHTML = state.index.games.filter((id) => state.games[id]).map((id) =>
    `<button role="tab" data-id="${id}">${esc(state.games[id].meta.name)}</button>`).join("");
  tabs.onclick = (e) => { const b = e.target.closest("button"); if (b) show(b.dataset.id); };
  let start = location.hash.slice(1);
  if (!state.games[start]) { try { start = localStorage.getItem("lott.tab"); } catch { start = null; } }
  show(state.games[start] ? start : state.index.games.find((id) => state.games[id]));
}

function show(id) {
  state.current = id;
  try { localStorage.setItem("lott.tab", id); } catch { /* ignore */ }
  history.replaceState(null, "", "#" + id);
  document.querySelectorAll("#tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.id === id));
  render(state.games[id]);
}

/* ---------- small renderers ---------- */
function balls(main, supp = [], opts = {}) {
  const { pb = null, hitMain = null, hitSupp = null, lg = false, suppLabel = true } = opts;
  const cls = (base) => base + (lg ? " lg" : "");
  let h = main.map((n) => {
    const hit = hitMain && hitMain.has(n);
    const hs = hitSupp && hitSupp.has(n);
    return `<span class="${cls("ball" + (hit ? " hit" : "") + (hs ? " hit-supp" : ""))}" title="${hit ? "Trùng số chính" : hs ? "Trùng số phụ" : ""}">${pad(n)}</span>`;
  }).join("");
  if (supp && supp.length && suppLabel) h += `<span class="sep"></span>` + supp.map((n) => `<span class="${cls("ball supp")}" title="Số phụ">${pad(n)}</span>`).join("");
  if (pb != null) h += `<span class="sep"></span><span class="${cls("ball pb")}" title="Powerball">${pad(pb)}</span>`;
  return `<span class="balls">${h}</span>`;
}
function drawBalls(g, d, lg) {
  return g.meta.pb_pool ? balls(d.main, [], { pb: d.supp[0], lg }) : balls(d.main, d.supp, { lg });
}
function tile(k, v, d = "") { return `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="d">${d}</div></div>`; }

/* ---------- main render ---------- */
function render(g) {
  const parts = [nextCard(g), latestCard(g), trackingCard(g), statsCard(g), backtestCard(g), oddsCard(g)];
  $("#app").innerHTML = parts.join("");
  bindCharts();
  bindNext(g);
  tickCountdown();
}

function statusWarn(g) {
  const s = g.status || {};
  let h = "";
  if (s.conflicts && s.conflicts.length) h += `<div class="warn">⚠ Hai nguồn báo số khác nhau, đã giữ số cũ, cần kiểm tra: ${esc(s.conflicts.join("; "))}</div>`;
  if (s.fetch_ok === false && s.checked_at) h += `<div class="warn">⚠ Đến ${fmtDateTime(s.checked_at)} vẫn chưa lấy được kết quả kỳ mới nhất${s.error ? ` (${esc(s.error.slice(0, 160))})` : ""}.
  Hệ thống sẽ tự thử lại mỗi giờ. Nếu cần gấp, nhập tay bằng workflow “Nhập kết quả thủ công” trên GitHub.</div>`;
  return h;
}

const SOURCE_NAMES = { lotteryextreme: "LotteryExtreme", "national-lottery": "National-Lottery", manual: "nhập tay", csv: "CSV", test: "test" };
function sourceText(d) {
  const list = d.sources || (d.source ? [d.source] : []);
  const names = list.map((x) => SOURCE_NAMES[x] || x).join(", ");
  return list.length >= 2 ? `✓ Đã đối chiếu ${list.length} nguồn (${esc(names)})` : `Nguồn: ${esc(names)}`;
}

function nextCard(g) {
  const n = g.next;
  if (!n) return `<section class="card"><h2>Kỳ quay tiếp theo</h2><p class="muted">Chưa có kết quả nào nên chưa tạo được bộ số. Hệ thống đang tải dữ liệu.</p>${statusWarn(g)}</section>`;
  const sim = n.simulation || {}, rnd = n.simulation_random || {};
  const isPB = !!g.meta.pb_pool;
  const lines = n.lines.map((l, i) => `<div class="line"><span class="idx">${pad(i + 1)}</span>${balls(l.main, [], { pb: isPB ? l.pb : null })}</div>`).join("");
  const lockTxt = n.status === "locked"
    ? `Đã chốt lúc ${fmtDateTime(n.locked_at)}`
    : `Sẽ chốt lúc ${fmtDateTime(n.lock_at)} · <span data-countdown="${esc(n.lock_at)}"></span>`;
  return `<section class="card">
    <div class="card-head">
      <div>
        <h2>${esc(g.meta.name)} · kỳ #${n.draw}</h2>
        <div class="sub">${fmtDate(n.date)}${n.jackpot ? ` · Giải độc đắc ${fmtMoney(n.jackpot)}` : ""}</div>
        <div class="sub">${lockTxt}</div>
      </div>
      <span class="badge ${n.status}">${n.status === "locked" ? "🔒" : "✎"} ${STATUS[n.status] || n.status}</span>
    </div>
    <div class="stats-row">
      ${tile("Có ≥ 1 dòng trúng giải", sim.p_any != null ? pct(sim.p_any) : "—", `50 dòng ngẫu nhiên: ${rnd.p_any != null ? pct(rnd.p_any) : "—"}`)}
      ${tile("Số dòng trúng trung bình / kỳ", sim.mean_prizes != null ? num(sim.mean_prizes) : "—", "Không chiến lược nào thay đổi được con số này")}
      ${tile("Xác suất giải độc đắc / dòng", "1 / " + fmtInt(g.meta.odds[0].one_in), `${n.lines.length} dòng: 1 / ${fmtInt(Math.round(g.meta.odds[0].one_in / n.lines.length))}`)}
      ${tile("Chiến lược", esc(n.strategy === "balanced" ? "Phủ đều" : n.strategy), esc(n.strategy_label))}
    </div>
    <div class="card-head" style="margin-bottom:8px">
      <h3>${n.lines.length} dòng số${isPB ? " (số đỏ = Powerball)" : ""}</h3>
      <div class="actions">
        <button class="btn primary" id="copyBtn">Sao chép</button>
        <button class="btn" id="csvBtn">Tải CSV</button>
      </div>
    </div>
    <div class="lines">${lines}</div>
    ${statusWarn(g)}
  </section>`;
}

function bindNext(g) {
  const n = g.next;
  if (!n) return;
  const text = () => `${g.meta.name} #${n.draw} - ${n.date} (${STATUS[n.status]})\n` +
    n.lines.map((l, i) => `${pad(i + 1)}: ${l.main.map(pad).join(" ")}${l.pb != null ? "  PB " + pad(l.pb) : ""}`).join("\n");
  const copy = $("#copyBtn"), csv = $("#csvBtn");
  if (copy) copy.onclick = async () => {
    try { await navigator.clipboard.writeText(text()); copy.textContent = "Đã sao chép ✓"; }
    catch { copy.textContent = "Không sao chép được"; }
    setTimeout(() => (copy.textContent = "Sao chép"), 1800);
  };
  if (csv) csv.onclick = () => {
    const head = ["line", ...Array.from({ length: g.meta.pick }, (_, i) => "n" + (i + 1)), ...(g.meta.pb_pool ? ["powerball"] : [])];
    const rows = n.lines.map((l, i) => [i + 1, ...l.main, ...(l.pb != null ? [l.pb] : [])]);
    const blob = new Blob([[head, ...rows].map((r) => r.join(",")).join("\n")], { type: "text/csv" });
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `${g.meta.id}-${n.draw}.csv` });
    a.click(); URL.revokeObjectURL(a.href);
  };
}

function tickCountdown() {
  const els = document.querySelectorAll("[data-countdown]");
  els.forEach((el) => {
    const ms = new Date(el.dataset.countdown) - Date.now();
    if (ms <= 0) { el.textContent = "đang chờ hệ thống chốt"; return; }
    const d = Math.floor(ms / 864e5), h = Math.floor(ms / 36e5) % 24, m = Math.floor(ms / 6e4) % 60;
    el.textContent = `còn ${d ? d + " ngày " : ""}${h} giờ ${m} phút`;
  });
  clearTimeout(tickCountdown.t);
  if (els.length) tickCountdown.t = setTimeout(tickCountdown, 30000);
}

function latestCard(g) {
  if (!g.latest.length) return "";
  const [last, ...rest] = g.latest;
  const divs = (last.dividends || []).filter((d) => d.amount != null);
  const divTable = divs.length ? `<div class="table-scroll"><table><thead><tr><th>Hạng</th><th>Điều kiện</th><th class="num">Số người trúng</th><th class="num">Tiền / người</th></tr></thead><tbody>${
    divs.map((d) => `<tr><td>Giải ${d.div}</td><td>${esc(g.meta.odds[d.div - 1]?.rule || "")}</td><td class="num">${d.winners != null ? fmtInt(d.winners) : "—"}</td><td class="num">${fmtMoney(d.amount)}</td></tr>`).join("")
  }</tbody></table></div>` : "";
  return `<section class="card">
    <h2>Kết quả mới nhất · kỳ #${last.draw}</h2>
    <div class="sub" style="margin-bottom:10px">${fmtDate(last.date)} · ${sourceText(last)}</div>
    ${drawBalls(g, last, true)}
    ${divTable ? `<div style="margin-top:14px">${divTable}</div>` : ""}
    <details style="margin-top:12px"><summary>Các kỳ trước</summary>
      <div class="table-scroll"><table><tbody>${rest.map((d) => `<tr><td>#${d.draw}</td><td>${fmtDate(d.date)}</td><td>${drawBalls(g, d)}</td></tr>`).join("")}</tbody></table></div>
    </details>
  </section>`;
}

function trackingCard(g) {
  const t = g.tracking || [], tot = g.totals || {};
  const isPB = !!g.meta.pb_pool;
  const body = t.length ? t.map((p) => {
    const r = p.result || {};
    const hitMain = new Set(r.main || []);
    const hitSupp = new Set(isPB ? [] : r.supp || []);
    const wins = Object.entries(p.divisions || {}).map(([k, v]) => `${v}× giải ${k}`).join(", ");
    const rows = p.lines.map((l, i) => {
      const c = (p.checks || [])[i] || {};
      const pbHit = isPB && l.pb === (r.supp || [])[0];
      return `<div class="line"><span class="idx">${pad(i + 1)}</span>${balls(l.main, [], { hitMain, hitSupp, pb: isPB ? l.pb : null })}${pbHit ? "" : ""}${c.div ? `<span class="div-tag">Giải ${c.div}</span>` : ""}</div>`;
    }).join("");
    return `<details><summary><strong>#${p.draw}</strong><span class="muted">${fmtDate(p.date)}</span>
      <span>${wins ? `<span class="good">✓ ${wins}</span>` : "Không trúng"}</span>
      <span class="muted">trùng nhiều nhất ${p.best} số</span>${p.prize ? `<span class="good">${fmtMoney(p.prize)}</span>` : ""}</summary>
      <div style="margin:6px 0 10px">Kết quả: ${drawBalls(g, r)}</div>
      <div class="lines">${rows}</div></details>`;
  }).join("") : `<p class="muted">Chưa có kỳ nào được chốt và có kết quả. Bảng này sẽ tự cập nhật sau mỗi kỳ quay.</p>`;
  const expected = (tot.lines || 0) * g.meta.any_prize_p;
  return `<section class="card">
    <h2>Theo dõi bộ số đã chốt</h2>
    <div class="sub" style="margin-bottom:12px">So sánh kết quả thật với kỳ vọng toán học</div>
    <div class="stats-row">
      ${tile("Số kỳ đã theo dõi", fmtInt(tot.draws || 0), `${fmtInt(tot.lines || 0)} dòng`)}
      ${tile("Dòng trúng giải", fmtInt(tot.prize_lines || 0), `Kỳ vọng: ${num(expected, 1)}`)}
      ${tile("Tổng tiền thưởng", fmtMoney(tot.prize || 0), tot.cost != null ? `Chi phí vé: ${fmtMoney(tot.cost)}` : "Điền giá vé trong config.json để tính lãi/lỗ")}
    </div>
    ${body}
  </section>`;
}

/* ---------- statistics ---------- */
function barChart(id, data, expected, labelFn) {
  const narrow = (document.getElementById("app")?.clientWidth || 800) < 600;
  const W = narrow ? 380 : 760, H = narrow ? 210 : 220, m = { l: 30, r: 6, t: 14, b: 24 };
  const max = Math.max(expected * 1.25, ...data.map((d) => d.count));
  const step = niceStep(max / 4);
  const top = Math.ceil(max / step) * step;
  const iw = W - m.l - m.r, ih = H - m.t - m.b, bw = iw / data.length;
  const barW = Math.min(24, Math.max(4, bw - 3));
  const y = (v) => m.t + ih - (v / top) * ih;
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(labelFn("title"))}">`;
  for (let v = 0; v <= top + 1e-9; v += step) s += `<line class="grid" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${m.l - 6}" y="${y(v) + 4}" text-anchor="end">${fmtInt(v)}</text>`;
  data.forEach((d, i) => {
    const cx = m.l + bw * i + bw / 2, x = cx - barW / 2, h = Math.max(0, y(0) - y(d.count)), r = Math.min(4, h, barW / 2);
    const path = h > 0 ? `M${x},${y(0)} V${y(0) - h + r} Q${x},${y(0) - h} ${x + r},${y(0) - h} H${x + barW - r} Q${x + barW},${y(0) - h} ${x + barW},${y(0) - h + r} V${y(0)} Z` : "";
    s += `<g data-tip="${esc(labelFn(d))}"><rect class="hitbox" x="${m.l + bw * i}" y="${m.t}" width="${bw}" height="${ih}"/><path class="bar" d="${path}"/></g>`;
    if ((data.length <= 20 && !narrow) || d.n % 5 === 0 || d.n === 1) s += `<text x="${cx}" y="${H - 8}" text-anchor="middle">${d.n}</text>`;
  });
  s += `<line class="ref" x1="${m.l}" x2="${W - m.r}" y1="${y(expected)}" y2="${y(expected)}"/>`;
  s += `<text x="${W - m.r}" y="${y(expected) - 5}" text-anchor="end">kỳ vọng ${num(expected, 1)}</text>`;
  return s + "</svg>";
}
function niceStep(x) {
  const p = Math.pow(10, Math.floor(Math.log10(Math.max(x, 1e-9)))), f = x / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}
function bindCharts() {
  const tip = $("#tip");
  document.querySelectorAll("[data-tip]").forEach((el) => {
    el.addEventListener("pointermove", (e) => {
      tip.textContent = el.dataset.tip; tip.style.opacity = 1;
      const x = Math.min(e.clientX + 12, innerWidth - tip.offsetWidth - 8);
      tip.style.left = x + "px"; tip.style.top = (e.clientY - tip.offsetHeight - 10) + "px";
    });
    el.addEventListener("pointerleave", () => (tip.style.opacity = 0));
  });
}
function pVerdict(p) {
  if (p == null) return "—";
  return `p = ${num(p, 3)} · ${p < 0.01 ? "<strong>có sai lệch bất thường</strong>" : "phù hợp với ngẫu nhiên"}`;
}

function statsCard(g) {
  const s = g.stats;
  if (!s || !s.n_draws) return "";
  const nums = s.numbers;
  const hot = [...nums].sort((a, b) => b.recent - a.recent || a.n - b.n).slice(0, 8);
  const cold = [...nums].sort((a, b) => b.gap - a.gap || a.n - b.n).slice(0, 8);
  const chip = (list, f) => `<div class="chip-list">${list.map((x) => `<span title="${esc(f(x))}">${balls([x.n])}</span>`).join("")}</div>`;
  const chart = barChart("freq", nums, s.expected_count, (d) => d === "title" ? "Tần suất từng số" : `Số ${d.n}: ra ${d.count} lần (kỳ vọng ${num(s.expected_count, 1)}), ${d.recent} lần trong 20 kỳ gần nhất, ${d.gap} kỳ chưa ra`);
  const pbChart = s.pb ? `<h3 style="margin-top:18px">Tần suất Powerball</h3>${barChart("pb", s.pb, s.pb_expected, (d) => d === "title" ? "Tần suất Powerball" : `Powerball ${d.n}: ${d.count} lần (kỳ vọng ${num(s.pb_expected, 1)})`)}<p class="sub">Kiểm định chi bình phương Powerball: ${pVerdict(s.p_chi2_pb)}</p>` : "";
  const table = `<details><summary>Xem bảng số liệu</summary><div class="table-scroll"><table><thead><tr><th>Số</th><th class="num">Số lần ra</th><th class="num">20 kỳ gần nhất</th><th class="num">Số kỳ chưa ra</th></tr></thead><tbody>${
    nums.map((d) => `<tr><td>${d.n}</td><td class="num">${d.count}</td><td class="num">${d.recent}</td><td class="num">${d.gap}</td></tr>`).join("")}</tbody></table></div></details>`;
  return `<section class="card">
    <h2>Thống kê ${fmtInt(s.n_draws)} kỳ quay</h2>
    <div class="sub" style="margin-bottom:12px">Từ ${fmtDate(s.first)} đến ${fmtDate(s.last)}, chỉ tính các kỳ theo luật hiện tại</div>
    <div class="legend"><span class="key"><i></i>Số lần ra của mỗi số</span><span class="key"><i class="refkey"></i>Kỳ vọng nếu hoàn toàn ngẫu nhiên</span></div>
    ${chart}
    ${table}
    <div class="stats-row" style="margin-top:14px">
      ${tile("Kiểm định tần suất (chi bình phương)", s.p_chi2 < 0.01 ? "Bất thường" : "Ngẫu nhiên", pVerdict(s.p_chi2))}
      ${tile("Kiểm định độc lập giữa các kỳ", s.p_serial == null ? "—" : s.p_serial < 0.01 ? "Bất thường" : "Độc lập", `Số trùng TB với kỳ trước: ${num(s.mean_overlap ?? 0)} (kỳ vọng ${num(s.expected_overlap)}) · ${pVerdict(s.p_serial)}`)}
    </div>
    ${pbChart}
    <div class="cols" style="margin-top:14px">
      <div><h3>Số “nóng” (20 kỳ gần nhất)</h3>${chip(hot, (x) => `${x.recent} lần`)}</div>
      <div><h3>Số “lạnh” (lâu chưa ra)</h3>${chip(cold, (x) => `${x.gap} kỳ chưa ra`)}</div>
    </div>
    <p class="sub" style="margin-top:12px">Số nóng/lạnh chỉ để tham khảo: chênh lệch so với kỳ vọng nằm trong mức dao động ngẫu nhiên bình thường, không giúp đoán kỳ sau.</p>
  </section>`;
}

function backtestCard(g) {
  const b = g.backtest;
  if (!b || !b.draws) return `<section class="card"><h2>Thử nghiệm chiến lược</h2><p class="muted">Chưa chạy. Workflow “Thử nghiệm chiến lược” chạy mỗi tuần, hoặc bạn chạy tay trên GitHub.</p></section>`;
  const rows = Object.entries(b.strategies).map(([k, r]) => `<tr><td title="${esc(r.label)}">${esc(SHORT[k] || r.label)}</td><td class="num">${num(r.mean_match, 3)}</td><td class="num">${fmtInt(r.prize_lines)}</td><td class="num">${r.prize_total ? fmtMoney(Math.round(r.prize_total)) : "—"}</td><td class="num">${r.p_value == null ? "—" : num(r.p_value)}</td></tr>`).join("");
  return `<section class="card">
    <h2>Thử nghiệm chiến lược trên ${fmtInt(b.draws)} kỳ đã qua</h2>
    <div class="sub" style="margin-bottom:12px">Mỗi kỳ, mỗi chiến lược tạo ${b.strategies.random.lines / b.draws} dòng chỉ từ dữ liệu trước kỳ đó (${fmtDate(b.from)} → ${fmtDate(b.to)})</div>
    <div class="table-scroll"><table><thead><tr><th>Chiến lược</th><th class="num">Trùng TB</th><th class="num">Dòng trúng</th><th class="num">Tiền thưởng</th><th class="num">p</th></tr></thead>
    <tbody>${rows}<tr><td><em>Lý thuyết (ngẫu nhiên)</em></td><td class="num">${num(b.theory.mean_match, 3)}</td><td class="num">${num(b.theory.prize_lines, 1)}</td><td class="num">—</td><td class="num"></td></tr></tbody></table></div>
    <p class="sub" style="margin-top:10px">“Trùng TB” = số trùng trung bình mỗi dòng. p lớn (&gt; 0,05) nghĩa là chiến lược đó không khác gì chọn ngẫu nhiên. Cập nhật lúc ${fmtDateTime(b.run_at)}.</p>
  </section>`;
}

function oddsCard(g) {
  const rows = g.meta.odds.map((o) => `<tr><td>Giải ${o.division}</td><td>${esc(o.rule)}</td><td class="num">1 / ${fmtInt(Math.round(o.one_in))}</td></tr>`).join("");
  return `<section class="card">
    <h2>Xác suất trúng mỗi dòng</h2>
    <div class="table-scroll"><table><thead><tr><th>Hạng</th><th>Điều kiện</th><th class="num">Xác suất</th></tr></thead><tbody>${rows}
    <tr><td colspan="2"><strong>Trúng bất kỳ giải nào</strong></td><td class="num"><strong>1 / ${num(1 / g.meta.any_prize_p, 1)}</strong></td></tr></tbody></table></div>
  </section>`;
}

load();
