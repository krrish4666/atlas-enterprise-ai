/**
 * Atlas Enterprise AI — Frontend Application Shell (Phase 5)
 * Pure Vanilla JavaScript — zero build tools or external frameworks.
 */

(function () {
  "use strict";

  // ── State & Constants ──────────────────────────────────────────────────────
  const STORAGE_ROLE_KEY = "atlas_user_role";
  const DEFAULT_ROLE = "employee";
  const VALID_VIEWS = ["chat", "documents", "approvals", "ops"];

  const state = {
    currentRole: DEFAULT_ROLE,
    currentView: "chat",
  };

  // ── DOM Elements ───────────────────────────────────────────────────────────
  const roleSelector = document.getElementById("role-selector");
  const tabButtons = document.querySelectorAll(".tab-btn");
  const viewPanels = document.querySelectorAll(".view");

  // ── Role Management ────────────────────────────────────────────────────────
  function initRole() {
    const savedRole = localStorage.getItem(STORAGE_ROLE_KEY);
    if (savedRole && ["employee", "reviewer", "admin"].includes(savedRole)) {
      state.currentRole = savedRole;
    } else {
      state.currentRole = DEFAULT_ROLE;
    }

    if (roleSelector) {
      roleSelector.value = state.currentRole;
      roleSelector.addEventListener("change", handleRoleChange);
    }
  }

  function handleRoleChange(e) {
    const newRole = e.target.value;
    state.currentRole = newRole;
    localStorage.setItem(STORAGE_ROLE_KEY, newRole);

    // Dispatch custom event for any view components to react to role changes
    window.dispatchEvent(
      new CustomEvent("atlas:role-change", { detail: { role: newRole } })
    );

    console.info(`[Atlas] Role updated to: ${newRole}`);
  }

  function getUserRole() {
    return state.currentRole;
  }

  function getAuthHeaders() {
    return {
      "X-User-Role": state.currentRole,
    };
  }

  // ── Tab & View Switching ───────────────────────────────────────────────────
  function switchView(targetView) {
    if (!VALID_VIEWS.includes(targetView)) {
      targetView = "chat";
    }

    state.currentView = targetView;

    // Update Tab Buttons
    tabButtons.forEach((btn) => {
      const view = btn.getAttribute("data-view");
      const isActive = view === targetView;
      btn.classList.toggle("active", isActive);
      btn.setAttribute("aria-selected", isActive ? "true" : "false");
    });

    // Update View Panels
    viewPanels.forEach((panel) => {
      const panelId = panel.id;
      const isTarget = panelId === `view-${targetView}`;
      panel.classList.toggle("active", isTarget);
    });

    // Update URL Hash without jumping scroll
    if (window.location.hash !== `#${targetView}`) {
      history.replaceState(null, "", `#${targetView}`);
    }

    // Dispatch view changed event
    window.dispatchEvent(
      new CustomEvent("atlas:view-change", { detail: { view: targetView } })
    );

    console.info(`[Atlas] Switched view to: ${targetView}`);
  }

  function initNavigation() {
    // Click listeners on tab buttons
    tabButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const targetView = btn.getAttribute("data-view");
        switchView(targetView);
      });
    });

    // Hash change listener (browser back/forward or manual hash edit)
    window.addEventListener("hashchange", () => {
      const hashView = window.location.hash.replace("#", "");
      if (VALID_VIEWS.includes(hashView) && hashView !== state.currentView) {
        switchView(hashView);
      }
    });

    // Initial view from URL hash if valid, otherwise default to chat
    const initialHash = window.location.hash.replace("#", "");
    if (VALID_VIEWS.includes(initialHash)) {
      switchView(initialHash);
    } else {
      switchView("chat");
    }
  }

  // ── Operations & Audits View Module ─────────────────────────────────────────
  const ops = {
    healthData: null,
    auditData: [],
    isLoadingHealth: false,
    isLoadingAudits: false,
  };

  // DOM Elements for Ops View
  const headerHealth = document.getElementById("header-health");
  const headerHealthDot = document.getElementById("header-health-dot");
  const headerHealthLabel = document.getElementById("header-health-label");

  const refreshOpsBtn = document.getElementById("refresh-ops-btn");
  const statusPostgres = document.getElementById("status-postgres");
  const statusRustfs = document.getElementById("status-rustfs");
  const statusOllama = document.getElementById("status-ollama");
  const statusLangfuse = document.getElementById("status-langfuse");
  const storageServiceName = document.getElementById("storage-service-name");
  const storageEndpointLabel = document.getElementById("storage-endpoint-label");
  const docsStorageLabel = document.getElementById("docs-storage-label");
  const inventoryStorageLabel = document.getElementById("inventory-storage-label");
  const embedServiceName = document.getElementById("embed-service-name");
  const embedModelLabel = document.getElementById("embed-model-label");
  const embedEndpointLabel = document.getElementById("embed-endpoint-label");
  const langfuseUiLink = document.getElementById("langfuse-ui-link");
  const langfuseUiLabel = document.getElementById("langfuse-ui-label");
  const langfuseModeLabel = document.getElementById("langfuse-mode-label");

  const auditEventFilter = document.getElementById("audit-event-filter");
  const auditLimitFilter = document.getElementById("audit-limit-filter");
  const auditTableBody = document.getElementById("audit-table-body");

  function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function formatTimestamp(isoStr) {
    if (!isoStr) return "-";
    try {
      const d = new Date(isoStr);
      if (isNaN(d.getTime())) return isoStr;
      const pad = (n) => String(n).padStart(2, "0");
      const year = d.getFullYear();
      const month = pad(d.getMonth() + 1);
      const day = pad(d.getDate());
      const hours = pad(d.getHours());
      const minutes = pad(d.getMinutes());
      const seconds = pad(d.getSeconds());
      return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
    } catch {
      return isoStr;
    }
  }

  function getEventPillClass(eventType) {
    switch (eventType) {
      case "guardrail_blocked":
      case "pii_redacted":
        return "event-pill pill-rose";
      case "hitl_queued":
      case "document_upload_requested":
        return "event-pill pill-amber";
      case "document_indexed":
      case "chat_completed":
      case "hitl_decided":
        return "event-pill pill-emerald";
      default:
        return "event-pill";
    }
  }

  async function fetchHealth() {
    ops.isLoadingHealth = true;
    try {
      const res = await fetch("/api/health", {
        headers: getAuthHeaders(),
      });
      if (!res.ok) {
        throw new Error(`Health check HTTP error: ${res.status}`);
      }
      const data = await res.json();
      ops.healthData = data;
      renderHealth(data);
    } catch (err) {
      console.warn("[Atlas Ops] Health check notice:", err.message);
      renderHealthError();
    } finally {
      ops.isLoadingHealth = false;
    }
  }

  function setServicePill(el, isUp, upText = "UP", downText = "DOWN") {
    if (!el) return;
    const dot = isUp ? "dot-emerald" : "dot-rose";
    const pillClass = isUp ? "service-pill pill-emerald" : "service-pill pill-rose";
    const text = isUp ? upText : downText;
    el.className = pillClass;
    el.innerHTML = `<span class="status-dot ${dot}"></span><span class="status-text">${text}</span>`;
  }

  function renderHealth(data) {
    const isOverallOk = data && data.status === "ok";

    // 1. Update Header Health Pill
    if (headerHealthDot && headerHealthLabel) {
      headerHealthDot.className = `status-dot ${isOverallOk ? "dot-emerald" : "dot-rose"}`;
      headerHealthLabel.textContent = isOverallOk ? "System Ready" : "Degraded";
    }

    const services = (data && data.services) || {};

    // 2. Postgres Card
    const isPgUp = services.postgres === "up";
    setServicePill(statusPostgres, isPgUp);

    // 3. Object Storage Card & Document Management Labels
    const isRustfsUp = services.rustfs === "up";
    setServicePill(statusRustfs, isRustfsUp);
    const storageLabel = services.storage_label || "Object Storage";
    if (storageServiceName) {
      storageServiceName.textContent = storageLabel.endsWith("Storage")
        ? storageLabel
        : `${storageLabel} Storage`;
    }
    if (storageEndpointLabel) {
      storageEndpointLabel.textContent = storageLabel === "RustFS" ? ":9000 / :9001" : "Cloud S3";
    }
    if (docsStorageLabel) {
      docsStorageLabel.textContent = storageLabel;
    }
    if (inventoryStorageLabel) {
      inventoryStorageLabel.textContent = storageLabel;
    }

    // 4. Embeddings Card (Ollama locally or Gemini API in cloud)
    const isOllamaUp = services.ollama === "up";
    setServicePill(statusOllama, isOllamaUp);
    if (embedServiceName) {
      embedServiceName.textContent =
        services.embed_provider === "gemini" ? "Gemini Embeddings" : "Host Ollama";
    }
    if (embedModelLabel && embedEndpointLabel) {
      if (services.embed_provider === "gemini") {
        embedModelLabel.textContent = "gemini-embedding-001";
        embedEndpointLabel.textContent = "768d API";
      } else {
        embedModelLabel.textContent = "nomic-embed-text";
        embedEndpointLabel.textContent = ":11434";
      }
    }

    // 5. Langfuse Card
    if (statusLangfuse) {
      const isLfEnabled = Boolean(services.langfuse_enabled);
      statusLangfuse.className = isLfEnabled ? "service-pill pill-emerald" : "service-pill pill-neutral";
      statusLangfuse.innerHTML = `<span class="status-dot ${isLfEnabled ? "dot-emerald" : "dot-neutral"}"></span><span class="status-text">${isLfEnabled ? "ACTIVE" : "DISABLED"}</span>`;
    }
    if (langfuseUiLink && services.langfuse_host) {
      langfuseUiLink.href = services.langfuse_host;
      const isLocal = services.langfuse_host.includes("localhost") || services.langfuse_host.includes("127.0.0.1");
      if (langfuseUiLabel) {
        langfuseUiLabel.textContent = isLocal ? "UI :3000" : "Langfuse Cloud";
      }
      if (langfuseModeLabel) {
        langfuseModeLabel.textContent = isLocal ? "v2 Self-Hosted" : "Cloud Free";
      }
    }
  }

  function renderHealthError() {
    if (headerHealthDot && headerHealthLabel) {
      headerHealthDot.className = "status-dot dot-rose";
      headerHealthLabel.textContent = "Offline";
    }
    setServicePill(statusPostgres, false, "UP", "DOWN");
    setServicePill(statusRustfs, false, "UP", "DOWN");
    setServicePill(statusOllama, false, "UP", "DOWN");
    if (statusLangfuse) {
      statusLangfuse.className = "service-pill pill-neutral";
      statusLangfuse.innerHTML = `<span class="status-dot dot-neutral"></span><span class="status-text">UNKNOWN</span>`;
    }
  }

  async function fetchAuditLogs() {
    if (!auditTableBody) return;
    ops.isLoadingAudits = true;

    const eventType = auditEventFilter ? auditEventFilter.value : "all";
    const limit = auditLimitFilter ? auditLimitFilter.value : "50";

    const params = new URLSearchParams();
    params.set("limit", limit);
    if (eventType && eventType !== "all") {
      params.set("event_type", eventType);
    }

    try {
      const res = await fetch(`/api/audit-logs?${params.toString()}`, {
        headers: getAuthHeaders(),
      });
      if (!res.ok) {
        throw new Error(`Audit logs HTTP error: ${res.status}`);
      }
      const data = await res.json();
      ops.auditData = data.audit_logs || [];
      renderAuditLogs(ops.auditData);
    } catch (err) {
      console.warn("[Atlas Ops] Audit logs notice:", err.message);
      renderAuditError();
    } finally {
      ops.isLoadingAudits = false;
    }
  }

  function renderAuditLogs(logs) {
    if (!auditTableBody) return;
    if (!logs || logs.length === 0) {
      auditTableBody.innerHTML = `
        <tr>
          <td colspan="5" class="table-empty">
            No audit records found matching the current filters.
          </td>
        </tr>
      `;
      return;
    }

    const rowsHtml = logs
      .map((log) => {
        const timeFormatted = formatTimestamp(log.created_at);
        const eventClass = getEventPillClass(log.event_type);
        const actor = escapeHtml(log.actor_role || "system");
        const resourceId = log.resource_id ? escapeHtml(log.resource_id) : "-";
        const details = log.details || {};
        const keyCount = Object.keys(details).length;
        const jsonFormatted = escapeHtml(JSON.stringify(details, null, 2));

        return `
          <tr>
            <td class="tabular-nums" style="white-space: nowrap;">${timeFormatted}</td>
            <td>
              <span class="${eventClass}">${escapeHtml(log.event_type)}</span>
            </td>
            <td>
              <span class="actor-pill">${actor}</span>
            </td>
            <td>
              <span class="resource-code" title="${resourceId}">${resourceId}</span>
            </td>
            <td>
              <details class="audit-details">
                <summary>JSON (${keyCount} ${keyCount === 1 ? "field" : "fields"})</summary>
                <pre><code>${jsonFormatted}</code></pre>
              </details>
            </td>
          </tr>
        `;
      })
      .join("");

    auditTableBody.innerHTML = rowsHtml;
  }

  function renderAuditError() {
    if (!auditTableBody) return;
    auditTableBody.innerHTML = `
      <tr>
        <td colspan="5" class="table-error">
          Failed to load audit logs. Please ensure the backend is running and reachable.
        </td>
      </tr>
    `;
  }

  async function refreshOps() {
    if (refreshOpsBtn) refreshOpsBtn.classList.add("is-loading");
    try {
      await Promise.all([fetchHealth(), fetchAuditLogs()]);
    } finally {
      if (refreshOpsBtn) refreshOpsBtn.classList.remove("is-loading");
    }
  }

  function initOps() {
    // Refresh button click
    if (refreshOpsBtn) {
      refreshOpsBtn.addEventListener("click", refreshOps);
    }

    // Header health pill click to refresh
    if (headerHealth) {
      headerHealth.style.cursor = "pointer";
      headerHealth.addEventListener("click", fetchHealth);
    }

    // Filter dropdowns
    if (auditEventFilter) {
      auditEventFilter.addEventListener("change", fetchAuditLogs);
    }
    if (auditLimitFilter) {
      auditLimitFilter.addEventListener("change", fetchAuditLogs);
    }

    // Fetch initial health on shell load
    fetchHealth();

    // If initial view is ops, fetch audit logs
    if (state.currentView === "ops") {
      fetchAuditLogs();
    }

    // React to view changes
    window.addEventListener("atlas:view-change", (e) => {
      if (e.detail && e.detail.view === "ops") {
        fetchHealth();
        fetchAuditLogs();
      }
    });

    // React to role changes
    window.addEventListener("atlas:role-change", () => {
      if (state.currentView === "ops") {
        fetchAuditLogs();
      }
    });
  }

  // ── Document Management View Module ─────────────────────────────────────────
  const docs = {
    list: [],
    isLoading: false,
    isUploading: false,
    pollTimer: null,
  };

  // DOM Elements for Docs View
  const refreshDocsBtn = document.getElementById("refresh-docs-btn");
  const docFileInput = document.getElementById("doc-file-input");
  const uploadDropzone = document.getElementById("upload-dropzone");
  const uploadProgressCard = document.getElementById("upload-progress-card");
  const uploadFilename = document.getElementById("upload-filename");
  const uploadFilesize = document.getElementById("upload-filesize");
  const uploadStatusText = document.getElementById("upload-status-text");
  const uploadPercent = document.getElementById("upload-percent");
  const uploadProgressFill = document.getElementById("upload-progress-fill");
  const docsCountBadge = document.getElementById("docs-count-badge");
  const docsTableBody = document.getElementById("docs-table-body");

  function formatFileSize(bytes) {
    if (!bytes || bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    const val = (bytes / Math.pow(k, i)).toFixed(i === 0 ? 0 : 1);
    return `${val} ${sizes[i]}`;
  }

  function getFileExt(filename) {
    if (!filename) return "FILE";
    const parts = filename.split(".");
    return parts.length > 1 ? parts.pop().toUpperCase() : "FILE";
  }

  function getDocStatusPill(status, errorMessage) {
    switch (status) {
      case "indexed":
        return `<span class="service-pill pill-emerald"><span class="status-dot dot-emerald"></span><span class="status-text">INDEXED</span></span>`;
      case "processing":
        return `<span class="service-pill pill-amber"><span class="status-dot dot-amber"></span><span class="status-text">PROCESSING</span></span>`;
      case "pending_upload":
        return `<span class="service-pill pill-amber"><span class="status-dot dot-amber"></span><span class="status-text">PENDING</span></span>`;
      case "failed":
        const tooltip = errorMessage ? ` title="${escapeHtml(errorMessage)}"` : "";
        return `<span class="service-pill pill-rose"${tooltip}><span class="status-dot dot-rose"></span><span class="status-text">FAILED</span></span>`;
      default:
        return `<span class="service-pill pill-neutral"><span class="status-dot dot-neutral"></span><span class="status-text">${escapeHtml(status || "UNKNOWN")}</span></span>`;
    }
  }

  async function fetchDocuments() {
    if (!docsTableBody) return;
    docs.isLoading = true;

    try {
      const res = await fetch("/api/documents", {
        headers: getAuthHeaders(),
      });
      if (!res.ok) {
        throw new Error(`Failed to load documents: HTTP ${res.status}`);
      }
      const data = await res.json();
      docs.list = data.documents || [];
      renderDocuments(docs.list);
      checkAndManagePolling();
    } catch (err) {
      console.warn("[Atlas Docs] Failed to load documents:", err.message);
      renderDocumentsError();
    } finally {
      docs.isLoading = false;
    }
  }

  function checkAndManagePolling() {
    const hasProcessing = docs.list.some(
      (d) => d.status === "processing" || d.status === "pending_upload"
    );

    if (hasProcessing) {
      if (!docs.pollTimer) {
        docs.pollTimer = setTimeout(async () => {
          docs.pollTimer = null;
          await fetchDocuments();
        }, 2000);
      }
    } else if (docs.pollTimer) {
      clearTimeout(docs.pollTimer);
      docs.pollTimer = null;
    }
  }

  function renderDocuments(documentList) {
    if (!docsTableBody) return;

    if (docsCountBadge) {
      docsCountBadge.textContent = String(documentList.length);
    }

    if (documentList.length === 0) {
      docsTableBody.innerHTML = `
        <tr>
          <td colspan="6" class="table-empty">
            No documents uploaded yet. Drag &amp; drop a PDF, TXT, MD, or DOCX file above to begin.
          </td>
        </tr>
      `;
      return;
    }

    const rowsHtml = documentList
      .map((doc) => {
        const ext = getFileExt(doc.filename);
        const sizeFormatted = formatFileSize(doc.file_size);
        const timeFormatted = formatTimestamp(doc.created_at);
        const statusBadge = getDocStatusPill(doc.status, doc.error_message);
        const chunkCount = doc.chunk_count !== undefined ? doc.chunk_count : 0;
        const filename = escapeHtml(doc.filename);
        const docId = escapeHtml(doc.id);

        return `
          <tr id="doc-row-${docId}">
            <td>
              <div class="doc-name-cell">
                <span class="doc-ext-badge">${ext}</span>
                <span class="doc-name-text" title="${filename}">${filename}</span>
              </div>
            </td>
            <td class="font-mono tabular-nums">${sizeFormatted}</td>
            <td>${statusBadge}</td>
            <td class="font-mono tabular-nums">${chunkCount}</td>
            <td class="font-mono tabular-nums" style="white-space: nowrap;">${timeFormatted}</td>
            <td style="text-align: right;" id="doc-actions-${docId}">
              <button class="btn-icon-danger btn-delete-doc" data-id="${docId}" data-name="${filename}" title="Delete document atomically">
                <svg class="delete-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor">
                  <path d="M2.5 4.5h11M5.5 4.5V2.5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v2M12.5 4.5v9a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1v-9M6.5 7.5v4M9.5 7.5v4"/>
                </svg>
              </button>
            </td>
          </tr>
        `;
      })
      .join("");

    docsTableBody.innerHTML = rowsHtml;
  }

  function renderDocumentsError() {
    if (!docsTableBody) return;
    docsTableBody.innerHTML = `
      <tr>
        <td colspan="6" class="table-error">
          Failed to load documents. Please ensure the backend is running and reachable.
        </td>
      </tr>
    `;
  }

  function handleFileSelected(file) {
    if (!file) return;

    const allowedExts = [".pdf", ".txt", ".md", ".docx"];
    const ext = "." + file.name.split(".").pop().toLowerCase();
    if (!allowedExts.includes(ext)) {
      alert(`Unsupported file format. Please upload: ${allowedExts.join(", ")}`);
      return;
    }

    uploadDocumentDirect(file);
  }

  function getStorageLabel() {
    return (
      (ops.healthData && ops.healthData.services && ops.healthData.services.storage_label) ||
      "Object Storage"
    );
  }

  async function uploadDocumentDirect(file) {
    if (docs.isUploading) return;
    docs.isUploading = true;

    // Single Content-Type resolution
    const resolvedContentType = file.type || "application/octet-stream";

    // Show Progress Card
    if (uploadProgressCard) {
      uploadProgressCard.style.display = "flex";
      uploadProgressFill.style.width = "0%";
      uploadProgressFill.classList.remove("is-failed");
      uploadFilename.textContent = file.name;
      uploadFilesize.textContent = formatFileSize(file.size);
      uploadStatusText.textContent = "Requesting presigned upload URL…";
      uploadPercent.textContent = "0%";
    }

    try {
      // Step 1: Request upload URL from FastAPI
      const urlRes = await fetch("/api/documents/upload-url", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify({
          filename: file.name,
          content_type: resolvedContentType,
          file_size: file.size,
        }),
      });

      if (!urlRes.ok) {
        const errorData = await urlRes.json().catch(() => ({}));
        throw new Error(errorData.detail || `Upload URL request failed (${urlRes.status})`);
      }

      const { document_id, upload_url } = await urlRes.json();

      // Step 2: Direct PUT to Object Storage using XMLHttpRequest for progress
      uploadStatusText.textContent = `Uploading directly to ${getStorageLabel()}…`;

      const xhr = new XMLHttpRequest();
      xhr.open("PUT", upload_url);
      xhr.setRequestHeader("Content-Type", resolvedContentType);

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          const pct = Math.round((e.loaded / e.total) * 100);
          uploadPercent.textContent = `${pct}%`;
          uploadProgressFill.style.width = `${pct}%`;
        }
      };

      xhr.onload = async () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          uploadStatusText.textContent = "Triggering ingestion pipeline…";
          uploadPercent.textContent = "100%";
          uploadProgressFill.style.width = "100%";

          // Step 3: Trigger ingestion background task
          try {
            const ingestRes = await fetch(`/api/documents/${document_id}/ingest`, {
              method: "POST",
              headers: getAuthHeaders(),
            });
            if (!ingestRes.ok) {
              throw new Error(`Ingest trigger failed: ${ingestRes.status}`);
            }
            uploadStatusText.textContent = "Processing & indexing chunks…";

            // Refresh document list and start polling
            await fetchDocuments();

            setTimeout(() => {
              if (uploadProgressCard) {
                uploadProgressCard.style.display = "none";
                uploadProgressFill.style.width = "0%";
              }
              docs.isUploading = false;
            }, 1800);
          } catch (ingestErr) {
            console.warn("[Atlas Docs] Ingestion trigger warning:", ingestErr.message);
            uploadStatusText.textContent = "Ingest trigger failed";
            uploadProgressFill.classList.add("is-failed");
            docs.isUploading = false;
          }
        } else {
          uploadStatusText.textContent = `${getStorageLabel()} upload error (${xhr.status})`;
          uploadProgressFill.classList.add("is-failed");
          docs.isUploading = false;
        }
      };

      xhr.onerror = () => {
        uploadStatusText.textContent = `Network error connecting to ${getStorageLabel()}`;
        uploadProgressFill.classList.add("is-failed");
        docs.isUploading = false;
      };

      xhr.send(file);
    } catch (err) {
      console.warn("[Atlas Docs] Direct upload failed:", err.message);
      if (uploadStatusText) uploadStatusText.textContent = err.message;
      if (uploadProgressFill) uploadProgressFill.classList.add("is-failed");
      docs.isUploading = false;
    }
  }

  async function executeAtomicDelete(documentId) {
    const actionCell = document.getElementById(`doc-actions-${documentId}`);
    if (actionCell) {
      actionCell.innerHTML = `<span class="service-meta font-mono" style="font-size:0.75rem;">Deleting…</span>`;
    }

    try {
      const res = await fetch(`/api/documents/${documentId}`, {
        method: "DELETE",
        headers: getAuthHeaders(),
      });
      if (!res.ok) {
        throw new Error(`Delete failed: HTTP ${res.status}`);
      }
      await fetchDocuments();

      // If ops view or audit log is loaded, refresh audits too
      if (typeof fetchAuditLogs === "function") {
        fetchAuditLogs();
      }
    } catch (err) {
      console.warn("[Atlas Docs] Delete failed:", err.message);
      alert(`Failed to delete document: ${err.message}`);
      await fetchDocuments();
    }
  }

  function initDocs() {
    // Refresh button
    if (refreshDocsBtn) {
      refreshDocsBtn.addEventListener("click", async () => {
        refreshDocsBtn.classList.add("is-loading");
        try {
          await fetchDocuments();
        } finally {
          refreshDocsBtn.classList.remove("is-loading");
        }
      });
    }

    // Dropzone click -> trigger file input
    if (uploadDropzone && docFileInput) {
      uploadDropzone.addEventListener("click", () => {
        docFileInput.click();
      });

      uploadDropzone.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          docFileInput.click();
        }
      });

      // Drag and Drop listeners
      uploadDropzone.addEventListener("dragover", (e) => {
        e.preventDefault();
        uploadDropzone.classList.add("is-dragover");
      });

      uploadDropzone.addEventListener("dragleave", (e) => {
        e.preventDefault();
        uploadDropzone.classList.remove("is-dragover");
      });

      uploadDropzone.addEventListener("drop", (e) => {
        e.preventDefault();
        uploadDropzone.classList.remove("is-dragover");
        if (e.dataTransfer && e.dataTransfer.files.length > 0) {
          handleFileSelected(e.dataTransfer.files[0]);
        }
      });

      docFileInput.addEventListener("change", (e) => {
        if (e.target.files && e.target.files.length > 0) {
          handleFileSelected(e.target.files[0]);
          e.target.value = ""; // Reset for next selection
        }
      });
    }

    // Table delegate for inline delete confirmation
    if (docsTableBody) {
      docsTableBody.addEventListener("click", (e) => {
        const deleteBtn = e.target.closest(".btn-delete-doc");
        if (deleteBtn) {
          const docId = deleteBtn.getAttribute("data-id");
          const actionCell = document.getElementById(`doc-actions-${docId}`);
          if (actionCell) {
            actionCell.innerHTML = `
              <div class="delete-confirm-group">
                <span class="delete-confirm-prompt">Delete?</span>
                <button class="btn-delete-confirm" data-id="${docId}">Yes</button>
                <button class="btn-delete-cancel" data-id="${docId}">No</button>
              </div>
            `;
          }
          return;
        }

        const confirmBtn = e.target.closest(".btn-delete-confirm");
        if (confirmBtn) {
          const docId = confirmBtn.getAttribute("data-id");
          executeAtomicDelete(docId);
          return;
        }

        const cancelBtn = e.target.closest(".btn-delete-cancel");
        if (cancelBtn) {
          const docId = cancelBtn.getAttribute("data-id");
          const actionCell = document.getElementById(`doc-actions-${docId}`);
          if (actionCell) {
            actionCell.innerHTML = `
              <button class="btn-icon-danger btn-delete-doc" data-id="${docId}" title="Delete document atomically">
                <svg class="delete-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor">
                  <path d="M2.5 4.5h11M5.5 4.5V2.5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v2M12.5 4.5v9a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1v-9M6.5 7.5v4M9.5 7.5v4"/>
                </svg>
              </button>
            `;
          }
        }
      });
    }

    // Initial load if active
    if (state.currentView === "documents") {
      fetchDocuments();
    }

    // React to view changes
    window.addEventListener("atlas:view-change", (e) => {
      if (e.detail && e.detail.view === "documents") {
        fetchDocuments();
      } else if (docs.pollTimer) {
        clearTimeout(docs.pollTimer);
        docs.pollTimer = null;
      }
    });

    // React to role changes
    window.addEventListener("atlas:role-change", () => {
      if (state.currentView === "documents") {
        fetchDocuments();
      }
    });
  }

  // ── Chat & Knowledge Q&A View Module ────────────────────────────────────────
  const chat = {
    currentSessionId: null,
    sessions: [],
    messages: [],
    isLoading: false,
    messageStore: new Map(),
  };

  // DOM Elements for Chat View
  const chatLayout = document.getElementById("chat-layout");
  const chatSidebar = document.getElementById("chat-sidebar");
  const btnNewChat = document.getElementById("btn-new-chat");
  const btnToggleSidebar = document.getElementById("btn-toggle-sidebar");
  const btnExpandSidebar = document.getElementById("btn-expand-sidebar");
  const chatSessionsList = document.getElementById("chat-sessions-list");
  const sessionCountBadge = document.getElementById("session-count-badge");

  const chatMessages = document.getElementById("chat-messages");
  const chatEmptyState = document.getElementById("chat-empty-state");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const chatSendBtn = document.getElementById("chat-send-btn");

  const sourceInspector = document.getElementById("source-inspector");
  const inspectorContent = document.getElementById("inspector-content");
  const btnCloseInspector = document.getElementById("btn-close-inspector");

  function generateSessionId() {
    return "sess-" + Math.random().toString(36).substring(2, 10);
  }

  function formatCitationBadges(text) {
    if (!text) return "";
    let formatted = escapeHtml(text);
    formatted = formatted.replace(/\[(\d+)\]/g, (match, num) => {
      return `<button class="citation-badge" data-cite-index="${num}" title="Inspect source citation ${match}">${match}</button>`;
    });
    return formatted;
  }

  async function fetchSessions() {
    if (!chatSessionsList) return;

    try {
      const res = await fetch("/api/chat/sessions", {
        headers: getAuthHeaders(),
      });
      if (!res.ok) {
        throw new Error(`Failed to load sessions: ${res.status}`);
      }
      const data = await res.json();
      chat.sessions = data.sessions || [];
      renderSessions(chat.sessions);
    } catch (err) {
      console.warn("[Atlas Chat] Failed to load sessions:", err.message);
      chatSessionsList.innerHTML = `<div class="sessions-empty">Could not load sessions.</div>`;
    }
  }

  function renderSessions(sessionsList) {
    if (!chatSessionsList) return;

    if (sessionCountBadge) {
      sessionCountBadge.textContent = String(sessionsList.length);
    }

    if (sessionsList.length === 0) {
      chatSessionsList.innerHTML = `<div class="sessions-empty">No conversation history yet.</div>`;
      return;
    }

    const html = sessionsList
      .map((s) => {
        const isActive = s.session_id === chat.currentSessionId;
        const timeFormatted = formatTimestamp(s.updated_at);
        const title = escapeHtml(s.title || s.last_message || "New conversation");
        const sid = escapeHtml(s.session_id);

        return `
          <div class="session-item ${isActive ? "active" : ""}" data-session-id="${sid}" role="button" tabindex="0">
            <span class="session-item-title" title="${title}">${title}</span>
            <div class="session-item-meta">
              <span class="font-mono tabular-nums">${timeFormatted}</span>
              <span class="font-mono tabular-nums">${s.message_count} ${s.message_count === 1 ? "msg" : "msgs"}</span>
            </div>
          </div>
        `;
      })
      .join("");

    chatSessionsList.innerHTML = html;
  }

  async function loadSession(sessionId) {
    chat.currentSessionId = sessionId;
    renderSessions(chat.sessions);

    if (!chatMessages) return;
    chatMessages.innerHTML = `<div class="sessions-loading">Loading conversation history…</div>`;
    closeInspector();

    try {
      const res = await fetch(`/api/chat/sessions/${sessionId}/messages`, {
        headers: getAuthHeaders(),
      });
      if (!res.ok) {
        throw new Error(`Failed to load messages: HTTP ${res.status}`);
      }
      const data = await res.json();
      chat.messages = data.messages || [];

      chatMessages.innerHTML = "";
      if (chat.messages.length === 0) {
        if (chatEmptyState) {
          chatMessages.appendChild(chatEmptyState);
          chatEmptyState.style.display = "flex";
        }
      } else {
        chat.messages.forEach((msg) => {
          renderMessage(msg);
        });
        scrollChatToBottom();
      }
    } catch (err) {
      console.warn("[Atlas Chat] Failed to load session messages:", err.message);
      chatMessages.innerHTML = `<div class="table-error">Failed to load conversation history.</div>`;
    }
  }

  function startNewChat() {
    chat.currentSessionId = generateSessionId();
    chat.messages = [];
    closeInspector();
    renderSessions(chat.sessions);

    if (chatMessages && chatEmptyState) {
      chatMessages.innerHTML = "";
      chatMessages.appendChild(chatEmptyState);
      chatEmptyState.style.display = "flex";
    }

    if (chatInput) {
      chatInput.value = "";
      chatInput.focus();
      updateSendButtonState();
    }
  }

  function renderMessage(msg) {
    if (!chatMessages) return;

    if (chatEmptyState && chatEmptyState.parentElement === chatMessages) {
      chatEmptyState.style.display = "none";
    }

    const turn = document.createElement("div");
    turn.className = `message-turn ${msg.role}`;
    const msgId = msg.id || ("tmp-" + Math.random().toString(36).substring(2, 9));
    turn.id = `msg-${msgId}`;

    if (msg.role === "user") {
      turn.innerHTML = `
        <div class="message-bubble-user">
          <div class="message-content">${escapeHtml(msg.content)}</div>
        </div>
      `;
    } else {
      chat.messageStore.set(String(msgId), msg);

      const citations = Array.isArray(msg.citations) ? msg.citations : [];
      const hasCitations = citations.length > 0;
      const formattedContent = formatCitationBadges(msg.content);

      let piiNoticeHtml = "";
      if (msg.pii_redacted) {
        piiNoticeHtml = `
          <div class="pii-notice-banner">
            <svg class="banner-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor">
              <path d="M8 1.5l5.5 2.5v4c0 3.5-2.5 6-5.5 6.5C5 14 2.5 11.5 2.5 8V4L8 1.5z"/>
              <path d="M8 5.5v3M8 10.5h.01"/>
            </svg>
            <span>Output Guardrail: Sensitive PII was redacted from this response in accordance with enterprise data protection policy.</span>
          </div>
        `;
      }

      let blockedCardHtml = "";
      if (msg.status === "blocked") {
        blockedCardHtml = `
          <div class="blocked-state-card">
            <svg class="banner-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor">
              <circle cx="8" cy="8" r="6"/>
              <path d="M4 12l8-8"/>
            </svg>
            <div>
              <strong>Security Guardrail Intercept</strong>
              <span>This request was blocked by input validation rules (prompt injection attempt or prohibited enterprise topic detected).</span>
            </div>
          </div>
        `;
      }

      let pendingApprovalHtml = "";
      if (msg.status === "pending_approval") {
        const approvalId = msg.hitl_approval_id || "pending";
        pendingApprovalHtml = `
          <div class="pending-approval-card">
            <svg class="banner-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor">
              <circle cx="8" cy="8" r="6"/>
              <path d="M8 4.5v3.5l2.5 1.5"/>
            </svg>
            <div>
              <strong>Pending Supervisor Review (HITL)</strong>
              <span>This query triggered a sensitive action or fell below retrieval confidence. It has been queued in the Approvals queue.</span>
              <div><span class="approval-id-tag">Approval ID: ${escapeHtml(approvalId)}</span></div>
            </div>
          </div>
        `;
      }

      let citationsBarHtml = "";
      if (hasCitations) {
        const pills = citations
          .map((c, idx) => {
            const citeNum = idx + 1;
            const scoreFormatted = (typeof c.score === "number") ? c.score.toFixed(2) : "";
            return `
              <button class="citation-pill-btn" data-msg-id="${msgId}" data-cite-index="${citeNum}" title="${escapeHtml(c.filename)}">
                <span>[${citeNum}] ${escapeHtml(c.filename)}</span>
                ${scoreFormatted ? `<span class="inspector-score font-mono tabular-nums">${scoreFormatted}</span>` : ""}
              </button>
            `;
          })
          .join("");

        citationsBarHtml = `
          <div class="message-citations-bar">
            <span>Sources:</span>
            ${pills}
          </div>
        `;
      }

      const feedbackBarHtml = `
        <div class="message-actions-bar">
          <span class="font-mono tabular-nums" style="font-size:0.7rem;">${formatTimestamp(msg.created_at)}</span>
          <div class="feedback-buttons">
            <button class="btn-feedback" data-msg-id="${msgId}" data-rating="1" title="Helpful grounded answer">
              <svg class="feedback-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor">
                <path d="M4.5 7.5v6M4.5 13.5H2a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1h2.5m0 0l3-5.5a1.5 1.5 0 0 1 2.5 1.5l-.8 4h3.3a1.5 1.5 0 0 1 1.5 1.7l-.8 4.5a1.5 1.5 0 0 1-1.5 1.3H4.5"/>
              </svg>
            </button>
            <button class="btn-feedback" data-msg-id="${msgId}" data-rating="-1" title="Unhelpful or inaccurate">
              <svg class="feedback-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor">
                <path d="M4.5 8.5v-6M4.5 2.5H2a1 1 0 0 0-1 1v4a1 1 0 0 0 1 1h2.5m0 0l3 5.5a1.5 1.5 0 0 0 2.5-1.5l-.8-4h3.3a1.5 1.5 0 0 0 1.5-1.7l-.8-4.5a1.5 1.5 0 0 0-1.5-1.3H4.5"/>
              </svg>
            </button>
          </div>
        </div>
      `;

      turn.innerHTML = `
        <div class="message-bubble-assistant">
          ${blockedCardHtml}
          ${pendingApprovalHtml}
          ${piiNoticeHtml}
          ${msg.content ? `<div class="message-content">${formattedContent}</div>` : ""}
          ${citationsBarHtml}
          ${feedbackBarHtml}
        </div>
      `;
    }

    chatMessages.appendChild(turn);
  }

  function scrollChatToBottom() {
    if (chatMessages) {
      chatMessages.scrollTop = chatMessages.scrollHeight;
    }
  }

  async function sendMessage(queryText) {
    const query = (queryText || (chatInput ? chatInput.value : "")).trim();
    if (!query || chat.isLoading) return;

    if (!chat.currentSessionId) {
      chat.currentSessionId = generateSessionId();
    }

    if (chatInput) {
      chatInput.value = "";
      updateSendButtonState();
    }

    renderMessage({
      role: "user",
      content: query,
      created_at: new Date().toISOString(),
    });

    const loadingTurn = document.createElement("div");
    loadingTurn.className = "message-turn assistant is-thinking";
    loadingTurn.id = "assistant-loading-turn";
    loadingTurn.innerHTML = `
      <div class="message-bubble-assistant">
        <div class="message-content font-mono" style="font-size:0.8rem; color:var(--text-muted); display:flex; align-items:center; gap:0.5rem;">
          <svg class="btn-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" style="animation: spin 0.8s linear infinite;">
            <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9L14 2"/>
            <path d="M14 6V2h-4"/>
          </svg>
          <span>Searching vector database &amp; generating grounded synthesis…</span>
        </div>
      </div>
    `;
    chatMessages.appendChild(loadingTurn);
    scrollChatToBottom();

    chat.isLoading = true;

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify({
          session_id: chat.currentSessionId,
          query: query,
        }),
      });

      const loader = document.getElementById("assistant-loading-turn");
      if (loader) loader.remove();

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        throw new Error(errorData.detail || `Chat query failed (HTTP ${res.status})`);
      }

      const chatData = await res.json();

      renderMessage({
        id: chatData.message_id,
        role: "assistant",
        content: chatData.answer,
        citations: chatData.citations || [],
        status: chatData.status,
        pii_redacted: Boolean(chatData.pii_redacted),
        hitl_approval_id: chatData.hitl_approval_id,
        trace_id: chatData.trace_id,
        created_at: new Date().toISOString(),
      });

      scrollChatToBottom();
      fetchSessions();
    } catch (err) {
      console.warn("[Atlas Chat] Query failed:", err.message);
      const loader = document.getElementById("assistant-loading-turn");
      if (loader) loader.remove();

      renderMessage({
        role: "assistant",
        content: `Unable to complete request: ${err.message}`,
        status: "blocked",
        created_at: new Date().toISOString(),
      });
      scrollChatToBottom();
    } finally {
      chat.isLoading = false;
      updateSendButtonState();
      if (chatInput) chatInput.focus();
    }
  }

  function openSourceInspector(citations, highlightIndex = null) {
    if (!sourceInspector || !inspectorContent) return;

    if (!citations || citations.length === 0) {
      inspectorContent.innerHTML = `<div class="sessions-empty">No source citations available for this turn.</div>`;
      sourceInspector.style.display = "flex";
      return;
    }

    const cardsHtml = citations
      .map((c, idx) => {
        const citeIndex = idx + 1;
        const score = typeof c.score === "number" ? c.score.toFixed(3) : "N/A";
        const isHighlight = highlightIndex !== null && citeIndex === Number(highlightIndex);

        return `
          <div class="inspector-card" style="${isHighlight ? "border-color: var(--border-focus);" : ""}">
            <div class="inspector-card-header">
              <span class="inspector-doc-name">[${citeIndex}] ${escapeHtml(c.filename)}</span>
              <span class="inspector-score font-mono tabular-nums">similarity: ${score}</span>
            </div>
            <div class="inspector-meta font-mono">chunk_index: ${c.chunk_index} · doc_id: ${escapeHtml(c.document_id || "-")}</div>
            <div class="inspector-snippet">${escapeHtml(c.snippet || "No snippet content provided.")}</div>
          </div>
        `;
      })
      .join("");

    inspectorContent.innerHTML = cardsHtml;
    sourceInspector.style.display = "flex";
  }

  function closeInspector() {
    if (sourceInspector) {
      sourceInspector.style.display = "none";
    }
  }

  async function submitFeedback(messageId, rating, btnEl) {
    if (!messageId) return;

    try {
      const res = await fetch("/api/chat/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify({
          message_id: messageId,
          rating: rating,
        }),
      });

      if (!res.ok) {
        throw new Error(`Feedback failed: HTTP ${res.status}`);
      }

      const parent = btnEl.closest(".feedback-buttons");
      if (parent) {
        parent.querySelectorAll(".btn-feedback").forEach((b) => {
          b.classList.remove("active-up", "active-down");
        });
        btnEl.classList.add(rating === 1 ? "active-up" : "active-down");
      }
    } catch (err) {
      console.warn("[Atlas Chat] Feedback submission failed:", err.message);
    }
  }

  function updateSendButtonState() {
    if (!chatSendBtn || !chatInput) return;
    const hasText = chatInput.value.trim().length > 0;
    chatSendBtn.disabled = !hasText || chat.isLoading;
  }

  function initChat() {
    if (btnNewChat) {
      btnNewChat.addEventListener("click", startNewChat);
    }

    if (btnToggleSidebar && chatLayout) {
      btnToggleSidebar.addEventListener("click", () => {
        chatLayout.classList.add("sidebar-collapsed");
        if (btnExpandSidebar) btnExpandSidebar.style.display = "inline-flex";
      });
    }

    if (btnExpandSidebar && chatLayout) {
      btnExpandSidebar.addEventListener("click", () => {
        chatLayout.classList.remove("sidebar-collapsed");
        btnExpandSidebar.style.display = "none";
      });
    }

    if (btnCloseInspector) {
      btnCloseInspector.addEventListener("click", closeInspector);
    }

    if (chatForm && chatInput) {
      chatForm.addEventListener("submit", (e) => {
        e.preventDefault();
        sendMessage();
      });

      chatInput.addEventListener("input", () => {
        updateSendButtonState();
        chatInput.style.height = "auto";
        chatInput.style.height = Math.min(chatInput.scrollHeight, 160) + "px";
      });

      chatInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          sendMessage();
        }
      });
    }

    document.addEventListener("click", (e) => {
      const chip = e.target.closest(".suggestion-chip");
      if (chip) {
        const query = chip.getAttribute("data-query");
        if (query) sendMessage(query);
      }
    });

    if (chatSessionsList) {
      chatSessionsList.addEventListener("click", (e) => {
        const sessionItem = e.target.closest(".session-item");
        if (sessionItem) {
          const sid = sessionItem.getAttribute("data-session-id");
          if (sid && sid !== chat.currentSessionId) {
            loadSession(sid);
          }
        }
      });
    }

    if (chatMessages) {
      chatMessages.addEventListener("click", (e) => {
        const citationBadge = e.target.closest(".citation-badge");
        if (citationBadge) {
          e.preventDefault();
          const citeIndex = citationBadge.getAttribute("data-cite-index");
          const turn = citationBadge.closest(".message-turn");
          const msgId = turn ? turn.id.replace("msg-", "") : null;
          const msg = msgId ? chat.messageStore.get(msgId) : null;
          if (msg && msg.citations) {
            openSourceInspector(msg.citations, citeIndex);
          }
          return;
        }

        const citationPill = e.target.closest(".citation-pill-btn");
        if (citationPill) {
          e.preventDefault();
          const citeIndex = citationPill.getAttribute("data-cite-index");
          const msgId = citationPill.getAttribute("data-msg-id");
          const msg = msgId ? chat.messageStore.get(msgId) : null;
          if (msg && msg.citations) {
            openSourceInspector(msg.citations, citeIndex);
          }
          return;
        }

        const feedbackBtn = e.target.closest(".btn-feedback");
        if (feedbackBtn) {
          e.preventDefault();
          const msgId = feedbackBtn.getAttribute("data-msg-id");
          const rating = parseInt(feedbackBtn.getAttribute("data-rating"), 10);
          submitFeedback(msgId, rating, feedbackBtn);
        }
      });
    }

    if (!chat.currentSessionId) {
      chat.currentSessionId = generateSessionId();
    }

    fetchSessions();

    window.addEventListener("atlas:view-change", (e) => {
      if (e.detail && e.detail.view === "chat") {
        fetchSessions();
      }
    });
  }

  // ── Human-in-the-Loop (HITL) Approvals View Module ──────────────────────────
  const approvals = {
    list: [],
    currentFilter: "pending",
    isLoading: false,
    pendingCount: 0,
  };

  // DOM Elements for Approvals View
  const refreshApprovalsBtn = document.getElementById("refresh-approvals-btn");
  const approvalRoleNotice = document.getElementById("approval-role-notice");
  const pendingTabBadge = document.getElementById("pending-tab-badge");
  const approvalsBadge = document.getElementById("approvals-badge");
  const filterTabBtns = document.querySelectorAll(".filter-tab-btn");
  const approvalsQueue = document.getElementById("approvals-queue");

  function getApprovalStatusPill(status) {
    switch (status) {
      case "approved":
        return `<span class="service-pill pill-emerald"><span class="status-dot dot-emerald"></span><span class="status-text">APPROVED</span></span>`;
      case "edited_and_approved":
        return `<span class="service-pill pill-emerald"><span class="status-dot dot-emerald"></span><span class="status-text">EDITED &amp; APPROVED</span></span>`;
      case "pending":
        return `<span class="service-pill pill-amber"><span class="status-dot dot-amber"></span><span class="status-text">PENDING REVIEW</span></span>`;
      case "rejected":
        return `<span class="service-pill pill-rose"><span class="status-dot dot-rose"></span><span class="status-text">REJECTED</span></span>`;
      default:
        return `<span class="service-pill pill-neutral"><span class="status-dot dot-neutral"></span><span class="status-text">${escapeHtml(status || "UNKNOWN")}</span></span>`;
    }
  }

  async function updatePendingApprovalsCount() {
    try {
      const res = await fetch("/api/approvals?status=pending", {
        headers: getAuthHeaders(),
      });
      if (res.ok) {
        const data = await res.json();
        const count = (data.approvals || []).length;
        approvals.pendingCount = count;

        if (pendingTabBadge) pendingTabBadge.textContent = String(count);
        if (approvalsBadge) {
          approvalsBadge.textContent = String(count);
          approvalsBadge.classList.toggle("has-pending", count > 0);
        }
      }
    } catch (err) {
      console.warn("[Atlas Approvals] Pending count check notice:", err.message);
    }
  }

  async function fetchApprovals(statusFilter = null) {
    if (!approvalsQueue) return;
    approvals.isLoading = true;

    const filter = statusFilter !== null ? statusFilter : approvals.currentFilter;
    approvals.currentFilter = filter;

    if (filterTabBtns) {
      filterTabBtns.forEach((btn) => {
        const s = btn.getAttribute("data-status");
        btn.classList.toggle("active", s === filter);
      });
    }

    const url = filter && filter !== "all"
      ? `/api/approvals?status=${encodeURIComponent(filter)}`
      : "/api/approvals";

    try {
      const res = await fetch(url, {
        headers: getAuthHeaders(),
      });
      if (!res.ok) {
        throw new Error(`Failed to load approvals: HTTP ${res.status}`);
      }
      const data = await res.json();
      approvals.list = data.approvals || [];
      renderApprovals(approvals.list);
    } catch (err) {
      console.warn("[Atlas Approvals] Failed to fetch approvals:", err.message);
      renderApprovalsError();
    } finally {
      approvals.isLoading = false;
      updatePendingApprovalsCount();
    }
  }

  function renderApprovals(tickets) {
    if (!approvalsQueue) return;

    const isEmployee = state.currentRole === "employee";
    if (approvalRoleNotice) {
      approvalRoleNotice.style.display = isEmployee ? "flex" : "none";
    }

    if (tickets.length === 0) {
      approvalsQueue.innerHTML = `
        <div class="scaffold-placeholder">
          <p>No approval tickets found with status: <strong>${escapeHtml(approvals.currentFilter)}</strong>.</p>
        </div>
      `;
      return;
    }

    const cardsHtml = tickets
      .map((item) => {
        const id = escapeHtml(item.id);
        const sid = escapeHtml(item.session_id);
        const timeFormatted = formatTimestamp(item.created_at);
        const score = typeof item.max_similarity_score === "number"
          ? item.max_similarity_score.toFixed(3)
          : "N/A";
        const triggerReason = escapeHtml(item.trigger_reason || "low_confidence");
        const statusBadge = getApprovalStatusPill(item.status);
        const citations = Array.isArray(item.citations) ? item.citations : [];
        const isPending = item.status === "pending";

        let citationsHtml = "";
        if (citations.length > 0) {
          const cards = citations
            .map((c, idx) => {
              const cScore = typeof c.score === "number" ? c.score.toFixed(3) : "N/A";
              return `
                <div class="inspector-card">
                  <div class="inspector-card-header">
                    <span class="inspector-doc-name">[${idx + 1}] ${escapeHtml(c.filename)}</span>
                    <span class="inspector-score font-mono tabular-nums">score: ${cScore}</span>
                  </div>
                  <div class="inspector-meta font-mono">chunk: ${c.chunk_index} · doc_id: ${escapeHtml(c.document_id || "-")}</div>
                  <div class="inspector-snippet">${escapeHtml(c.snippet || "No text snippet.")}</div>
                </div>
              `;
            })
            .join("");

          citationsHtml = `
            <details class="ticket-citations-details">
              <summary>Inspect Retrieved Context (${citations.length} ${citations.length === 1 ? "source chunk" : "source chunks"})</summary>
              <div class="ticket-citations-grid">${cards}</div>
            </details>
          `;
        }

        let resolutionHtml = "";
        if (!isPending) {
          resolutionHtml = `
            ${item.final_answer ? `
              <div class="ticket-section">
                <span class="ticket-label">Final Released Answer</span>
                <div class="ticket-final-text">${escapeHtml(item.final_answer)}</div>
              </div>
            ` : ""}
            <div class="ticket-section">
              <span class="ticket-label">Resolution Details</span>
              <div class="ticket-meta-review font-mono">
                Decided by: <strong>${escapeHtml(item.reviewed_by || "reviewer")}</strong> on ${formatTimestamp(item.reviewed_at)}
                ${item.reviewer_notes ? ` · Note: "${escapeHtml(item.reviewer_notes)}"` : ""}
              </div>
            </div>
          `;
        }

        let actionControlsHtml = "";
        if (isPending) {
          const disabledAttr = isEmployee ? 'disabled title="Reviewer or admin role required via X-User-Role header."' : "";
          const disabledClass = isEmployee ? "btn-disabled-notice" : "";

          actionControlsHtml = `
            <div class="ticket-actions-bar" id="actions-bar-${id}">
              <div class="ticket-actions-group">
                <button class="btn btn-approve ${disabledClass}" data-action="approve" data-id="${id}" ${disabledAttr}>
                  Approve (Release to Session)
                </button>
                <button class="btn btn-edit-approve ${disabledClass}" data-action="toggle-edit" data-id="${id}" ${disabledAttr}>
                  Edit &amp; Approve…
                </button>
                <button class="btn btn-reject ${disabledClass}" data-action="toggle-reject" data-id="${id}" ${disabledAttr}>
                  Reject
                </button>
              </div>
              ${isEmployee ? '<span class="font-mono" style="font-size:0.75rem; color:var(--text-dim);">Read-Only (Employee)</span>' : ""}
            </div>

            <!-- Inline Edit & Approve Box -->
            <div class="inline-edit-box" id="edit-box-${id}" style="display: none;">
              <h5>Edit Draft Response Before Release</h5>
              <textarea class="inline-textarea" id="edit-textarea-${id}" rows="4">${escapeHtml(item.draft_answer)}</textarea>
              <input type="text" class="inline-input" id="edit-notes-${id}" placeholder="Optional reviewer notes explaining edit (e.g. Clarified policy clause effective date)…" />
              <div class="inline-actions-row">
                <button class="btn btn-secondary btn-cancel-inline" data-id="${id}">Cancel</button>
                <button class="btn btn-approve btn-confirm-edit" data-id="${id}">Submit Edited &amp; Approved</button>
              </div>
            </div>

            <!-- Inline Reject Box -->
            <div class="inline-reject-box" id="reject-box-${id}" style="display: none;">
              <h5>Reject AI Draft Response</h5>
              <input type="text" class="inline-input" id="reject-notes-${id}" placeholder="Reason for rejection (e.g. Unsupported policy claim, hallucinated terms)…" />
              <div class="inline-actions-row">
                <button class="btn btn-secondary btn-cancel-inline" data-id="${id}">Cancel</button>
                <button class="btn btn-reject btn-confirm-reject" data-id="${id}">Confirm Rejection</button>
              </div>
            </div>
          `;
        }

        return `
          <div class="approval-ticket-card" id="ticket-${id}">
            <div class="ticket-card-header">
              <div class="ticket-meta-left">
                <span class="ticket-id-tag" title="Ticket UUID: ${id}">Ticket #${id.substring(0, 8)}</span>
                <span class="font-mono">session: ${sid}</span>
                <span class="font-mono tabular-nums">${timeFormatted}</span>
              </div>
              <div class="ticket-meta-right">
                <span class="ticket-trigger-tag">${triggerReason}</span>
                <span class="ticket-score-pill">max_similarity: ${score}</span>
                ${statusBadge}
              </div>
            </div>

            <div class="ticket-section">
              <span class="ticket-label">Employee Query</span>
              <div class="ticket-query-text">${escapeHtml(item.user_query)}</div>
            </div>

            <div class="ticket-section">
              <span class="ticket-label">Drafted AI Answer (Post-PII Redaction)</span>
              <div class="ticket-draft-text">${escapeHtml(item.draft_answer)}</div>
            </div>

            ${citationsHtml}
            ${resolutionHtml}
            ${actionControlsHtml}
          </div>
        `;
      })
      .join("");

    approvalsQueue.innerHTML = cardsHtml;
  }

  function renderApprovalsError() {
    if (!approvalsQueue) return;
    approvalsQueue.innerHTML = `
      <div class="scaffold-placeholder">
        <p class="table-error">Failed to load approval tickets. Please ensure the backend is running and reachable.</p>
      </div>
    `;
  }

  async function submitApprovalDecision(approvalId, decision, editedAnswer = null, reviewerNotes = null) {
    if (!approvalId || !decision) return;

    const payload = {
      decision: decision,
    };
    if (decision === "edit_and_approve" && editedAnswer !== null) {
      payload.edited_answer = editedAnswer;
    }
    if (reviewerNotes) {
      payload.reviewer_notes = reviewerNotes;
    }

    try {
      const res = await fetch(`/api/approvals/${approvalId}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        throw new Error(errorData.detail || `Decision submission failed (HTTP ${res.status})`);
      }

      await fetchApprovals();
      updatePendingApprovalsCount();

      if (typeof fetchAuditLogs === "function") {
        fetchAuditLogs();
      }
    } catch (err) {
      console.warn("[Atlas Approvals] Decision error:", err.message);
      alert(`Approval decision failed: ${err.message}`);
    }
  }

  function initApprovals() {
    if (refreshApprovalsBtn) {
      refreshApprovalsBtn.addEventListener("click", async () => {
        refreshApprovalsBtn.classList.add("is-loading");
        try {
          await fetchApprovals();
        } finally {
          refreshApprovalsBtn.classList.remove("is-loading");
        }
      });
    }

    if (filterTabBtns) {
      filterTabBtns.forEach((btn) => {
        btn.addEventListener("click", () => {
          const status = btn.getAttribute("data-status");
          fetchApprovals(status);
        });
      });
    }

    if (approvalsQueue) {
      approvalsQueue.addEventListener("click", (e) => {
        const target = e.target;

        const approveBtn = target.closest('[data-action="approve"]');
        if (approveBtn) {
          const id = approveBtn.getAttribute("data-id");
          submitApprovalDecision(id, "approve");
          return;
        }

        const toggleEditBtn = target.closest('[data-action="toggle-edit"]');
        if (toggleEditBtn) {
          const id = toggleEditBtn.getAttribute("data-id");
          const editBox = document.getElementById(`edit-box-${id}`);
          const rejectBox = document.getElementById(`reject-box-${id}`);
          if (rejectBox) rejectBox.style.display = "none";
          if (editBox) {
            editBox.style.display = editBox.style.display === "none" ? "flex" : "none";
          }
          return;
        }

        const toggleRejectBtn = target.closest('[data-action="toggle-reject"]');
        if (toggleRejectBtn) {
          const id = toggleRejectBtn.getAttribute("data-id");
          const rejectBox = document.getElementById(`reject-box-${id}`);
          const editBox = document.getElementById(`edit-box-${id}`);
          if (editBox) editBox.style.display = "none";
          if (rejectBox) {
            rejectBox.style.display = rejectBox.style.display === "none" ? "flex" : "none";
          }
          return;
        }

        const cancelBtn = target.closest(".btn-cancel-inline");
        if (cancelBtn) {
          const id = cancelBtn.getAttribute("data-id");
          const editBox = document.getElementById(`edit-box-${id}`);
          const rejectBox = document.getElementById(`reject-box-${id}`);
          if (editBox) editBox.style.display = "none";
          if (rejectBox) rejectBox.style.display = "none";
          return;
        }

        const confirmEditBtn = target.closest(".btn-confirm-edit");
        if (confirmEditBtn) {
          const id = confirmEditBtn.getAttribute("data-id");
          const textarea = document.getElementById(`edit-textarea-${id}`);
          const notesInput = document.getElementById(`edit-notes-${id}`);
          const editedText = textarea ? textarea.value.trim() : "";
          const notes = notesInput ? notesInput.value.trim() : "";
          submitApprovalDecision(id, "edit_and_approve", editedText, notes);
          return;
        }

        const confirmRejectBtn = target.closest(".btn-confirm-reject");
        if (confirmRejectBtn) {
          const id = confirmRejectBtn.getAttribute("data-id");
          const notesInput = document.getElementById(`reject-notes-${id}`);
          const notes = notesInput ? notesInput.value.trim() : "";
          submitApprovalDecision(id, "reject", null, notes);
        }
      });
    }

    updatePendingApprovalsCount();

    if (state.currentView === "approvals") {
      fetchApprovals();
    }

    window.addEventListener("atlas:view-change", (e) => {
      if (e.detail && e.detail.view === "approvals") {
        fetchApprovals();
      }
      updatePendingApprovalsCount();
    });

    window.addEventListener("atlas:role-change", () => {
      if (state.currentView === "approvals") {
        renderApprovals(approvals.list);
      }
      updatePendingApprovalsCount();
    });
  }

  // ── Global Namespace Export ────────────────────────────────────────────────
  // Expose clean interface on window.Atlas for subsequent view implementations
  window.Atlas = {
    getUserRole,
    getAuthHeaders,
    switchView,
    fetchHealth,
    fetchAuditLogs,
    fetchDocuments,
    fetchSessions,
    loadSession,
    startNewChat,
    sendMessage,
    openSourceInspector,
    closeInspector,
    fetchApprovals,
    submitApprovalDecision,
    approvals,
    chat,
    docs,
    state,
  };

  // ── Initialization ─────────────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", () => {
    initRole();
    initNavigation();
    initOps();
    initDocs();
    initChat();
    initApprovals();
    console.info(
      `%c[Atlas Enterprise AI]%c Shell loaded (Role: ${state.currentRole}, View: ${state.currentView})`,
      "font-weight: bold; color: #10b981",
      "color: inherit"
    );
  });
})();
