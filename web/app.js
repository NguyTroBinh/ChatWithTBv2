const uploadForm = document.querySelector("#upload-form");
const fileInput = document.querySelector("#pdf-file");
const dropzone = document.querySelector("#dropzone");
const fileList = document.querySelector("#file-list");
const fileCount = document.querySelector("#file-count");
const sourceCount = document.querySelector("#source-count");
const uploadSubmit = document.querySelector("#upload-submit");
const uploadStatus = document.querySelector("#upload-status");
const sourceScope = document.querySelector("#source-scope");
const chatForm = document.querySelector("#chat-form");
const queryInput = document.querySelector("#query");
const messages = document.querySelector("#messages");
const chatState = document.querySelector("#chat-state");
const workspace = document.querySelector("#workspace");
const attachButton = document.querySelector("#attach-button");
const composerFiles = document.querySelector("#composer-files");
const activeDocumentsNode = document.querySelector("#active-documents");
const historyToggle = document.querySelector("#history-toggle");
const historyPanel = document.querySelector("#history-panel");
const newChatButton = document.querySelector("#new-chat");
const conversationList = document.querySelector("#conversation-list");
const conversationExpand = document.querySelector("#conversation-expand");
const conversationStatus = document.querySelector("#conversation-status");
const conversationDialog = document.querySelector("#conversation-dialog");
const conversationDialogForm = document.querySelector("#conversation-dialog-form");
const conversationDialogTitle = document.querySelector("#conversation-dialog-title");
const conversationTitleInput = document.querySelector("#conversation-title-input");
const documentSearch = document.querySelector("#document-search");
const documentList = document.querySelector("#document-list");
const documentStatus = document.querySelector("#document-status");
const refreshDocumentsButton = document.querySelector("#refresh-documents");
const SESSION_STORAGE_KEY = "chat_with_tb_session_id";

const state = {
  files: [],
  availableDocuments: [],
  activeDocuments: [],
  conversations: [],
  sessionId: getOrCreateSessionId(),
  conversationKnown: false,
  historyOpen: false,
  historyExpanded: false,
  busy: false,
  documentsLoading: false,
  documentLoadToken: 0,
  mode: "fast",
};

const MODE_LABELS = {
  fast: "Fast",
  balanced: "Balanced",
  deep: "Deep",
};

renderFiles();
renderDocumentLibrary();
renderActiveDocuments();
applyHistoryState();
renderConversations();
loadDocuments();
loadConversations();

fileInput.addEventListener("change", () => {
  addFiles(fileInput.files);
  fileInput.value = "";
});

for (const eventName of ["dragenter", "dragover"]) {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.add("is-dragging");
  });
}

for (const eventName of ["dragleave", "drop"]) {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.remove("is-dragging");
  });
}

dropzone.addEventListener("drop", (event) => {
  addFiles(event.dataTransfer.files);
});

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await uploadQueuedFiles();
});

let documentSearchTimer = null;
documentSearch.addEventListener("input", () => {
  clearTimeout(documentSearchTimer);
  documentSearchTimer = setTimeout(() => loadDocuments(), 250);
});

refreshDocumentsButton.addEventListener("click", () => loadDocuments());

attachButton.addEventListener("click", () => composerFiles.click());

composerFiles.addEventListener("change", () => {
  addFiles(composerFiles.files);
  composerFiles.value = "";
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await submitQuestion();
});

queryInput.addEventListener("input", resizeQueryInput);
queryInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    chatForm.requestSubmit();
  }
});

historyToggle.addEventListener("click", () => setHistoryOpen(!state.historyOpen));
newChatButton.addEventListener("click", () => createNewConversation());
conversationExpand.addEventListener("click", () => {
  state.historyExpanded = !state.historyExpanded;
  renderConversations();
});
conversationDialogForm.addEventListener("submit", handleConversationDialogSubmit);
conversationDialog.addEventListener("cancel", () => {
  conversationDialog.returnValue = "cancel";
});
document.addEventListener("click", (event) => {
  if (!event.target.closest?.(".conversation-item")) closeConversationMenus();
});

bindSuggestionButtons();
bindModeButtons();

function setHistoryOpen(isOpen) {
  state.historyOpen = isOpen;
  applyHistoryState();
}

function applyHistoryState() {
  workspace.classList.toggle("history-open", state.historyOpen);
  historyPanel.hidden = !state.historyOpen;
  historyToggle.setAttribute("aria-expanded", String(state.historyOpen));
}

async function loadConversations({ openCurrent = true } = {}) {
  setConversationStatus("");
  try {
    const response = await fetch("/api/conversations?limit=50");
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Không thể tải lịch sử chat.");
    state.conversations = result.conversations || [];
    state.conversationKnown = state.conversations.some((item) => item.sessionId === state.sessionId);
    renderConversations();
    if (openCurrent && state.conversationKnown) {
      await loadConversation(state.sessionId, { touch: false, refreshList: false });
    }
  } catch (error) {
    setConversationStatus(error.message, true);
  }
}

async function createNewConversation() {
  const sessionId = randomSessionId();
  const title = await askConversationTitle({
    heading: "Đặt tên đoạn chat",
    fallback: `TB ${sessionId}`,
  });
  if (title === null) return;

  try {
    const response = await fetch("/api/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, title, document_ids: [] }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Không thể tạo đoạn chat mới.");
    activateConversationPayload(result, { clearWhenEmpty: true });
    upsertConversation(result.conversation);
    setHistoryOpen(true);
    renderConversations();
    setChatState("Sẵn sàng");
  } catch (error) {
    setConversationStatus(error.message, true);
  }
}

async function loadConversation(sessionId, { touch = true, refreshList = true } = {}) {
  try {
    const response = await fetch(
      touch
        ? `/api/conversations/${encodeURIComponent(sessionId)}/open`
        : `/api/conversations/${encodeURIComponent(sessionId)}`,
      { method: touch ? "POST" : "GET" },
    );
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Không thể mở đoạn chat.");
    activateConversationPayload(result);
    if (refreshList) await loadConversations({ openCurrent: false });
  } catch (error) {
    setConversationStatus(error.message, true);
  }
}

function activateConversationPayload(result, { clearWhenEmpty = false } = {}) {
  const conversation = result.conversation;
  if (!conversation?.sessionId) return;
  state.sessionId = conversation.sessionId;
  localStorage.setItem(SESSION_STORAGE_KEY, state.sessionId);
  state.conversationKnown = true;
  state.files = [];
  state.activeDocuments = (result.documents || []).map(normalizeDocument);
  renderFiles();
  renderActiveDocuments();
  renderDocumentLibrary();
  renderHistoryMessages(result.messages || [], clearWhenEmpty);
  setChatState("Sẵn sàng");
}

function renderHistoryMessages(history, clearWhenEmpty = false) {
  messages.innerHTML = "";
  if (!history.length) {
    messages.innerHTML = emptyStateMarkup();
    bindSuggestionButtons();
    if (clearWhenEmpty) scrollMessagesToBottom();
    return;
  }
  history.forEach((item) => {
    if (item.role === "user") addUserMessage(item.content || "");
    else addAssistantMessage(item.content || "");
  });
  scrollMessagesToBottom();
}

function upsertConversation(conversation) {
  if (!conversation?.sessionId) return;
  state.conversations = [
    conversation,
    ...state.conversations.filter((item) => item.sessionId !== conversation.sessionId),
  ];
}

async function renameConversation(conversation) {
  const title = await askConversationTitle({
    heading: "Đổi tên đoạn chat",
    value: conversation.title || "",
    fallback: conversation.title || `TB ${conversation.sessionId}`,
  });
  if (title === null) return;
  try {
    const response = await fetch(`/api/conversations/${encodeURIComponent(conversation.sessionId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Không thể đổi tên đoạn chat.");
    upsertConversation(result.conversation);
    renderConversations();
  } catch (error) {
    setConversationStatus(error.message, true);
  }
}

async function deleteConversation(conversation) {
  if (!confirm(`Xóa "${conversation.title}" và toàn bộ lịch sử liên quan?`)) return;
  try {
    const response = await fetch(`/api/conversations/${encodeURIComponent(conversation.sessionId)}`, {
      method: "DELETE",
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Không thể xóa đoạn chat.");
    state.conversations = state.conversations.filter((item) => item.sessionId !== conversation.sessionId);
    if (state.sessionId === conversation.sessionId) {
      state.sessionId = resetSessionId();
      state.conversationKnown = false;
      state.files = [];
      state.activeDocuments = [];
      messages.innerHTML = emptyStateMarkup();
      bindSuggestionButtons();
      renderFiles();
      renderActiveDocuments();
      renderDocumentLibrary();
      setChatState("Sẵn sàng");
    }
    renderConversations();
  } catch (error) {
    setConversationStatus(error.message, true);
  }
}

async function syncActiveConversationDocuments() {
  if (!state.conversationKnown) return;
  try {
    const response = await fetch(`/api/conversations/${encodeURIComponent(state.sessionId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document_ids: state.activeDocuments.map((doc) => doc.document_id) }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Không thể lưu phạm vi tài liệu.");
    upsertConversation(result.conversation);
    renderConversations();
  } catch (error) {
    setConversationStatus(error.message, true);
  }
}

function askConversationTitle({ heading, value = "", fallback }) {
  if (!conversationDialog.showModal) {
    const title = prompt(heading, value);
    return Promise.resolve(title === null ? null : (title.trim() || fallback));
  }
  return new Promise((resolve) => {
    conversationDialogTitle.textContent = heading;
    conversationDialog.dataset.fallback = fallback;
    conversationTitleInput.value = value;
    conversationDialog._resolve = resolve;
    conversationDialog.showModal();
    conversationTitleInput.focus();
    conversationTitleInput.select();
  });
}

function handleConversationDialogSubmit(event) {
  event.preventDefault();
  const action = event.submitter?.value || "confirm";
  conversationDialog.close(action);
}

conversationDialog.addEventListener("close", () => {
  const resolve = conversationDialog._resolve;
  if (!resolve) return;
  conversationDialog._resolve = null;
  if (conversationDialog.returnValue !== "confirm") {
    resolve(null);
    return;
  }
  const fallback = conversationDialog.dataset.fallback || "";
  resolve(conversationTitleInput.value.trim() || fallback);
});

function bindModeButtons() {
  const trigger = document.querySelector(".mode-trigger");
  const dropdown = document.querySelector(".mode-dropdown");
  const modeLabel = document.querySelector(".mode-label");

  trigger.addEventListener("click", () => {
    const isOpen = !dropdown.hidden;
    dropdown.hidden = isOpen;
    trigger.setAttribute("aria-expanded", String(!isOpen));
  });

  document.addEventListener("click", (e) => {
    if (!trigger.contains(e.target) && !dropdown.contains(e.target)) {
      dropdown.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
    }
  });

  document.querySelectorAll(".mode-option").forEach((option) => {
    option.addEventListener("click", () => {
      state.mode = option.dataset.mode;
      modeLabel.textContent = option.textContent;
      document.querySelectorAll(".mode-option").forEach((o) => o.classList.toggle("is-selected", o === option));
      dropdown.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
    });
  });
}

function addFiles(fileListLike) {
  const files = Array.from(fileListLike || []);
  const invalid = files.find((file) => !isPdf(file));
  if (invalid) {
    setUploadStatus("Chỉ hỗ trợ tệp PDF.", true);
    return;
  }

  for (const file of files) {
    const id = fileKey(file);
    if (!state.files.some((item) => item.id === id)) {
      state.files.push({ id, file, status: "ready", document: null });
    }
  }

  setUploadStatus("");
  renderFiles();
}

async function uploadQueuedFiles() {
  const pendingFiles = state.files.filter((item) => item.status === "ready" || item.status === "error");
  if (!pendingFiles.length || state.busy) return;

  state.busy = true;
  pendingFiles.forEach((item) => { item.status = "uploading"; });
  renderFiles();
  uploadSubmit.disabled = true;
  setUploadStatus(`Đang xử lý ${pendingFiles.length} tài liệu...`);

  const data = new FormData();
  pendingFiles.forEach((item) => data.append("files", item.file));

  try {
    const response = await fetch("/api/upload", { method: "POST", body: data });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Không thể nạp tài liệu.");

    for (const doc of result.documents || []) {
      const item = pendingFiles.find((entry) => entry.file.name === doc.file_name);
      if (item) {
        item.status = "uploaded";
        item.document = doc;
      }
      upsertActiveDocument(doc);
    }

    pendingFiles.forEach((item) => {
      if (item.status === "uploading") item.status = "error";
    });
    setUploadStatus(`${state.activeDocuments.length} tài liệu đang được dùng để chat.`);
    renderActiveDocuments();
    syncActiveConversationDocuments();
    loadDocuments();
  } catch (error) {
    pendingFiles.forEach((item) => { item.status = "error"; });
    setUploadStatus(error.message, true);
  } finally {
    state.busy = false;
    renderFiles();
    updateUploadButton();
  }
}

async function loadDocuments() {
  const loadToken = state.documentLoadToken + 1;
  state.documentLoadToken = loadToken;
  state.documentsLoading = true;
  renderDocumentLibrary();
  setDocumentStatus("");

  const params = new URLSearchParams();
  const query = documentSearch.value.trim();
  if (query) params.set("q", query);

  try {
    const response = await fetch(`/api/documents${params.toString() ? `?${params}` : ""}`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Không thể tải tài liệu trong DB.");
    if (loadToken !== state.documentLoadToken) return;

    state.availableDocuments = result.documents || [];
    setDocumentStatus(state.availableDocuments.length ? "" : "Không có tài liệu phù hợp.");
  } catch (error) {
    if (loadToken !== state.documentLoadToken) return;
    state.availableDocuments = [];
    setDocumentStatus(error.message, true);
  } finally {
    if (loadToken === state.documentLoadToken) {
      state.documentsLoading = false;
      renderDocumentLibrary();
    }
  }
}

async function submitQuestion() {
  const query = queryInput.value.trim();
  if (!query || state.busy) return;

  if (!state.activeDocuments.length) {
    clearEmptyState();
    addAssistantMessage("Hãy nạp ít nhất một PDF trước khi đặt câu hỏi.");
    return;
  }

  clearEmptyState();
  addUserMessage(query);
  queryInput.value = "";
  resizeQueryInput();
  setChatState(state.mode === "deep" ? "Đang lập kế hoạch trả lời" : "Đang trả lời");

  const pending = addAssistantMessage("");
  pending.bubble.classList.add("is-pending");
  pending.bubble.innerHTML = `<span>${pendingMessage()}</span><span class="thinking-dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span>`;

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        session_id: state.sessionId,
        top_k: 5,
        document_ids: state.activeDocuments.map((doc) => doc.document_id),
        mode: state.mode,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Không thể nhận câu trả lời.");

    pending.bubble.classList.remove("is-pending");
    pending.bubble.innerHTML = "";
    appendAssistantContent(pending.bubble, result.answer || "Chưa có câu trả lời.");
    if (result.conversation) {
      state.conversationKnown = true;
      upsertConversation(result.conversation);
      renderConversations();
    } else {
      loadConversations({ openCurrent: false });
    }
    setChatState(result.warnings?.length ? "Đã trả lời, có cảnh báo" : "Đã trả lời");
  } catch (error) {
    pending.bubble.classList.remove("is-pending");
    pending.bubble.textContent = error.message;
    pending.bubble.classList.add("error-message");
    setChatState("Có lỗi");
  }

  scrollMessagesToBottom();
}

function renderFiles() {
  fileCount.textContent = state.files.length;
  if (!state.files.length) {
    fileList.innerHTML = '<div class="file-empty">Chưa có tệp nào được chọn.</div>';
  } else {
    fileList.innerHTML = "";
    state.files.forEach((item) => fileList.appendChild(fileItemNode(item)));
  }
  updateUploadButton();
}

function renderDocumentLibrary() {
  if (state.documentsLoading) {
    documentList.innerHTML = '<div class="file-empty">Đang tải tài liệu trong DB...</div>';
    return;
  }

  if (!state.availableDocuments.length) {
    documentList.innerHTML = '<div class="file-empty">Chưa có tài liệu trong DB.</div>';
    return;
  }

  documentList.innerHTML = "";
  state.availableDocuments.forEach((doc) => {
    documentList.appendChild(storedDocumentNode(doc));
  });
}

function renderConversations() {
  const visible = state.historyExpanded ? state.conversations : state.conversations.slice(0, 5);
  conversationList.innerHTML = "";

  if (!visible.length) {
    conversationList.innerHTML = '<div class="conversation-empty">Chưa có đoạn chat nào.</div>';
  } else {
    visible.forEach((conversation) => {
      conversationList.appendChild(conversationNode(conversation));
    });
  }

  const hiddenCount = Math.max(0, state.conversations.length - 5);
  conversationExpand.hidden = hiddenCount === 0;
  conversationExpand.textContent = state.historyExpanded
    ? "Thu gọn ↑"
    : `Mở rộng ${hiddenCount} ↓`;
}

function conversationNode(conversation) {
  const row = document.createElement("div");
  row.className = `conversation-item${conversation.sessionId === state.sessionId ? " is-active" : ""}`;

  const open = document.createElement("button");
  open.className = "conversation-open";
  open.type = "button";
  open.addEventListener("click", () => loadConversation(conversation.sessionId));

  const dot = document.createElement("span");
  dot.className = "conversation-active-dot";
  dot.setAttribute("aria-hidden", "true");
  const copy = document.createElement("span");
  copy.className = "conversation-copy";
  const title = document.createElement("span");
  title.className = "conversation-title";
  title.title = conversation.title || conversation.sessionId;
  title.textContent = conversation.title || `TB ${conversation.sessionId}`;
  const meta = document.createElement("span");
  meta.className = "conversation-meta";
  meta.textContent = formatConversationTime(conversation.updatedAt);
  copy.append(title, meta);
  open.append(dot, copy);

  const menuButton = document.createElement("button");
  menuButton.className = "conversation-menu-button";
  menuButton.type = "button";
  menuButton.title = "Tùy chọn";
  menuButton.setAttribute("aria-label", "Tùy chọn đoạn chat");
  menuButton.textContent = "⋯";

  const menu = document.createElement("div");
  menu.className = "conversation-menu";
  menu.hidden = true;
  const rename = document.createElement("button");
  rename.type = "button";
  rename.innerHTML = '<span aria-hidden="true">✎</span> Đổi tên';
  rename.addEventListener("click", () => {
    menu.hidden = true;
    renameConversation(conversation);
  });
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "is-danger";
  remove.innerHTML = '<span aria-hidden="true">×</span> Xóa';
  remove.addEventListener("click", () => {
    menu.hidden = true;
    deleteConversation(conversation);
  });
  menu.append(rename, remove);

  menuButton.addEventListener("click", (event) => {
    event.stopPropagation();
    closeConversationMenus(menu);
    menu.hidden = !menu.hidden;
  });

  row.append(open, menuButton, menu);
  return row;
}

function closeConversationMenus(exceptMenu = null) {
  document.querySelectorAll(".conversation-menu").forEach((menu) => {
    if (menu !== exceptMenu) menu.hidden = true;
  });
}

function storedDocumentNode(doc) {
  const selected = isActiveDocument(doc.document_id);
  const node = document.createElement("button");
  node.className = `stored-doc-item${selected ? " is-selected" : ""}`;
  node.type = "button";
  node.setAttribute("aria-pressed", String(selected));

  const details = document.createElement("span");
  const name = document.createElement("span");
  name.className = "stored-doc-name";
  name.title = doc.file_name || "PDF";
  name.textContent = doc.file_name || "PDF";
  const updated = document.createElement("span");
  updated.className = "stored-doc-date";
  updated.textContent = formatUpdatedAt(doc.updated_at);
  details.append(name, updated);

  const check = document.createElement("span");
  check.className = "stored-doc-check";
  check.setAttribute("aria-hidden", "true");
  check.textContent = "✓";

  node.append(details, check);
  node.addEventListener("click", () => toggleStoredDocument(doc));
  return node;
}

function toggleStoredDocument(doc) {
  if (isActiveDocument(doc.document_id)) {
    removeActiveDocument(doc.document_id, false);
    return;
  } else {
    upsertActiveDocument(doc);
    renderActiveDocuments();
  }
  renderDocumentLibrary();
  syncActiveConversationDocuments();
}

function fileItemNode(item) {
  const node = document.createElement("div");
  node.className = `file-item${item.status === "uploaded" ? " is-uploaded" : ""}`;

  const icon = document.createElement("span");
  icon.className = "file-icon";
  icon.textContent = "PDF";

  const details = document.createElement("div");
  details.className = "file-details";
  const name = document.createElement("span");
  name.className = "file-name";
  name.title = item.file.name;
  name.textContent = item.file.name;
  const meta = document.createElement("span");
  meta.className = "file-meta";
  meta.textContent = `${formatBytes(item.file.size)} · ${fileStatusLabel(item.status, item.document)}`;
  details.append(name, meta);

  const remove = document.createElement("button");
  remove.className = "remove-file";
  remove.type = "button";
  remove.title = item.status === "uploaded" ? "Bỏ khỏi phạm vi chat" : "Bỏ tệp";
  remove.setAttribute("aria-label", remove.title);
  remove.textContent = "×";
  remove.addEventListener("click", () => removeFile(item.id));

  node.append(icon, details, remove);
  return node;
}

function removeFile(id) {
  const removed = state.files.find((item) => item.id === id);
  state.files = state.files.filter((item) => item.id !== id);
  if (removed?.document?.document_id) {
    state.activeDocuments = state.activeDocuments.filter((doc) => doc.document_id !== removed.document.document_id);
  }
  renderFiles();
  renderActiveDocuments();
  renderDocumentLibrary();
  if (removed?.document?.document_id) syncActiveConversationDocuments();
  setUploadStatus("");
}

function renderActiveDocuments() {
  sourceCount.textContent = state.activeDocuments.length;
  sourceScope.textContent = state.activeDocuments.length
    ? `${state.activeDocuments.length} tài liệu đang chọn`
    : "Chưa có tài liệu";
  activeDocumentsNode.innerHTML = "";

  state.activeDocuments.forEach((activeDocument) => {
    const node = document.createElement("div");
    node.className = "active-doc";
    const label = document.createElement("span");
    label.title = activeDocument.file_name || "PDF";
    label.textContent = activeDocument.file_name || "PDF";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.title = "Bỏ khỏi phạm vi chat";
    remove.setAttribute("aria-label", remove.title);
    remove.textContent = "×";
    remove.addEventListener("click", () => removeActiveDocument(activeDocument.document_id));
    node.append(label, remove);
    activeDocumentsNode.appendChild(node);
  });
}

function upsertActiveDocument(doc) {
  const normalized = normalizeDocument(doc);
  if (!normalized.document_id) return;

  const existingIndex = state.activeDocuments.findIndex(
    (entry) => entry.document_id === normalized.document_id,
  );
  if (existingIndex >= 0) state.activeDocuments[existingIndex] = normalized;
  else state.activeDocuments.push(normalized);
}

function normalizeDocument(doc) {
  return {
    ...doc,
    document_id: doc.document_id || doc.documentId,
    file_name: doc.file_name || doc.fileName || "PDF",
    updated_at: doc.updated_at || doc.updatedAt || null,
  };
}

function isActiveDocument(documentId) {
  return state.activeDocuments.some((doc) => doc.document_id === documentId);
}

function removeActiveDocument(documentId, removeUploadedFile = true) {
  state.activeDocuments = state.activeDocuments.filter((doc) => doc.document_id !== documentId);
  if (removeUploadedFile) {
    state.files = state.files.filter((item) => item.document?.document_id !== documentId);
  }
  renderFiles();
  renderActiveDocuments();
  renderDocumentLibrary();
  syncActiveConversationDocuments();
}

function appendAssistantContent(bubble, text) {
  const kicker = document.createElement("div");
  kicker.className = "message-kicker";
  kicker.textContent = "Chat With TB";
  const answer = document.createElement("div");
  answer.className = "answer-content";
  answer.innerHTML = formatAnswer(text);
  bubble.append(kicker, answer);
}

function pendingMessage() {
  if (state.mode === "deep") return "Đang lập kế hoạch sub-query và tổng hợp câu trả lời";
  return `Đang trả lời (${MODE_LABELS[state.mode] || "Fast"})`;
}

function addUserMessage(text) {
  const row = document.createElement("div");
  row.className = "message-row user";
  const avatar = document.createElement("span");
  avatar.className = "message-avatar";
  avatar.textContent = "U";
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.textContent = text;
  row.append(bubble, avatar);
  messages.appendChild(row);
  scrollMessagesToBottom();
}

function addAssistantMessage(text) {
  const row = document.createElement("div");
  row.className = "message-row assistant";
  const avatar = document.createElement("span");
  avatar.className = "message-avatar";
  avatar.textContent = "TB";
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  if (text) appendAssistantContent(bubble, text);
  row.append(avatar, bubble);
  messages.appendChild(row);
  scrollMessagesToBottom();
  return { row, bubble };
}

function clearEmptyState() {
  messages.querySelector(".empty-state")?.remove();
}

function emptyStateMarkup() {
  return '<div class="empty-state"><span class="empty-mark">TB</span><h3>Hỏi về tài liệu của bạn</h3><p>Nạp PDF ở cột bên trái, sau đó đặt câu hỏi để bắt đầu.</p><div class="suggestions" aria-label="Câu hỏi gợi ý"><button class="suggestion" type="button" data-query="Tóm tắt nội dung chính của tài liệu.">Tóm tắt tài liệu</button><button class="suggestion" type="button" data-query="Các điểm quan trọng cần lưu ý là gì?">Điểm quan trọng</button></div></div>';
}

function bindSuggestionButtons() {
  document.querySelectorAll(".suggestion").forEach((button) => {
    button.addEventListener("click", () => {
      queryInput.value = button.dataset.query || "";
      resizeQueryInput();
      queryInput.focus();
    });
  });
}

function formatAnswer(text) {
  const lines = escapeHtml(text || "").split(/\r?\n/);
  const html = [];
  let listType = null;
  let paragraph = [];

  const closeList = () => {
    if (listType) {
      html.push(`</${listType}>`);
      listType = null;
    }
  };

  const flushParagraph = () => {
    if (!paragraph.length) return;
    html.push(`<p>${formatInline(paragraph.join(" "))}</p>`);
    paragraph = [];
  };

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      flushParagraph();
      closeList();
      continue;
    }

    const heading = line.match(/^#{1,3}\s+(.+)$/);
    const numbered = line.match(/^\d+[.)]\s+(.+)$/);
    const bullet = line.match(/^[-*•]\s+(.+)$/);
    const quote = line.match(/^&gt;\s?(.+)$/);

    if (heading) {
      flushParagraph();
      closeList();
      html.push(`<h4>${formatInline(heading[1])}</h4>`);
    } else if (numbered || bullet) {
      flushParagraph();
      const nextListType = numbered ? "ol" : "ul";
      if (listType && listType !== nextListType) closeList();
      if (!listType) {
        listType = nextListType;
        html.push(`<${listType}>`);
      }
      html.push(`<li>${formatInline((numbered || bullet)[1])}</li>`);
    } else if (quote) {
      flushParagraph();
      closeList();
      html.push(`<blockquote>${formatInline(quote[1])}</blockquote>`);
    } else {
      closeList();
      paragraph.push(line);
    }
  }

  flushParagraph();
  closeList();
  return html.join("") || "<p>Chưa có nội dung trả lời.</p>";
}

function formatInline(text) {
  return text
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_]+)__/g, "<strong>$1</strong>");
}

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function resizeQueryInput() {
  queryInput.style.height = "auto";
  queryInput.style.height = `${Math.min(queryInput.scrollHeight, 132)}px`;
}

function scrollMessagesToBottom() {
  requestAnimationFrame(() => {
    messages.scrollTop = messages.scrollHeight;
  });
}

function updateUploadButton() {
  uploadSubmit.disabled = state.busy || !state.files.some((item) => item.status === "ready" || item.status === "error");
}

function setUploadStatus(text, isError = false) {
  uploadStatus.textContent = text;
  uploadStatus.classList.toggle("is-error", isError);
}

function setDocumentStatus(text, isError = false) {
  documentStatus.textContent = text;
  documentStatus.classList.toggle("is-error", isError);
}

function setConversationStatus(text, isError = false) {
  conversationStatus.textContent = text;
  conversationStatus.classList.toggle("is-error", isError);
}

function setChatState(text) {
  chatState.textContent = text;
}

function fileStatusLabel(status, document) {
  if (status === "uploading") return "đang xử lý";
  if (status === "uploaded") return `${document?.chunk_count || 0} chunks`;
  if (status === "error") return "lỗi xử lý";
  return "chờ nạp";
}

function isPdf(file) {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

function fileKey(file) {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function formatBytes(bytes) {
  if (!bytes) return "0 KB";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function formatUpdatedAt(value) {
  if (!value) return "Chưa rõ ngày cập nhật";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return `Cập nhật ${String(value).split("T")[0]}`;
  return `Cập nhật ${date.toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" })}`;
}

function formatConversationTime(value) {
  if (!value) return "Chưa có hoạt động";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).split("T")[0];
  return date.toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" });
}

function getOrCreateSessionId() {
  const existing = localStorage.getItem(SESSION_STORAGE_KEY);
  if (existing) return existing;
  return resetSessionId();
}

function resetSessionId() {
  const id = randomSessionId();
  localStorage.setItem(SESSION_STORAGE_KEY, id);
  return id;
}

function randomSessionId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}
