/* Command palette: one keyboard entry to every workbench surface. */
(function () {
  "use strict";
  var root = document.getElementById("ds-palette");
  if (!root) return;
  var input = document.getElementById("ds-palette-input");
  var list = document.getElementById("ds-palette-list");
  var items = [
    { label: "新建修复内容", hint: "空白草稿", href: "/agent/new?new=1", keys: "new task draft repair" },
    { label: "修复工作台", hint: "任务与最近记录", href: "/agent", keys: "workbench home tasks" },
    { label: "连接器与插件", hint: "接入工具链", href: "/connectors", keys: "connectors plugins tools integrations" },
    { label: "方法与工具", hint: "二进制修复证据链", href: "/methods", keys: "methods tools binary ghidra" },
    { label: "设置", hint: "主题、密度、发送键", href: "/settings", keys: "settings preferences theme" },
    { label: "使用说明", hint: "完整操作手册", href: "/guide", keys: "guide manual help docs" },
    { label: "切换浅色主题", hint: "外观", action: "theme-light", keys: "light theme" },
    { label: "切换深色主题", hint: "外观", action: "theme-dark", keys: "dark theme" },
    { label: "跟随系统主题", hint: "外观", action: "theme-system", keys: "system theme" },
    { label: "收起 / 展开侧栏", hint: "布局", action: "sidebar", keys: "sidebar layout collapse" }
  ];
  if (document.body && document.body.classList.contains("agent-focus")) {
    items.push({ label: "退出专注对话", hint: "布局", action: "focus" });
  }
  var filtered = items.slice();
  var active = 0;

  function close() {
    root.hidden = true;
    document.body.classList.remove("palette-open");
  }
  function open() {
    root.hidden = false;
    document.body.classList.add("palette-open");
    input.value = "";
    filtered = items.slice();
    active = 0;
    render();
    input.focus();
  }
  function act(item) {
    if (item.href) { window.location.href = item.href; return; }
    if (item.action === "theme-light" || item.action === "theme-dark" || item.action === "theme-system") {
      var value = item.action.replace("theme-", "");
      var themeButton = document.querySelector('[data-theme-set="' + value + '"]');
      if (themeButton) {
        themeButton.click();
      } else {
        try { window.localStorage.setItem("br.theme", value); } catch (error) { /* ignore */ }
        document.documentElement.dataset.theme = value === "system"
          ? (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
          : value;
      }
      close();
      return;
    }
    if (item.action === "sidebar") {
      var toggle = document.getElementById("ds-sidebar-toggle");
      var expand = document.getElementById("ds-sidebar-expand");
      if (document.documentElement.classList.contains("side-collapsed")) { if (expand) expand.click(); }
      else if (toggle) { toggle.click(); }
      close();
      return;
    }
    if (item.action === "focus") {
      var exit = document.getElementById("agent-focus-exit");
      if (exit) exit.click();
      close();
    }
  }
  function render() {
    list.textContent = "";
    if (!filtered.length) {
      var empty = document.createElement("p");
      empty.className = "ds-palette-empty";
      empty.textContent = "没有匹配的命令。";
      list.appendChild(empty);
      return;
    }
    filtered.forEach(function (item, index) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "ds-palette-item" + (index === active ? " is-active" : "");
      var name = document.createElement("span");
      name.className = "ds-palette-name";
      name.textContent = item.label;
      var hint = document.createElement("span");
      hint.className = "ds-palette-hint-inline";
      hint.textContent = item.hint;
      button.appendChild(name);
      button.appendChild(hint);
      button.addEventListener("click", function () { act(item); });
      list.appendChild(button);
    });
  }
  function filter() {
    var query = input.value.trim().toLowerCase();
    filtered = items.filter(function (item) {
      return (item.label + " " + item.hint + " " + (item.keys || "")).toLowerCase().indexOf(query) >= 0;
    });
    active = 0;
    render();
  }
  document.querySelectorAll("[data-palette-open]").forEach(function (button) {
    button.addEventListener("click", open);
  });
  document.addEventListener("keydown", function (event) {
    var meta = event.metaKey || event.ctrlKey;
    if (meta && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (root.hidden) open(); else close();
      return;
    }
    if (root.hidden) return;
    if (event.key === "Escape") { event.preventDefault(); close(); return; }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      active = Math.min(active + 1, filtered.length - 1);
      render();
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      active = Math.max(active - 1, 0);
      render();
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (filtered[active]) act(filtered[active]);
    }
  });
  if (input) input.addEventListener("input", filter);
  root.addEventListener("click", function (event) {
    if (event.target === root) close();
  });
  render();
})();
