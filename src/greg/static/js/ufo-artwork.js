export function initUfoArtwork() {
  const motion = matchMedia('(hover: hover) and (prefers-reduced-motion: no-preference)');
  document.querySelectorAll('.entry-stage, .balance-stage, .assistant-stage').forEach((stage) => {
    if (stage.querySelector('.ufo-scene')) return;
    const scene = document.createElement('div');
    scene.className = 'ufo-scene';
    scene.setAttribute('aria-hidden', 'true');
    scene.innerHTML = '<span class="ufo-float ufo-near"><img src="/static/art/chrome-ufo.svg" alt="" draggable="false"></span><span class="ufo-float ufo-far"><img src="/static/art/chrome-ufo.svg" alt="" draggable="false"></span><span class="ufo-flyby"><img src="/static/art/chrome-ufo.svg" alt="" draggable="false"></span>';
    stage.prepend(scene);
    stage.classList.add('ufo-backdrop');
    function reset() {
      scene.style.setProperty('--ufo-tilt', '0deg');
      scene.style.setProperty('--ufo-shift', '0px');
    }
    stage.addEventListener('pointermove', (event) => {
      if (!motion.matches || event.pointerType !== 'mouse') return;
      const bounds = stage.getBoundingClientRect();
      const amount = (event.clientX - bounds.left) / bounds.width - 0.5;
      scene.style.setProperty('--ufo-tilt', `${amount * 8}deg`);
      scene.style.setProperty('--ufo-shift', `${amount * 12}px`);
    }, { passive: true });
    stage.addEventListener('pointerleave', reset);
    motion.addEventListener('change', reset);
  });
  const card = document.getElementById('choose-csv');
  if (!card || card.querySelector('.ufo-scanner')) return;
  const scanner = document.createElement('span');
  scanner.className = 'ufo-scanner';
  scanner.setAttribute('aria-hidden', 'true');
  scanner.innerHTML = '<span class="ufo-beam"></span><img src="/static/art/chrome-ufo.svg" alt="" draggable="false">';
  card.append(scanner);
  function fitScanner() {
    const title = card.querySelector('.source-title');
    const gap = title.getBoundingClientRect().top - scanner.getBoundingClientRect().top - 16;
    scanner.style.height = `${Math.max(0, Math.min(250, gap))}px`;
  }
  new ResizeObserver(fitScanner).observe(card);
  document.fonts.ready.then(fitScanner);
  fitScanner();
}
