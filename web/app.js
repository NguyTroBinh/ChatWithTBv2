const uploadForm = document.querySelector("#upload-form");
const fileInput = document.querySelector("#pdf-file");
const fileLabel = document.querySelector("#file-label");
const uploadStatus = document.querySelector("#upload-status");
const chatForm = document.querySelector("#chat-form");
const queryInput = document.querySelector("#query");
const messages = document.querySelector("#messages");
const attachButton = document.querySelector("#attach-button");
const composerFiles = document.querySelector("#composer-files");
const activeDocumentsNode = document.querySelector("#active-documents");
const activeDocuments = [];

fileInput.addEventListener("change", () => {
  fileLabel.textContent = fileInput.files.length > 1
    ? `${fileInput.files.length} PDF đã chọn`
    : fileInput.files[0]?.name || "Chọn PDF";
});

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await uploadFiles(fileInput.files);
});

attachButton.addEventListener("click", () => {
  composerFiles.click();
});

composerFiles.addEventListener("change", async () => {
  await uploadFiles(composerFiles.files);
  composerFiles.value = "";
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (!query) return;
  if (!activeDocuments.length) {
    clearEmptyState();
    addMessage("Hãy upload ít nhất một PDF bằng nút + trước khi hỏi.", "assistant");
    return;
  }

  clearEmptyState();
  addMessage(query, "user");
  queryInput.value = "";
  queryInput.style.height = "auto";
  const pending = addMessage("", "assistant");
  pending.innerHTML = 'Đang suy nghĩ<span class="thinking-dots"><span>.</span><span>.</span><span>.</span></span>';

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        top_k: 5,
        document_ids: activeDocuments.map((document) => document.document_id),
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Chat failed");
    pending.innerHTML = formatAnswer(result.answer);
    if (result.citations?.length) pending.appendChild(renderCitations(result.citations));
    scrollToBottom();
  } catch (error) {
    pending.textContent = error.message;
    scrollToBottom();
  }
});

async function uploadFiles(fileList) {
  const files = Array.from(fileList);
  if (!files.length) {
    uploadStatus.textContent = "Chưa chọn PDF.";
    return;
  }
  if (files.some((file) => file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf"))) {
    uploadStatus.textContent = "Chỉ hỗ trợ PDF.";
    return;
  }

  uploadStatus.textContent = `Đang xử lý ${files.length} PDF...`;
  const data = new FormData();
  for (const file of files) data.append("files", file);

  try {
    const response = await fetch("/api/upload", { method: "POST", body: data });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Upload failed");

    for (const document of result.documents || []) {
      const existing = activeDocuments.findIndex((item) => item.document_id === document.document_id);
      if (existing >= 0) activeDocuments[existing] = document;
      else activeDocuments.push(document);
    }
    uploadStatus.textContent = `${activeDocuments.length} PDF đang được dùng để chat.`;
    renderActiveDocuments();
  } catch (error) {
    uploadStatus.textContent = error.message;
  }
}

function renderActiveDocuments() {
  activeDocumentsNode.textContent = activeDocuments.length
    ? `Đang tìm trong: ${activeDocuments.map((document) => document.file_name).join(", ")}`
    : "";
}

queryInput.addEventListener("input", () => {
  queryInput.style.height = "auto";
  queryInput.style.height = `${queryInput.scrollHeight}px`;
});

function clearEmptyState() {
  const empty = messages.querySelector(".empty-state");
  if (empty) empty.remove();
}

function addMessage(text, role) {
  const node = document.createElement("div");
  node.className = `message ${role}`;
  node.textContent = text;
  messages.appendChild(node);
  scrollToBottom();
  return node;
}

function scrollToBottom() {
  window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" });
}

function formatAnswer(text) {
  const escaped = escapeHtml(text || "");
  const lines = escaped.split(/\n+/).map((line) => line.trim()).filter(Boolean);
  const html = [];
  let listType = "";

  for (const line of lines) {
    const numbered = line.match(/^(\d+)\.\s+(.*)$/);
    const bullet = line.match(/^[-*]\s+(.*)$/);
    if (numbered) {
      if (listType && listType !== "ol") html.push(`</${listType}>`);
      if (listType !== "ol") {
        html.push('<ol class="answer-list">');
        listType = "ol";
      }
      html.push(`<li>${formatInline(numbered[2])}</li>`);
      continue;
    }

    if (bullet) {
      if (listType && listType !== "ul") html.push(`</${listType}>`);
      if (listType !== "ul") {
        html.push('<ul class="answer-list">');
        listType = "ul";
      }
      html.push(`<li>${formatInline(bullet[1])}</li>`);
      continue;
    }

    if (listType) {
      html.push(`</${listType}>`);
      listType = "";
    }
    html.push(`<p class="answer-paragraph">${formatInline(line)}</p>`);
  }

  if (listType) html.push(`</${listType}>`);
  return html.join("");
}

function formatInline(text) {
  return text.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
}

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function renderCitations(citations) {
  const list = document.createElement("div");
  list.className = "citation-list";
  list.textContent = citations
    .map((citation) => {
      const page = citation.pageStart ? `p.${citation.pageStart}` : "p.?";
      return `[${citation.id}] ${citation.fileName} ${page}`;
    })
    .join("  ");
  return list;
}
