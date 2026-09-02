const notice = document.querySelector("#notice");
const currentDraftEl = document.querySelector("#current-draft");
const draftCount = document.querySelector("#draft-count");
const resumeLabel = document.querySelector("#resume-label");
const historyList = document.querySelector("#history-list");
const needsContactEl = document.querySelector("#needs-contact");

// Tag-input: Enter/comma adds a chip, click "x" removes it. Suggestion list
// is optional and just a convenience — free-typed values are accepted too.
function createTagInput({ tagListEl, inputEl, suggestionsEl, suggestions, initial }) {
  let tags = [...(initial || [])];

  function render() {
    tagListEl.innerHTML = tags
      .map(
        (tag, i) => `
          <span class="tag-chip">
            ${tag.replace(/</g, "&lt;")}
            <button type="button" class="tag-remove" data-index="${i}" aria-label="Remove ${tag}">&times;</button>
          </span>
        `,
      )
      .join("");
  }

  function addTag(value) {
    const trimmed = value.trim();
    if (!trimmed || tags.some((t) => t.toLowerCase() === trimmed.toLowerCase())) return;
    tags.push(trimmed);
    render();
  }

  function removeTag(index) {
    tags.splice(index, 1);
    render();
  }

  function hideSuggestions() {
    if (suggestionsEl) {
      suggestionsEl.hidden = true;
      suggestionsEl.innerHTML = "";
    }
  }

  function showSuggestions(query) {
    if (!suggestionsEl || !suggestions) return;
    const q = query.trim().toLowerCase();
    if (!q) return hideSuggestions();
    const matches = suggestions
      .filter((s) => s.toLowerCase().includes(q) && !tags.some((t) => t.toLowerCase() === s.toLowerCase()))
      .slice(0, 8);
    if (!matches.length) return hideSuggestions();
    suggestionsEl.innerHTML = matches
      .map((m) => `<button type="button" class="tag-suggestion" data-value="${m.replace(/"/g, "&quot;")}">${m}</button>`)
      .join("");
    suggestionsEl.hidden = false;
  }

  tagListEl.addEventListener("click", (event) => {
    const button = event.target.closest(".tag-remove");
    if (button) removeTag(Number(button.dataset.index));
  });

  inputEl.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      addTag(inputEl.value);
      inputEl.value = "";
      hideSuggestions();
    } else if (event.key === "Backspace" && !inputEl.value && tags.length) {
      removeTag(tags.length - 1);
    }
  });

  inputEl.addEventListener("input", () => showSuggestions(inputEl.value));
  inputEl.addEventListener("blur", () => setTimeout(hideSuggestions, 150));

  suggestionsEl?.addEventListener("click", (event) => {
    const button = event.target.closest(".tag-suggestion");
    if (!button) return;
    addTag(button.dataset.value);
    inputEl.value = "";
    inputEl.focus();
    hideSuggestions();
  });

  render();
  return { getTags: () => [...tags] };
}

let locationTagInput = null;
let roleTagInput = null;

function initSearchPreferenceInputs() {
  const locationTagsEl = document.querySelector("#location-tags");
  const roleTagsEl = document.querySelector("#role-tags");
  if (!locationTagsEl || !roleTagsEl) return;

  locationTagInput = createTagInput({
    tagListEl: locationTagsEl,
    inputEl: document.querySelector("#location-input"),
    suggestionsEl: document.querySelector("#location-suggestions"),
    suggestions: window.LOCATION_SUGGESTIONS || [],
    initial: window.savedLocations || [],
  });
  roleTagInput = createTagInput({
    tagListEl: roleTagsEl,
    inputEl: document.querySelector("#role-input"),
    initial: window.savedRoles || [],
  });
}

initSearchPreferenceInputs();

function updateKeyStatus(status) {
  window.keyStatus = status || window.keyStatus || {};
  document.querySelector('[data-action="search"]')?.toggleAttribute(
    "disabled",
    !(window.keyStatus.gemini && window.keyStatus.tavily),
  );
  document.querySelector('[data-action="draft"]')?.toggleAttribute(
    "disabled",
    !(window.keyStatus.gemini && window.hasResume),
  );
  document.querySelectorAll(".key-status span").forEach((item) => {
    const text = item.textContent.toLowerCase();
    const key = text.includes("gemini") ? "gemini" : text.includes("tavily") ? "tavily" : "hunter";
    item.classList.toggle("ok", Boolean(window.keyStatus[key]));
  });
}

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
  const matchBadge =
    draft.fit_score === null || draft.fit_score === undefined
      ? ""
      : `<span class="match-badge" title="Predicted fit score">Match ${Math.round(draft.fit_score * 100)}%</span>`;
  return `
    <article class="draft" data-id="${draft.id}" data-status="${escapeHtml(draft.status)}">
      <div class="draft-meta">
        <div>
          <h3>${escapeHtml(draft.company)}</h3>
          <p>${escapeHtml(draft.role)}</p>
        </div>
        <div class="draft-meta-badges">
          ${matchBadge}
          <span class="pill ${escapeHtml(draft.status)}">${escapeHtml(statusText)}</span>
        </div>
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
          <dd>${escapeHtml(draft.resume_name || "Resume")}</dd>
        </div>
      </dl>

      <pre>${escapeHtml(draft.body)}</pre>

      <div class="draft-actions">
        <a class="source" href="${escapeHtml(draft.source_url)}" target="_blank" rel="noreferrer">Source</a>
        <button class="ghost" data-action="remove" data-id="${draft.id}">Remove</button>
        <button class="primary" data-action="send" data-id="${draft.id}" ${disabled}>Send</button>
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

function renderNeedsContact(items) {
  if (!needsContactEl) return;
  if (!items || !items.length) {
    needsContactEl.innerHTML = "";
    return;
  }
  needsContactEl.innerHTML = `
    <h3>Needs contact</h3>
    <p>These are excluded from the send queue until a recipient is found.</p>
    ${items
      .map(
        (item) => `
          <div class="compact-row">
            <strong>${escapeHtml(item.company)}</strong>
            <span>${escapeHtml(item.role)}</span>
            <em>needs contact</em>
          </div>
        `,
      )
      .join("")}
  `;
}

function renderQueue(data) {
  if (currentDraftEl) currentDraftEl.innerHTML = renderDraft(data.current_draft);
  if (draftCount) draftCount.textContent = data.pending_count || 0;
  renderNeedsContact(data.needs_contact || []);
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
    const fileName = data.resume_path.split("/").pop().replaceAll("_", " ");
    resumeLabel.textContent = fileName;
    document.querySelector("#resume-card")?.classList.add("uploaded");
    const resumeSubtext = document.querySelector("#resume-card span");
    if (resumeSubtext) resumeSubtext.textContent = "Ready for drafting";
    document.querySelector('[data-action="draft"]')?.removeAttribute("disabled");
    window.hasResume = true;
    setNotice("Resume uploaded. You can draft emails now.");
  } catch (error) {
    setNotice(error.message, true);
  }
});

document.querySelector("#settings-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  setNotice("Saving API keys...");
  try {
    const data = await postForm("/api/settings", new FormData(event.currentTarget));
    updateKeyStatus(data.api_key_status);
    event.currentTarget.reset();
    setNotice("API keys saved. Free-tier usage now comes from this user's keys.");
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
      if (!(window.keyStatus?.gemini && window.keyStatus?.tavily)) {
        setNotice("Add Gemini and Tavily keys before finding leads.", true);
        return;
      }
      const formData = new FormData();
      formData.set("limit", document.querySelector("#limit").value || "10");
      for (const loc of locationTagInput?.getTags() || []) formData.append("locations", loc);
      for (const role of roleTagInput?.getTags() || []) formData.append("roles", role);
      target.disabled = true;
      setNotice("Finding internships and contacts. This can take a minute...");
      const data = await postForm("/api/search", formData);
      if (data.warning) {
        setNotice(`${data.warning} Showing ${data.internships_count} cached leads and ${data.contacts_count} contacts.`, true);
        target.disabled = false;
        return;
      }
      setNotice(`Found ${data.internships_count} leads and ${data.contacts_count} contacts.`);
      window.location.reload();
      return;
    }

    if (action === "draft") {
      if (!window.hasResume) {
        setNotice("Upload your resume before drafting emails.", true);
        return;
      }
      if (!window.keyStatus?.gemini) {
        setNotice("Add your Gemini key before drafting emails.", true);
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
      const draftId = target.dataset.id;
      target.disabled = true;
      setNotice(action === "send" ? "Sending approved email..." : "Removing draft...");
      await postForm(`/api/drafts/${draftId}/${action}`);
      await refreshQueue();
      setNotice(action === "send" ? "Email sent. Next draft loaded." : "Draft removed. Next draft loaded.");
    }
  } catch (error) {
    target.disabled = false;
    setNotice(error.message, true);
  }
});
