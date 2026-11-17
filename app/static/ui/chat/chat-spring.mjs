// Animate the clipping edge of an overlay and keep the content grid stable.
export function bindSidebarSpring(sidebar, toggle) {
  const hover = matchMedia('(hover: hover) and (pointer: fine)');
  const motion = matchMedia('(prefers-reduced-motion: reduce)');
  let hoverOpen = false;
  let focusOpen = false;
  let pinned = false;
  let frame = 0;
  let position = 0;
  let velocity = 0;
  let previous = 0;
  let target = 0;

  function draw() {
    // Small overshoot is intentionally clipped at the usable bounds.
    sidebar.style.setProperty('--reveal', Math.max(0, Math.min(1, position)));
  }

  function step(now) {
    const dt = Math.min((now - previous) / 1000, .024);
    previous = now;
    const acceleration = 520 * (target - position) - 36 * velocity;
    velocity += acceleration * dt;
    position += velocity * dt;
    if (Math.abs(target - position) < .003 && Math.abs(velocity) < .025) {
      position = target;
      velocity = 0;
      frame = 0;
    } else frame = requestAnimationFrame(step);
    draw();
  }

  function sync() {
    target = hoverOpen || focusOpen || pinned ? 1 : 0;
    sidebar.dataset.expanded = String(Boolean(target));
    toggle.setAttribute('aria-expanded', String(Boolean(target)));
    toggle.setAttribute('aria-pressed', String(pinned));
    if (motion.matches) {
      cancelAnimationFrame(frame);
      frame = 0;
      position = target;
      velocity = 0;
      draw();
      return;
    }
    if (!frame) {
      previous = performance.now();
      frame = requestAnimationFrame(step);
    }
  }

  sidebar.addEventListener('pointerenter', event => {
    if (!hover.matches || event.pointerType === 'touch') return;
    hoverOpen = true;
    sync();
  });
  sidebar.addEventListener('pointerleave', () => {
    hoverOpen = false;
    sync();
  });
  sidebar.addEventListener('focusin', () => {
    if (!sidebar.matches(':focus-within') || !sidebar.querySelector(':focus-visible')) return;
    focusOpen = true;
    sync();
  });
  sidebar.addEventListener('focusout', event => {
    if (sidebar.contains(event.relatedTarget)) return;
    focusOpen = false;
    sync();
  });
  toggle.addEventListener('click', () => {
    pinned = !pinned;
    if (!pinned) {
      hoverOpen = false;
      focusOpen = false;
    }
    sync();
  });
  const owner = new AbortController();
  motion.addEventListener('change', sync, {signal: owner.signal});
  sync();
  return () => {
    owner.abort();
    cancelAnimationFrame(frame);
  };
}
