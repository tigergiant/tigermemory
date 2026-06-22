/**
 * TigerMemory Dashboard Common Utilities
 * Exposed under window.tmDashboard namespace.
 */

(() => {
  const FULL_DOCUMENT_ROUTES = new Set(['/start']);

  function syncDocumentTitle(doc) {
    const nextTitle = doc.querySelector('title');
    if (!nextTitle) return;
    let currentTitle = document.querySelector('title');
    if (!currentTitle) {
      currentTitle = document.createElement('title');
      document.head.appendChild(currentTitle);
    }
    Array.from(currentTitle.attributes).forEach(attr => currentTitle.removeAttribute(attr.name));
    Array.from(nextTitle.attributes).forEach(attr => currentTitle.setAttribute(attr.name, attr.value));
    currentTitle.textContent = nextTitle.textContent || '';
    document.title = currentTitle.textContent || document.title;
  }

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;'
    }[ch]));
  }

  function statusClass(status) {
    if (status === 'ok') return 'status-ok';
    if (status === 'fail') return 'status-fail';
    if (status === 'optional') return 'status-info';
    return 'status-warn';
  }

  function statusText(status, label) {
    if (label) return label;
    if (status === 'ok') return '正常';
    if (status === 'fail') return '故障';
    if (status === 'optional') return '可选';
    return '警告';
  }

  function badge(status, label) {
    return `<span class="status-badge ${statusClass(status)}"><span class="status-dot"></span>${esc(statusText(status, label))}</span>`;
  }

  function statusBadge(status, label) {
    return `<span class="status-badge ${statusClass(status)}"><span class="status-dot"></span>${esc(label || statusText(status))}</span>`;
  }

  function numberText(value, suffix = '') {
    if (value === undefined || value === null || Number.isNaN(Number(value))) return '—';
    return `${Number(value).toLocaleString('zh-CN')}${suffix}`;
  }

  function kpiCard(icon, label, value, subline, status = 'ok') {
    return `
      <article class="card p-5">
        <div class="mb-3 flex items-center justify-between gap-2">
          <div class="flex items-center gap-2 text-sm font-semibold text-[#1f1d1b]">
            <i data-lucide="${icon}" class="h-5 w-5 text-[#c8a560]"></i>${esc(label)}
          </div>
          ${statusBadge(status, status === 'ok' ? '正常' : '关注')}
        </div>
        <div class="text-3xl font-extrabold text-[#1f1d1b]">${esc(value)}</div>
        <div class="mt-2 text-xs leading-5 text-[#8a8275]">${esc(subline)}</div>
      </article>
    `;
  }

  function serviceName(name) {
    const mapping = {
      Dashboard: '控制台',
      'tm-http': '记忆服务',
      'tm-mcp': 'AI 连接服务',
      Mem0: '即时记忆',
      OpenClaw: 'OpenClaw',
    };
    return mapping[name] || name;
  }

  function serviceDetail(service) {
    if (service.name === 'Dashboard') return service.detail || '运行中';
    const mapping = {
      'tm-http': '本机记忆接口',
      'tm-mcp': '给 AI 调用的接口',
      Mem0: '近期对话记忆',
      OpenClaw: '等待接入检查',
    };
    return mapping[service.name] || service.detail || statusText(service.status, service.status_label);
  }

  function formatTime(value) {
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return '';
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function getPathFromUrl(url) {
    try {
      return new URL(url, window.location.href).pathname;
    } catch (_error) {
      return '';
    }
  }

  const DEFAULT_CACHE_TTL_MS = 5000;
  const CACHE_STRATEGY = [
    ['/start', 60000, false],
    ['/health', 3000, true],
    ['/quality', 3000, true],
    ['/canvas', 60000, false],
    ['/self-evolution', 8000, true],
    ['/digest', 8000, true]
  ];

  function cacheStrategy(pathname) {
    const route = CACHE_STRATEGY.find(([routePath]) => pathname === routePath || pathname.startsWith(`${routePath}/`));
    if (!route) return { ttlMs: DEFAULT_CACHE_TTL_MS, staleAsCached: true };
    return { ttlMs: route[1], staleAsCached: route[2] };
  }

  let swRegistered = false;
  function registerServiceWorker() {
    if (swRegistered) return;
    if ('serviceWorker' in navigator) {
      let refreshingForServiceWorker = false;
      navigator.serviceWorker.addEventListener('controllerchange', () => {
        if (refreshingForServiceWorker) return;
        refreshingForServiceWorker = true;
        window.location.reload();
      });
      navigator.serviceWorker.register('/service-worker.js')
        .then(registration => {
          swRegistered = true;
          registration.update().catch(() => {});
          if (registration.waiting && navigator.serviceWorker.controller) {
            registration.waiting.postMessage({ type: 'SKIP_WAITING' });
          }
        })
        .catch(console.error);
    }
  }

  function updateNavHighlight() {
    const nav = document.querySelector('nav.relative');
    if (!nav) return;
    const highlight = nav.querySelector('.nav-highlight');
    if (!highlight) return;
    const activeTab = nav.querySelector('.nav-tab.active');
    if (activeTab) {
      highlight.style.width = `${activeTab.offsetWidth}px`;
      highlight.style.height = `${activeTab.offsetHeight}px`;
      highlight.style.transform = `translate3d(${activeTab.offsetLeft}px, ${activeTab.offsetTop}px, 0)`;
      highlight.classList.remove('opacity-0');
    } else {
      highlight.classList.add('opacity-0');
    }
  }

  function updateActiveTab(pageName) {
    document.querySelectorAll('.nav-tab').forEach(el => el.classList.remove('active'));
    document.querySelectorAll(`[data-target-page="${pageName}"]`).forEach(el => el.classList.add('active'));
    requestAnimationFrame(() => updateNavHighlight());
  }

  const tmDashboardRouter = {
    fetchAbortController: null,
    pageRefreshState: {},
    requestId: 0,
    cache: {},
    init() {
      document.addEventListener('click', this.handleClick.bind(this));
      window.addEventListener('popstate', this.handlePopState.bind(this));
      document.addEventListener('tm-lang-change', () => {
        this.clearCache();
        requestAnimationFrame(() => {
          setTimeout(() => updateNavHighlight(), 30);
        });
      });
      window.addEventListener('resize', () => updateNavHighlight());
      requestAnimationFrame(() => updateNavHighlight());
    },
    clearCache() {
      this.pageRefreshState = {};
      this.cache = {};
    },
    isDashboardRoute(url) {
      try {
        const parsed = new URL(url, window.location.href);
        if (parsed.origin !== window.location.origin) return false;
        const path = parsed.pathname;
        return path === '/health' ||
               path === '/start' ||
               path === '/quality' ||
               path === '/canvas' ||
               path === '/self-evolution' ||
               path === '/agent-tools' ||
               path === '/settings' ||
               path === '/digest' ||
               path.startsWith('/digest/');
      } catch (e) {
        return false;
      }
    },
    isFullDocumentRoute(url) {
      try {
        const parsed = new URL(url, window.location.href);
        return FULL_DOCUMENT_ROUTES.has(parsed.pathname) ||
               FULL_DOCUMENT_ROUTES.has(window.location.pathname) ||
               document.body.dataset.page === 'start';
      } catch (e) {
        return false;
      }
    },
    getController(key) {
      if (!key) return null;
      const camelKey = key.replace(/-([a-z])/g, (g) => g[1].toUpperCase());
      return window.tmPages && window.tmPages[camelKey];
    },
    handleClick(e) {
      const anchor = e.target.closest('a');
      if (!anchor) return;
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) {
        return;
      }
      if (anchor.target === '_blank') return;
      const url = anchor.getAttribute('href');
      if (!url) return;
      if (this.isDashboardRoute(url)) {
        if (this.isFullDocumentRoute(url)) return;
        e.preventDefault();
        this.navigateTo(url, true);
      }
    },
    handlePopState(e) {
      const url = window.location.pathname + window.location.search;
      if (this.isFullDocumentRoute(url)) {
        window.location.reload();
        return;
      }
      this.navigateTo(url, false);
    },
    markRefreshSuccess(cacheKey, state = {}) {
      const path = getPathFromUrl(cacheKey || window.location.href);
      if (!path) return;
      this.pageRefreshState[path] = {
        ...(this.pageRefreshState[path] || {}),
        lastSuccessAt: state.timestamp || Date.now(),
        lastErrorAt: null,
        lastError: null,
        source: state.source || 'router'
      };
    },

    markRefreshFailure(cacheKey, error) {
      const path = getPathFromUrl(cacheKey || window.location.href);
      if (!path) return;
      this.pageRefreshState[path] = {
        ...(this.pageRefreshState[path] || {}),
        lastErrorAt: Date.now(),
        lastError: error ? String(error) : 'unknown'
      };
    },

    getRefreshState(cacheKey) {
      const path = getPathFromUrl(cacheKey || window.location.href);
      return this.pageRefreshState[path] || {};
    },

    shouldUseCache(cacheKey, cached) {
      const path = getPathFromUrl(cacheKey);
      const policy = cacheStrategy(path);
      if (!cached || !Number.isFinite(cached.timestamp)) return false;
      if (policy.ttlMs <= 0) return false;
      return Date.now() - cached.timestamp < policy.ttlMs;
    },

    updateRefreshIndicator(cacheKey, cached, refreshing, options = {}) {
      const el = document.getElementById('last-refresh');
      if (!el) return;
      const path = getPathFromUrl(cacheKey || window.location.href);
      const strategy = cacheStrategy(path);
      const state = this.getRefreshState(path);
      const t = (key, fallback, vars = {}) => window.tmI18n ? window.tmI18n.t(key, fallback, vars) : fallback;
      const updatedAt = state.lastSuccessAt ? formatTime(state.lastSuccessAt) : '';
      const baseKey = options.error
        ? 'header.refresh_failed'
        : refreshing
          ? 'header.refreshing'
          : cached
            ? (strategy.staleAsCached && options.isStale ? 'header.cache_stale' : 'header.cached')
            : 'header.just_now';

      const fallbackMap = {
        'header.refresh_failed': '刷新失败',
        'header.refreshing': '正在刷新...',
        'header.cache_stale': '刚刚 (缓存，已过期)',
        'header.cached': '刚刚 (缓存)',
        'header.just_now': '刚刚'
      };
      let message = t(baseKey, fallbackMap[baseKey] || '刚刚');

      if (updatedAt) {
        message = `${message} · ${t('header.last_updated', '上次更新')} ${updatedAt}`;
      }

      if (state.lastError) {
        message = `${message} · ${t('term.error', '错误')}: ${state.lastError}`;
      }

      if (strategy.staleAsCached && cached && options.isStale) {
        message = `${message} · ${t('header.refresh_required', '建议刷新以保证实时')}`;
      }

      if (options.error) {
        el.classList.add('text-[#8a3527]');
      } else {
        el.classList.remove('text-[#8a3527]');
      }

      const dataI18nKey = options.error
        ? 'header.refresh_failed'
        : refreshing
          ? 'header.refreshing'
          : cached
            ? (strategy.staleAsCached && options.isStale ? 'header.cache_stale' : 'header.cached')
            : 'header.just_now';
      el.setAttribute('data-i18n', dataI18nKey);
      el.title = message;
      el.textContent = message;
    },
    renderHTML(htmlText, url, pushState, runInit = true, isBackgroundUpdate = false) {
      const oldPageKey = document.body.dataset.page;
      const oldController = this.getController(oldPageKey);
      if (oldController && typeof oldController.destroy === 'function') {
        try {
          oldController.destroy();
        } catch (err) {
          console.error('Error destroying old controller:', err);
        }
      }

      const parser = new DOMParser();
      const doc = parser.parseFromString(htmlText, 'text/html');
      const newMain = doc.querySelector('main');
      const newBody = doc.querySelector('body');

      if (!newMain || !newBody) {
        throw new Error('Parsed HTML missing main or body element');
      }

      if (pushState) {
        window.history.pushState(null, '', url);
      }

      syncDocumentTitle(doc);

      document.body.dataset.page = newBody.dataset.page || '';

      if (isBackgroundUpdate) {
        document.body.classList.add('tm-is-updating');
      }

      const currentMain = document.querySelector('main');
      if (currentMain) {
        currentMain.outerHTML = newMain.outerHTML;
        if (!isBackgroundUpdate) {
          window.scrollTo(0, 0);
        }
      }

      window.tmDashboard.updateActiveTab(document.body.dataset.page);

      const newPageKey = document.body.dataset.page;
      const dataElementMap = {
        'daily': 'digest-data',
        'start': 'start-data',
        'health': 'health-data',
        'quality': 'quality-data',
        'agent-tools': 'agent-tools-data',
        'settings': 'settings-data',
        'canvas': 'canvas-data',
        'self-evolution': 'self-evolution-data'
      };
      const dataId = dataElementMap[newPageKey];
      let pageData = {};
      if (dataId) {
        const dataEl = doc.getElementById(dataId);
        if (dataEl) {
          let existingDataEl = document.getElementById(dataId);
          if (existingDataEl) {
            existingDataEl.textContent = dataEl.textContent;
          } else {
            const scriptNode = document.createElement('script');
            scriptNode.id = dataId;
            scriptNode.type = 'application/json';
            scriptNode.textContent = dataEl.textContent;
            document.body.appendChild(scriptNode);
          }
          try {
            pageData = JSON.parse(dataEl.textContent);
          } catch (err) {
            console.error('Failed to parse page JSON:', err);
          }
        }
      }

      if (window.tmI18n) {
        window.tmI18n.apply(document);
        const titleNode = document.querySelector('title');
        if (titleNode && titleNode.textContent) {
          document.title = titleNode.textContent;
        }
      }

      void document.body.offsetWidth;
      document.documentElement.classList.remove('tm-page-leaving');
      document.documentElement.classList.add('tm-page-ready');
      document.body.classList.remove('tm-page-leaving');
      document.body.classList.add('tm-page-ready');

      if (runInit) {
        const newController = this.getController(newPageKey);
        if (newController && typeof newController.init === 'function') {
          newController.init(document, pageData);
        }
      }

      if (isBackgroundUpdate) {
        requestAnimationFrame(() => {
          document.body.classList.remove('tm-is-updating');
        });
      }
    },
    async fetchBackground(url, cacheKey, requestId) {
      try {
        const response = await fetch(url, { signal: this.fetchAbortController.signal });
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
        const htmlText = await response.text();

        if (requestId !== this.requestId) {
          return;
        }

        const responseTime = Date.now();
        const currentUrlObj = new URL(window.location.href);
        const currentPath = currentUrlObj.pathname + currentUrlObj.search;

        this.cache[cacheKey] = {
          htmlText,
          timestamp: responseTime,
          source: 'refresh'
        };
        this.markRefreshSuccess(cacheKey, { timestamp: responseTime, source: 'background-refresh' });

        if (currentPath === cacheKey) {
          this.renderHTML(htmlText, url, false, true, true);
          this.updateRefreshIndicator(cacheKey, true, false);
        }
      } catch (err) {
        console.warn('Background refresh failed:', err);
        if (requestId !== this.requestId) {
          return;
        }
        this.markRefreshFailure(cacheKey, err.message || 'refresh error');
        const currentPath = new URL(window.location.href).pathname + new URL(window.location.href).search;
        if (currentPath === cacheKey) {
          this.updateRefreshIndicator(cacheKey, true, false, { error: err.message || 'refresh failed' });
        }
      }
    },
    async navigateTo(url, pushState = true) {
      if (this.isFullDocumentRoute(url)) {
        window.location.href = new URL(url, window.location.href).href;
        return;
      }
      if (this.fetchAbortController) {
        this.fetchAbortController.abort();
      }
      if (this.transitionTimeoutId) {
        clearTimeout(this.transitionTimeoutId);
        this.transitionTimeoutId = null;
      }
      const requestId = ++this.requestId;
      this.fetchAbortController = new AbortController();
      const signal = this.fetchAbortController.signal;
      const isDashboardRequest = this.isDashboardRoute(url);
      const strategy = isDashboardRequest ? cacheStrategy(getPathFromUrl(url)) : { ttlMs: DEFAULT_CACHE_TTL_MS, staleAsCached: true };

      const urlObj = new URL(url, window.location.href);
      const cacheKey = urlObj.pathname + urlObj.search;
      const cached = this.cache[cacheKey];
      const isCacheHit = this.shouldUseCache(cacheKey, cached);
      const shouldRenderCached = Boolean(cached) && isCacheHit;

      // Start fade out transition
      document.documentElement.classList.remove('tm-page-ready');
      document.documentElement.classList.add('tm-page-leaving');
      document.body.classList.remove('tm-page-ready');
      document.body.classList.add('tm-page-leaving');

      // Wait 100ms for fade out transition (sync / async transition time)
      await new Promise(resolve => {
        this.transitionTimeoutId = setTimeout(() => {
          this.transitionTimeoutId = null;
          resolve();
        }, 100);
      });

      if (shouldRenderCached) {
        try {
          this.renderHTML(cached.htmlText, url, pushState, true);
          this.updateRefreshIndicator(cacheKey, true, false, { isStale: false });
          this.markRefreshSuccess(cacheKey, { timestamp: cached.timestamp, source: 'cache' });
          return;
        } catch (err) {
          console.error('Failed to render from cache, falling back to network:', err);
        }
      }

      this.updateRefreshIndicator(cacheKey, Boolean(cached), true);

      try {
        const response = await fetch(url, {
          signal,
          cache: strategy.ttlMs === 0 ? 'no-store' : 'default'
        });
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
        const htmlText = await response.text();

        if (requestId !== this.requestId) return;

        this.cache[cacheKey] = {
          htmlText,
          timestamp: Date.now()
        };
        this.markRefreshSuccess(cacheKey, { source: 'network' });

        this.renderHTML(htmlText, url, pushState, true);
        this.updateRefreshIndicator(cacheKey, false, false);
      } catch (err) {
        if (err.name === 'AbortError') {
          return;
        }
        this.markRefreshFailure(cacheKey, err.message);
        this.updateRefreshIndicator(cacheKey, Boolean(cached), false, { error: err.message || 'fetch failed' });
        console.error('PJAX navigation failed, falling back to href:', err);
        window.location.href = url;
      }
    }
  };

  window.tmDashboard = {
    esc,
    statusClass,
    statusText,
    badge,
    statusBadge,
    numberText,
    kpiCard,
    serviceName,
    serviceDetail,
    registerServiceWorker,
    updateActiveTab,
    updateNavHighlight
  };

  window.tmDashboardRouter = tmDashboardRouter;

  // Auto-initialize router
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => tmDashboardRouter.init());
  } else {
    tmDashboardRouter.init();
  }
})();
