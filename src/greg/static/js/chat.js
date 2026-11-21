/** Floating Greg messenger UI, keyboard close, explain trigger, and chat streaming. */
import { postJson } from "./api.js";

export function createChatController(els, { getHorizonPayload }) {
  const {
    chatLauncher,
    messenger,
    chatCloseBtn,
    messagesEl,
    chatForm,
    promptEl,
    sendBtn,
    aiExplainBtn,
  } = els;

  const chatHistory = [];

  function setOpen(open) {
    messenger.hidden = !open;
    chatLauncher.hidden = open;
    chatLauncher.setAttribute("aria-expanded", String(open));
    chatLauncher.setAttribute("aria-label", open ? "Close chat with Greg" : "Open chat with Greg");
    if (open) {
      if (!promptEl.disabled) promptEl.focus();
      messagesEl.scrollTop = messagesEl.scrollHeight;
    } else {
      chatLauncher.focus();
    }
  }

  function isOpen() {
    return !messenger.hidden;
  }

  function appendMessage(role, text) {
    setOpen(true);
    const div = document.createElement("div");
    div.className = `msg ${role}`;
    div.textContent = text;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return div;
  }

  chatLauncher.addEventListener("click", () => setOpen(messenger.hidden));
  if (chatCloseBtn) {
    chatCloseBtn.addEventListener("click", () => setOpen(false));
  }
  messenger.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    }
  });

  if (aiExplainBtn) {
    aiExplainBtn.addEventListener("click", async () => {
      aiExplainBtn.disabled = true;
      const pending = appendMessage(
        "assistant",
        "Asking Greg on OpenShift AI to explain this month and forecast in simple words…"
      );
      try {
        const res = await postJson("/api/explain", getHorizonPayload());
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
        pending.textContent = data.reply;
        chatHistory.push({ role: "assistant", content: data.reply });
      } catch (err) {
        pending.className = "msg error";
        pending.textContent = `Error: ${err.message}`;
      } finally {
        aiExplainBtn.disabled = false;
      }
    });
  }

  chatForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = promptEl.value.trim();
    if (!text) return;

    promptEl.value = "";
    promptEl.disabled = true;
    sendBtn.disabled = true;

    chatHistory.push({ role: "user", content: text });
    appendMessage("user", text);
    const pendingEl = appendMessage("assistant", "Thinking…");

    try {
      const res = await postJson(
        "/api/chat",
        Object.assign({ messages: chatHistory }, getHorizonPayload())
      );
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      pendingEl.textContent = data.reply;
      chatHistory.push({ role: "assistant", content: data.reply });
    } catch (err) {
      pendingEl.className = "msg error";
      pendingEl.textContent = `Error: ${err.message}`;
      chatHistory.pop();
    } finally {
      promptEl.disabled = false;
      sendBtn.disabled = false;
      promptEl.focus();
    }
  });

  return { setOpen, isOpen, appendMessage };
}
