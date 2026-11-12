/* 主题：白天 / 夜晚 / 跟随系统。
 *
 * 首屏由各页 <head> 里的小段内联脚本先行落 data-theme，避免白闪；
 * 这里负责绑定三态按钮、跨页持久化，以及系统主题变化时的实时跟随。
 */
(function () {
  "use strict";

  var KEY = "br.theme";
  var root = document.documentElement;
  var query = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function readPref() {
    try {
      var value = window.localStorage.getItem(KEY);
      if (value === "light" || value === "dark" || value === "system") { return value; }
    } catch (error) { /* private mode */ }
    return "system";
  }

  function resolve(pref) {
    if (pref === "light" || pref === "dark") { return pref; }
    return query && query.matches ? "dark" : "light";
  }

  function paint(pref) {
    var theme = resolve(pref);
    root.setAttribute("data-theme", theme);
    root.setAttribute("data-theme-pref", pref);

    var buttons = document.querySelectorAll("[data-theme-set]");
    for (var index = 0; index < buttons.length; index += 1) {
      var on = buttons[index].getAttribute("data-theme-set") === pref;
      buttons[index].setAttribute("aria-pressed", on ? "true" : "false");
      if (on) { buttons[index].classList.add("is-active"); }
      else { buttons[index].classList.remove("is-active"); }
    }

    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) { meta.setAttribute("content", theme === "dark" ? "#0b0e14" : "#fbfcff"); }
  }

  function choose(pref) {
    try { window.localStorage.setItem(KEY, pref); } catch (error) { /* ignore */ }
    paint(pref);
  }

  function bind() {
    var buttons = document.querySelectorAll("[data-theme-set]");
    for (var index = 0; index < buttons.length; index += 1) {
      buttons[index].addEventListener("click", function () {
        choose(this.getAttribute("data-theme-set"));
      });
    }
    if (query) {
      var follow = function () {
        if (readPref() === "system") { paint("system"); }
      };
      if (query.addEventListener) { query.addEventListener("change", follow); }
      else if (query.addListener) { query.addListener(follow); }
    }
  }

  paint(readPref());
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { paint(readPref()); bind(); });
  } else {
    bind();
  }
})();
