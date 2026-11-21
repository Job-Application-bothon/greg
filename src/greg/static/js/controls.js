/** UI interaction polish: step carousel, number steppers, and dark dot-grid hover glow. */

export function animateHeight(element, update) {
  const from = element.getBoundingClientRect().height;
  element.getAnimations().filter(a => a.id === 'layout-height').forEach(a => a.cancel());
  update();
  const to = element.getBoundingClientRect().height;
  if (matchMedia('(prefers-reduced-motion: reduce)').matches || Math.abs(to - from) < 1) return;
  return element.animate(
    [{ height: `${from}px`, overflow: 'clip' }, { height: `${to}px`, overflow: 'clip' }],
    { id: 'layout-height', duration: 360, easing: 'cubic-bezier(.22,1,.36,1)' }
  );
}

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
    animateHeight(root.parentElement, () => {
      root.hidden = !root.hidden;
      toggle.setAttribute("aria-expanded", String(!root.hidden));
      if (!root.hidden) select(0);
    });
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
