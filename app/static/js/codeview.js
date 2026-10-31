/* Interactive code view: tab switching and vulnerability/change details. */
(function () {
  "use strict";
  document.querySelectorAll("[data-code-route]").forEach(function (card) {
    var tabs = Array.prototype.slice.call(card.querySelectorAll(".code-tab"));
    var panels = Array.prototype.slice.call(card.querySelectorAll(".code-panel"));
    var detail = card.querySelector(".code-detail");

    function show(view) {
      tabs.forEach(function (tab) {
        var active = tab.dataset.view === view;
        tab.classList.toggle("is-active", active);
        tab.setAttribute("aria-selected", active ? "true" : "false");
      });
      panels.forEach(function (panel) {
        panel.classList.toggle("is-hidden", panel.dataset.panel !== view);
      });
    }

    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        show(tab.dataset.view);
      });
    });

    card.querySelectorAll(".code-line[data-detail]").forEach(function (line) {
      function reveal() {
        if (!detail) return;
        var addr = line.dataset.addr ? line.dataset.addr + " · " : "";
        detail.textContent = addr + line.dataset.detail;
      }
      line.addEventListener("click", reveal);
      line.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          reveal();
        }
      });
    });
  });
})();
