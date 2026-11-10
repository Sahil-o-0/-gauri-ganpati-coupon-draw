/* Gauri Ganpati Lucky Draw: all screens. Plain JavaScript, no build step. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const state = { me: null, config: { coupon_digits: 3, per_coupon: 21000, closed: false }, timer: null };

const STATUS = {
  active: "Coupons issued", no_coupon: "Below minimum", waiting_phone: "Waiting for phone",
  cancelled: "Cancelled", deleted: "Deleted in Tally", outside_campaign: "Outside campaign dates",
};

async function api(path, method = "GET", body) {
  const r = await fetch("api" + path, {
    method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401 && path !== "/login" && path !== "/setup") { state.me = null; boot(); throw new Error("Please log in."); }
  let data = null;
  try { data = await r.json(); } catch (e) { /* not json */ }
  if (!r.ok) {
    let d = data && data.detail;
    if (Array.isArray(d)) d = d.map((x) => x.msg).join("; ");
    throw new Error(d || "Something went wrong.");
  }
  return data;
}
function toast(msg, bad) {
  const t = $("#toast"); t.textContent = msg; t.className = "show" + (bad ? " bad" : "");
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.className = ""), bad ? 6000 : 2800);
}
const cp = (n) => String(n).padStart(state.config.coupon_digits, "0");
const inr = (x) => "₹" + Number(x || 0).toLocaleString("en-IN", { maximumFractionDigits: 2 });
const dmy = (d) => (d && /^\d{4}-\d{2}-\d{2}/.test(d) ? d.slice(8, 10) + "-" + d.slice(5, 7) + "-" + d.slice(0, 4) : d || "");
const mask = (p) => (p && p.length === 10 ? p.slice(0, 2) + "XXXXX" + p.slice(-3) : p || "");
const when = (t) => (t ? t.replace("T", " ") : "never");
const isAdmin = () => state.me && state.me.role === "admin";
const badge = (s) => `<span class="badge ${esc(s)}">${esc(STATUS[s] || s)}</span>`;
const cps = (list) => (list || []).map((c) => `<span class="cp ${c.status === "won" ? "won" : ""}">${cp(c.number)}</span>`).join("") || '<span class="muted">none</span>';
const view = (html) => { $("#view").innerHTML = html; window.scrollTo(0, 0); };
const guard = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } };

/* ------------------------------------------------------------------ boot / login */
async function boot() {
  const me = await fetch("api/me").then((r) => r.json());
  state.config = me.config; state.me = me.user;
  document.title = me.config.campaign_name;
  $("#brand-name").textContent = me.config.campaign_name;
  if (me.setup_needed) return loginView(true);
  if (!me.user) return loginView(false);
  $("#top").hidden = false;
  $("#who").textContent = `${me.user.username} (${me.user.role})`;
  buildNav();
  setBanner();
  route();
}
function loginView(setup) {
  $("#top").hidden = true; $("#banner").hidden = true;
  view(`<div class="card login"><h1>${setup ? "Set up the admin login" : "Log in"}</h1>
    <p class="muted">${setup ? "Create the first admin account. You can add staff logins later." : esc(state.config.campaign_name)}</p>
    <form id="lf"><div class="field"><label>Username</label><input name="u" autofocus required style="width:100%"></div>
    <div class="field"><label>Password</label><input name="p" type="password" required style="width:100%"><div class="hint">${setup ? "At least 6 characters." : ""}</div></div>
    <button class="primary" style="width:100%">${setup ? "Create admin and start" : "Log in"}</button></form></div>`);
  $("#lf").onsubmit = guard(async (e) => {
    e.preventDefault();
    await api(setup ? "/setup" : "/login", "POST", { username: e.target.u.value, password: e.target.p.value });
    boot();
  });
}
$("#logout").onclick = async () => { await api("/logout", "POST"); boot(); };
$("#search").onsubmit = (e) => { e.preventDefault(); const q = $("#search-box").value.trim(); if (q) location.hash = "#/search/" + encodeURIComponent(q); };

const NAV = [["dashboard", "Dashboard"], ["bills", "Bills"], ["waiting", "Waiting for phone"], ["customers", "Customers"],
  ["lookup", "Coupon lookup"], ["prizes", "Prizes"], ["draw", "Lucky draw", true], ["winners", "Winners"], ["tally", "Tally setup", true], ["settings", "Settings", true]];
function buildNav() {
  $("#nav").innerHTML = NAV.filter((n) => !n[2] || isAdmin()).map((n) => `<a href="#/${n[0]}" data-r="${n[0]}">${n[1]}</a>`).join("");
}
function setBanner() {
  const b = $("#banner");
  if (state.config.closed) { b.hidden = false; b.className = "warn"; b.textContent = "Campaign is closed: syncing is off and coupons are locked."; }
  else b.hidden = true;
}

/* ------------------------------------------------------------------ router */
function route() {
  clearInterval(state.timer);
  const [, name = "dashboard", arg] = (location.hash || "#/dashboard").split("/");
  $$("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.r === name || (name === "customer" && a.dataset.r === "customers")));
  const views = { dashboard, bills, waiting, customers, customer, lookup, prizes, draw, winners, settings, search, tally: tallySetup };
  (views[name] || dashboard)(arg ? decodeURIComponent(arg) : undefined).catch((e) => view(`<div class="msg bad">${esc(e.message)}</div>`));
}
window.addEventListener("hashchange", route);

/* ------------------------------------------------------------------ dashboard */
async function dashboard() {
  const draw = async () => {
    const d = await api("/dashboard");
    state.config.closed = d.campaign.closed; setBanner();
    const t = d.tally;
    const viaConn = t.source === "connector";
    const lightText = viaConn
      ? { green: "Tally connected (through the connector)", amber: t.connector_last_seen ? "Waiting for Tally" : "Waiting for the connector to start", red: "Connector offline: the Tally PC is not sending bills", closed: "Campaign closed" }[t.light]
      : { green: "Tally connected", amber: "Waiting for Tally", red: "Tally NOT reachable", closed: "Campaign closed" }[t.light];
    view(`<div class="row sp"><div><h1>${esc(d.campaign.name)}</h1>
      <div class="muted">${dmy(d.campaign.start)} to ${dmy(d.campaign.end)} &middot; 1 coupon for every ${inr(d.campaign.per_coupon)} on a bill</div></div></div>
      <div class="grid" style="margin:16px 0">
        <div class="stat"><div class="n">${d.bills_live}</div><div class="l">Bills read from Tally</div></div>
        <div class="stat"><div class="n">${d.customers}</div><div class="l">Customers</div></div>
        <div class="stat"><div class="n">${d.coupons_issued}</div><div class="l">Coupons issued (${d.coupons_today} today)</div></div>
        <a href="#/waiting" style="text-decoration:none;color:inherit"><div class="stat ${d.waiting_phone ? "warn" : ""}"><div class="n">${d.waiting_phone}</div><div class="l">Bills waiting for a phone number</div></div></a>
        <div class="stat"><div class="n">${inr(d.amount_rupees)}</div><div class="l">Total billed (with GST)</div></div>
        <div class="stat"><div class="n">${d.winners}${d.total_prizes ? " / " + d.total_prizes : ""}</div><div class="l">Winners drawn</div></div>
      </div>
      <div class="card"><h2><span class="light ${t.light}"></span>${lightText}</h2>
        <div>Last check: <b>${when(t.last.at)}</b> &mdash; ${esc(t.last.message || "")}</div>
        <div class="muted">Last full check: ${when(t.last_full_at)} &middot; Last successful: ${when(t.last_ok_at)}</div>
        ${t.light === "red" ? `<div class="msg bad">${viaConn && !t.connector_online ? "The connector on the Tally PC has not been heard from since " + when(t.connector_last_seen) + ". Check that the PC is on, connector.bat is running and the internet works." : "The last checks failed. Make sure Tally is open on the Tally PC with the company loaded and the server connection on."} Sales are not lost: they are picked up as soon as it is back.</div>` : ""}
        ${t.light === "amber" && viaConn && !t.connector_last_seen ? '<div class="msg warn">The connector has not connected yet. Open <a href="#/tally">Tally setup</a> for the steps.</div>' : ""}
        <div class="row" style="margin-top:10px"><button class="primary" id="sync" ${d.campaign.closed || t.running ? "disabled" : ""}>${t.running ? "Checking..." : "Sync now (last few days)"}</button>
        ${isAdmin() ? `<button id="full" ${d.campaign.closed || t.running ? "disabled" : ""}>Import all bills since ${dmy(d.campaign.start)} (billing start)</button>` : ""}</div>
        <div class="hint" style="margin-top:6px">Sync now picks up new and changed bills. The import reads every Sales bill from the billing start date to today, so older bills are brought in too, and bills deleted in Tally are found. It also runs by itself every hour.</div></div>
      <div class="card"><h2>Backup</h2><div>Last backup: <b>${when(d.backup.at)}</b></div><div class="muted">${esc(d.backup.note || "No backup yet. One is made every hour.")}</div></div>`);
    const syncWait = guard(async (mode, label) => {
      const before = d.tally.last.at;
      $("#sync").disabled = true; if ($("#full")) $("#full").disabled = true;
      await api("/sync", "POST", { mode });
      toast(label + ": asking Tally, this can take up to a minute...");
      for (let i = 0; i < 50; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        const d2 = await api("/dashboard");
        if (d2.tally.last.at !== before) { toast(d2.tally.last.message || "Done.", d2.tally.last.ok === false); break; }
      }
      draw();
    });
    $("#sync").onclick = () => syncWait("quick", "Sync");
    if (isAdmin()) $("#full").onclick = () => syncWait("full", "Import");
  };
  await draw();
  state.timer = setInterval(() => draw().catch(() => {}), 15000);
}

/* ------------------------------------------------------------------ bills */
const bills = async () => {
  const f = { status: "all", q: "", offset: 0 };
  const chips = [["all", "All"], ["active", "Coupons issued"], ["waiting_phone", "Waiting for phone"], ["no_coupon", "Below minimum"],
    ["cancelled", "Cancelled"], ["deleted", "Deleted"], ["outside_campaign", "Outside dates"]];
  view(`<h1>Bills</h1><p class="muted">Every Sales bill read from Tally, newest first.</p>
    <div class="row"><input id="bq" placeholder="Filter by bill no., name or phone" style="width:300px"></div>
    <div class="chips">${chips.map((c) => `<button class="chip ${c[0] === "all" ? "on" : ""}" data-s="${c[0]}">${c[1]}</button>`).join("")}</div>
    <div class="tablewrap"><table><thead><tr><th>Entry</th><th>Bill no.</th><th>Date</th><th>Customer</th><th class="num">Amount</th><th>Coupons</th><th>Status</th><th></th></tr></thead><tbody id="rows"></tbody></table></div>
    <div class="row" style="margin-top:12px"><button id="more" hidden>Show more</button><span class="muted" id="count"></span></div>`);
  const load = guard(async (reset) => {
    if (reset) { f.offset = 0; $("#rows").innerHTML = ""; }
    const r = await api(`/sales?status=${f.status}&q=${encodeURIComponent(f.q)}&limit=100&offset=${f.offset}`);
    $("#rows").insertAdjacentHTML("beforeend", r.items.map(billRow).join("") || (reset ? '<tr><td colspan="8" class="muted">No bills found.</td></tr>' : ""));
    f.offset += r.items.length;
    $("#more").hidden = f.offset >= r.total; $("#count").textContent = `Showing ${f.offset} of ${r.total}`;
    $$("#rows [data-slip]").forEach((b) => (b.onclick = guard(() => printSlip(b.dataset.slip))));
    $$("#rows [data-cust]").forEach((a) => (a.onclick = () => (location.hash = "#/customer/" + a.dataset.cust)));
  });
  $$(".chip").forEach((c) => (c.onclick = () => { $$(".chip").forEach((x) => x.classList.remove("on")); c.classList.add("on"); f.status = c.dataset.s; load(true); }));
  let tm; $("#bq").oninput = (e) => { clearTimeout(tm); tm = setTimeout(() => { f.q = e.target.value; load(true); }, 300); };
  $("#more").onclick = () => load(false);
  load(true);
};
function billRow(s) {
  const name = s.customer_name || s.raw_name || s.party_ledger || "";
  const cust = s.customer_id ? `<a href="javascript:void 0" data-cust="${s.customer_id}"><b>${esc(name)}</b></a>` : `<b>${esc(name)}</b>`;
  return `<tr><td>${String(s.id).padStart(4, "0")}</td><td>${esc(s.voucher_no)}</td><td>${dmy(s.voucher_date)}</td>
    <td>${cust}<div class="muted">${esc(s.customer_city || s.raw_city || "")} ${esc(s.phone || "")}</div></td>
    <td class="num">${inr(s.amount_paise / 100)}</td><td>${s.status === "active" ? cps(s.coupons) : '<span class="muted">-</span>'}</td>
    <td>${badge(s.status)}</td><td>${s.status === "active" ? `<button class="small" data-slip="${s.id}">Print slip</button>` : ""}</td></tr>`;
}

/* ------------------------------------------------------------------ waiting for phone */
async function waiting() {
  const r = await api("/sales?status=waiting_phone&limit=500");
  view(`<h1>Waiting for phone number</h1>
    <p class="muted">These bills are held and earn <b>no coupons</b> until a customer mobile number is entered. As soon as you save it, the coupons are issued.</p>
    ${state.config.closed ? '<div class="msg warn">The campaign is closed. An admin must reopen it before numbers can be entered.</div>' : ""}
    ${r.items.length ? `<div class="tablewrap"><table><thead><tr><th>Bill</th><th class="num">Amount</th><th>What Tally has</th><th>Customer phone</th><th>Name</th><th>City</th><th></th></tr></thead><tbody>
    ${r.items.map((s) => `<tr data-id="${s.id}"><td><b>${esc(s.voucher_no)}</b><div class="muted">${dmy(s.voucher_date)}</div></td><td class="num">${inr(s.amount_paise / 100)}<div class="muted">${Math.floor(s.amount_paise / 100 / state.config.per_coupon)} coupon(s)</div></td>
      <td style="max-width:260px">${esc(s.raw_name || s.party_ledger)}<div class="muted">${esc(s.raw_address)}</div></td>
      <td><input data-f="phone" inputmode="numeric" maxlength="14" placeholder="10-digit mobile" style="width:150px"></td>
      <td><input data-f="name" value="${esc(s.raw_name || s.party_ledger)}" style="width:170px"></td>
      <td><input data-f="city" value="${esc(s.raw_city)}" style="width:120px"></td>
      <td><button class="primary small" data-save>Save</button></td></tr>`).join("")}</tbody></table></div>`
      : '<div class="card msg ok">No bills are waiting. Every bill that earns coupons has a phone number.</div>'}`);
  $$("[data-save]").forEach((b) => (b.onclick = guard(async () => {
    const tr = b.closest("tr"); const g = (f) => $(`[data-f=${f}]`, tr).value;
    const res = await api(`/sales/${tr.dataset.id}/phone`, "POST", { phone: g("phone"), name: g("name"), city: g("city") });
    toast(`Saved. ${res.coupons} coupon(s) issued.`); waiting();
  })));
}

/* ------------------------------------------------------------------ customers */
async function customers() {
  view(`<h1>Customers</h1><div class="row"><input id="cq" placeholder="Search name, phone or city" style="width:320px" autofocus></div>
    <div class="tablewrap" style="margin-top:12px"><table><thead><tr><th>Name</th><th>Phone</th><th>City</th><th class="num">Bills</th><th class="num">Total spent</th><th class="num">Coupons</th><th></th></tr></thead><tbody id="rows"></tbody></table></div>
    <div class="muted" id="count" style="margin-top:8px"></div>`);
  const load = guard(async (q) => {
    const r = await api(`/customers?q=${encodeURIComponent(q)}&limit=300`);
    $("#rows").innerHTML = r.items.map((c) => `<tr class="click" data-id="${c.id}"><td><b>${esc(c.name || "(no name)")}</b></td><td>${esc(c.phone)}</td><td>${esc(c.city)}</td>
      <td class="num">${c.bills}</td><td class="num">${inr(c.spend_paise / 100)}</td><td class="num"><b>${c.coupons}</b></td><td>${c.won ? '<span class="badge won">Winner</span>' : ""}</td></tr>`).join("")
      || '<tr><td colspan="7" class="muted">No customers found.</td></tr>';
    $("#count").textContent = `Showing ${r.items.length} of ${r.total}`;
    $$("#rows tr[data-id]").forEach((tr) => (tr.onclick = () => (location.hash = "#/customer/" + tr.dataset.id)));
  });
  let tm; $("#cq").oninput = (e) => { clearTimeout(tm); tm = setTimeout(() => load(e.target.value), 250); };
  load("");
}
async function customer(id) {
  const r = await api("/customers/" + id); const c = r.customer;
  const total = r.sales.filter((s) => ["active", "no_coupon"].includes(s.status)).reduce((a, s) => a + s.amount_paise, 0) / 100;
  const live = r.coupons.filter((x) => x.status !== "void");
  view(`<p><a href="#/customers">&larr; All customers</a></p>
    <div class="card"><div class="row sp"><div><h1>${esc(c.name || "(no name)")}</h1><div class="muted">${esc(c.city)} &middot; ${esc(c.phone)}</div></div>
      <div style="text-align:right"><div class="stat" style="display:inline-block"><div class="n">${live.length}</div><div class="l">coupons &middot; ${inr(total)} spent</div></div></div></div>
      ${r.winners.filter((w) => w.status === "active").map((w) => `<div class="msg ok"><b>Winner!</b> Coupon ${cp(w.coupon_number)} won ${esc(w.prize_name || "a prize")}.</div>`).join("")}
      <h3>Edit details</h3><form id="ef" class="formgrid"><div><label>Name</label><input name="name" value="${esc(c.name)}"></div>
      <div><label>City</label><input name="city" value="${esc(c.city)}"></div><div><label>Phone</label><input name="phone" value="${esc(c.phone)}"></div>
      <div style="align-self:end"><button class="primary">Save details</button></div></form></div>
    <div class="card"><h2>Coupons</h2><div>${cps(live)}</div></div>
    <h2>Bills</h2><div class="tablewrap"><table><thead><tr><th>Entry</th><th>Bill no.</th><th>Date</th><th class="num">Amount</th><th>Coupons</th><th>Status</th><th></th></tr></thead><tbody>
    ${r.sales.map((s) => `<tr><td>${String(s.id).padStart(4, "0")}</td><td>${esc(s.voucher_no)}</td><td>${dmy(s.voucher_date)}</td><td class="num">${inr(s.amount_paise / 100)}</td>
      <td>${cps(s.coupons)}</td><td>${badge(s.status)}</td><td>${s.status === "active" ? `<button class="small" data-slip="${s.id}">Print slip</button>` : ""}</td></tr>`).join("")}</tbody></table></div>`);
  $("#ef").onsubmit = guard(async (e) => {
    e.preventDefault(); const f = e.target;
    await api("/customers/" + id, "PUT", { name: f.name.value, city: f.city.value, phone: f.phone.value });
    toast("Saved."); customer(id);
  });
  $$("[data-slip]").forEach((b) => (b.onclick = guard(() => printSlip(b.dataset.slip))));
}

/* ------------------------------------------------------------------ coupon lookup + search */
function couponCard(d) {
  const w = d.winner;
  return `<div class="card"><div class="row sp"><div><span class="cp" style="font-size:28px">${cp(d.number)}</span> ${badge(d.status === "valid" ? "active" : d.status)}</div></div>
    ${w ? `<div class="msg ok"><b>Winner</b> &mdash; ${esc(w.prize_name || "prize to be added")}, drawn ${when(w.drawn_at)}</div>` : ""}
    ${d.void_reason ? `<div class="muted">Void: ${esc(d.void_reason)}</div>` : ""}
    <dl class="kv" style="margin-top:12px"><dt>Customer</dt><dd><a href="#/customer/${d.customer_id}"><b>${esc(d.name)}</b></a></dd><dt>Phone</dt><dd>${esc(d.phone)}</dd><dt>City</dt><dd>${esc(d.city)}</dd>
    <dt>Bill</dt><dd>${esc(d.voucher_no)} &middot; ${dmy(d.voucher_date)} &middot; ${inr(d.amount_paise / 100)}</dd></dl></div>`;
}
async function lookup() {
  view(`<h1>Coupon lookup</h1><p class="muted">Type a coupon number to see who owns it.</p>
    <form id="lf" class="row"><input id="n" inputmode="numeric" placeholder="e.g. 102" style="font-size:22px;width:220px" autofocus><button class="primary">Find</button></form><div id="out" style="margin-top:16px"></div>`);
  $("#lf").onsubmit = guard(async (e) => {
    e.preventDefault(); const n = parseInt($("#n").value, 10);
    try { $("#out").innerHTML = couponCard(await api("/coupons/" + n)); } catch (err) { $("#out").innerHTML = `<div class="msg bad">${esc(err.message)}</div>`; }
  });
}
async function search(q) {
  const r = await api("/search?q=" + encodeURIComponent(q));
  view(`<h1>Search: ${esc(q)}</h1>${r.coupon ? "<h2>Coupon</h2>" + couponCard(r.coupon) : ""}
    <h2>Customers</h2>${r.customers.length ? `<div class="tablewrap"><table><tbody>${r.customers.map((c) => `<tr class="click" onclick="location.hash='#/customer/${c.id}'"><td><b>${esc(c.name)}</b></td><td>${esc(c.phone)}</td><td>${esc(c.city)}</td></tr>`).join("")}</tbody></table></div>` : '<p class="muted">No customers match.</p>'}
    <h2>Bills</h2>${r.sales.length ? `<div class="tablewrap"><table><tbody>${r.sales.map((s) => `<tr><td>${String(s.id).padStart(4, "0")}</td><td><b>${esc(s.voucher_no)}</b></td><td>${dmy(s.voucher_date)}</td><td class="num">${inr(s.amount_paise / 100)}</td><td>${badge(s.status)}</td></tr>`).join("")}</tbody></table></div>` : '<p class="muted">No bills match.</p>'}`);
}

/* ------------------------------------------------------------------ prizes */
async function prizes() {
  const r = await api("/prizes");
  view(`<h1>Prizes</h1><p class="muted">The draw has <b>${r.total_prizes || "no limit of"}</b> prizes in total. You can start the draw before every prize is named and add names later.</p>
    ${r.not_yet_named ? `<div class="msg warn">${r.not_yet_named} of ${r.total_prizes} prizes do not have a name yet. ${isAdmin() ? "Add them below whenever you have the list." : "Ask an admin to add them."}</div>` : ""}
    ${r.winners_without_prize ? `<div class="msg warn">${r.winners_without_prize} winner(s) have no prize assigned yet. Assign them on the Winners page.</div>` : ""}
    ${r.items.length ? `<div class="tablewrap"><table><thead><tr><th>Prize</th><th class="num">Quantity</th><th class="num">Drawn</th><th class="num">Left</th><th></th></tr></thead><tbody>
    ${r.items.map((p) => `<tr><td><b>${esc(p.name)}</b></td><td class="num">${p.quantity}</td><td class="num">${p.drawn}</td><td class="num">${p.remaining}</td>
      <td>${isAdmin() ? `<button class="small" data-edit="${p.id}" data-n="${esc(p.name)}" data-q="${p.quantity}">Edit</button> <button class="small danger" data-del="${p.id}">Delete</button>` : ""}</td></tr>`).join("")}</tbody></table></div>` : '<div class="card muted">No prizes added yet.</div>'}
    ${isAdmin() ? `<div class="card" style="margin-top:16px"><h2>Add a prize</h2><form id="pf" class="row"><input name="name" placeholder="Prize name, e.g. Gold coin" required style="width:300px">
      <input name="qty" type="number" min="1" value="1" style="width:90px" aria-label="Quantity"><button class="primary">Add</button></form></div>` : ""}`);
  if (!isAdmin()) return;
  $("#pf").onsubmit = guard(async (e) => { e.preventDefault(); await api("/prizes", "POST", { name: e.target.name.value, quantity: +e.target.qty.value }); prizes(); });
  $$("[data-del]").forEach((b) => (b.onclick = guard(async () => { if (confirm("Delete this prize?")) { await api("/prizes/" + b.dataset.del, "DELETE"); prizes(); } })));
  $$("[data-edit]").forEach((b) => (b.onclick = guard(async () => {
    const name = prompt("Prize name", b.dataset.n); if (name === null) return;
    const q = prompt("Quantity", b.dataset.q); if (q === null) return;
    await api("/prizes/" + b.dataset.edit, "PUT", { name, quantity: +q }); prizes();
  })));
}

/* ------------------------------------------------------------------ the draw */
let drawBusy = false;
async function draw(keepStage) {
  const [st, pr] = await Promise.all([api("/draw/status"), api("/prizes")]);
  const left = st.remaining;
  view(`<div class="row sp"><div><h1>Lucky draw</h1><div class="muted"><b>${st.winners_drawn}</b>${st.total_prizes ? " of " + st.total_prizes : ""} winners drawn &middot; ${st.eligible_customers} customers and ${st.eligible_coupons} coupons still in the draw</div></div>
      <button id="fs">Full screen</button></div>
    ${st.warnings.map((w) => `<div class="msg warn">${esc(w)}</div>`).join("")}
    <div class="card row"><label style="margin:0">Prize</label><select id="prize"><option value="">Next prize (name it later)</option>
      ${pr.items.map((p) => `<option value="${p.id}" ${p.remaining <= 0 ? "disabled" : ""}>${esc(p.name)} (${p.remaining} left)</option>`).join("")}</select>
      <button class="primary" data-n="1" ${left === 0 ? "disabled" : ""}>Draw 1 winner</button>
      <button data-n="5" ${left === 0 ? "disabled" : ""}>Draw 5</button><button data-n="10" ${left === 0 ? "disabled" : ""}>Draw 10</button>
      <label style="margin:0 0 0 8px"><input type="checkbox" id="anim" checked> Show the reveal</label></div>
    <div class="stage" id="stage"><div class="tag">${esc(state.config.campaign_name)}</div><div class="big">?</div><div class="sub">Press "Draw" to pick the next winner</div></div>
    <h2 style="margin-top:20px">Drawn so far</h2><div id="recent"></div>`);
  if (keepStage) $("#stage").innerHTML = keepStage;   // keep showing the winner after the page refreshes its numbers
  $("#fs").onclick = () => ($("#stage").requestFullscreen ? $("#stage").requestFullscreen() : null);
  const recent = async () => {
    const w = (await api("/winners")).filter((x) => x.status === "active").slice(-8).reverse();
    $("#recent").innerHTML = w.length ? `<div class="tablewrap"><table><tbody>${w.map((x) => `<tr><td>#${x.seq}</td><td><b>${cp(x.coupon_number)}</b></td><td>${esc(x.name)}</td><td>${esc(x.city)}</td><td>${esc(x.prize_name || "prize to be added")}</td></tr>`).join("")}</tbody></table></div>` : '<p class="muted">Nobody yet.</p>';
  };
  recent();
  $$("[data-n]").forEach((b) => (b.onclick = guard(async () => {
    if (drawBusy) return; drawBusy = true;
    try {
      const body = { count: +b.dataset.n, prize_id: $("#prize").value ? +$("#prize").value : null };
      const res = await api("/draw", "POST", body);  // saved at once; the reveal below is only for show
      await reveal(res.winners, $("#anim").checked);
      await draw($("#stage").innerHTML);
    } finally { drawBusy = false; }
  })));
}
async function reveal(winners, animate) {
  const stage = $("#stage"); const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const card = (w) => `<div class="tag">Winner #${w.seq} &middot; ${esc(w.prize_name || "prize to be added")}</div><div class="big">${cp(w.coupon_number)}</div>
    <div class="wname">${esc(w.name)}</div><div class="sub">${esc(w.city)} &middot; ${esc(mask(w.phone))}</div>`;
  const spin = async (ms) => {
    stage.classList.add("spin");
    const t = setInterval(() => ($(".big", stage).textContent = cp(Math.floor(Math.random() * 999) + 1)), 70);
    await sleep(ms); clearInterval(t); stage.classList.remove("spin");
  };
  if (winners.length === 1) {
    stage.innerHTML = `<div class="tag">Drawing...</div><div class="big">?</div>`;
    if (animate) await spin(2600);
    stage.innerHTML = card(winners[0]);
  } else {
    stage.innerHTML = `<div class="tag">Drawing ${winners.length} winners...</div><div class="big">?</div>`;
    if (animate) await spin(2600);
    stage.innerHTML = `<div class="tag">${winners.length} winners</div><div class="winnergrid">${winners.map((w) => `<div class="wcard"><div class="cn">${cp(w.coupon_number)}</div><b>${esc(w.name)}</b><div>${esc(w.city)} &middot; ${esc(mask(w.phone))}</div><div class="muted" style="color:#9bb3d4">${esc(w.prize_name || "prize to be added")}</div></div>`).join("")}</div>`;
  }
  await sleep(600);
}

/* ------------------------------------------------------------------ winners */
async function winners() {
  const [list, pr] = await Promise.all([api("/winners"), api("/prizes")]);
  const active = list.filter((w) => w.status === "active");
  view(`<div class="row sp"><div><h1>Winners</h1><div class="muted">${active.length} winner(s) &middot; one prize per customer</div></div>
      <div class="row"><button id="pr">Print list</button><a class="btn" href="api/export/winners.xlsx">Download Excel</a></div></div>
    <div class="tablewrap" style="margin-top:12px"><table><thead><tr><th>#</th><th>Coupon</th><th>Customer</th><th>City</th><th>Phone</th><th>Bill</th><th>Prize</th><th>Status</th><th></th></tr></thead><tbody>
    ${list.map((w) => `<tr><td>${w.seq}</td><td><span class="cp won">${cp(w.coupon_number)}</span></td><td><a href="#/customer/${w.customer_id}"><b>${esc(w.name)}</b></a></td><td>${esc(w.city)}</td><td>${esc(w.phone)}</td><td>${esc(w.voucher_no)}</td>
      <td>${isAdmin() && w.status === "active" ? `<select data-prize="${w.id}"><option value="">(to be added)</option>${pr.items.map((p) => `<option value="${p.id}" ${p.id === w.prize_id ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select>` : esc(w.prize_name || "(to be added)")}</td>
      <td>${w.status === "active" ? '<span class="badge active">Winner</span>' : `<span class="badge cancelled">Cancelled</span><div class="muted">${esc(w.cancel_reason)}</div>`}</td>
      <td>${isAdmin() && w.status === "active" ? `<button class="small danger" data-cancel="${w.id}">Cancel</button>` : ""}</td></tr>`).join("") || '<tr><td colspan="9" class="muted">No winners yet.</td></tr>'}</tbody></table></div>`);
  $$("[data-prize]").forEach((s) => (s.onchange = guard(async () => { await api(`/winners/${s.dataset.prize}/prize`, "PUT", { prize_id: s.value ? +s.value : null }); toast("Prize saved."); })));
  $$("[data-cancel]").forEach((b) => (b.onclick = guard(async () => {
    const reason = prompt("Why is this winner being cancelled? (required, it is recorded)"); if (!reason) return;
    const back = confirm("Put this customer back into the draw?\n\nOK = yes, back in the draw.\nCancel = no, keep them out.");
    await api(`/winners/${b.dataset.cancel}/cancel`, "POST", { reason, put_back: back }); toast("Winner cancelled. Draw a replacement from the Lucky draw page."); winners();
  })));
  $("#pr").onclick = () => {
    $("#print-area").innerHTML = `<div class="wlist"><h1 style="text-align:center">${esc(state.config.campaign_name)}: Winners</h1><table><thead><tr><th>#</th><th>Coupon</th><th>Name</th><th>City</th><th>Phone</th><th>Prize</th></tr></thead><tbody>
      ${active.map((w) => `<tr><td>${w.seq}</td><td>${cp(w.coupon_number)}</td><td>${esc(w.name)}</td><td>${esc(w.city)}</td><td>${esc(w.phone)}</td><td>${esc(w.prize_name || "")}</td></tr>`).join("")}</tbody></table></div>`;
    window.print();
  };
}

/* ------------------------------------------------------------------ slips */
async function printSlip(id) {
  const s = await api(`/sales/${id}/slip`);
  state.config.coupon_digits = s.digits;
  $("#print-area").innerHTML = `<div class="slip"><h1>${esc(s.campaign)}</h1><div class="sub">Lucky draw coupon slip</div>
    <table><tr><td>Customer</td><td><b>${esc(s.name)}</b></td></tr><tr><td>Phone</td><td>${esc(s.phone)}</td></tr><tr><td>City</td><td>${esc(s.city)}</td></tr>
    <tr><td>Bill no.</td><td>${esc(s.bill_no)} &nbsp; (${dmy(s.date)})</td></tr><tr><td>Bill amount</td><td>${inr(s.amount)}</td></tr><tr><td>Entry no.</td><td>${String(s.entry_no).padStart(4, "0")}</td></tr></table>
    <div class="cps">${s.coupons.map((n) => `<span>${cp(n)}</span>`).join("")}</div>
    <div class="foot">${s.coupons.length} coupon(s). Please keep this slip safe.</div></div>`;
  window.print();
}

/* ------------------------------------------------------------------ settings */
async function settings() {
  const [s, users, log, aud, dash] = await Promise.all([api("/settings"), api("/users"), api("/sync/log"), api("/audit"), api("/dashboard")]);
  const f = (k, label, hint = "", type = "text") => `<div><label>${label}</label><input name="${k}" type="${type}" value="${esc(s[k])}">${hint ? `<div class="hint">${hint}</div>` : ""}</div>`;
  view(`<h1>Settings</h1>
    <div class="card"><h2>Campaign and coupons</h2><form id="sf"><div class="formgrid">
      ${f("campaign_name", "Campaign name")}${f("campaign_start", "First bill date", "Bills before this date do not count", "date")}${f("campaign_end", "Last bill date", "Bills after this date do not count", "date")}
      ${f("amount_per_coupon", "Rupees per coupon", "One coupon for every full amount on a bill")}${f("total_prizes", "Total prizes", "0 means no limit")}${f("coupon_digits", "Coupon number digits", "3 gives 001, 002...")}
      </div><h2 style="margin-top:20px">Tally connection</h2><div class="formgrid">
      <div><label>How this software reads Tally</label><select name="tally_source"><option value="connector" ${s.tally_source === "connector" ? "selected" : ""}>Through the connector on the Tally PC (website on a server)</option><option value="direct" ${s.tally_source === "direct" ? "selected" : ""}>Directly (this software runs on the Tally PC)</option></select><div class="hint">See Tally setup for the steps</div></div>
      ${s.tally_source === "direct" ? f("tally_host", "Tally PC address", "localhost if this software is on the Tally PC") + f("tally_port", "Tally port", "Usually 9000") : ""}${f("tally_company", "Company name", "Leave empty to use the company open in Tally")}
      ${f("voucher_types", "Voucher types that count", "Comma separated. Normally just: Sales")}${f("extra_scan_tags", "Extra Tally fields to search for the phone", "Optional, comma separated tag names")}
      ${f("sync_interval_seconds", "Check Tally every (seconds)")}${f("quick_window_days", "Quick check looks back (days)")}${f("full_check_minutes", "Full check every (minutes)")}
      <div><label>Syncing</label><select name="sync_enabled"><option value="1" ${s.sync_enabled === "1" ? "selected" : ""}>On</option><option value="0" ${s.sync_enabled === "0" ? "selected" : ""}>Off</option></select></div>
      </div><h2 style="margin-top:20px">Backup</h2><div class="formgrid">${f("backup_dir", "Backup folder", "A copy is made here and replaced every time")}${f("backup_interval_minutes", "Backup every (minutes)")}</div>
      <div class="row" style="margin-top:16px"><button class="primary">Save settings</button>${s.tally_source === "direct" ? '<button type="button" id="test">Test Tally connection</button>' : '<a class="btn" href="#/tally">Open Tally setup</a>'}<button type="button" id="bk">Back up now</button></div></form>
      <div id="testout"></div></div>
    <div class="card"><h2>Campaign</h2><p class="muted">Close the campaign just before the draw: it does a last check with Tally, then stops syncing and locks the coupons. After the draw, download everything to Excel.</p>
      <div class="row">${dash.campaign.closed ? '<button id="reopen">Reopen campaign</button>' : '<button class="danger" id="close">Close campaign (final check)</button>'}
      <a class="btn" href="api/export/all.xlsx">Download everything to Excel</a></div></div>
    <div class="card"><h2>Logins</h2><div class="tablewrap"><table><thead><tr><th>Username</th><th>Role</th><th>Active</th><th></th></tr></thead><tbody>
      ${users.map((u) => `<tr><td><b>${esc(u.username)}</b></td><td>${u.role}</td><td>${u.active ? "Yes" : "No"}</td><td><button class="small" data-pw="${u.id}">Set password</button> <button class="small" data-role="${u.id}" data-r="${u.role}">Make ${u.role === "admin" ? "staff" : "admin"}</button> <button class="small ${u.active ? "danger" : ""}" data-act="${u.id}" data-a="${u.active}">${u.active ? "Disable" : "Enable"}</button></td></tr>`).join("")}</tbody></table></div>
      <form id="uf" class="row" style="margin-top:12px"><input name="u" placeholder="New username" required><input name="p" type="password" placeholder="Password (6+ characters)" required>
      <select name="r"><option value="staff">Staff</option><option value="admin">Admin</option></select><button class="primary">Add login</button></form>
      <p class="hint">Admin: settings, draw, prizes. Staff: view, search, enter phone numbers and print slips.</p></div>
    <div class="card"><h2>Check history with Tally</h2><div class="tablewrap" style="max-height:300px"><table><thead><tr><th>Time</th><th>Kind</th><th>Result</th></tr></thead><tbody>
      ${log.map((l) => `<tr><td>${when(l.at)}</td><td>${esc(l.kind)}</td><td>${l.ok ? "OK" : '<b style="color:var(--bad)">Failed</b>'} &mdash; ${esc(l.message)}</td></tr>`).join("") || '<tr><td colspan="3" class="muted">No checks yet.</td></tr>'}</tbody></table></div></div>
    <div class="card"><h2>Activity log</h2><div class="tablewrap" style="max-height:300px"><table><thead><tr><th>Time</th><th>Who</th><th>What</th></tr></thead><tbody>
      ${aud.map((a) => `<tr><td>${when(a.at)}</td><td>${esc(a.user)}</td><td><b>${esc(a.action)}</b> ${esc(a.detail)}</td></tr>`).join("")}</tbody></table></div></div>`);
  $("#sf").onsubmit = guard(async (e) => {
    e.preventDefault(); const body = {};
    $$("input[name],select[name]", e.target).forEach((i) => (body[i.name] = i.value));
    const r = await api("/settings", "PUT", body); toast(r.bills_rechecked ? `Saved. ${r.bills_rechecked} bills re-checked.` : "Saved.");
    if (body.tally_source !== s.tally_source) return settings();
    const me = await fetch("api/me").then((x) => x.json()); state.config = me.config;
  });
  if ($("#test")) $("#test").onclick = guard(async () => {
    $("#testout").innerHTML = '<p class="muted">Testing...</p>';
    try {
      const r = await api("/tally/test", "POST");
      $("#testout").innerHTML = `<div class="msg ok">Connected. ${r.vouchers} vouchers in the last 30 days, ${r.sales} Sales bills, ${r.with_phone} with a phone number. Voucher types seen: ${esc(r.types.join(", ") || "none")}.</div>
        ${r.sample.length ? `<div class="tablewrap"><table><thead><tr><th>Bill</th><th>Date</th><th class="num">Amount</th><th>Name</th><th>Phone found</th><th>City found</th></tr></thead><tbody>${r.sample.map((x) => `<tr><td>${esc(x.no)}</td><td>${dmy(x.date)}</td><td class="num">${inr(x.amount)}</td><td>${esc(x.name)}</td><td>${esc(x.phone || "-")}</td><td>${esc(x.city || "-")}</td></tr>`).join("")}</tbody></table></div>` : ""}`;
    } catch (err) { $("#testout").innerHTML = `<div class="msg bad">${esc(err.message)}</div>`; }
  });
  $("#bk").onclick = guard(async () => { const r = await api("/backup/run", "POST"); toast(r.ok ? "Backup done." : r.note, !r.ok); });
  const cl = $("#close"); if (cl) cl.onclick = guard(async () => {
    if (!confirm("Close the campaign?\n\nThis does a final check with Tally, then stops syncing and locks all coupons.")) return;
    try { await api("/campaign/close", "POST", { force: false }); }
    catch (err) { if (!confirm(err.message + "\n\nClose anyway?")) return; await api("/campaign/close", "POST", { force: true }); }
    toast("Campaign closed."); boot();
  });
  const ro = $("#reopen"); if (ro) ro.onclick = guard(async () => { if (confirm("Reopen the campaign and start syncing again?")) { await api("/campaign/reopen", "POST"); toast("Reopened."); boot(); } });
  $("#uf").onsubmit = guard(async (e) => { e.preventDefault(); await api("/users", "POST", { username: e.target.u.value, password: e.target.p.value, role: e.target.r.value }); toast("Login added."); settings(); });
  $$("[data-pw]").forEach((b) => (b.onclick = guard(async () => { const p = prompt("New password (6+ characters)"); if (!p) return; await api("/users/" + b.dataset.pw, "PUT", { password: p }); toast("Password changed."); })));
  $$("[data-role]").forEach((b) => (b.onclick = guard(async () => { await api("/users/" + b.dataset.role, "PUT", { role: b.dataset.r === "admin" ? "staff" : "admin" }); settings(); })));
  $$("[data-act]").forEach((b) => (b.onclick = guard(async () => { await api("/users/" + b.dataset.act, "PUT", { active: b.dataset.a !== "1" }); settings(); })));
}

/* ------------------------------------------------------------------ tally setup (connector) */
function probeTable(p) {
  if (!p) return '<p class="muted">Nothing checked yet. Press <b>Check Tally now</b>.</p>';
  if (!p.ok) return `<div class="msg bad">${esc(p.message)}<div class="hint">${when(p.at)}</div></div>`;
  return `<div class="msg ${p.with_phone ? "ok" : "warn"}">${esc(p.message)} <span class="hint">(${when(p.at)}, ${(p.bytes / 1024).toFixed(0)} KB from Tally)</span></div>
    <p class="muted">Voucher types seen: ${esc((p.types || []).join(", ") || "none")}</p>
    ${p.sample && p.sample.length ? `<div class="tablewrap"><table><thead><tr><th>Bill</th><th>Date</th><th class="num">Amount</th><th>Name</th><th>Phone found</th><th>City found</th><th>Address as read</th></tr></thead><tbody>${p.sample.map((x) => `<tr><td>${esc(x.no)}</td><td>${dmy(x.date)}</td><td class="num">${inr(x.amount)}</td><td>${esc(x.name)}</td><td>${esc(x.phone || "-")}</td><td>${esc(x.city || "-")}</td><td>${esc(x.address || "-")}</td></tr>`).join("")}</tbody></table></div>` : ""}`;
}
async function tallySetup() {
  const draw = async (keep) => {
    const y = window.scrollY;
    const c = await api("/connector/status");
    if (c.source !== "connector") {
      view(`<h1>Tally setup</h1><div class="card"><p>This software is set to read Tally <b>directly</b>, so it must run on the Tally PC. Use <a href="#/settings">Settings</a> to test the connection, or switch to the connector there.</p></div>`);
      return;
    }
    view(`<h1>Tally setup</h1><p class="muted">Three steps on the Tally PC. You can do them from this page, then watch the status here.</p>
      <div class="card"><h2><span class="light ${c.online ? "green" : c.last_seen ? "red" : "amber"}"></span>${c.online ? "Connector is running" : c.last_seen ? "Connector is offline" : "Connector has not connected yet"}</h2>
        <div class="muted">${c.last_seen ? `Last heard from: <b>${when(c.last_seen)}</b>${c.info && c.info.computer ? ` &middot; PC: <b>${esc(c.info.computer)}</b>` : ""}` : "Nothing has connected yet."}</div></div>
      <div class="card"><h2>Step 1. Turn on Tally's server (on the Tally PC)</h2><ol>
        <li>Open TallyPrime and load the billing company. Keep it open.</li>
        <li>Press <b>F1</b> (Help) &gt; <b>Settings</b> &gt; <b>Connectivity</b> &gt; <b>Client/Server configuration</b>.</li>
        <li>Set <b>TallyPrime acts as</b> to <b>Both</b>, set <b>Enable ODBC</b> to <b>Yes</b>, port <b>9000</b>.</li>
        <li>Say Yes to restart Tally, then load the company again.</li></ol>
        <div class="hint">Menu names can differ a little between Tally versions.</div></div>
      <div class="card"><h2>Step 2. Install the connector (on the Tally PC)</h2>
        <ol><li>Open this website on the Tally PC and log in as admin. Press the button below. Open the zip and use <b>Extract all</b>.</li>
        <li>Double-click <b>connector.bat</b>. A black window stays open. That is normal. Nothing needs to be installed.</li>
        <li>Optional: double-click <b>install_autostart.bat</b> so it starts by itself at login.</li></ol>
        <div class="row"><a class="btn primary" href="api/connector/download">Download the connector</a>
        <button id="newkey" class="small danger">Make a new key</button></div>
        <div class="hint">The zip holds a secret key (ends in ${esc(c.key_tail)}). Do not share it. Making a new key stops the old connector until the new zip is installed.</div></div>
      <div class="card"><h2>Step 3. Check that Tally is read correctly</h2>
        <p class="muted">The connector asks Tally for the last 30 days and shows what the software could read: phone, name and city. If the phone numbers look wrong, download the file and send it to the developer.</p>
        <div class="row"><button class="primary" id="probe" ${c.probe_pending || !c.online ? "disabled" : ""}>${c.probe_pending ? "Asking Tally..." : "Check Tally now"}</button>
        ${c.has_probe_file ? '<a class="btn" href="api/connector/probe.xml">Download Tally\'s reply (XML)</a>' : ""}</div>
        ${!c.online ? '<div class="hint">Start the connector first (step 2).</div>' : ""}
        <div id="probeout" style="margin-top:12px">${probeTable(c.probe)}</div></div>`);
    if (keep) window.scrollTo(0, y);
    $("#probe").onclick = guard(async () => { await api("/connector/probe", "POST"); toast("Asking Tally. This takes up to 15 seconds."); draw(true); });
    $("#newkey").onclick = guard(async () => {
      if (!confirm("Make a new key?\n\nThe connector on the Tally PC stops working until you download and install the new one.")) return;
      await api("/connector/regenerate-key", "POST"); toast("New key made. Download the connector again."); draw();
    });
  };
  await draw();
  state.timer = setInterval(() => draw(true).catch(() => {}), 6000);
}

boot();
