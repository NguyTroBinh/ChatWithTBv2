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
const attachButton = document.querySelector("#attach-button");
const composerFiles = document.querySelector("#composer-files");
const activeDocumentsNode = document.querySelector("#active-documents");
const evidenceList = document.querySelector("#evidence-list");
const evidenceCount = document.querySelector("#evidence-count");
const evidenceSummary = document.querySelector("#evidence-summary");
const newChatButton = document.querySelector("#new-chat");

const state = {
  files: [],
  activeDocuments: [],
  busy: false,
};

renderFiles();
renderActiveDocuments();

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

newChatButton.addEventListener("click", () => {
  messages.innerHTML = emptyStateMarkup();
  bindSuggestionButtons();
  clearEvidence();
  setChatState("Sẵn sàng");
});

bindSuggestionButtons();

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

    for (const document of result.documents || []) {
      const item = pendingFiles.find((entry) => entry.file.name === document.file_name);
      if (item) {
        item.status = "uploaded";
        item.document = document;
      }
      const existingIndex = state.activeDocuments.findIndex(
        (entry) => entry.document_id === document.document_id,
      );
      if (existingIndex >= 0) state.activeDocuments[existingIndex] = document;
      else state.activeDocuments.push(document);
    }

    pendingFiles.forEach((item) => {
      if (item.status === "uploading") item.status = "error";
    });
    setUploadStatus(`${state.activeDocuments.length} tài liệu đang được dùng để chat.`);
    renderActiveDocuments();
  } catch (error) {
    pendingFiles.forEach((item) => { item.status = "error"; });
    setUploadStatus(error.message, true);
  } finally {
    state.busy = false;
    renderFiles();
    updateUploadButton();
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
  setChatState("Đang truy xuất");

  const pending = addAssistantMessage("");
  pending.bubble.classList.add("is-pending");
  pending.bubble.innerHTML = '<span>Đang tìm evidence</span><span class="thinking-dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span>';

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        top_k: 5,
        document_ids: state.activeDocuments.map((document) => document.document_id),
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Không thể nhận câu trả lời.");

    pending.bubble.classList.remove("is-pending");
    pending.bubble.innerHTML = "";
    appendAssistantContent(pending.bubble, result.answer || "Chưa có câu trả lời.");
    appendInlineSources(pending.bubble, result);
    renderEvidence(result);
    setChatState(result.warnings?.length ? "Cần kiểm tra nguồn" : "Đã trả lời");
  } catch (error) {
    pending.bubble.classList.remove("is-pending");
    pending.bubble.textContent = error.message;
    pending.bubble.classList.add("error-message");
    setChatState("Có lỗi");
    clearEvidence();
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
    state.activeDocuments = state.activeDocuments.filter(
      (document) => document.document_id !== removed.document.document_id,
    );
  }
  renderFiles();
  renderActiveDocuments();
  setUploadStatus("");
}

function renderActiveDocuments() {
  sourceCount.textContent = state.activeDocuments.length;
  sourceScope.textContent = state.activeDocuments.length
    ? `${state.activeDocuments.length} tài liệu đã nạp`
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

function removeActiveDocument(documentId) {
  state.activeDocuments = state.activeDocuments.filter((document) => document.document_id !== documentId);
  state.files = state.files.filter((item) => item.document?.document_id !== documentId);
  renderFiles();
  renderActiveDocuments();
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

function appendInlineSources(bubble, result) {
  const evidence = responseEvidence(result);
  const fileNames = [...new Set(
    evidence
      .map((source) => source.file_name || source.fileName)
      .filter(Boolean),
  )];
  if (!fileNames.length) return;

  const wrapper = document.createElement("div");
  wrapper.className = "inline-sources";
  fileNames.forEach((fileName) => {
    const chip = document.createElement("span");
    chip.className = "source-chip";
    chip.textContent = fileName;
    wrapper.appendChild(chip);
  });
  bubble.appendChild(wrapper);
}

function renderEvidence(result) {
  const evidence = responseEvidence(result);
  evidenceCount.textContent = evidence.length;
  evidenceSummary.textContent = evidence.length
    ? `${evidence.length} đoạn được dùng cho câu trả lời`
    : "Không tìm thấy evidence phù hợp";
  evidenceList.innerHTML = "";

  if (!evidence.length) {
    evidenceList.innerHTML = '<div class="evidence-empty"><span class="evidence-empty-icon" aria-hidden="true">⌕</span><p>Chưa có đoạn trích đủ phù hợp để hiển thị.</p></div>';
    return;
  }

  evidence.forEach((chunk, index) => {
    const metadata = chunk.metadata || {};
    const fileName = chunk.fileName || chunk.file_name || "Tài liệu";
    const pageStart = chunk.pageStart ?? metadata.page_start;
    const pageEnd = chunk.pageEnd ?? metadata.page_end;
    const card = document.createElement("article");
    card.className = "evidence-card";

    const header = document.createElement("div");
    header.className = "evidence-card-header";
    const rank = document.createElement("span");
    rank.className = "evidence-rank";
    rank.textContent = `#${index + 1}`;
    const source = document.createElement("div");
    source.className = "evidence-source";
    const file = document.createElement("span");
    file.className = "evidence-file";
    file.title = fileName;
    file.textContent = fileName;
    const page = document.createElement("span");
    page.className = "evidence-page";
    page.textContent = pageLabel(pageStart, pageEnd);
    source.append(file, page);
    header.append(rank, source);

    if (typeof chunk.score === "number") {
      const score = document.createElement("span");
      score.className = "evidence-score";
      score.textContent = `score ${chunk.score.toFixed(3)}`;
      header.appendChild(score);
    }

    const text = document.createElement("p");
    text.className = "evidence-text";
    text.textContent = chunk.text || chunk.content || "Không có nội dung đoạn trích.";
    card.append(header, text);
    evidenceList.appendChild(card);
  });
  evidenceList.scrollTop = 0;
}

function responseEvidence(result) {
  return [result.evidence, result.usedEvidence, result.citations].find(Array.isArray) || [];
}

function clearEvidence() {
  evidenceCount.textContent = "0";
  evidenceSummary.textContent = "Bằng chứng cho câu trả lời mới nhất";
  evidenceList.innerHTML = '<div class="evidence-empty"><span class="evidence-empty-icon" aria-hidden="true">⌕</span><p>Evidence sẽ xuất hiện sau câu hỏi đầu tiên.</p></div>';
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

function pageLabel(start, end) {
  if (start && end && start !== end) return `Trang ${start}-${end}`;
  if (start) return `Trang ${start}`;
  return "Trang chưa xác định";
}
