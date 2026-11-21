/** UI interaction polish: number steppers and dark dot-grid hover glow. */

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
