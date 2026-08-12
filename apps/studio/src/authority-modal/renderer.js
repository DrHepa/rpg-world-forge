const state = { nonce: null, criteria: [], objectUrl: null, used: false };

window.worldForgeAuthorityModal.onPayload((payload) => {
  if (state.used) return;
  state.used = true;
  state.nonce = payload.nonce;
  state.criteria = payload.criteria;
  document.getElementById("title").textContent = payload.title;
  document.getElementById("sha256").textContent = payload.preview.sha256;
  document.getElementById("bytes").textContent = String(payload.preview.byteLength);
  document.getElementById("media-type").textContent = payload.preview.mediaType;
  document.getElementById("artifact").textContent = `${payload.preview.artifactId} / ${payload.preview.subject.format}@${payload.preview.subject.formatVersion} / ${payload.preview.subject.id}`;
  renderPreview(payload.preview);
  const list = document.getElementById("criteria");
  list.replaceChildren(
    ...payload.criteria.map((criterion) => {
      const item = document.createElement("li");
      item.textContent = criterion;
      return item;
    }),
  );
});

function renderPreview(preview) {
  const container = document.getElementById("preview");
  container.replaceChildren();
  const bytes = new Uint8Array(preview.data);
  if (bytes.byteLength !== preview.byteLength) {
    container.textContent = "Preview byte length mismatch.";
    state.nonce = null;
    return;
  }
  if (preview.mediaType === "image/png") {
    const blob = new Blob([bytes], { type: "image/png" });
    state.objectUrl = URL.createObjectURL(blob);
    const image = document.createElement("img");
    image.alt = "Verified authority preview";
    image.src = state.objectUrl;
    container.append(image);
    return;
  }
  if (preview.mediaType === "audio/wav") {
    const blob = new Blob([bytes], { type: "audio/wav" });
    state.objectUrl = URL.createObjectURL(blob);
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.src = state.objectUrl;
    container.append(audio);
    return;
  }
  if (preview.mediaType === "text/plain") {
    const text = new TextDecoder("utf-8", { fatal: false }).decode(bytes);
    const pre = document.createElement("pre");
    pre.textContent = text;
    container.append(pre);
    return;
  }
  container.textContent = "Unsupported binary preview cannot be approved.";
  state.nonce = null;
}

function cleanup() {
  if (state.objectUrl !== null) {
    URL.revokeObjectURL(state.objectUrl);
    state.objectUrl = null;
  }
}

function reply(action) {
  if (state.nonce === null) return;
  const decision =
    action === "reject" ? "rejected" : "approved";
  window.worldForgeAuthorityModal.reply({
    nonce: state.nonce,
    action,
    criterionDecisions: state.criteria.map(() => decision),
  });
  cleanup();
}

window.addEventListener("pagehide", cleanup, { once: true });

document.getElementById("approve").addEventListener("click", () => reply("approve"));
document.getElementById("reject").addEventListener("click", () => reply("reject"));
document.getElementById("cancel").addEventListener("click", () => reply("cancel"));
