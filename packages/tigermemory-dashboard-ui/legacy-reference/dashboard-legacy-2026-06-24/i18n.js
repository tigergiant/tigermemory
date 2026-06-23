(function () {
  const STORE_KEY = "tm_lang";
  const LEGACY_STORE_KEY = "tm-lang";
  const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "PRE", "CODE", "TEXTAREA", "INPUT", "OPTION"]);
  let dict = null;
  let lang = "zh";
  let readyPromise = null;
  let observer = null;
  let applying = false;
  let phrasePairs = {zh: [], en: []};

  function readLang() {
    try {
      const urlLang = new URLSearchParams(window.location.search).get("lang");
      if (urlLang === "zh" || urlLang === "en") {
        writeLang(urlLang);
        return urlLang;
      }
    } catch (_error) {
      // Ignore URL parsing issues and fall back to storage/default language.
    }
    try {
      const stored = localStorage.getItem(STORE_KEY) || localStorage.getItem(LEGACY_STORE_KEY);
      if (stored === "zh" || stored === "en") return stored;
    } catch (_error) {
      return "zh";
    }
    return "zh";
  }

  function writeLang(next) {
    try {
      localStorage.setItem(STORE_KEY, next);
      localStorage.setItem(LEGACY_STORE_KEY, next);
    } catch (_error) {
      // localStorage may be disabled in restricted browser contexts.
    }
  }

  function interpolate(value, vars) {
    if (!vars) return value;
    return String(value).replace(/\{([a-zA-Z0-9_]+)\}/g, (_match, key) => {
      return Object.prototype.hasOwnProperty.call(vars, key) ? String(vars[key]) : `{${key}}`;
    });
  }

  function lookup(key, fallback, vars) {
    const table = dict || {};
    const value = table[lang]?.[key] ?? table.zh?.[key] ?? fallback ?? key;
    return interpolate(value, vars);
  }

  function buildPhrasePairs() {
    const zh = dict?.zh || {};
    const en = dict?.en || {};
    const pairs = Object.keys(zh)
      .map(key => [zh[key], en[key]])
      .filter(([zhValue, enValue]) => zhValue && enValue && zhValue !== enValue)
      .filter(([zhValue, enValue]) => String(zhValue).length >= 2 && String(enValue).length >= 2);
    phrasePairs.en = pairs
      .map(([source, target]) => [String(source), String(target)])
      .sort((a, b) => b[0].length - a[0].length);
    phrasePairs.zh = pairs
      .map(([target, source]) => [String(source), String(target)])
      .sort((a, b) => b[0].length - a[0].length);
  }

  function translateFreeText(value) {
    if (!dict || !value || !String(value).trim()) return value;
    const pairs = phrasePairs[lang] || [];
    let next = String(value);
    for (const [source, target] of pairs) {
      if (next.includes(target)) continue;
      if (next.includes(source)) next = next.split(source).join(target);
    }
    return next;
  }

  function markMissing(el, key) {
    el.dataset.i18nMissing = key;
    el.style.backgroundColor = "#f0d6d2";
    el.style.color = "#8a3527";
  }

  function applyDataI18n(root) {
    root.querySelectorAll?.("[data-i18n]").forEach(el => {
      const key = el.getAttribute("data-i18n");
      const value = dict?.[lang]?.[key] ?? dict?.zh?.[key];
      if (!value) {
        el.removeAttribute("data-i18n-missing");
        el.style.backgroundColor = "";
        el.style.color = "";
        return;
      }
      el.removeAttribute("data-i18n-missing");
      el.style.backgroundColor = "";
      el.style.color = "";
      if (value.includes("<") && value.includes(">")) {
        el.innerHTML = value;
      } else {
        el.textContent = value;
      }
    });
  }

  function applyAttrI18n(root) {
    root.querySelectorAll?.("[data-i18n-attr]").forEach(el => {
      const attrs = String(el.getAttribute("data-i18n-attr") || "").split(",").map(x => x.trim()).filter(Boolean);
      attrs.forEach(attr => {
        let key = el.getAttribute(`data-i18n-${attr}`);
        if (!key && attr.includes(":")) {
          const parts = attr.split(":");
          attr = parts[0];
          key = parts[1];
        }
        if (key) el.setAttribute(attr, lookup(key, el.getAttribute(attr) || ""));
      });
    });
  }

  function shouldSkipNode(node) {
    const parent = node.parentElement;
    if (!parent) return true;
    if (SKIP_TAGS.has(parent.tagName)) return true;
    if (parent.closest("[data-no-i18n]")) return true;
    if (parent.closest("pre, code, textarea, script, style")) return true;
    if (parent.closest(".font-mono")) return true;
    if (parent.hasAttribute("data-i18n")) return true;
    if (parent.closest(".chip, [data-chip-key], [data-action]")) return true;
    return false;
  }

  function applyTextFallback(root) {
    if (!root || !dict) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const updates = [];
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (shouldSkipNode(node)) continue;
      const next = translateFreeText(node.nodeValue);
      if (next !== node.nodeValue) updates.push([node, next]);
    }
    updates.forEach(([node, next]) => {
      node.nodeValue = next;
    });
    root.querySelectorAll?.("[title], [aria-label], [placeholder]").forEach(el => {
      ["title", "aria-label", "placeholder"].forEach(attr => {
        const value = el.getAttribute(attr);
        const next = translateFreeText(value);
        if (next !== value) el.setAttribute(attr, next);
      });
    });
  }

  function updateLangButton() {
    const button = document.getElementById("lang-toggle");
    if (button) button.textContent = lang === "zh" ? "中" : "EN";
  }

  function apply(root) {
    if (!dict || applying) return;
    applying = true;
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    applyDataI18n(root || document);
    applyAttrI18n(root || document);
    applyTextFallback((root || document).body || document.body);
    updateLangButton();
    applying = false;
  }

  function scheduleApply() {
    if (applying || !dict) return;
    window.requestAnimationFrame(() => apply(document));
  }

  function startObserver() {
    if (observer || !document.body) return;
    observer = new MutationObserver(scheduleApply);
    observer.observe(document.body, {
      childList: true,
      characterData: true,
      attributes: true,
      subtree: true,
      attributeFilter: ["title", "aria-label", "placeholder"]
    });
  }

  async function init() {
    if (readyPromise) return readyPromise;
    lang = readLang();
    updateLangButton();
    readyPromise = fetch("/static/i18n.json")
      .then(response => response.json())
      .then(payload => {
        dict = payload;
        buildPhrasePairs();
        apply(document);
        startObserver();
        document.dispatchEvent(new CustomEvent("tm-i18n-ready", {detail: {lang}}));
        return window.tmI18n;
      })
      .catch(error => {
        console.error("i18n load failed", error);
        return window.tmI18n;
      });
    return readyPromise;
  }

  function setLang(next) {
    if (next !== "zh" && next !== "en") return;
    lang = next;
    writeLang(next);
    apply(document);
    document.dispatchEvent(new CustomEvent("tm-lang-change", {detail: {lang}}));
  }

  window.tmI18n = {
    init,
    t: lookup,
    get: lookup,
    getLang: () => lang,
    setLang,
    apply
  };
})();
