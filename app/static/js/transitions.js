/* Subtle sci-fi page transition. Navigation is delayed at most 130 ms; users
   with reduced motion or the "animations off" preference keep instant links. */
(function () {
  "use strict";
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduced || document.documentElement.dataset.motion === "off") return;

  var overlay = document.createElement("div");
  overlay.className = "ds-warp";
  overlay.setAttribute("aria-hidden", "true");
  document.body.appendChild(overlay);

  window.requestAnimationFrame(function () {
    document.body.classList.add("page-enter");
  });

  document.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var link = event.target.closest ? event.target.closest("a") : null;
    if (!link || !link.href || link.target || link.hasAttribute("download") || link.dataset.noWarp !== undefined) return;
    var url;
    try { url = new URL(link.href, window.location.href); } catch (error) { return; }
    if (url.origin !== window.location.origin) return;
    if (url.pathname === window.location.pathname && url.search === window.location.search) return;
    event.preventDefault();
    document.body.classList.add("page-leaving");
    window.setTimeout(function () { window.location.href = link.href; }, 130);
  }, true);
})();
