/** UI interaction polish: step carousel, number steppers, and dark dot-grid hover glow. */

export function initStepCarousel() {
  const root = document.getElementById("forecast-steps");
  const toggle = document.getElementById("steps-toggle");
  if (!root || !toggle) return;

  const signal = new AbortController().signal;
  function current(buttons, index, token) {
    buttons.forEach((b, i) => {
      if (i === index) b.setAttribute("aria-current", token);
      else b.removeAttribute("aria-current");
    });
  }

  const buttons = [...root.querySelectorAll("[data-slide]")];
  const slides = [...root.querySelectorAll(".collection-slide")];
  const win = root.querySelector(".collection-window");
  const prev = root.querySelector("[data-prev-slide]");
  const next = root.querySelector("[data-next-slide]");
  let slide = 0;
  let start = null;

  function select(i) {
    slide = (i + slides.length) % slides.length;
    root.style.setProperty("--slide", slide);
    current(buttons, slide, "true");
    slides.forEach((s, j) => {
      s.inert = j !== slide;
      s.setAttribute("aria-hidden", String(j !== slide));
    });
    const statusEl = root.querySelector("[data-slide-status]");
    if (statusEl) {
      statusEl.textContent = `${slides[slide].querySelector("h3").textContent}, ${slide + 1} of 3`;
    }
  }

  buttons.forEach((b, i) => b.addEventListener("click", () => select(i), { signal }));
  if (prev) prev.addEventListener("click", () => select(slide - 1), { signal });
  if (next) next.addEventListener("click", () => select(slide + 1), { signal });
  if (win) {
    win.addEventListener(
      "keydown",
      (e) => {
        if (e.target !== win || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) {
          return;
        }
        e.preventDefault();
        select(
          e.key === "Home" ? 0 : e.key === "End" ? 2 : slide + (e.key === "ArrowRight" ? 1 : -1)
        );
      },
      { signal }
    );
    win.addEventListener(
      "pointerdown",
      (e) => {
        if (
          e.pointerType !== "touch" ||
          !e.isPrimary ||
          e.button !== 0 ||
          e.target.closest("button,input,select,textarea,a")
        ) {
          return;
        }
        start = { x: e.clientX, y: e.clientY, id: e.pointerId };
      },
      { signal }
    );
    win.addEventListener(
      "pointerup",
      (e) => {
        if (!start || start.id !== e.pointerId) return;
        const dx = e.clientX - start.x;
        const dy = e.clientY - start.y;
        start = null;
        if (Math.abs(dx) > 35 && Math.abs(dx) > Math.abs(dy) * 1.25) {
          select(slide + (dx < 0 ? 1 : -1));
        }
      },
      { signal }
    );
    win.addEventListener(
      "pointercancel",
      () => {
        start = null;
      },
      { signal }
    );
    win.addEventListener(
      "lostpointercapture",
      () => {
        start = null;
      },
      { signal }
    );
  }
  select(0);

  toggle.addEventListener("click", () => {
    root.hidden = !root.hidden;
    toggle.setAttribute("aria-expanded", String(!root.hidden));
    if (!root.hidden) select(0);
  });
}

export function initNumberSteppers() {
  for (const input of document.querySelectorAll('input[type="number"]')) {
    const wrapper = document.createElement("div");
    wrapper.className = "number-stepper";
    const buttons = document.createElement("div");
    buttons.className = "stepper-buttons";
    input.before(wrapper);
    wrapper.append(input, buttons);
    const lblText = input.labels && input.labels[0] ? input.labels[0].textContent : "Value";
    for (const [label, glyph, method] of [
      ["Increase", "▴", "stepUp"],
      ["Decrease", "▾", "stepDown"],
    ]) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = glyph;
      button.setAttribute("aria-label", `${label}: ${lblText}`);
      button.addEventListener("mousedown", (event) => event.preventDefault());
      button.addEventListener("click", () => {
        input[method]();
        input.dispatchEvent(new Event("change", { bubbles: true }));
      });
      buttons.append(button);
    }
  }
}

export function initForecastDotHover() {
  const forecastStage = document.querySelector(".forecast-stage");
  if (!forecastStage) return;
  const dotMotion = matchMedia("(any-hover: hover) and (prefers-reduced-motion: no-preference)");
  forecastStage.addEventListener("pointermove", (event) => {
    if (!dotMotion.matches || event.pointerType !== "mouse") return;
    const bounds = forecastStage.getBoundingClientRect();
    forecastStage.style.setProperty("--dot-x", `${event.clientX - bounds.left}px`);
    forecastStage.style.setProperty("--dot-y", `${event.clientY - bounds.top}px`);
    forecastStage.setAttribute("data-dot-hover", "");
  });
  forecastStage.addEventListener("pointerleave", () =>
    forecastStage.removeAttribute("data-dot-hover")
  );
  dotMotion.addEventListener("change", () => forecastStage.removeAttribute("data-dot-hover"));
}
