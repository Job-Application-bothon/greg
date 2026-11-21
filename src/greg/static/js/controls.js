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
  const stage = document.querySelector(".forecast-stage");
  if (!stage || stage.querySelector('.forecast-halftone')) return;
  const canvas = document.createElement('canvas');
  canvas.className = 'forecast-halftone';
  canvas.setAttribute('aria-hidden', 'true');
  stage.prepend(canvas);
  const ctx = canvas.getContext('2d');
  const motion = matchMedia('(any-hover: hover) and (prefers-reduced-motion: no-preference)');
  const pointer = { x: -1000, y: -1000 };
  let width = 0, height = 0, frame = 0, strength = 0, lastTime = 0;

  function draw(time) {
    frame = 0;
    const dt = Math.min((time - lastTime) / 16.67 || 1, 3);
    lastTime = time;
    strength = motion.matches ? Math.max(0, strength - 0.018 * dt) : 0;
    ctx.clearRect(0, 0, width, height);
    for (let y = 8; y < height; y += 16) {
      for (let x = 8; x < width; x += 16) {
        const dx = x - pointer.x, dy = y - pointer.y;
        const distance = Math.hypot(dx, dy);
        const influence = Math.max(0, 1 - distance / 125) ** 2 * strength;
        const push = influence * 22 / (distance || 1);
        ctx.fillStyle = `rgba(255,255,255,${0.55 + influence * 0.4})`;
        ctx.beginPath();
        ctx.arc(x + dx * push, y + dy * push, 0.7 + influence * 1.2, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    if (strength > 0) frame = requestAnimationFrame(draw);
  }
  function schedule() {
    if (!frame) {
      lastTime = performance.now();
      frame = requestAnimationFrame(draw);
    }
  }
  function resize() {
    width = stage.clientWidth;
    height = stage.clientHeight;
    const dpr = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    schedule();
  }
  stage.addEventListener('pointermove', (event) => {
    if (!motion.matches || event.pointerType !== 'mouse') return;
    const bounds = stage.getBoundingClientRect();
    pointer.x = event.clientX - bounds.left - stage.clientLeft;
    pointer.y = event.clientY - bounds.top - stage.clientTop;
    strength = 1;
    schedule();
  }, { passive: true });
  stage.addEventListener('pointerleave', schedule);
  motion.addEventListener('change', schedule);
  new ResizeObserver(resize).observe(stage);
  resize();
}
