/* ============================================================================
 * 侧栏任务列表（所有工作台页面共用）。
 *
 * 数据来源：本机 localStorage + 逐条 GET /api/jobs/{id} 核对状态。
 * 新增：搜索、置顶、重命名、删除本机记录；排序受设置页控制。
 * 删除只移除本机记录，不删除后端任务。
 * ========================================================================== */
(function () {
  "use strict";

  var KEY = "br.jobs.v1";
  var LIMIT = 50;
  var query = "";

  var PHASE_LABELS = {
    draft: "草稿", queued: "排队中", running: "执行中", screening: "输入筛查",
    analyzing: "二进制分析", roundtrip: "C 往返校验", generating: "候选生成",
    applying: "写入副本", validating: "共享校验", finished: "已完成"
  };

  function read() {
    try {
      var raw = window.localStorage.getItem(KEY);
      if (!raw) return [];
      var list = JSON.parse(raw);
      if (!Array.isArray(list)) return [];
      return list.filter(function (item) {
        return item && typeof item.id === "string" && item.id;
      });
    } catch (error) {
      return [];
    }
  }

  function write(list) {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(list.slice(0, LIMIT)));
    } catch (error) { /* ignore */ }
  }

  function settings() {
    try { return window.BRSettings ? window.BRSettings.get() : {}; } catch (error) { return {}; }
  }

  function labelOf(entry) {
    return entry.label || "任务 " + entry.id.slice(0, 8);
  }

  function sortList(list) {
    var sort = settings().taskSort || "recent";
    return list.slice().sort(function (left, right) {
      if (!!left.pinned !== !!right.pinned) { return left.pinned ? -1 : 1; }
      if (sort === "name") { return labelOf(left).localeCompare(labelOf(right), "zh-CN"); }
      return new Date(right.added_at || 0) - new Date(left.added_at || 0);
    });
  }

  function currentId() {
    if (document.body && document.body.dataset.jobId) return document.body.dataset.jobId;
    var match = /^\/agent\/([^/?#]+)$/.exec(window.location.pathname);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function actionButton(label, title, className) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "ds-job-action " + (className || "");
    button.textContent = label;
    button.title = title;
    button.setAttribute("aria-label", title);
    return button;
  }

  function render() {
    var nav = document.getElementById("ds-job-list");
    if (!nav) return;
    var list = sortList(read()).filter(function (entry) {
      if (!query) return true;
      return (labelOf(entry) + " " + entry.id).toLowerCase().indexOf(query) >= 0;
    });
    nav.textContent = "";

    if (!list.length) {
      var empty = document.createElement("p");
      empty.className = "ds-job-empty";
      empty.dataset.testid = "ds-job-empty";
      empty.textContent = query ? "没有匹配的任务。" : "还没有本机任务记录。";
      nav.appendChild(empty);
      return;
    }

    var active = currentId();
    list.forEach(function (entry) {
      var row = document.createElement("div");
      row.className = "ds-job-row";
      row.dataset.jobId = entry.id;

      var link = document.createElement("a");
      link.className = "ds-job-item";
      link.href = "/agent/" + encodeURIComponent(entry.id);
      link.dataset.jobId = entry.id;
      link.dataset.testid = "ds-job-item";
      if (entry.id === active) { link.classList.add("is-active"); }

      var dot = document.createElement("span");
      dot.className = "ds-job-dot";
      dot.setAttribute("aria-hidden", "true");

      var name = document.createElement("span");
      name.className = "ds-job-name";
      name.textContent = (entry.pinned ? "★ " : "") + labelOf(entry);
      name.title = entry.id;

      var phase = document.createElement("span");
      phase.className = "ds-job-phase";
      phase.textContent = "";

      link.appendChild(dot);
      link.appendChild(name);
      link.appendChild(phase);
      row.appendChild(link);

      var actions = document.createElement("span");
      actions.className = "ds-job-actions";
      var pin = actionButton(entry.pinned ? "★" : "☆", entry.pinned ? "取消置顶" : "置顶任务", "ds-job-pin");
      pin.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        togglePin(entry.id);
      });
      var rename = actionButton("✎", "重命名任务", "ds-job-edit");
      rename.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        renameTask(entry.id);
      });
      var remove = actionButton("×", "删除本机记录", "ds-job-remove");
      remove.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        removeTask(entry.id);
      });
      actions.appendChild(pin);
      actions.appendChild(rename);
      actions.appendChild(remove);
      row.appendChild(actions);
      nav.appendChild(row);
    });

    hydrate(nav);
  }

  function hydrate(nav) {
    Array.prototype.forEach.call(nav.querySelectorAll(".ds-job-item"), function (item) {
      var id = item.dataset.jobId;
      fetch("/api/jobs/" + encodeURIComponent(id), { headers: { Accept: "application/json" } })
        .then(function (response) {
          if (response.status === 404) { mark(item, "missing", "后端已不可查"); return null; }
          if (!response.ok) throw new Error("HTTP " + response.status);
          return response.json();
        })
        .then(function (data) {
          if (!data) return;
          if (data.cancelled) { mark(item, "cancelled", "已取消"); return; }
          var phase = data.phase || "unknown";
          mark(item, phase, PHASE_LABELS[phase] || phase);
        })
        .catch(function () { mark(item, "unreachable", "状态未知"); });
    });
  }

  var states = {};

  function mark(item, state, text) {
    item.dataset.state = state;
    var phase = item.querySelector(".ds-job-phase");
    if (phase) phase.textContent = text;
    states[item.dataset.jobId] = { state: state, text: text };
    document.dispatchEvent(new CustomEvent("brjobs:state", {
      detail: { id: item.dataset.jobId, state: state, text: text }
    }));
  }

  function findEntry(list, id) {
    for (var index = 0; index < list.length; index += 1) {
      if (list[index].id === id) return list[index];
    }
    return null;
  }

  function register(id, label) {
    if (!id || typeof id !== "string") return;
    var list = read();
    var existing = findEntry(list, id);
    var entry = {
      id: id,
      label: label || (existing && existing.label) || "",
      added_at: (existing && existing.added_at) || new Date().toISOString(),
      pinned: !!(existing && existing.pinned)
    };
    var rest = list.filter(function (item) { return item.id !== id; });
    write([entry].concat(rest));
    render();
  }

  function togglePin(id) {
    var list = read();
    var entry = findEntry(list, id);
    if (!entry) return;
    entry.pinned = !entry.pinned;
    write(list);
    render();
  }

  function renameTask(id) {
    var list = read();
    var entry = findEntry(list, id);
    if (!entry) return;
    var value = window.prompt("任务名称（留空恢复默认）", labelOf(entry));
    if (value === null) return;
    entry.label = value.trim().slice(0, 60);
    write(list);
    render();
  }

  function removeTask(id) {
    var list = read();
    var entry = findEntry(list, id);
    if (!entry) return;
    if (!window.confirm("从本机列表移除「" + labelOf(entry) + "」？后端任务不会被删除。")) return;
    write(list.filter(function (item) { return item.id !== id; }));
    if (currentId() === id) { window.location.href = "/agent"; return; }
    render();
  }

  window.BRJobs = {
    register: register,
    render: render,
    currentId: currentId,
    list: function () { return sortList(read()); },
    togglePin: togglePin,
    rename: renameTask,
    remove: removeTask,
    states: states
  };

  var search = document.getElementById("ds-job-search");
  if (search) {
    search.addEventListener("input", function () {
      query = search.value.trim().toLowerCase();
      render();
    });
  }
  document.addEventListener("brsettings:change", render);

  render();

  window.addEventListener("storage", function (event) {
    if (event.key === KEY) render();
  });
})();
