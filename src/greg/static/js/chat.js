/** Floating Greg messenger UI, period-analysis handoff, keyboard close, explain trigger, and chat. */
import { postJson } from "./api.js";
import { fmtMoney } from "./format.js";

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
    chatPeriodContext,
    chatPeriodModeEl,
    chatPeriodDatesEl,
    chatPeriodIncEl,
    chatPeriodExpEl,
    chatPeriodNetEl,
    chatPeriodDismissBtn,
  } = els;

  const chatHistory = [];
  let activeSelectedPeriod = null;
  const initialMessagesHtml = messagesEl ? messagesEl.innerHTML : "";

  function setOpen(open) {
    messenger.hidden = !open;
    chatLauncher.hidden = open;
    const floatingLauncher = document.getElementById("floating-chat-launcher");
    if (floatingLauncher) {
      floatingLauncher.hidden = open;
      floatingLauncher.setAttribute("aria-expanded", String(open));
    }
    chatLauncher.setAttribute("aria-expanded", String(open));
    chatLauncher.setAttribute("aria-label", open ? "Close chat with Greg" : "Open chat with Greg");
    if (open) {
      if (!promptEl.disabled) promptEl.focus();
      messagesEl.scrollTop = messagesEl.scrollHeight;
    } else {
      (floatingLauncher || chatLauncher).focus({ preventScroll: true });
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

  function clearPeriodContext() {
    activeSelectedPeriod = null;
    if (chatPeriodContext) chatPeriodContext.classList.add("hidden");
    window.GREG_SELECTED_PERIOD = null;
  }

  function resetConversation() {
    chatHistory.length = 0;
    clearPeriodContext();
    if (promptEl) promptEl.value = "";
    if (messagesEl) messagesEl.innerHTML = initialMessagesHtml;
  }

  function receivePeriodAnalysis(payload) {
    if (!payload) return;

    activeSelectedPeriod = payload;
    window.GREG_SELECTED_PERIOD = payload;
    window.dispatchEvent(new CustomEvent("greg:analyze-period", { detail: payload }));

    if (chatPeriodContext) {
      const spanUnit = payload.spanUnit || (payload.granularity === "month" ? "m" : "d");
      if (chatPeriodModeEl) {
        chatPeriodModeEl.textContent = payload.modeLabel;
        chatPeriodModeEl.className = `chat-period-mode${payload.mode === "past" ? "" : ` ${payload.mode}`}`;
      }
      if (chatPeriodDatesEl) {
        chatPeriodDatesEl.textContent = `${payload.startDate} → ${payload.endDate} (${payload.dayCount}${spanUnit})`;
      }
      if (chatPeriodIncEl) {
        chatPeriodIncEl.textContent = `+${fmtMoney(payload.totalIncome, payload.currency)}`;
      }
      if (chatPeriodExpEl) {
        chatPeriodExpEl.textContent = `-${fmtMoney(payload.totalExpenses, payload.currency)}`;
      }
      if (chatPeriodNetEl) {
        const netSign = payload.netFlow > 0 ? "+" : "";
        chatPeriodNetEl.textContent = `Net: ${netSign}${fmtMoney(payload.netFlow, payload.currency)}`;
      }
      chatPeriodContext.classList.remove("hidden");
    }

    if (promptEl) {
      promptEl.value = payload.suggestedPrompt || "";
    }

    setOpen(true);

    if (messenger) {
      messenger.classList.remove("chat-handoff-pulse");
      void messenger.offsetWidth;
      messenger.classList.add("chat-handoff-pulse");
    }

    if (promptEl && !promptEl.disabled) {
      requestAnimationFrame(() => {
        promptEl.focus();
        if (typeof promptEl.setSelectionRange === "function") {
          const end = promptEl.value.length;
          promptEl.setSelectionRange(end, end);
        }
      });
    }
  }

  chatLauncher.addEventListener("click", () => setOpen(messenger.hidden));
  if (chatCloseBtn) {
    chatCloseBtn.addEventListener("click", () => setOpen(false));
  }
  if (chatPeriodDismissBtn) {
    chatPeriodDismissBtn.addEventListener("click", () => clearPeriodContext());
  }
  messenger.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
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
      const chatPayload = Object.assign({ messages: chatHistory }, getHorizonPayload());
      if (activeSelectedPeriod) {
        chatPayload.selected_period = activeSelectedPeriod;
      }
      const res = await postJson("/api/chat", chatPayload);
      const data = await res.json();
      if (data.blocked) {
        // Guardrails refused the message: show Greg's refusal, keep it out of history.
        pendingEl.textContent = data.error;
        chatHistory.pop();
        return;
      }
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

  return {
    setOpen,
    isOpen,
    appendMessage,
    receivePeriodAnalysis,
    clearPeriodContext,
    resetConversation,
  };
}
