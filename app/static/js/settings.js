/* Local operator preferences. Values never leave this browser. */
(function () {
  "use strict";
  var KEY = "br.settings.v1";
  var DEFAULTS = {
    density: "comfortable",
    fontSize: "default",
    codeFont: 12,
    animations: true,
    sendKey: "enter",
    autoScroll: true,
    taskSort: "recent",
    historySync: true,
    defaultWorkspace: "",
    defaultMode: ""
  };
  var FONT_SIZES = { small: "13px", "default": "13.5px", large: "15px" };

  function read() {
    var base = {};
    Object.keys(DEFAULTS).forEach(function (key) { base[key] = DEFAULTS[key]; });
    try {
      var raw = window.localStorage.getItem(KEY);
      if (raw) {
        var stored = JSON.parse(raw);
        if (stored && typeof stored === "object") {
          Object.keys(DEFAULTS).forEach(function (key) {
            if (stored[key] !== undefined) { base[key] = stored[key]; }
          });
        }
      }
    } catch (error) { /* defaults */ }
    return base;
  }

  function apply(settings) {
    var root = document.documentElement;
    root.dataset.density = settings.density || "comfortable";
    root.dataset.motion = settings.animations === false ? "off" : "on";
    root.dataset.font = settings.fontSize || "default";
    root.style.setProperty("--chat-font-size", FONT_SIZES[settings.fontSize] || "13.5px");
    root.style.setProperty("--code-font-size", String(settings.codeFont || 12) + "px");
  }

  function save(settings) {
    var next = settings;
    try {
      window.localStorage.setItem(KEY, JSON.stringify(next));
    } catch (error) { /* storage unavailable */ }
    apply(next);
    document.dispatchEvent(new CustomEvent("brsettings:change", { detail: next }));
    return next;
  }

  function reset() {
    try { window.localStorage.removeItem(KEY); } catch (error) { /* ignore */ }
    apply(DEFAULTS);
    document.dispatchEvent(new CustomEvent("brsettings:change", { detail: DEFAULTS }));
    return DEFAULTS;
  }

  window.BRSettings = {
    get: read,
    save: save,
    reset: reset,
    defaults: DEFAULTS,
    apply: apply
  };

  apply(read());

  var page = document.getElementById("settings-page");
  if (!page) { return; }

  var current = read();
  var saved = document.getElementById("settings-saved");
  var flash = function () {
    if (!saved) return;
    saved.hidden = false;
    window.setTimeout(function () { saved.hidden = true; }, 1200);
  };
  var syncControls = function () {
    page.querySelectorAll("[data-setting]").forEach(function (input) {
      var key = input.dataset.setting;
      if (input.type === "checkbox") { input.checked = !!current[key]; }
      else { input.value = String(current[key]); }
    });
    var output = document.getElementById("code-font-value");
    if (output) { output.textContent = String(current.codeFont) + " px"; }
  };

  page.querySelectorAll("[data-setting]").forEach(function (input) {
    var key = input.dataset.setting;
    var commit = function () {
      if (input.type === "checkbox") { current[key] = input.checked; }
      else if (key === "codeFont") { current[key] = Number(input.value); }
      else { current[key] = input.value; }
      save(current);
      syncControls();
      flash();
    };
    input.addEventListener(input.type === "range" ? "input" : "change", commit);
  });

  var file = document.getElementById("settings-import-file");
  var exportButton = document.getElementById("settings-export");
  if (exportButton) {
    exportButton.addEventListener("click", function () {
      var blob = new Blob([JSON.stringify(read(), null, 2)], { type: "application/json" });
      var url = URL.createObjectURL(blob);
      var link = document.createElement("a");
      link.href = url;
      link.download = "binaryrepair-settings.json";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    });
  }
  if (file) {
    file.addEventListener("change", function () {
      var selected = file.files && file.files[0];
      if (!selected) return;
      selected.text().then(function (text) {
        var parsed = JSON.parse(text);
        var next = read();
        Object.keys(DEFAULTS).forEach(function (key) {
          if (parsed && parsed[key] !== undefined) { next[key] = parsed[key]; }
        });
        current = save(next);
        syncControls();
        flash();
      }).catch(function () {
        var status = document.getElementById("settings-status");
        if (status) status.textContent = "导入失败：不是有效的设置 JSON。";
      });
      file.value = "";
    });
  }

  var resetButton = document.getElementById("settings-reset");
  if (resetButton) {
    resetButton.addEventListener("click", function () {
      if (!window.confirm("恢复所有本机设置为默认值？")) return;
      current = reset();
      syncControls();
      flash();
    });
  }

  function clearTasks() {
    if (!window.confirm("清空本浏览器记录的任务列表？服务端任务不会被删除。")) return;
    try { window.localStorage.removeItem("br.jobs.v1"); } catch (error) { /* ignore */ }
    window.location.reload();
  }
  function clearLocalHistory() {
    if (!window.confirm("清空本浏览器缓存的对话记录？服务端历史不会被删除。")) return;
    try {
      var keys = [];
      for (var index = 0; index < window.localStorage.length; index += 1) {
        var key = window.localStorage.key(index);
        if (key && key.indexOf("br.msg.v1.") === 0) { keys.push(key); }
      }
      keys.forEach(function (key) { window.localStorage.removeItem(key); });
    } catch (error) { /* ignore */ }
    window.location.reload();
  }
  function clearServerHistory() {
    if (!window.confirm("删除服务端保存的所有任务/草稿对话？此操作无法恢复。")) return;
    var meta = document.querySelector('meta[name="csrf"]');
    var csrf = meta ? meta.getAttribute("content") : "";
    fetch("/api/history", { method: "DELETE", headers: { "X-CSRF-Token": csrf } })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (payload) {
        var status = document.getElementById("settings-status");
        if (status) status.textContent = "已清空 " + (payload.deleted || 0) + " 个服务端对话。";
      })
      .catch(function (error) {
        var status = document.getElementById("settings-status");
        if (status) status.textContent = "清空失败：" + error.message;
      });
  }

  ["settings-clear-tasks", "settings-clear-local", "settings-clear-server"].forEach(function (id) {
    var button = document.getElementById(id);
    if (!button) return;
    button.addEventListener("click", function () {
      if (id === "settings-clear-tasks") clearTasks();
      if (id === "settings-clear-local") clearLocalHistory();
      if (id === "settings-clear-server") clearServerHistory();
    });
  });

  syncControls();
})();
