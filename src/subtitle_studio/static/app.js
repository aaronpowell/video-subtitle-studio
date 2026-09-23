const elements = {
  uploadForm: document.querySelector("#upload-form"),
  videoFile: document.querySelector("#video-file"),
  language: document.querySelector("#language"),
  uploadMessage: document.querySelector("#upload-message"),
  jobs: document.querySelector("#jobs"),
  refreshJobs: document.querySelector("#refresh-jobs"),
  health: document.querySelector("#health"),
  emptyState: document.querySelector("#empty-state"),
  editor: document.querySelector("#editor"),
  projectTitle: document.querySelector("#project-title"),
  projectLanguage: document.querySelector("#project-language"),
  preview: document.querySelector("#preview"),
  captionRows: document.querySelector("#caption-rows"),
  captionCount: document.querySelector("#caption-count"),
  editorMessage: document.querySelector("#editor-message"),
  saveCaptions: document.querySelector("#save-captions"),
  addCaption: document.querySelector("#add-caption"),
  downloadSrt: document.querySelector("#download-srt"),
  downloadVtt: document.querySelector("#download-vtt"),
  downloadStl: document.querySelector("#download-stl"),
  jobTemplate: document.querySelector("#job-template"),
  captionTemplate: document.querySelector("#caption-template"),
  activeCuePreview: document.querySelector("#active-cue-preview"),
  prepareStyle: document.querySelector("#prepare-style"),
  prepareWordsPerBlock: document.querySelector("#prepare-words-per-block"),
  prepareRemovePunctuation: document.querySelector("#prepare-remove-punctuation"),
  prepareCasing: document.querySelector("#prepare-casing"),
  prepareApply: document.querySelector("#prepare-apply"),
  prepareMessage: document.querySelector("#prepare-message"),
};

let jobs = [];
let selectedJobId = null;
let currentProject = null;
let previewTrack = null;
let projectRequestVersion = 0;
let dragDepth = 0;
let reformatPresets = {};
let activeCueId = null;

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : detail;
    } catch {
      // Preserve the status-based message when the response is not JSON.
    }
    throw new Error(detail);
  }
  return response;
}

function setMessage(element, message, isError = false) {
  element.textContent = message;
  element.classList.toggle("error", isError);
}

async function checkHealth() {
  try {
    await api("/health");
    elements.health.textContent = "Server ready";
    elements.health.classList.add("ok");
  } catch {
    elements.health.textContent = "Server unavailable";
    elements.health.classList.remove("ok");
  }
}

function renderJobs() {
  elements.jobs.replaceChildren();
  if (jobs.length === 0) {
    const message = document.createElement("p");
    message.textContent = "No projects yet.";
    message.className = "message";
    elements.jobs.append(message);
    return;
  }

  for (const job of jobs) {
    const item = elements.jobTemplate.content.firstElementChild.cloneNode(true);
    item.dataset.jobId = job.id;
    item.classList.toggle("selected", job.id === selectedJobId);
    item.querySelector(".job-name").textContent = job.source_filename;
    item.querySelector(".job-status").textContent = job.status.replace("_", " ");
    item.querySelector(".job-error").textContent = job.error || "";
    item.querySelector(".job-open").addEventListener("click", () => selectJob(job));
    item.querySelector(".delete-job").addEventListener("click", () => deleteJob(job));
    elements.jobs.append(item);
  }
}

async function loadJobs() {
  try {
    const response = await api("/api/jobs");
    jobs = await response.json();
    renderJobs();
    if (selectedJobId) {
      const selected = jobs.find((job) => job.id === selectedJobId);
      if (selected?.status === "completed" && currentProject === null) {
        await loadProject(selected.id);
      } else if (!selected) {
        selectedJobId = null;
        currentProject = null;
        projectRequestVersion += 1;
        elements.editor.hidden = true;
        elements.emptyState.hidden = false;
        elements.emptyState.querySelector("h2").textContent = "Select a completed project";
        elements.emptyState.querySelector("p").textContent =
          "The video preview and caption editor will appear here.";
        renderJobs();
      }
    }
  } catch (error) {
    elements.jobs.replaceChildren();
    setMessage(elements.uploadMessage, error.message, true);
  }
}

async function selectJob(job) {
  selectedJobId = job.id;
  currentProject = null;
  projectRequestVersion += 1;
  renderJobs();
  if (job.status !== "completed") {
    elements.editor.hidden = true;
    elements.emptyState.hidden = false;
    elements.emptyState.querySelector("h2").textContent =
      job.status === "failed" ? "Transcription failed" : "Transcription in progress";
    elements.emptyState.querySelector("p").textContent =
      job.error || "This page updates automatically while the worker runs.";
    return;
  }
  await loadProject(job.id);
}

async function loadProject(jobId) {
  const requestVersion = ++projectRequestVersion;
  try {
    const response = await api(`/api/jobs/${jobId}/captions`);
    const project = await response.json();
    if (selectedJobId !== jobId || requestVersion !== projectRequestVersion) {
      return;
    }
    currentProject = project;
    elements.emptyState.hidden = true;
    elements.editor.hidden = false;
    elements.projectTitle.textContent = currentProject.source_filename;
    elements.projectLanguage.textContent = `Language: ${currentProject.language}`;
    elements.preview.src = `/api/jobs/${jobId}/media`;
    elements.downloadSrt.href = `/api/jobs/${jobId}/export.srt`;
    elements.downloadVtt.href = `/api/jobs/${jobId}/export.vtt`;
    elements.downloadStl.href = `/api/jobs/${jobId}/export.stl`;
    activeCueId = null;
    renderCaptions();
    setMessage(elements.editorMessage, "");
  } catch (error) {
    setMessage(elements.editorMessage, error.message, true);
  }
}

function renderCaptions() {
  elements.captionRows.replaceChildren();
  currentProject.cues.forEach((cue) => {
    const row = elements.captionTemplate.content.firstElementChild.cloneNode(true);
    row.dataset.cueId = cue.id;
    row.querySelector(".cue-start").value = (cue.start_ms / 1000).toFixed(3);
    row.querySelector(".cue-end").value = (cue.end_ms / 1000).toFixed(3);
    row.querySelector(".cue-text").value = cue.text;
    row.querySelectorAll("input, textarea").forEach((input) => {
      input.addEventListener("input", updatePreviewFromEditor);
    });
    row.querySelector(".delete-cue").addEventListener("click", () => {
      row.remove();
      updateCaptionCount();
      updatePreviewFromEditor();
    });
    elements.captionRows.append(row);
  });
  updateCaptionCount();
  updatePreviewTrack(currentProject.cues);
}

function readCues() {
  return [...elements.captionRows.querySelectorAll("tr")].map((row) => ({
    id: row.dataset.cueId,
    start_ms: Math.round(Number(row.querySelector(".cue-start").value) * 1000),
    end_ms: Math.round(Number(row.querySelector(".cue-end").value) * 1000),
    text: row.querySelector(".cue-text").value.trim(),
  }));
}

function updateCaptionCount() {
  const count = elements.captionRows.querySelectorAll("tr").length;
  elements.captionCount.textContent = `${count} cue${count === 1 ? "" : "s"}`;
}

function updatePreviewFromEditor() {
  const cues = readCues().filter(
    (cue) =>
      Number.isFinite(cue.start_ms) &&
      Number.isFinite(cue.end_ms) &&
      cue.end_ms > cue.start_ms &&
      cue.text,
  );
  updatePreviewTrack(cues);
}

function updatePreviewTrack(cues) {
  if (!previewTrack) {
    previewTrack = elements.preview.addTextTrack("captions", "Edited captions", currentProject.language);
  }
  [...previewTrack.cues].forEach((cue) => previewTrack.removeCue(cue));
  cues.forEach((cue) => {
    previewTrack.addCue(new VTTCue(cue.start_ms / 1000, cue.end_ms / 1000, cue.text));
  });
  previewTrack.mode = "showing";
}

// Mirrors the server's estimate_word_timings(): the transcriber only gives
// phrase-level timing, so per-word timing is approximated proportionally to
// each word's character length.
function estimateWordTimings(text, startMs, endMs) {
  const words = text.split(/\s+/).filter(Boolean);
  if (words.length === 0) {
    return [];
  }
  const duration = endMs - startMs;
  const weights = words.map((word) => word.length);
  const totalWeight = weights.reduce((sum, weight) => sum + weight, 0) || words.length;
  let cursor = startMs;
  let cumulative = 0;
  return words.map((word, index) => {
    cumulative += (weights[index] / totalWeight) * duration;
    let wordEnd = index === words.length - 1 ? endMs : startMs + Math.round(cumulative);
    wordEnd = Math.max(wordEnd, cursor + 1);
    const timing = { word, start_ms: cursor, end_ms: wordEnd };
    cursor = wordEnd;
    return timing;
  });
}

function renderActiveCuePreview(cue, activeWordIndex) {
  elements.activeCuePreview.replaceChildren();
  if (!cue) {
    return;
  }
  const timings = estimateWordTimings(cue.text, cue.start_ms, cue.end_ms);
  timings.forEach((timing, index) => {
    const span = document.createElement("button");
    span.type = "button";
    span.className = "word";
    span.classList.toggle("active", index === activeWordIndex);
    span.textContent = timing.word;
    span.addEventListener("click", () => jumpToWord(cue, timing));
    elements.activeCuePreview.append(span);
  });
}

function jumpToWord(cue, timing) {
  elements.preview.currentTime = timing.start_ms / 1000;
  const row = elements.captionRows.querySelector(`tr[data-cue-id="${CSS.escape(cue.id)}"]`);
  const textarea = row?.querySelector(".cue-text");
  if (!textarea) {
    return;
  }
  textarea.focus();
  const offset = cue.text.indexOf(timing.word);
  if (offset >= 0) {
    textarea.setSelectionRange(offset, offset + timing.word.length);
  }
}

function syncActiveCaption() {
  if (!currentProject) {
    return;
  }
  const currentMs = elements.preview.currentTime * 1000;
  const cues = readCues();
  const active = cues.find((cue) => currentMs >= cue.start_ms && currentMs < cue.end_ms);
  elements.captionRows.querySelectorAll("tr").forEach((row) => {
    row.classList.toggle("active-row", active !== undefined && row.dataset.cueId === active.id);
  });
  activeCueId = active?.id ?? null;
  if (!active) {
    renderActiveCuePreview(null, -1);
    return;
  }
  const timings = estimateWordTimings(active.text, active.start_ms, active.end_ms);
  const activeWordIndex = timings.findIndex(
    (timing) => currentMs >= timing.start_ms && currentMs < timing.end_ms,
  );
  renderActiveCuePreview(active, activeWordIndex);
}

async function loadReformatPresets() {
  try {
    const response = await api("/api/reformat-presets");
    reformatPresets = await response.json();
  } catch {
    reformatPresets = {};
  }
}

function applyPresetToFields(style) {
  const preset = reformatPresets[style];
  if (!preset) {
    return;
  }
  elements.prepareWordsPerBlock.value = preset.words_per_block ?? "";
  elements.prepareRemovePunctuation.checked = Boolean(preset.remove_punctuation);
  elements.prepareCasing.value = preset.casing ?? "default";
}

async function applyPrepareSettings() {
  if (!currentProject) {
    return;
  }
  const jobId = currentProject.job_id;
  const wordsPerBlockRaw = elements.prepareWordsPerBlock.value.trim();
  const body = {
    words_per_block: wordsPerBlockRaw ? Number(wordsPerBlockRaw) : null,
    remove_punctuation: elements.prepareRemovePunctuation.checked,
    casing: elements.prepareCasing.value,
  };
  elements.prepareApply.disabled = true;
  setMessage(elements.prepareMessage, "Applying...");
  try {
    const response = await api(`/api/jobs/${jobId}/reformat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const project = await response.json();
    if (selectedJobId !== jobId) {
      return;
    }
    currentProject = project;
    renderCaptions();
    setMessage(
      elements.prepareMessage,
      "Caption blocks updated. Review below, then Save changes to keep them.",
    );
  } catch (error) {
    setMessage(elements.prepareMessage, error.message, true);
  } finally {
    elements.prepareApply.disabled = false;
  }
}

elements.prepareStyle.addEventListener("change", () => {
  if (elements.prepareStyle.value) {
    applyPresetToFields(elements.prepareStyle.value);
  }
});
elements.prepareApply.addEventListener("click", applyPrepareSettings);
elements.preview.addEventListener("timeupdate", syncActiveCaption);

function addCaption() {
  const existing = readCues();
  const previousEnd = existing.at(-1)?.end_ms || 0;
  currentProject.cues = existing;
  currentProject.cues.push({
    id: crypto.randomUUID(),
    start_ms: previousEnd,
    end_ms: previousEnd + 2000,
    text: "New caption",
  });
  renderCaptions();
  elements.captionRows.lastElementChild?.querySelector(".cue-text").focus();
}

async function saveCaptions() {
  if (!currentProject) {
    return;
  }
  const projectJobId = currentProject.job_id;
  elements.saveCaptions.disabled = true;
  setMessage(elements.editorMessage, "Saving...");
  try {
    const response = await api(`/api/jobs/${projectJobId}/captions`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cues: readCues() }),
    });
    const project = await response.json();
    if (selectedJobId !== projectJobId) {
      return;
    }
    currentProject = project;
    renderCaptions();
    setMessage(elements.editorMessage, "Changes saved. Preview and exports are current.");
  } catch (error) {
    setMessage(elements.editorMessage, error.message, true);
  } finally {
    elements.saveCaptions.disabled = false;
  }
}

async function deleteJob(job) {
  const activeStatuses = new Set(["uploading", "extracting", "transcribing"]);
  if (activeStatuses.has(job.status)) {
    setMessage(elements.uploadMessage, "Wait for active processing to finish before deleting.", true);
    return;
  }
  if (!confirm(`Delete "${job.source_filename}" and all generated captions?`)) {
    return;
  }
  try {
    await api(`/api/jobs/${job.id}`, { method: "DELETE" });
    if (selectedJobId === job.id) {
      selectedJobId = null;
      currentProject = null;
      projectRequestVersion += 1;
      elements.preview.removeAttribute("src");
      elements.editor.hidden = true;
      elements.emptyState.hidden = false;
      elements.emptyState.querySelector("h2").textContent = "Project deleted";
      elements.emptyState.querySelector("p").textContent =
        "Select another completed project or upload a new MP4.";
    }
    setMessage(elements.uploadMessage, "Project deleted.");
    await loadJobs();
  } catch (error) {
    setMessage(elements.uploadMessage, error.message, true);
  }
}

elements.uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = elements.videoFile.files[0];
  if (!file) {
    return;
  }
  const submit = elements.uploadForm.querySelector("button[type=submit]");
  submit.disabled = true;
  setMessage(elements.uploadMessage, "Uploading...");
  try {
    const data = new FormData();
    data.append("file", file);
    data.append("language", elements.language.value.trim());
    const response = await api("/api/jobs", { method: "POST", body: data });
    const job = await response.json();
    elements.uploadForm.reset();
    elements.language.value = "en";
    setMessage(elements.uploadMessage, "Upload accepted. Transcription is queued.");
    await loadJobs();
    await selectJob(job);
  } catch (error) {
    setMessage(elements.uploadMessage, error.message, true);
  } finally {
    submit.disabled = false;
  }
});

elements.refreshJobs.addEventListener("click", loadJobs);
elements.saveCaptions.addEventListener("click", saveCaptions);
elements.addCaption.addEventListener("click", addCaption);

function isMp4(file) {
  return file && (file.name.toLowerCase().endsWith(".mp4") || file.type === "video/mp4");
}

function setDragActive(active) {
  document.body.classList.toggle("drag-active", active);
}

document.addEventListener("dragenter", (event) => {
  event.preventDefault();
  dragDepth += 1;
  setDragActive(true);
});

document.addEventListener("dragover", (event) => {
  event.preventDefault();
  if (event.dataTransfer) {
    event.dataTransfer.dropEffect = "copy";
  }
});

document.addEventListener("dragleave", (event) => {
  event.preventDefault();
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) {
    setDragActive(false);
  }
});

document.addEventListener("drop", (event) => {
  event.preventDefault();
  dragDepth = 0;
  setDragActive(false);
  const [file] = event.dataTransfer?.files || [];
  if (!file) {
    return;
  }
  if (!isMp4(file)) {
    setMessage(elements.uploadMessage, "Drop an MP4 video file.", true);
    return;
  }
  const transfer = new DataTransfer();
  transfer.items.add(file);
  elements.videoFile.files = transfer.files;
  setMessage(elements.uploadMessage, `${file.name} selected. Uploading...`);
  elements.uploadForm.requestSubmit();
});

checkHealth();
loadJobs();
loadReformatPresets();
setInterval(loadJobs, 2500);
