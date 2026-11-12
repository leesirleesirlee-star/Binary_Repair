/* Collapsible application sidebar. The state is persisted and applied before
 * paint by the inline boot script in base.html; this file only handles the
 * two buttons and the accessibility state. */
(function () {
  "use strict";
  var root = document.documentElement;
  var toggle = document.getElementById("ds-sidebar-toggle");
  var expand = document.getElementById("ds-sidebar-expand");
  var sidebar = document.getElementById("ds-sidebar");
  if (!toggle || !expand || !sidebar) return;

  var workspaceButton = document.getElementById("ds-workspace-button");
  var workspaceMenu = document.getElementById("ds-workspace-menu");
  if (workspaceButton && workspaceMenu) {
    var closeWorkspace = function () {
      workspaceMenu.hidden = true;
      workspaceButton.setAttribute("aria-expanded", "false");
    };
    workspaceButton.addEventListener("click", function (event) {
      event.stopPropagation();
      var open = !workspaceMenu.hidden;
      workspaceMenu.hidden = open;
      workspaceButton.setAttribute("aria-expanded", open ? "false" : "true");
    });
    document.addEventListener("click", function (event) {
      if (!workspaceMenu.hidden && !workspaceMenu.contains(event.target)) { closeWorkspace(); }
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !workspaceMenu.hidden) { closeWorkspace(); }
    });
  }

  function persist(collapsed) {
    try { window.localStorage.setItem("br.sidebar", collapsed ? "collapsed" : "open"); } catch (e) {}
  }

  function setCollapsed(collapsed, save) {
    root.classList.toggle("side-collapsed", collapsed);
    toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    expand.setAttribute("aria-expanded", collapsed ? "true" : "false");
    toggle.setAttribute("aria-label", collapsed ? "展开侧栏" : "收起侧栏");
    expand.setAttribute("aria-label", collapsed ? "展开侧栏" : "收起侧栏");
    if (save) { persist(collapsed); }
    if (save) {
      if (collapsed) {
        expand.focus({ preventScroll: true });
      } else {
        var first = sidebar.querySelector("a, button");
        if (first) { first.focus({ preventScroll: true }); }
      }
    }
  }

  setCollapsed(root.classList.contains("side-collapsed"), false);
  toggle.addEventListener("click", function () { setCollapsed(true, true); });
  expand.addEventListener("click", function () { setCollapsed(false, true); });
  document.addEventListener("keydown", function (event) {
    if ((event.metaKey || event.ctrlKey) && event.key === "\\") {
      event.preventDefault();
      setCollapsed(!root.classList.contains("side-collapsed"), true);
    }
  });

  var mobileMenu = document.getElementById("ds-mobile-menu");
  var scrim = document.getElementById("ds-scrim");
  var closeMobile = function () {
    sidebar.classList.remove("is-open");
    if (mobileMenu) mobileMenu.setAttribute("aria-expanded", "false");
    if (scrim) scrim.hidden = true;
    document.body.classList.remove("sidebar-open");
  };
  if (mobileMenu) {
    mobileMenu.addEventListener("click", function () {
      var open = !sidebar.classList.contains("is-open");
      sidebar.classList.toggle("is-open", open);
      mobileMenu.setAttribute("aria-expanded", open ? "true" : "false");
      if (scrim) scrim.hidden = !open;
      document.body.classList.toggle("sidebar-open", open);
    });
    if (scrim) scrim.addEventListener("click", closeMobile);
    var drawerClose = document.getElementById("ds-drawer-close");
    if (drawerClose) drawerClose.addEventListener("click", closeMobile);
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeMobile();
    });
    sidebar.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", closeMobile);
    });
  }

})();
