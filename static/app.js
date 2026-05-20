const notice = document.querySelector("#notice");
const draftsEl = document.querySelector("#drafts");
const draftCount = document.querySelector("#draft-count");
const resumeLabel = document.querySelector("#resume-label");

function setNotice(message, isError = false) {
  if (!notice) return;
  notice.textContent = message;
  notice.classList.toggle("error", isError);
}

async function postForm(url, formData = new FormData()) {
  const response = await fetch(url, { method: "POST", body: formData });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || "Request failed.");
  }
  return data;
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderDraft(draft) {
  const disabled = !draft.to || draft.status === "sent" ? "disabled" : "";
  const statusText = String(draft.status || "").replaceAll("_", " ");
  return `
    <article class="draft" data-index="${draft.index}" data-status="${escapeHtml(draft.status)}">
      <div class="draft-meta">
        <div>
          <h3>${escapeHtml(draft.company)}</h3>
          <p>${escapeHtml(draft.role)}</p>
        </div>
        <span class="pill ${escapeHtml(draft.status)}">${escapeHtml(statusText)}</span>
      </div>

      <dl class="mail-meta">
        <div>
          <dt>To</dt>
          <dd>${escapeHtml(draft.to || "No recipient found")}</dd>
        </div>
        <div>
          <dt>Subject</dt>
          <dd>${escapeHtml(draft.subject)}</dd>
        </div>
        <div>
          <dt>Attachment</dt>
          <dd>${escapeHtml(draft.resume_path || "None")}</dd>
        </div>
      </dl>

      <pre>${escapeHtml(draft.body)}</pre>

      <div class="draft-actions">
        <a class="source" href="${escapeHtml(draft.source_url)}" target="_blank" rel="noreferrer">Source</a>
        <button class="ghost" data-action="skip" data-index="${draft.index}">Skip</button>
        <button class="primary" data-action="send" data-index="${draft.index}" ${disabled}>Send</button>
      </div>
    </article>
  `;
}

function renderDrafts(drafts) {
  if (!draftsEl) return;
  draftsEl.innerHTML = drafts.length ? drafts.map(renderDraft).join("") : '<div class="empty">No drafts yet.</div>';
  if (draftCount) draftCount.textContent = drafts.length;
}

async function refreshDrafts() {
  const response = await fetch("/api/drafts");
  const data = await response.json();
  renderDrafts(data.drafts || []);
}

document.querySelector("#upload-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  setNotice("Uploading resume...");
  try {
    const data = await postForm("/api/upload", new FormData(event.currentTarget));
    resumeLabel.textContent = data.resume_path;
    setNotice("Resume uploaded.");
  } catch (error) {
    setNotice(error.message, true);
  }
});

document.addEventListener("click", async (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;

  const action = target.dataset.action;
  try {
    if (action === "logout") {
      await postForm("/api/logout");
      window.location.reload();
      return;
    }

    if (action === "search") {
      const formData = new FormData();
      formData.set("limit", document.querySelector("#limit").value || "10");
      target.disabled = true;
      setNotice("Finding internships and contacts. This can take a minute...");
      const data = await postForm("/api/search", formData);
      setNotice(`Found ${data.internships_count} leads and ${data.contacts_count} contacts.`);
      window.location.reload();
      return;
    }

    if (action === "draft") {
      const formData = new FormData();
      formData.set("limit", document.querySelector("#limit").value || "10");
      formData.set("reset", "true");
      target.disabled = true;
      setNotice("Drafting emails from your uploaded resume...");
      const data = await postForm("/api/draft", formData);
      renderDrafts(data.drafts || []);
      setNotice(`Created ${data.drafts_count} drafts.`);
      target.disabled = false;
      return;
    }

    if (action === "refresh") {
      await refreshDrafts();
      setNotice("Draft queue refreshed.");
      return;
    }

    if (action === "skip" || action === "send") {
      const index = target.dataset.index;
      target.disabled = true;
      setNotice(action === "send" ? "Sending approved email..." : "Skipping draft...");
      await postForm(`/api/drafts/${index}/${action}`);
      await refreshDrafts();
      setNotice(action === "send" ? "Email sent." : "Draft skipped.");
    }
  } catch (error) {
    target.disabled = false;
    setNotice(error.message, true);
  }
});
