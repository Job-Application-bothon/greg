/*
MIT License

Copyright (c) 2026 SLtowl

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
*/
(() => {
const mounted = new WeakMap();
function mountSlider(root, { onChange, formatValue } = {}) {
  if (mounted.has(root)) return mounted.get(root);
  if (onChange !== undefined && typeof onChange !== 'function') throw new TypeError('onChange must be a function.');
  if (formatValue !== undefined && typeof formatValue !== 'function') throw new TypeError('formatValue must be a function.');
  const inputs = [...root.querySelectorAll('input[type="range"]')];
  const output = root.querySelector('[data-output]');
  if (!inputs.length || !output) throw new TypeError('A native range and output are required.');
  const lifecycle = new AbortController();
  const view = root.ownerDocument.defaultView;
  const dual = inputs.length === 2;
  const labels = root.dataset.labels?.split(',');
  const format = value => formatValue ? String(formatValue(value)) : dual ? '$' + value : value + '%';
  const track = root.querySelector('.slider-track');
  let dragging = null, destroyed = false;
  const values = () => inputs.map(input => input.valueAsNumber);
  const percent = input => (input.valueAsNumber - Number(input.min)) / (Number(input.max) - Number(input.min)) * 100;
  function render(changed, emit = false) {
    if (destroyed) return;
    if (dual) {
      if (inputs[0].valueAsNumber > inputs[1].valueAsNumber) {
        if (changed === inputs[0]) inputs[0].value = inputs[1].value;
        else inputs[1].value = inputs[0].value;
      }
      root.style.setProperty('--low', percent(inputs[0]) + '%');
      root.style.setProperty('--high', percent(inputs[1]) + '%');
      inputs[0].setAttribute('aria-valuemax', inputs[1].value);
      inputs[1].setAttribute('aria-valuemin', inputs[0].value);
      for (const input of inputs) input.setAttribute('aria-valuetext', format(input.valueAsNumber));
      output.textContent = format(inputs[0].valueAsNumber) + ' – ' + format(inputs[1].valueAsNumber);
    } else {
      const p = percent(inputs[0]);
      root.style.setProperty('--high', p + '%');
      root.style.setProperty('--position', p + '%');
      if (root.hasAttribute('data-centered')) {
        const center = (0 - Number(inputs[0].min)) / (Number(inputs[0].max) - Number(inputs[0].min)) * 100;
        root.style.setProperty('--low', Math.min(p, center) + '%');
        root.style.setProperty('--high', Math.max(p, center) + '%');
      }
      root.style.setProperty('--sun-angle', (p * .9) + 'deg');
      root.style.setProperty('--sun-opacity', String(.25 + p * .0075));
      root.dataset.zero = String(p === 0);
      root.dataset.quiet = String(p <= 50);
      if (root.dataset.slider === 'volume') {
        // Only the emblem eases; the thumb and filled track always follow input.
        root.style.setProperty('--wave-one', String(Math.min(1, p / 35)));
        root.style.setProperty('--wave-two', String(Math.max(0, Math.min(1, (p - 20) / 80))));
      }
      output.textContent = labels ? labels[inputs[0].valueAsNumber] : format(inputs[0].valueAsNumber);
      inputs[0].setAttribute('aria-valuetext', output.textContent);
      for (const marker of root.querySelectorAll('[data-step]')) marker.classList.toggle('is-current', Number(marker.dataset.step) === inputs[0].valueAsNumber);
    }
    if (emit) {
      onChange?.(values());
      root.dispatchEvent(new view.CustomEvent('sliderchange', { bubbles: true, detail: { values: values() } }));
    }
  }
  root.addEventListener('input', event => { if (inputs.includes(event.target)) render(event.target, true); }, { signal: lifecycle.signal });
  // In the dual control, native thumbs drag normally. Empty-track dragging uses
  // the nearest thumb, with pointer capture, while both inputs remain focusable.
  if (dual) {
    const updatePointer = event => {
      const rect = track.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left - 12) / (rect.width - 24)));
      const min = Number(dragging.min), max = Number(dragging.max), step = Number(dragging.step) || 1;
      dragging.value = String(min + Math.round(ratio * (max - min) / step) * step);
      dragging.dispatchEvent(new view.Event('input', { bubbles: true }));
    };
    track.addEventListener('pointerdown', event => {
      if (event.button !== 0 || inputs.includes(event.target) || dragging) return;
      const candidates = inputs.filter(input => !input.disabled);
      if (!candidates.length) return;
      const rect = track.getBoundingClientRect();
      const p = (event.clientX - rect.left - 12) / (rect.width - 24) * 100;
      dragging = candidates.reduce((nearest, input) => Math.abs(percent(input)-p) < Math.abs(percent(nearest)-p) ? input : nearest);
      dragging.focus({ preventScroll: true });
      track.setPointerCapture(event.pointerId);
      updatePointer(event);
      event.preventDefault();
    }, { signal: lifecycle.signal });
    track.addEventListener('pointermove', event => { if (dragging && track.hasPointerCapture(event.pointerId)) updatePointer(event); }, { signal: lifecycle.signal });
    const finish = () => { const input = dragging; dragging = null; input?.dispatchEvent(new view.Event('change', { bubbles: true })); };
    track.addEventListener('pointerup', finish, { signal: lifecycle.signal });
    track.addEventListener('pointercancel', finish, { signal: lifecycle.signal });
    track.addEventListener('lostpointercapture', finish, { signal: lifecycle.signal });
  }
  function reset() {
    if (destroyed) return;
    inputs.forEach(input => { input.value = input.defaultValue; });
    render();
  }
  inputs[0].form?.addEventListener('reset', () => queueMicrotask(reset), { signal: lifecycle.signal });
  // History restoration can replace native values after module initialization.
  // Reconcile the visual state without emitting a user change or resetting it.
  view.addEventListener('pageshow', () => render(), { signal: lifecycle.signal });
  render();
  const controller = { reset, sync() { render(); }, get values() { return values(); }, destroy() { destroyed = true; dragging = null; lifecycle.abort(); mounted.delete(root); } };
  mounted.set(root, controller);
  return controller;
}

function formatter(root) {
  return value => {
    switch (root.dataset.format) {
      case 'time': return Math.floor(value / 60) + ':' + String(Math.floor(value) % 60).padStart(2,'0');
      case 'hours': return String(value).padStart(2,'0') + ':00';
      case 'temperature': return value + ' °C';
      case 'balance': return value === 0 ? 'Center' : Math.abs(value) + '% ' + (value < 0 ? 'L' : 'R');
      case 'exposure': return (value > 0 ? '+' : '') + value.toFixed(1) + ' EV';
      case 'rating': return value + ' / 5';
      case 'distance': return value + ' km';
      case 'pixels': return value + ' px';
      default: return value + '%';
    }
  };
}

function mountSliderStudy(root, { onChange, formatValue } = {}) {
  if (onChange !== undefined && typeof onChange !== 'function') throw new TypeError('onChange must be a function.');
  const lifecycle = new AbortController();
  const roots = root.matches('.sl-slider') ? [root] : [...root.querySelectorAll('.sl-slider')];
  const instances = roots.map(root => {
    const input = root.querySelector('input'), format = formatValue ?? formatter(root);
    root.addEventListener('input', () => { root.dataset.interacted = 'true'; }, { signal: lifecycle.signal });
    const choices = [...root.querySelectorAll('[data-rating],button[data-step]')];
    const waveform = [...root.querySelectorAll('.scrub-art span')];
    let playedBars = -1;
    const update = values => {
      const value = values[0], fraction = (value - Number(input.min)) / (Number(input.max) - Number(input.min));
      root.style.setProperty('--fraction', String(fraction));
      root.style.setProperty('--preview-value', value + 'px');
      root.style.setProperty('--zoom', String(value / 100));
      root.querySelector('[data-bubble]')?.replaceChildren(format(value));
      for (const choice of choices) {
        const n = Number(choice.dataset.rating ?? choice.dataset.step);
        if (choice.hasAttribute('data-rating')) {
          const star = choice.querySelector('svg');
          star.classList.toggle('is-filled', n <= value);
          star.style.setProperty('--star-fill', String(Math.max(0, Math.min(1, value - n + 1))));
          choice.setAttribute('aria-checked', String(n === value));
          choice.tabIndex = n === Math.round(value) ? 0 : -1;
        } else choice.setAttribute('aria-pressed', String(n === value));
      }
      const count = Math.round(fraction * waveform.length);
      if (count !== playedBars) {
        waveform.forEach((bar, index) => bar.classList.toggle('is-played', index < count));
        playedBars = count;
      }
    };
    const controller = mountSlider(root, { formatValue: format, onChange: values => { update(values); onChange?.(values); } });
    const setValue = value => {
      if (input.disabled) return;
      input.value = String(value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    };
    for (const choice of choices) {
      choice.addEventListener('click', () => setValue(Number(choice.dataset.rating ?? choice.dataset.step)), { signal: lifecycle.signal });
      if (!choice.hasAttribute('data-rating')) continue;
      choice.addEventListener('keydown', event => {
        const offset = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1 }[event.key];
        if (offset === undefined && !['Home','End'].includes(event.key)) return;
        event.preventDefault();
        const index = choices.indexOf(choice);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? choices.length - 1 : (index + offset + choices.length) % choices.length;
        choices[next].focus(); choices[next].click();
      }, { signal: lifecycle.signal });
    }
    const play = root.querySelector('[data-play]');
    let timer = null, startedAt = 0, startValue = 0;
    const pause = () => {
      cancelAnimationFrame(timer); timer = null;
      if (!play) return;
      root.dataset.playing = 'false';
      play.setAttribute('aria-label', 'Play preview');
      play.querySelector('[data-play-label]').textContent = 'Play';
    };
    if (play) {
      play.addEventListener('click', () => {
        if (timer !== null) { pause(); return; }
        if (input.valueAsNumber >= Number(input.max)) setValue(Number(input.min));
        startedAt = performance.now(); startValue = input.valueAsNumber;
        root.dataset.playing = 'true'; play.setAttribute('aria-label','Pause preview');
        play.querySelector('[data-play-label]').textContent = 'Pause';
        // Use elapsed time, not accumulated steps; render once per display frame.
        // No animation loop remains active on pause, reset or a hidden page.
        const tick = now => {
          const value = Math.min(Number(input.max), startValue + (now - startedAt) / 1000);
          if (value !== input.valueAsNumber) {
            input.value = String(value);
            input.dispatchEvent(new Event('input', { bubbles: true }));
          }
          if (value >= Number(input.max)) pause();
          else timer = requestAnimationFrame(tick);
        };
        timer = requestAnimationFrame(tick);
      }, { signal: lifecycle.signal });
      input.addEventListener('input', event => {
        if (event.isTrusted && timer !== null) { startedAt = performance.now(); startValue = input.valueAsNumber; }
      }, { signal: lifecycle.signal });
      input.addEventListener('keydown', event => {
        const direction = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1 }[event.key];
        if (direction === undefined) return;
        event.preventDefault();
        // Fine playback precision must not make keyboard seeking impractical.
        setValue(Math.max(Number(input.min), Math.min(Number(input.max), input.valueAsNumber + direction)));
        if (timer !== null) { startedAt = performance.now(); startValue = input.valueAsNumber; }
      }, { signal: lifecycle.signal });
      document.addEventListener('visibilitychange', () => { if (document.hidden) pause(); }, { signal: lifecycle.signal });
    }
    update(controller.values);
    return { get values() { return controller.values; }, sync() { controller.sync(); update(controller.values); }, pause, reset() { pause(); controller.reset(); update(controller.values); }, destroy() { pause(); controller.destroy(); } };
  });
  const sync = () => instances.forEach(instance => instance.sync());
  window.addEventListener('pageshow', sync, { signal: lifecycle.signal });
  window.addEventListener('pagehide', () => instances.forEach(instance => instance.pause()), { signal: lifecycle.signal });
  return {
    get values() { return instances.flatMap(instance => instance.values); },
    sync,
    reset() { instances.forEach(instance => instance.reset()); },
    destroy() { lifecycle.abort(); instances.forEach(instance => instance.destroy()); }
  };
}


window.MatteSlider = { mount: mountSliderStudy };
})();

/* No runtime dependencies. Each mount has its own state. */
(() => {
const duration = {
  "short-2": 100,
  "short-4": 200,
  "medium-3": 350,
  "medium-4": 400,
  "long-4": 600
};
const easing = {
  "linear": "cubic-bezier(0,0,1,1)",
  "ease-in-out-gentle": "cubic-bezier(0.4,0,0.2,1)"
};
// The original approved motion, scoped to one component instance.
const matteButtonInstances = new WeakMap();
function mountMatteButtons(root, { onSave, onCancel = () => {}, previewDuration = 0, confirmationMs = 1400 } = {}) {
  if (matteButtonInstances.has(root)) return matteButtonInstances.get(root);
  const save = root.querySelector('[data-action="save"]');
  const cancel = root.querySelector('[data-action="cancel"]');
  const fill = save?.querySelector('.bk-progress');
  const wash = cancel?.querySelector('.bk-wash');
  const feedback = root.querySelector('[data-feedback]');
  if (save && typeof onSave !== 'function') throw new TypeError('Pass your async onSave function.');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const events = new AbortController();
  const retiringFills = new Map();
  const pressTimers = new Set();
  let operation, saveReset, cancelReset, progressAnimation, washAnimation;
  let destroyed = false;

  function state(button, value, name) {
    if (!button) return;
    if (button === save && button.dataset.state !== value) {
      button.dataset.transition = button.dataset.state === 'saved' && value === 'idle' ? 'return' : 'default';
    }
    button.dataset.state = value;
    button.setAttribute('aria-label', name);
  }
  function announce(message, error = false) {
    if (!feedback) return;
    feedback.textContent = message;
    feedback.toggleAttribute('data-error', error);
  }
  function unlock() {
    save?.setAttribute('aria-disabled', 'false');
    save?.setAttribute('aria-busy', 'false');
  }
  function resetProgress(animate = false) {
    if (!fill) return;
    // Only sample a visible animation that actually needs a fade-out.
    // Idle/reset/destroy must not force a style calculation.
    const style = animate && !reduced.matches && progressAnimation ? getComputedStyle(fill) : null;
    const transform = style?.transform || 'none';
    const opacity = style ? Number(style.opacity) : 0;
    const width = transform === 'none' ? 0 : new DOMMatrixReadOnly(transform).a;
    if (animate && !reduced.matches && width > 0.001 && opacity > 0) {
      const retiring = fill.cloneNode(false);
      retiring.classList.add('bk-retiring');
      retiring.style.transform = transform;
      retiring.style.opacity = String(opacity);
      fill.before(retiring);
      const animation = retiring.animate([{ opacity }, { opacity: 0 }], {
        duration: save.dataset.transition === 'return' ? duration['long-4'] : duration['medium-4'],
        easing: easing['ease-in-out-gentle'], fill: 'forwards',
      });
      retiringFills.set(retiring, animation);
      animation.onfinish = () => {
        retiring.remove(); retiringFills.delete(retiring); animation.cancel();
      };
    }
    progressAnimation?.cancel();
    progressAnimation = null;
    if (!animate || reduced.matches) {
      for (const [node, animation] of retiringFills) { animation.cancel(); node.remove(); }
      retiringFills.clear();
    }
  }
  async function startSave() {
    if (destroyed || save.disabled || save.getAttribute('aria-disabled') === 'true') return;
    clearTimeout(saveReset); clearTimeout(cancelReset);
    washAnimation?.cancel();
    state(cancel, 'idle', 'Cancel');
    resetProgress(true);
    const current = new AbortController();
    operation = current;
    state(save, 'saving', 'Running simulation');
    save.setAttribute('aria-disabled', 'true');
    save.setAttribute('aria-busy', 'true');
    announce('Running simulation.');
    // Optional determinate animation for a demo with a known duration.
    // Leave previewDuration at zero for a real request of unknown length.
    if (!reduced.matches && previewDuration > duration['short-4']) {
      const start = duration['short-4'] / previewDuration;
      progressAnimation = fill.animate([
        { transform: 'scaleX(0)', opacity: 0 },
        { transform: `scaleX(${start})`, opacity: 1, offset: start },
        { transform: 'scaleX(1)', opacity: 1 },
      ], { duration: previewDuration, easing: easing.linear, fill: 'forwards' });
    }
    try {
      await onSave({ signal: current.signal });
      if (operation !== current || current.signal.aborted) return;
      operation = null;
      if (!progressAnimation && !reduced.matches) {
        progressAnimation = fill.animate([{ transform: 'scaleX(1)', opacity: 0 }, { transform: 'scaleX(1)', opacity: 1 }], {
          duration: duration['medium-3'], easing: easing['ease-in-out-gentle'], fill: 'forwards',
        });
      }
      state(save, 'saved', 'Forecast updated');
      save.setAttribute('aria-busy', 'false');
      announce('Forecast updated.');
      saveReset = setTimeout(() => {
        state(save, 'idle', 'Run simulation'); unlock(); resetProgress(true);
      }, confirmationMs);
    } catch (error) {
      if (operation !== current) return;
      operation = null;
      state(save, 'idle', 'Run simulation'); unlock(); resetProgress(true);
      announce(error?.name === 'AbortError' ? 'Simulation cancelled.' : 'Could not run the simulation. Try again.', true);
    }
  }
  function cancelSave() {
    if (destroyed || cancel.disabled || cancel.getAttribute('aria-disabled') === 'true') return;
    const wasSaving = Boolean(operation);
    operation?.abort(); operation = null;
    clearTimeout(saveReset); clearTimeout(cancelReset);
    state(save, 'idle', 'Run simulation'); unlock(); resetProgress(true);
    state(cancel, 'cancelled', 'Cancelled');
    washAnimation?.cancel();
    if (wash && !reduced.matches) {
      washAnimation = wash.animate([{ opacity: 0 }, { opacity: 0.35, offset: 0.5 }, { opacity: 0 }], {
        duration: duration['medium-4'], easing: easing['ease-in-out-gentle'],
      });
      const active = washAnimation;
      active.onfinish = () => { if (washAnimation === active) washAnimation = null; active.cancel(); };
    }
    announce(wasSaving ? 'Simulation cancelled.' : 'Cancelled.');
    cancelReset = setTimeout(() => state(cancel, 'idle', 'Cancel'), confirmationMs);
    onCancel({ wasSaving });
  }
  save?.addEventListener('click', startSave, { signal: events.signal });
  cancel?.addEventListener('click', cancelSave, { signal: events.signal });
  for (const button of [save, cancel].filter(Boolean)) {
    button.disabled = false;
    button.addEventListener('keydown', event => {
      if (event.key !== 'Enter' || event.repeat || button.getAttribute('aria-disabled') === 'true') return;
      button.classList.add('is-pressed');
      const timer = setTimeout(() => { button.classList.remove('is-pressed'); pressTimers.delete(timer); }, duration['short-2']);
      pressTimers.add(timer);
    }, { signal: events.signal });
    button.addEventListener('blur', () => button.classList.remove('is-pressed'), { signal: events.signal });
  }
  reduced.addEventListener('change', () => {
    if (reduced.matches) { resetProgress(); washAnimation?.cancel(); }
  }, { signal: events.signal });
  const controller = {
    destroy() {
      if (destroyed) return;
      destroyed = true;
      events.abort(); operation?.abort(); operation = null;
      clearTimeout(saveReset); clearTimeout(cancelReset);
      pressTimers.forEach(clearTimeout); pressTimers.clear();
      resetProgress(); washAnimation?.cancel();
      for (const button of [save, cancel].filter(Boolean)) { button.classList.remove('is-pressed'); button.disabled = true; }
      matteButtonInstances.delete(root);
    },
  };
  matteButtonInstances.set(root, controller);
  return controller;
}

window.MatteButtons = { mount: mountMatteButtons };
})();
