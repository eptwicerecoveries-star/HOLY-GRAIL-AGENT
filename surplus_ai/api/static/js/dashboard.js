import { PAGE_SIZE, apiGet, apiPost, listUrl } from "./api.js";

const main = document.getElementById("main");
const pageTitle = document.getElementById("page-title");
const pageEyebrow = document.getElementById("page-eyebrow");
const statusChip = document.getElementById("status-chip");
const userChip = document.getElementById("user-chip");
const logoutButton = document.getElementById("logout-button");
const navLinks = [...document.querySelectorAll("[data-nav]")];

const UUID =
  "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function display(value, fallback = "—") {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  return String(value);
}

function shortId(id) {
  const text = String(id || "");
  return text.length > 12 ? `${text.slice(0, 8)}…` : text;
}

function formatWhen(value) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return date.toISOString().slice(0, 10);
}

function parseRoute() {
  const raw = window.location.hash.replace(/^#/, "") || "/";
  const url = new URL(raw, "http://dashboard.local");
  const path = url.pathname.replace(/\/+$/, "") || "/";
  const offset = Math.max(0, Number.parseInt(url.searchParams.get("offset") || "0", 10) || 0);
  const matchers = [
    [new RegExp(`^/cases/(${UUID})$`), "case-detail"],
    [new RegExp(`^/leads/(${UUID})$`), "lead-detail"],
    [new RegExp(`^/reviews/(${UUID})$`), "review-detail"],
    [new RegExp(`^/contacts/(${UUID})$`), "contact-detail"],
  ];
  for (const [pattern, view] of matchers) {
    const match = path.match(pattern);
    if (match) {
      return { view, id: match[1], offset: 0 };
    }
  }
  if (path === "/cases") {
    return { view: "cases", id: null, offset };
  }
  if (path === "/leads") {
    return { view: "leads", id: null, offset };
  }
  if (path === "/reviews") {
    return { view: "reviews", id: null, offset };
  }
  if (path === "/contacts") {
    return { view: "contacts", id: null, offset };
  }
  return { view: "home", id: null, offset: 0 };
}

function setNav(view) {
  const map = {
    home: "home",
    cases: "cases",
    "case-detail": "cases",
    leads: "leads",
    "lead-detail": "leads",
    reviews: "reviews",
    "review-detail": "reviews",
    contacts: "contacts",
    "contact-detail": "contacts",
  };
  const key = map[view] || "home";
  navLinks.forEach((link) => {
    link.classList.toggle("is-active", link.dataset.nav === key);
  });
}

function banner(kind, text) {
  return `<div class="banner is-${kind}" role="status">${escapeHtml(text)}</div>`;
}

function pager(hashBase, offset, rowCount) {
  const prevOffset = Math.max(0, offset - PAGE_SIZE);
  const nextOffset = offset + PAGE_SIZE;
  const prevDisabled = offset === 0 ? "disabled" : "";
  const nextDisabled = rowCount < PAGE_SIZE ? "disabled" : "";
  return `
    <div class="pager">
      <button class="pager-btn" type="button" data-go="${hashBase}?offset=${prevOffset}" ${prevDisabled}>Previous</button>
      <button class="pager-btn" type="button" data-go="${hashBase}?offset=${nextOffset}" ${nextDisabled}>Next</button>
    </div>
  `;
}

async function refreshStatusChip() {
  try {
    const status = await apiGet("/api/v1/status");
    const ok = Boolean(status.database_reachable);
    statusChip.textContent = ok ? "Database reachable" : "Database unavailable";
    statusChip.classList.toggle("is-ok", ok);
    statusChip.classList.toggle("is-bad", !ok);
  } catch {
    statusChip.textContent = "API unavailable";
    statusChip.classList.remove("is-ok");
    statusChip.classList.add("is-bad");
  }
}

async function refreshUserChip() {
  try {
    const me = await apiGet("/api/v1/auth/me");
    userChip.hidden = false;
    userChip.textContent = `${me.name} · ${me.role}`;
  } catch {
    userChip.hidden = true;
    userChip.textContent = "";
  }
}

async function handleLogout() {
  try {
    await apiPost("/api/v1/auth/logout");
  } catch {
    // Still leave the dashboard; server clears cookie when possible.
  }
  window.location.assign("/login");
}

logoutButton.addEventListener("click", () => {
  void handleLogout();
});

async function renderHome() {
  pageEyebrow.textContent = "Operator console";
  pageTitle.textContent = "Overview";
  main.innerHTML = banner("loading", "Loading overview…");
  try {
    const [status, cases, leads, reviews, contacts] = await Promise.all([
      apiGet("/api/v1/status"),
      apiGet(listUrl("/api/v1/cases", 0)),
      apiGet(listUrl("/api/v1/leads", 0)),
      apiGet(listUrl("/api/v1/research/reviews", 0)),
      apiGet(listUrl("/api/v1/contacts", 0)),
    ]);
    const dbOk = Boolean(status.database_reachable);
    const migrated = Boolean(status.migrations_up_to_date);
    main.innerHTML = `
      <section class="panel" aria-labelledby="status-heading">
        <h3 id="status-heading">System status</h3>
        <div class="status-grid">
          <div class="kv"><dt>Application</dt><dd>${escapeHtml(status.application)} ${escapeHtml(status.version)}</dd></div>
          <div class="kv"><dt>Environment</dt><dd>${escapeHtml(status.environment)}</dd></div>
          <div class="kv"><dt>Database</dt><dd>${dbOk ? "Reachable" : "Unavailable"}</dd></div>
          <div class="kv"><dt>Migrations</dt><dd>${migrated ? "Up to date" : "Not current / unknown"}</dd></div>
          <div class="kv"><dt>Access</dt><dd>Local only · unauthenticated</dd></div>
        </div>
        <p class="muted">${escapeHtml(status.warning || "UNAUTHENTICATED P1 API MUST NOT BE INTERNET-FACING.")}</p>
      </section>
      <section class="grid-cards" aria-label="This page snapshot">
        <article class="card"><h3>Cases</h3><p>${cases.length}</p><p class="hint">Loaded on this page (not a database total)</p></article>
        <article class="card"><h3>Leads</h3><p>${leads.length}</p><p class="hint">Loaded on this page (not a database total)</p></article>
        <article class="card"><h3>Research reviews</h3><p>${reviews.length}</p><p class="hint">Loaded on this page (not a database total)</p></article>
        <article class="card"><h3>Contacts</h3><p>${contacts.length}</p><p class="hint">Metadata only · values omitted</p></article>
      </section>
    `;
  } catch (error) {
    main.innerHTML = banner("error", error.message || "Could not load overview.");
  }
}

function table(headers, rowsHtml, emptyText) {
  if (!rowsHtml) {
    return banner("empty", emptyText);
  }
  return `
    <div class="table-wrap">
      <table>
        <thead><tr>${headers.map((h) => `<th scope="col">${escapeHtml(h)}</th>`).join("")}</tr></thead>
        <tbody>${rowsHtml}</tbody>
      </table>
    </div>
  `;
}

async function renderList({ title, path, hashBase, empty, headers, row }) {
  const route = parseRoute();
  pageEyebrow.textContent = "Records";
  pageTitle.textContent = title;
  main.innerHTML = banner("loading", `Loading ${title.toLowerCase()}…`);
  try {
    const rows = await apiGet(listUrl(path, route.offset));
    const body = rows.map(row).join("");
    main.innerHTML = `
      <div class="toolbar">
        <p class="muted">Showing ${rows.length} row${rows.length === 1 ? "" : "s"} on this page (limit ${PAGE_SIZE}, offset ${route.offset}). Not a database total.</p>
      </div>
      ${table(headers, body, empty)}
      ${pager(hashBase, route.offset, rows.length)}
    `;
  } catch (error) {
    main.innerHTML = banner("error", error.message || `Could not load ${title.toLowerCase()}.`);
  }
}

function renderCases() {
  return renderList({
    title: "Cases",
    path: "/api/v1/cases",
    hashBase: "#/cases",
    empty: "No cases available.",
    headers: ["Case", "County", "State", "Parcel", "Surplus amount", "Status", "Sale date", "Has lead"],
    row: (item) => `
      <tr>
        <td><button class="row-button" type="button" data-go="#/cases/${escapeHtml(item.id)}">${escapeHtml(shortId(item.id))}</button></td>
        <td>${escapeHtml(display(item.county_name))}</td>
        <td>${escapeHtml(display(item.county_state))}</td>
        <td>${escapeHtml(display(item.parcel_id))}</td>
        <td>${escapeHtml(display(item.surplus_amount))}</td>
        <td>${escapeHtml(display(item.status))}</td>
        <td>${escapeHtml(display(item.sale_date))}</td>
        <td>${item.has_lead ? "Yes" : "No"}</td>
      </tr>
    `,
  });
}

function renderLeads() {
  return renderList({
    title: "Leads",
    path: "/api/v1/leads",
    hashBase: "#/leads",
    empty: "No leads available.",
    headers: ["Lead", "Case", "Status", "Score", "Contacts", "Qualified", "Created"],
    row: (item) => `
      <tr>
        <td><button class="row-button" type="button" data-go="#/leads/${escapeHtml(item.id)}">${escapeHtml(shortId(item.id))}</button></td>
        <td>${escapeHtml(shortId(item.surplus_case_id))}</td>
        <td>${escapeHtml(display(item.status))}</td>
        <td>${escapeHtml(display(item.score))}</td>
        <td>${escapeHtml(display(item.contact_count))}</td>
        <td>${escapeHtml(formatWhen(item.qualified_at))}</td>
        <td>${escapeHtml(formatWhen(item.created_at))}</td>
      </tr>
    `,
  });
}

function renderReviews() {
  return renderList({
    title: "Research reviews",
    path: "/api/v1/research/reviews",
    hashBase: "#/reviews",
    empty: "No research reviews available.",
    headers: ["Review", "Case", "Provider", "Reason", "Status", "Resolution", "Created", "Reviewed"],
    row: (item) => `
      <tr>
        <td><button class="row-button" type="button" data-go="#/reviews/${escapeHtml(item.id)}">${escapeHtml(shortId(item.id))}</button></td>
        <td>${escapeHtml(shortId(item.surplus_case_id))}</td>
        <td>${escapeHtml(display(item.provider))}</td>
        <td>${escapeHtml(display(item.reason))}</td>
        <td>${escapeHtml(display(item.status))}</td>
        <td>${escapeHtml(display(item.resolution))}</td>
        <td>${escapeHtml(formatWhen(item.created_at))}</td>
        <td>${escapeHtml(formatWhen(item.reviewed_at))}</td>
      </tr>
    `,
  });
}

function renderContacts() {
  return renderList({
    title: "Contacts",
    path: "/api/v1/contacts",
    hashBase: "#/contacts",
    empty: "No contacts available.",
    headers: ["Contact", "Lead", "Type", "Source", "Confidence", "Verified", "Created"],
    row: (item) => `
      <tr>
        <td><button class="row-button" type="button" data-go="#/contacts/${escapeHtml(item.id)}">${escapeHtml(shortId(item.id))}</button></td>
        <td>${escapeHtml(shortId(item.lead_id))}</td>
        <td>${escapeHtml(display(item.contact_type))}</td>
        <td>${escapeHtml(display(item.source))}</td>
        <td>${escapeHtml(display(item.confidence))}</td>
        <td>${item.is_verified ? "Yes" : "No"}</td>
        <td>${escapeHtml(formatWhen(item.created_at))}</td>
      </tr>
    `,
  });
}

function detailMarkup(backHref, backLabel, fields) {
  const rows = fields
    .map(
      ([label, value]) =>
        `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(display(value))}</dd>`
    )
    .join("");
  return `
    <a class="back-link" href="${escapeHtml(backHref)}">${escapeHtml(backLabel)}</a>
    <section class="panel">
      <dl class="detail-list">${rows}</dl>
    </section>
  `;
}

async function renderDetail({ title, path, backHref, backLabel, fields }) {
  pageEyebrow.textContent = "Record detail";
  pageTitle.textContent = title;
  main.innerHTML = banner("loading", "Loading record…");
  try {
    const item = await apiGet(path);
    main.innerHTML = detailMarkup(backHref, backLabel, fields(item));
  } catch (error) {
    main.innerHTML = `
      <a class="back-link" href="${escapeHtml(backHref)}">${escapeHtml(backLabel)}</a>
      ${banner("error", error.message || "Could not load record.")}
    `;
  }
}

function renderCaseDetail(id) {
  return renderDetail({
    title: "Case",
    path: `/api/v1/cases/${id}`,
    backHref: "#/cases",
    backLabel: "Back to cases",
    fields: (item) => [
      ["Case ID", item.id],
      ["County", item.county_name],
      ["State", item.county_state],
      ["County slug", item.county_slug],
      ["Parcel", item.parcel_id],
      ["Case number", item.case_number],
      ["Surplus amount", item.surplus_amount],
      ["Surplus explicit", item.surplus_is_explicit ? "Yes" : "No"],
      ["Status", item.status],
      ["Sale date", item.sale_date],
      ["Has lead", item.has_lead ? "Yes" : "No"],
      ["Created", item.created_at],
      ["Updated", item.updated_at],
    ],
  });
}

function renderLeadDetail(id) {
  return renderDetail({
    title: "Lead",
    path: `/api/v1/leads/${id}`,
    backHref: "#/leads",
    backLabel: "Back to leads",
    fields: (item) => [
      ["Lead ID", item.id],
      ["Case ID", item.surplus_case_id],
      ["Status", item.status],
      ["Score", item.score],
      ["Assigned user", item.assigned_user_id],
      ["Contact count", item.contact_count],
      ["Qualified", item.qualified_at],
      ["Created", item.created_at],
      ["Updated", item.updated_at],
    ],
  });
}

function renderReviewDetail(id) {
  return renderDetail({
    title: "Research review",
    path: `/api/v1/research/reviews/${id}`,
    backHref: "#/reviews",
    backLabel: "Back to reviews",
    fields: (item) => [
      ["Review ID", item.id],
      ["Case ID", item.surplus_case_id],
      ["Research result ID", item.research_result_id],
      ["Provider", item.provider],
      ["Reason", item.reason],
      ["Reason detail", item.reason_detail],
      ["Status", item.status],
      ["Resolution", item.resolution],
      ["Reviewed by", item.reviewed_by],
      ["Reviewed at", item.reviewed_at],
      ["Created", item.created_at],
      ["Updated", item.updated_at],
    ],
  });
}

function renderContactDetail(id) {
  return renderDetail({
    title: "Contact metadata",
    path: `/api/v1/contacts/${id}`,
    backHref: "#/contacts",
    backLabel: "Back to contacts",
    fields: (item) => [
      ["Contact ID", item.id],
      ["Lead ID", item.lead_id],
      ["Type", item.contact_type],
      ["Source", item.source],
      ["Confidence", item.confidence],
      ["Verified", item.is_verified ? "Yes" : "No"],
      ["Created", item.created_at],
    ],
  });
}

async function render() {
  const route = parseRoute();
  setNav(route.view);
  const views = {
    home: renderHome,
    cases: renderCases,
    leads: renderLeads,
    reviews: renderReviews,
    contacts: renderContacts,
    "case-detail": () => renderCaseDetail(route.id),
    "lead-detail": () => renderLeadDetail(route.id),
    "review-detail": () => renderReviewDetail(route.id),
    "contact-detail": () => renderContactDetail(route.id),
  };
  await (views[route.view] || renderHome)();
}

main.addEventListener("click", (event) => {
  const target = event.target.closest("[data-go]");
  if (!target || target.disabled) {
    return;
  }
  window.location.hash = target.dataset.go.replace(/^#/, "#");
});

window.addEventListener("hashchange", () => {
  void render();
});

void refreshUserChip();
void refreshStatusChip();
void render();
