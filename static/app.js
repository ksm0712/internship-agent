const notice = document.querySelector("#notice");
const currentDraftEl = document.querySelector("#current-draft");
const draftCount = document.querySelector("#draft-count");
const resumeLabel = document.querySelector("#resume-label");
const historyList = document.querySelector("#history-list");

function setNotice(message, isError = false) {
  if (!notice) return;
  notice.textContent = message;
  notice.classList.toggle("error", isError);
}

async function parseResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  const text = await response.text();
  return {
    ok: false,
    error: text.includes("<!doctype") ? "Server returned an error page. Check the Flask terminal." : text,
  };
}

async function postForm(url, formData = new FormData()) {
  const response = await fetch(url, { method: "POST", body: formData });
  const data = await parseResponse(response);
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
  if (!draft) {
    return '<div class="empty">No pending draft. Create drafts to start reviewing.</div>';
  }
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
        <button class="ghost" data-action="remove" data-index="${draft.index}">Remove</button>
        <button class="primary" data-action="send" data-index="${draft.index}" ${disabled}>Send</button>
      </div>
    </article>
  `;
}

function renderHistory(items) {
  if (!historyList) return;
  if (!items || !items.length) {
    historyList.innerHTML = '<div class="empty compact">No history yet.</div>';
    return;
  }
  historyList.innerHTML = items
    .map(
      (item) => `
        <div class="history-row">
          <strong>${escapeHtml(item.company)}</strong>
          <span>${escapeHtml(item.role)}</span>
          <em>${escapeHtml(item.status)}</em>
        </div>
      `,
    )
    .join("");
}

function renderQueue(data) {
  if (currentDraftEl) currentDraftEl.innerHTML = renderDraft(data.current_draft);
  if (draftCount) draftCount.textContent = data.pending_count || 0;
  renderHistory(data.history || []);
}

async function refreshQueue() {
  const response = await fetch("/api/drafts");
  const data = await parseResponse(response);
  if (data.ok === false) throw new Error(data.error || "Could not refresh drafts.");
  renderQueue(data);
}

document.querySelector("#upload-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  setNotice("Uploading resume...");
  try {
    const data = await postForm("/api/upload", new FormData(event.currentTarget));
    resumeLabel.textContent = data.resume_path;
    document.querySelector('[data-action="draft"]')?.removeAttribute("disabled");
    window.hasResume = true;
    setNotice("Resume uploaded. You can draft emails now.");
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
      if (!window.hasResume) {
        setNotice("Upload your resume before drafting emails.", true);
        return;
      }
      const formData = new FormData();
      formData.set("limit", document.querySelector("#limit").value || "10");
      target.disabled = true;
      setNotice("Drafting only companies not already in your history...");
      const data = await postForm("/api/draft", formData);
      renderQueue(data);
      setNotice(`Created queue. ${data.pending_count || 0} pending drafts.`);
      target.disabled = false;
      return;
    }

    if (action === "refresh") {
      await refreshQueue();
      setNotice("Queue refreshed.");
      return;
    }

    if (action === "remove" || action === "send") {
      const index = target.dataset.index;
      target.disabled = true;
      setNotice(action === "send" ? "Sending approved email..." : "Removing draft...");
      await postForm(`/api/drafts/${index}/${action}`);
      await refreshQueue();
      setNotice(action === "send" ? "Email sent. Next draft loaded." : "Draft removed. Next draft loaded.");
    }
  } catch (error) {
    target.disabled = false;
    setNotice(error.message, true);
  }
});
