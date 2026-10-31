/* Two-step setup wizard: landing choice cards -> sample/input -> contract -> submit. */
(function () {
  "use strict";
  var root = document.querySelector("[data-wizard]");
  if (!root) return;

  var form = root.querySelector("form");
  var views = Array.prototype.slice.call(root.querySelectorAll("[data-view]"));
  var error = null;

  function errorBox(view) {
    return view ? view.querySelector("[data-wizard-error]") : null;
  }

  function hideErrors() {
    root.querySelectorAll("[data-wizard-error]").forEach(function (box) {
      box.classList.add("is-hidden");
      box.textContent = "";
    });
  }

  function show(viewName, push) {
    hideErrors();
    views.forEach(function (view) {
      view.classList.toggle("is-hidden", view.dataset.view !== viewName);
    });
    root.dataset.step = viewName;
    if (push) {
      var target = viewName === "home" ? "#setup" : "#" + viewName;
      try {
        history.replaceState(null, "", target);
      } catch (err) {
        /* file:// or restricted contexts: ignore */
      }
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function fail(viewName, message) {
    var view = root.querySelector('[data-view="' + viewName + '"]');
    var box = errorBox(view);
    if (box) {
      box.textContent = message;
      box.classList.remove("is-hidden");
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  root.querySelectorAll("[data-goto]").forEach(function (button) {
    button.addEventListener("click", function () {
      show(button.dataset.goto, true);
    });
  });

  root.querySelectorAll("[data-back]").forEach(function (button) {
    button.addEventListener("click", function () {
      show(button.dataset.back, true);
    });
  });

  var next = root.querySelector('[data-testid="wizard-next"]');
  if (next) {
    next.addEventListener("click", function () {
      var file = form.querySelector('input[type="file"][name="binary"]');
      var defect = form.querySelector('[name="defect"]');
      if (!file || !file.files || !file.files.length) {
        fail("input", "请先选择二进制样本文件。");
        return;
      }
      if (!defect || !defect.value.trim()) {
        fail("input", "请填写缺陷描述（英文）。");
        return;
      }
      show("contract", true);
    });
  }

  form.addEventListener("submit", function (event) {
    var file = form.querySelector('input[type="file"][name="binary"]');
    if (!file || !file.files || !file.files.length) {
      event.preventDefault();
      show("input", true);
      fail("input", "请先选择二进制样本文件。");
    }
  });

  var initial = (location.hash || "").replace("#", "");
  show(initial === "input" || initial === "contract" ? initial : "home", false);
})();
