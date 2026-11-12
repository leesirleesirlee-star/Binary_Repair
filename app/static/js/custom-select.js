/* Replace native selects with an accessible, sci-fi listbox while keeping the
   original <select> as the value source for settings.js. */
(function () {
  "use strict";
  var selects = document.querySelectorAll("select[data-setting]");
  if (!selects.length) return;

  var instances = [];

  function build(select) {
    var wrap = document.createElement("div");
    wrap.className = "ds-cselect";
    var trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "ds-cselect-trigger";
    trigger.setAttribute("role", "combobox");
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    var label = document.createElement("span");
    label.className = "ds-cselect-label";
    var caret = document.createElement("i");
    caret.className = "ds-cselect-caret";
    caret.setAttribute("aria-hidden", "true");
    trigger.appendChild(label);
    trigger.appendChild(caret);
    var menu = document.createElement("div");
    menu.className = "ds-cselect-menu";
    menu.setAttribute("role", "listbox");
    menu.hidden = true;
    wrap.appendChild(trigger);
    wrap.appendChild(menu);

    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);
    select.classList.add("ds-cselect-native");
    select.setAttribute("tabindex", "-1");

    var active = 0;
    function options() { return Array.prototype.slice.call(select.options); }
    function syncLabel() {
      var option = select.options[select.selectedIndex];
      label.textContent = option ? option.textContent : "";
      menu.querySelectorAll(".ds-cselect-option").forEach(function (node, index) {
        var selected = index === select.selectedIndex;
        node.classList.toggle("is-selected", selected);
        node.setAttribute("aria-selected", selected ? "true" : "false");
      });
    }
    function render() {
      menu.textContent = "";
      options().forEach(function (option, index) {
        var item = document.createElement("button");
        item.type = "button";
        item.className = "ds-cselect-option" + (index === select.selectedIndex ? " is-selected" : "") + (index === active ? " is-active" : "");
        item.setAttribute("role", "option");
        item.textContent = option.textContent;
        item.addEventListener("click", function () {
          select.selectedIndex = index;
          select.dispatchEvent(new Event("change", { bubbles: true }));
          syncLabel();
          close();
          trigger.focus();
        });
        item.addEventListener("mouseenter", function () { active = index; });
        menu.appendChild(item);
      });
      syncLabel();
    }
    function open() {
      menu.hidden = false;
      trigger.setAttribute("aria-expanded", "true");
      active = Math.max(0, select.selectedIndex);
      render();
      var current = menu.querySelectorAll(".ds-cselect-option")[active];
      if (current) current.scrollIntoView({ block: "nearest" });
    }
    function close() {
      menu.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
    }
    trigger.addEventListener("click", function (event) {
      event.stopPropagation();
      if (menu.hidden) open(); else close();
    });
    trigger.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { close(); return; }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (menu.hidden) { open(); return; }
        active = (active + (event.key === "ArrowDown" ? 1 : -1) + options().length) % options().length;
        menu.querySelectorAll(".ds-cselect-option").forEach(function (node, index) {
          node.classList.toggle("is-active", index === active);
        });
        var current = menu.querySelectorAll(".ds-cselect-option")[active];
        if (current) current.scrollIntoView({ block: "nearest" });
      }
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        if (menu.hidden) { open(); return; }
        select.selectedIndex = active;
        select.dispatchEvent(new Event("change", { bubbles: true }));
        syncLabel(); close();
      }
    });
    document.addEventListener("click", function (event) {
      if (!wrap.contains(event.target)) close();
    });
    select.addEventListener("change", syncLabel);
    instances.push({ select: select, sync: syncLabel, close: close });
    syncLabel();
  }

  selects.forEach(build);
  document.addEventListener("brsettings:change", function () {
    instances.forEach(function (item) { item.sync(); });
  });
  document.addEventListener("brlang:change", function () {
    instances.forEach(function (item) { item.sync(); });
  });
})();
