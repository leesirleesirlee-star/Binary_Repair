/* Lightweight text-node i18n for the nine UI languages.
 * The source language is Simplified Chinese; translations are intentionally
 * limited to the product shell and primary controls so technical evidence and
 * model output are never machine-mangled. */
(function () {
  "use strict";
  var data = window.BR_I18N;
  if (!data) return;
  var NAMES = data.names || {};
  var sourceToId = {};
  Object.keys(data.keys).forEach(function (id) {
    sourceToId[data.keys[id].replace(/\s+/g, " ").trim()] = id;
  });
  var DICTS = {};
  var COMPACT = {};
  Object.keys(data.langs).forEach(function (lang) {
    DICTS[lang] = {};
    COMPACT[lang] = {};
    Object.keys(data.langs[lang]).forEach(function (id) {
      DICTS[lang][data.keys[id]] = data.langs[lang][id];
      COMPACT[lang][data.keys[id].replace(/\s+/g, "")] = data.langs[lang][id];
    });
  });

  // Auto-extracted page strings (Google-translated offline) fill the long tail
  // of rendered copy; curated strings above always win.
  var AUTO = window.BR_I18N_AUTO || {};
  Object.keys(AUTO).forEach(function (source) {
    var normalized = source.replace(/\s+/g, " ").trim();
    Object.keys(AUTO[source]).forEach(function (lang) {
      if (!DICTS[lang]) return;
      if (DICTS[lang][normalized] === undefined) DICTS[lang][normalized] = AUTO[source][lang];
      if (COMPACT[lang] && COMPACT[lang][normalized.replace(/\s+/g, "")] === undefined) {
        COMPACT[lang][normalized.replace(/\s+/g, "")] = AUTO[source][lang];
      }
    });
  });

  var originals = new WeakMap();
  var attrOriginals = new WeakMap();
  var current = "zh-Hans";
  var applying = false;
  var observer = null;

  function normalize(text) {
    return String(text).replace(/\s+/g, " ").trim();
  }
  function lookup(raw, lang) {
    if (lang === "zh-Hans") return null;
    var table = DICTS[lang];
    if (!table) return null;
    var core = normalize(raw);
    if (!core) return null;
    if (table[core]) return table[core];
    var stripped = core.replace(/^[＋+•·]+\s*/, "").replace(/\s*[→›]+\s*$/, "");
    if (table[stripped]) return raw.replace(core, table[stripped]);
    var compact = core.replace(/\s+/g, "");
    if (COMPACT[lang] && COMPACT[lang][compact]) return COMPACT[lang][compact];
    // Numbered headings such as "1 · 快速开始" keep their prefix but localize
    // the known label. Never replace a key inside a compound word.
    if (/^\d+\s*[·.]\s*/.test(core)) {
      var best = null;
      Object.keys(table).forEach(function (zh) {
        if (core.indexOf(zh) >= 0 && core.length <= zh.length + 18 && (!best || zh.length > best.length)) best = zh;
      });
      if (best) return raw.replace(best, table[best]);
    }
    return null;
  }
  function translateString(raw, lang) {
    if (!raw || !normalize(raw)) return null;
    var value = lookup(raw, lang);
    if (!value || value === raw) return null;
    var lead = raw.match(/^\s*/)[0];
    var trail = raw.match(/\s*$/)[0];
    return lead + value.trim() + trail;
  }

  function skipElement(element) {
    if (!element || element.nodeType !== 1) return false;
    return !!element.closest("script,style,noscript,code,pre,textarea,[data-i18n-ignore],.ds-lang-menu,.ds-code,.agent-code,.repair-diff");
  }
  function skipAttributeElement(element) {
    if (!element || element.nodeType !== 1) return false;
    return !!element.closest("script,style,noscript,[data-i18n-ignore],.ds-lang-menu,.ds-code");
  }

  function translateTextNode(node, lang) {
    var parent = node.parentElement;
    if (!parent || skipElement(parent)) return;
    var source = originals.get(node);
    if (!source) {
      source = node.nodeValue;
      if (!translateString(source, lang)) return;
      originals.set(node, source);
    }
    if (lang === "zh-Hans") {
      if (node.nodeValue !== source) node.nodeValue = source;
      originals.delete(node);
      return;
    }
    var next = translateString(source, lang);
    if (next && node.nodeValue !== next) node.nodeValue = next;
  }

  function translateAttributes(root, lang) {
    var attrs = ["placeholder", "title", "aria-label", "alt"];
    var nodes = root.querySelectorAll ? root.querySelectorAll("[placeholder],[title],[aria-label],[alt]") : [];
    Array.prototype.forEach.call(nodes, function (element) {
      if (skipAttributeElement(element)) return;
      var store = attrOriginals.get(element);
      if (!store) { store = {}; attrOriginals.set(element, store); }
      attrs.forEach(function (attr) {
        if (!element.hasAttribute(attr)) return;
        if (store[attr] === undefined) store[attr] = element.getAttribute(attr);
        var source = store[attr];
        var next = lang === "zh-Hans" ? source : translateString(source, lang);
        if (next && element.getAttribute(attr) !== next) element.setAttribute(attr, next);
      });
    });
  }

  function walk(root, lang) {
    if (!root) return;
    if (root.nodeType === 3) { translateTextNode(root, lang); return; }
    if (root.nodeType !== 1) return;
    if (skipElement(root)) return;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var node;
    while ((node = walker.nextNode())) translateTextNode(node, lang);
    translateAttributes(root, lang);
  }

  var originalTitle = null;
  function translateTitle(lang) {
    if (!originalTitle) originalTitle = document.title;
    var title = originalTitle;
    if (!title || lang === "zh-Hans") { document.title = originalTitle; return; }
    // Titles are "Page · BinaryRepair"; translate just the known page label.
    var best = null;
    Object.keys(DICTS[lang]).forEach(function (zh) {
      if (title.indexOf(zh) >= 0 && (!best || zh.length > best.length)) best = zh;
    });
    if (best) document.title = title.replace(best, DICTS[lang][best]);
  }

  function labels(lang) {
    document.documentElement.lang = lang === "en" ? "en" : lang;
    document.documentElement.dataset.lang = lang;
    document.querySelectorAll("[data-lang-current]").forEach(function (node) {
      node.textContent = NAMES[lang] || lang;
    });
    document.querySelectorAll("[data-lang-set]").forEach(function (button) {
      var active = button.dataset.langSet === lang;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
    });
  }

  function apply(lang) {
    if (!NAMES[lang]) lang = "zh-Hans";
    current = lang;
    applying = true;
    if (observer) observer.disconnect();
    try {
      walk(document.body, lang);
      translateTitle(lang);
      labels(lang);
    } finally {
      if (observer) observer.observe(document.body, { childList: true, subtree: true, characterData: true });
      applying = false;
    }
  }

  function preferred() {
    try {
      var stored = window.localStorage.getItem("br.lang");
      if (stored && NAMES[stored]) return stored;
    } catch (error) { /* ignore */ }
    return "zh-Hans";
  }

  document.addEventListener("click", function (event) {
    var option = event.target.closest ? event.target.closest("[data-lang-set]") : null;
    if (option) {
      var lang = option.dataset.langSet;
      try { window.localStorage.setItem("br.lang", lang); } catch (error) { /* ignore */ }
      apply(lang);
      document.querySelectorAll(".ds-lang-menu").forEach(function (menu) { menu.hidden = true; });
      document.querySelectorAll(".ds-lang-button").forEach(function (button) { button.setAttribute("aria-expanded", "false"); });
      document.dispatchEvent(new CustomEvent("brlang:change", { detail: { lang: lang } }));
      return;
    }
    var button = event.target.closest ? event.target.closest(".ds-lang-button") : null;
    if (button) {
      event.stopPropagation();
      var wrap = button.closest("[data-lang-switch]");
      var menu = wrap && wrap.querySelector(".ds-lang-menu");
      if (!menu) return;
      var open = menu.hidden;
      document.querySelectorAll(".ds-lang-menu").forEach(function (other) { if (other !== menu) other.hidden = true; });
      menu.hidden = !open;
      button.setAttribute("aria-expanded", open ? "true" : "false");
      return;
    }
    document.querySelectorAll(".ds-lang-menu").forEach(function (menu) {
      if (!menu.contains(event.target)) menu.hidden = true;
    });
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      document.querySelectorAll(".ds-lang-menu").forEach(function (menu) { menu.hidden = true; });
    }
  });

  observer = new MutationObserver(function (mutations) {
    if (applying) return;
    mutations.forEach(function (mutation) {
      if (mutation.type === "characterData") translateTextNode(mutation.target, current);
      mutation.addedNodes.forEach(function (node) { walk(node, current); });
    });
  });
  observer.observe(document.body, { childList: true, subtree: true, characterData: true });

  window.I18N = { apply: apply, get: function () { return current; }, names: NAMES };
  apply(preferred());
})();
