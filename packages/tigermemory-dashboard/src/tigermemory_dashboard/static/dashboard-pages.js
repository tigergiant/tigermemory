/**
 * TigerMemory Dashboard Pages Lifecycle Controllers
 * Exposed under window.tmPages namespace.
 */

(() => {
  window.tmPages = window.tmPages || {};

  function runWhenVisible(fn) {
    if (document.visibilityState === 'visible') fn();
  }

  window.tmPages.start = {
    abortController: null,
    toastTimer: null,
    currentStep: 0,
    selectedDepth: 'A',

    i18n(key, fallback, vars = {}) {
      return window.tmI18n ? window.tmI18n.t(key, fallback, vars) : fallback;
    },

    init(root, data) {
      this.destroy();
      this.abortController = new AbortController();
      const profile = document.getElementById('start-profile');
      if (profile) profile.textContent = data && data.profile ? data.profile : 'local';
      const prefs = data && data.preferences ? data.preferences : {};
      this.selectedDepth = prefs.communication_depth || 'A';
      const refresh = document.getElementById('last-refresh');
      if (refresh) {
        refresh.textContent = '开始';
        refresh.title = data && data.generated_at ? data.generated_at : '开始';
      }
      root.querySelectorAll('[data-copy-command]').forEach(button => {
        button.addEventListener('click', () => this.copyCommand(button), {signal: this.abortController.signal});
      });
      this.bindOnboarding(root);
      this.bindDepthChoices(root);
      this.renderDepthChoices(root);
      this.updateLlmCommand();
      if (window.lucide) window.lucide.createIcons();
    },

    destroy() {
      if (this.abortController) {
        this.abortController.abort();
        this.abortController = null;
      }
      if (this.toastTimer) {
        clearTimeout(this.toastTimer);
        this.toastTimer = null;
      }
      this.currentStep = 0;
    },

    bindOnboarding(root) {
      root.querySelectorAll('[data-onboarding-next]').forEach(button => {
        button.addEventListener('click', () => this.showStep(this.currentStep + 1), {signal: this.abortController.signal});
      });
      root.querySelectorAll('[data-onboarding-prev]').forEach(button => {
        button.addEventListener('click', () => this.showStep(this.currentStep - 1), {signal: this.abortController.signal});
      });
      root.querySelectorAll('[data-step-dot]').forEach(dot => {
        dot.addEventListener('click', () => this.showStep(Number(dot.dataset.stepDot || 0)), {signal: this.abortController.signal});
        dot.style.cursor = 'pointer';
      });
      const saveDepth = document.getElementById('save-start-depth');
      if (saveDepth) saveDepth.addEventListener('click', () => this.saveDepth(), {signal: this.abortController.signal});
      ['llm-api-key', 'llm-base-url', 'llm-model'].forEach(id => {
        const input = document.getElementById(id);
        if (input) input.addEventListener('input', () => this.updateLlmCommand(), {signal: this.abortController.signal});
      });
      const copyLlm = document.getElementById('copy-llm-command');
      if (copyLlm) copyLlm.addEventListener('click', () => this.copyGeneratedLlmCommand(), {signal: this.abortController.signal});
      const finish = document.getElementById('finish-onboarding');
      if (finish) finish.addEventListener('click', () => {
        try { localStorage.setItem('tigermemory_onboarding_done', 'true'); } catch (_error) {}
      }, {signal: this.abortController.signal});
      this.showStep(0);
    },

    bindDepthChoices(root = document) {
      root.querySelectorAll('[data-start-depth]').forEach(button => {
        button.addEventListener('click', () => {
          this.selectedDepth = button.dataset.startDepth || 'A';
          this.renderDepthChoices(root);
        }, {signal: this.abortController.signal});
      });
    },

    showStep(index) {
      const slides = Array.from(document.querySelectorAll('[data-onboarding-slide]'));
      if (!slides.length) return;
      const nextIndex = Math.max(0, Math.min(slides.length - 1, index));
      this.currentStep = nextIndex;
      slides.forEach((slide, idx) => {
        slide.classList.toggle('active', idx === nextIndex);
        slide.setAttribute('aria-hidden', idx === nextIndex ? 'false' : 'true');
      });
      document.querySelectorAll('[data-step-dot]').forEach(dot => {
        dot.classList.toggle('active', Number(dot.dataset.stepDot || 0) === nextIndex);
      });
      const label = document.getElementById('start-step-label');
      if (label) label.textContent = String(nextIndex + 1);
      const prev = document.querySelector('[data-onboarding-prev]');
      if (prev) prev.disabled = nextIndex === 0;
      const nextButtons = document.querySelectorAll('[data-onboarding-next]');
      nextButtons.forEach(button => {
        button.disabled = nextIndex === slides.length - 1 && !button.closest('[data-onboarding-slide]');
      });
      const help = document.getElementById('onboarding-step-help');
      const helps = [
        '先用一分钟认识 TigerMemory。',
        '从普通版开始，后面再升级。',
        '选一个你喜欢的回答风格。',
        '生成本机 LLM 配置命令，密钥不上传。',
        '了解常用页面分别做什么。',
        '跑通一次，就可以开始用了。'
      ];
      if (help) help.textContent = helps[nextIndex] || '';
    },

    renderDepthChoices(root = document) {
      root.querySelectorAll('[data-start-depth]').forEach(button => {
        button.classList.toggle('active', button.dataset.startDepth === this.selectedDepth);
      });
    },

    async saveDepth() {
      try {
        const response = await fetch('/api/settings/preferences', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({preferences: {communication_depth: this.selectedDepth}, propose_wiki: false}),
          signal: this.abortController.signal
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        this.toast(`已保存回复风格：${this.selectedDepth}`);
      } catch (error) {
        this.toast(`保存失败：${error.message || error}`, false);
      }
    },

    shellQuote(value, placeholder) {
      const text = String(value || '').trim() || placeholder;
      return text.replace(/"/g, '\\"');
    },

    buildLlmCommand() {
      const key = this.shellQuote(document.getElementById('llm-api-key')?.value, '<your_deepseek_api_key>');
      const base = this.shellQuote(document.getElementById('llm-base-url')?.value, 'https://api.deepseek.com');
      const model = this.shellQuote(document.getElementById('llm-model')?.value, 'deepseek-v4-flash');
      return [
        `setx DEEPSEEK_API_KEY "${key}"`,
        `setx DEEPSEEK_BASE_URL "${base}"`,
        `setx DEEPSEEK_MODEL "${model}"`,
        'tm llm status'
      ].join('\n');
    },

    updateLlmCommand() {
      const preview = document.getElementById('llm-command-preview');
      const copy = document.getElementById('copy-llm-command');
      const command = this.buildLlmCommand();
      if (preview) preview.textContent = command;
      if (copy) copy.setAttribute('data-copy-command', command);
    },

    async copyGeneratedLlmCommand() {
      this.updateLlmCommand();
      const button = document.getElementById('copy-llm-command');
      if (button) await this.copyCommand(button);
    },

    async copyCommand(button) {
      const command = button.getAttribute('data-copy-command') || button.textContent || '';
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(command);
        } else {
          const area = document.createElement('textarea');
          area.value = command;
          area.setAttribute('readonly', 'readonly');
          area.style.position = 'fixed';
          area.style.opacity = '0';
          document.body.appendChild(area);
          area.select();
          document.execCommand('copy');
          area.remove();
        }
        this.toast(this.i18n('start.toast.copied', `已复制：${command}`, {command}));
      } catch (_error) {
        this.toast(this.i18n('start.toast.copy_failed', `复制失败，请手动选择：${command}`, {command}), false);
      }
    },

    toast(message, ok = true) {
      const node = document.getElementById('start-toast');
      if (!node) return;
      node.textContent = message;
      node.className = `fixed bottom-6 left-1/2 z-50 max-w-[min(92vw,32rem)] -translate-x-1/2 rounded-xl border px-4 py-3 text-sm shadow-lg ${ok ? 'border-[#a0b889] bg-[#dde8ce] text-[#52733a]' : 'border-[#d49a91] bg-[#f0d6d2] text-[#8a3527]'}`;
      node.classList.remove('hidden');
      if (this.toastTimer) clearTimeout(this.toastTimer);
      this.toastTimer = setTimeout(() => node.classList.add('hidden'), 3200);
    }
  };

  window.tmPages.daily = {
    root: null,
    data: null,
    digest: null,
    counts: null,
    inboxRows: null,
    wikiProposalLedger: null,
    proposals: null,
    cronIntake: null,
    selectedInbox: null,
    wikiModalState: null,
    abortController: null,
    refreshTimer: null,
    toastTimeout: null,
    actionInFlight: false,
    writeQueue: null,
    queueProcessing: false,
    queueTimer: null,
    queueSeq: 0,
    queueTimeoutMs: 15000,
    queueRequestId: 0,
    fetchDigestRequestId: 0,
    fetchDigestAbortController: null,

    actionLabels: {
      archive: '归档',
      promote_mem0: '进入即时记忆',
      promote_wiki: '写入 Wiki',
      keep: '保留观察',
      keep_in_inbox: '保留观察'
    },

    actionHelps: {
      archive: '归档：把这条收件箱条目移到归档区，日常审批列表里不再显示；适合过期、重复或无继续处理价值的记录。',
      promote_mem0: '进入即时记忆：适合近期偏好、反馈、会话结论和还在变化的事实。',
      promote_wiki: '写入 Wiki：写入长期事实记忆，适合稳定规则、流程、系统边界和可长期引用的资料。',
      keep: '保留观察：暂不升格也不隐藏，继续留在收件箱等待后续日报复审。'
    },

    wikiPartitions: [
      ['systems', '系统'],
      ['operations', '运营'],
      ['investment', '投资'],
      ['production', '生产'],
      ['brand', '品牌'],
      ['self-evolution', '自我进化']
    ],

    init(root, data, cronIntakeData = null) {
      // Prevent duplicate initializations and clear any pre-existing timers/listeners
      this.destroy();

      this.root = root;
      this.data = data;
      this.digest = data || {};
      this.counts = this.digest.counts || {};
      this.inboxRows = this.digest.inbox_rows || [];
      this.wikiProposalLedger = this.digest.wiki_proposal_ledger || [];
      this.proposals = this.digest.proposals || [];
      this.cronIntake = cronIntakeData || {};
      this.selectedInbox = new Set();
      this.wikiModalState = null;
      this.actionInFlight = false;
      this.writeQueue = [];
      this.queueProcessing = false;
      this.queueTimer = null;
      this.queueSeq = 0;
      this.abortController = new AbortController();

      // Bind static events with abort signal
      this.bindEvents();

      // Render initial UI state
      this.applyDigest(this.digest);

      // Fetch dynamic state if page is currently loading shell
      if (this.digest.loading) {
        this.fetchDigest();
      }
      this.fetchCronIntake({quiet: true});
      this.startAutoRefresh();
    },

    destroy() {
      // Abort all active fetch calls and remove all event listeners dynamically via signal
      if (this.abortController) {
        this.abortController.abort();
        this.abortController = null;
      }
      if (this.toastTimeout) {
        clearTimeout(this.toastTimeout);
        this.toastTimeout = null;
      }
      if (this.refreshTimer) {
        clearInterval(this.refreshTimer);
        this.refreshTimer = null;
      }
      if (this.queueTimer) {
        clearTimeout(this.queueTimer);
        this.queueTimer = null;
      }
      if (this.fetchDigestAbortController) {
        this.fetchDigestAbortController.abort();
        this.fetchDigestAbortController = null;
      }
      // Reset variables and state
      this.selectedInbox = null;
      this.wikiModalState = null;
      this.actionInFlight = false;
      this.writeQueue = null;
      this.queueProcessing = false;
      this.queueTimer = null;
      this.digest = null;
      this.counts = null;
      this.inboxRows = null;
      this.wikiProposalLedger = null;
      this.proposals = null;
      this.cronIntake = null;
    },

    startAutoRefresh() {
      const refresh = () => {
        if (!document.hidden) this.fetchDigest({quiet: true});
      };
      this.refreshTimer = setInterval(refresh, 30000);
      window.addEventListener('focus', refresh, { signal: this.abortController.signal });
      document.addEventListener('visibilitychange', refresh, { signal: this.abortController.signal });
    },

    humanAction(action) {
      return this.actionLabels[action] || this.actionLabels.keep_in_inbox || action || '保留观察';
    },

    humanizeCopy(value) {
      const terms = [
        ['Memory Ops', '个人记忆控制台'],
        ['keep_in_inbox', '保留观察'],
        ['not older than 14 days; keep for daily review', '未到 14 天，继续保留给每日审阅'],
        ['archive', '归档'],
        ['promote_to_mem0', '进入即时记忆'],
        ['promote_to_wiki', '写入 Wiki'],
        ['promote', '提升'],
        ['discard quarantine', '待复检的丢弃项'],
        ['discard', '已忽略项'],
        ['Proposed Changes', 'AI 修改建议'],
        ['digest', '每日摘要'],
        ['trace', '思考路径'],
        ['hidden', '已隐藏'],
        ['failures', '失败次数'],
        ['fetch failed', '数据暂时没取到，请稍后重试']
      ];
      return terms.reduce((text, [from, to]) => text.replaceAll(from, to), String(value ?? ''));
    },

    toast(message, ok = true) {
      const node = document.getElementById('toast');
      if (!node) return;
      node.textContent = message;
      node.className = `fixed bottom-6 left-1/2 z-50 max-w-[min(92vw,36rem)] -translate-x-1/2 rounded-md border px-4 py-3 text-sm shadow-lg ${ok ? 'border-[#a0b889] bg-[#dde8ce] text-[#52733a]' : 'border-[#d49a91] bg-[#f0d6d2] text-[#8a3527]'}`;
      node.classList.remove('hidden');
      if (this.toastTimeout) clearTimeout(this.toastTimeout);
      this.toastTimeout = setTimeout(() => node.classList.add('hidden'), 4500);
    },

    async postJsonRaw(url, body) {
      try {
        const response = await fetch(url, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(body),
          signal: this.abortController ? this.abortController.signal : null
        });
        const data = await response.json();
        return {status: response.status, data};
      } catch (error) {
        if (error.name === 'AbortError') throw error;
        throw new Error('数据暂时没取到，请稍后重试');
      }
    },

    async postJson(url, body) {
      const {status, data} = await this.postJsonRaw(url, body);
      if (!data.ok) throw new Error(data.error || `错误码 ${status}：操作失败`);
      return data;
    },

    pill(text) {
      const c = window.tmDashboard;
      return `<span class="rounded-full border border-[#e6dfcc] bg-[#f0e9d8] px-2 py-1 text-[#4a443d]">${c.esc(text)}</span>`;
    },

    renderHeader() {
      const refresh = document.getElementById('last-refresh');
      if (refresh) refresh.textContent = this.digest.date || '刚刚';
      const heroArchive = document.getElementById('hero-archive');
      if (heroArchive) {
        heroArchive.classList.toggle('hidden', !this.counts.stale_archive);
      }
      const batchArchive = document.getElementById('batch-archive');
      if (batchArchive) {
        batchArchive.textContent = `一键归档全部 ${this.counts.stale_archive || 0} 条`;
      }
    },

    renderDecision() {
      if (this.digest.loading) {
        const decisionSection = document.getElementById('decision-section');
        const decisionGrid = document.getElementById('decision-grid');
        if (decisionSection) decisionSection.classList.remove('hidden');
        if (decisionGrid) {
          decisionGrid.innerHTML = Array.from({length: 4}).map(() => `
            <div class="rounded-md border border-[#e6dfcc] p-3 shimmer-loader" style="min-height: 78px">
              <div class="h-3 w-1/2 rounded bg-[#e8e0c8]/60"></div>
              <div class="mt-3 h-6 w-1/4 rounded bg-[#e8e0c8]/60"></div>
            </div>
          `).join('');
        }
        return;
      }
      const c = window.tmDashboard;
      const discardCount = this.discardCandidateCount();
      const cards = [
        ['14 天后自动归档', this.counts.stale_archive || 0, '#inbox-list'],
        ['提升候选', this.counts.promote || 0, '#inbox-list'],
        ['Wiki 提案台账', this.counts.wiki_proposal_inbox || 0, '#wiki-proposal-ledger-section'],
        ['AI 修改建议', this.counts.proposal || 0, '#proposal-list'],
        ['待复检的丢弃项', discardCount, '#appendix']
      ].filter(([, count]) => Number(count) > 0);
      const decisionGrid = document.getElementById('decision-grid');
      const decisionSection = document.getElementById('decision-section');
      if (decisionSection) decisionSection.classList.toggle('hidden', cards.length === 0);
      if (decisionGrid) {
        if (!cards.length) {
          decisionGrid.innerHTML = '';
          return;
        }
        decisionGrid.innerHTML = cards.map(([label, count, href]) => `
          <a href="${href}" class="rounded-md border border-[#e6dfcc] bg-[#f0e9d8] p-3 hover:bg-[#e8e0c8]">
            <div class="text-sm text-[#8a8275]">${c.esc(label)}</div>
            <div class="mt-1 text-2xl font-semibold">${count}</div>
          </a>
        `).join('');
      }
    },

    discardCandidateCount() {
      const raw = String(this.digest.discard_candidates || '').trim();
      if (!raw || raw.includes('- none')) return 0;
      const lines = raw.split('\n').filter(line => line.trim().startsWith('- '));
      return lines.length || 1;
    },

    renderCronIntake() {
      const c = window.tmDashboard;
      const intake = this.cronIntake || {};
      const section = document.getElementById('cron-intake-section');
      const status = document.getElementById('cron-intake-status');
      const summary = document.getElementById('cron-intake-summary');
      const actions = document.getElementById('cron-intake-actions');
      const reports = document.getElementById('cron-intake-reports');
      const learning = document.getElementById('cron-intake-learning');
      const warnings = document.getElementById('cron-intake-warnings');
      if (!section) return;

      const statusText = intake.status || 'loading';
      const statusClass = statusText === 'ok'
        ? 'border-[#a0b889] bg-[#dde8ce] text-[#52733a]'
        : statusText === 'partial' || statusText === 'warn'
          ? 'border-[#e0c889] bg-[#f4e6c4] text-[#8a6b1f]'
          : 'border-[#d49a91] bg-[#f0d6d2] text-[#8a3527]';
      if (status) {
        status.className = `rounded-full border px-3 py-1 text-xs ${statusClass}`;
        status.textContent = statusText === 'ok' ? '可承接' : statusText === 'partial' ? '部分缺失' : statusText === 'warn' ? '有警告' : statusText === 'error' ? '读取失败' : '加载中';
      }
      if (summary) summary.textContent = intake.summary || '正在读取 cron 承接短卡...';

      const actionItems = Array.isArray(intake.action_items) ? intake.action_items : [];
      if (actions) {
        actions.innerHTML = actionItems.length
          ? actionItems.map(item => `<div class="rounded-md border border-[#e6dfcc] bg-[#f0e9d8] px-3 py-2 text-sm text-[#4a443d]">${c.esc(this.humanizeCopy(item))}</div>`).join('')
          : '<div class="rounded-md border border-[#e6dfcc] bg-[#f0e9d8] px-3 py-2 text-sm text-[#8a8275]">暂无建议动作</div>';
      }

      const reportRows = Array.isArray(intake.reports) ? intake.reports : [];
      if (reports) {
        reports.innerHTML = reportRows.map(row => {
          const ok = row.exists && row.status === 'ok';
          const klass = ok ? 'border-[#a0b889] bg-[#dde8ce] text-[#52733a]' : 'border-[#e0c889] bg-[#f4e6c4] text-[#8a6b1f]';
          return `<span class="rounded-full border px-2 py-1 text-xs font-mono ${klass}">${c.esc(row.kind || 'report')} · ${c.esc(row.status || 'unknown')}</span>`;
        }).join('') || '<span class="text-xs text-[#8a8275]">暂无产物状态</span>';
      }

      const digest = reportRows.find(row => row.kind === 'memory_digest') || {};
      const radar = reportRows.find(row => row.kind === 'ai_agent_radar') || {};
      const learningLines = [
        ...(Array.isArray(digest.learning_card) ? digest.learning_card : []),
        ...(Array.isArray(radar.friendly_closeout) ? radar.friendly_closeout : [])
      ].filter(Boolean).slice(0, 6);
      if (learning) {
        learning.innerHTML = learningLines.length
          ? `<ul class="list-disc space-y-1 pl-5">${learningLines.map(line => `<li>${c.esc(this.humanizeCopy(line))}</li>`).join('')}</ul>`
          : '<p class="text-[#8a8275]">暂无可展示沉淀摘要。</p>';
      }

      const warningLines = Array.isArray(intake.warnings) ? intake.warnings : [];
      if (warnings) {
        warnings.innerHTML = warningLines.length
          ? warningLines.slice(0, 5).map(line => `<div>提醒：${c.esc(this.humanizeCopy(line))}</div>`).join('')
          : '';
      }
    },

    wikiProposalStatusLabel(status) {
      const labels = {
        pending: '待本线程归并',
        applied: '已处理',
        'investment-wiki': '可写入投研 Wiki',
        'investment-thread': '投资提案归档',
        truncated: '未完全展示'
      };
      return labels[status] || status || '待处理';
    },

    wikiProposalStatusClass(status) {
      if (status === 'investment-wiki' || status === 'investment-thread') return 'border-[#1f1d1b] bg-[#fbf8f1] text-[#1f1d1b] shadow-[inset_0_-2px_0_#c8a560]';
      if (status === 'applied') return 'border-[#a0b889] bg-[#dde8ce] text-[#52733a]';
      return 'border-[#e0c889] bg-[#f4e6c4] text-[#8a6b1f]';
    },

    wikiProposalTrustClass(level) {
      if (level === 'high') return 'border-[#a0b889] bg-[#dde8ce] text-[#52733a]';
      if (level === 'medium') return 'border-[#e0c889] bg-[#f4e6c4] text-[#8a6b1f]';
      if (level === 'handoff') return 'border-[#d49a91] bg-[#f0d6d2] text-[#8a3527]';
      return 'border-[#d8cfba] bg-[#f7f3ea] text-[#8a8275]';
    },

    wikiProposalScoreText(row) {
      const range = (min, max) => {
        if (min === null || min === undefined || min === '') return '-';
        return min === max || max === null || max === undefined || max === '' ? String(min) : `${min}-${max}`;
      };
      return `route ${range(row.route_score_min, row.route_score_max)} · L2 ${range(row.l2_review_score_min, row.l2_review_score_max)} · target ${range(row.target_confidence_min, row.target_confidence_max)}`;
    },

    cleanWikiProposalCopy(value) {
      let text = this.humanizeCopy(value || '');
      text = text
        .replace(/^\s*-\s*reason\s*[：:]\s*/i, '')
        .replace(/target\s+wiki_proposal\s*[：:]\s*/gi, '')
        .replace(/wiki_proposal/gi, 'Wiki 提案')
        .replace(/\s+-\s+/g, '；')
        .replace(/\s+/g, ' ')
        .trim();
      return text;
    },

    clipText(value, max = 150) {
      const text = String(value || '').trim();
      if (text.length <= max) return text;
      return `${text.slice(0, max).trim()}...`;
    },

    wikiProposalTargetName(target) {
      const name = String(target || '').split('/').pop() || '';
      return name.replace(/\.md$/i, '').replaceAll('-', ' ').trim();
    },

    wikiProposalCardTitle(row) {
      const samples = Array.isArray(row.sample_items) ? row.sample_items : [];
      const triage = row && row.investment_triage && typeof row.investment_triage === 'object' ? row.investment_triage : {};
      const sampleTitle = this.cleanWikiProposalCopy(samples[0] && samples[0].title);
      if (sampleTitle && !/^reason\b/i.test(sampleTitle) && !sampleTitle.includes('target Wiki 提案')) {
        return this.clipText(sampleTitle, 56);
      }
      if (row.status === 'investment-thread' || row.status === 'investment-wiki') {
        return `${triage.investment_doc_type_label || '投资资料'}提案`;
      }
      const targetName = this.wikiProposalTargetName(row.target);
      return targetName ? `Wiki 知识提案：${targetName}` : 'Wiki 知识提案';
    },

    wikiProposalCardSummary(row) {
      const samples = Array.isArray(row.sample_items) ? row.sample_items : [];
      const preview = this.cleanWikiProposalCopy((samples[0] && samples[0].preview) || row.preview_cn || row.cn_summary || row.raw_summary);
      if (preview) return this.clipText(preview, 170);
      if (row.status === 'investment-wiki') {
        return '这条内容被系统判断为有长期保存价值，可以先写入投研 Wiki；证据不足的部分会作为待确认点保留。';
      }
      if (row.status === 'investment-thread') {
        return '这条内容被系统判断为有长期保存价值，但需要先进入投资提案归档，暂不直接写入正式投研结论页。';
      }
      return '这条内容被系统判断为有长期保存价值，可在确认后写入长期 Wiki。';
    },

    wikiProposalRecommendation(row) {
      const triage = row && row.investment_triage && typeof row.investment_triage === 'object' ? row.investment_triage : {};
      if (row.status === 'investment-wiki') {
        const reason = this.cleanWikiProposalCopy(triage.reason || '');
        const suffix = reason ? ` 原因：${this.clipText(reason, 80)}` : '';
        return `建议：写入投研 Wiki，信息先沉淀；缺证据链的部分在正文标注待确认。${suffix}`;
      }
      if (row.status === 'investment-thread') {
        const reason = this.cleanWikiProposalCopy(triage.reason || '');
        const suffix = reason ? ` 原因：${this.clipText(reason, 80)}` : '';
        return `建议：移入投资提案归档，生成可检索摘要；暂不写入正式投研页。${suffix}`;
      }
      if (row.status === 'applied') return '建议：已处理，保留为追踪记录。';
      return '建议：确认内容边界后写入 Wiki，沉淀为长期知识。';
    },

    wikiProposalInvestmentMeta(row) {
      const c = window.tmDashboard;
      const triage = row && row.investment_triage && typeof row.investment_triage === 'object' ? row.investment_triage : null;
      if (!triage) return '';
      const lines = [];
      lines.push(`投资分类：${triage.investment_doc_type_label || triage.investment_doc_type || '-'} · 处理：${triage.investment_review_label || triage.investment_review_level || '-'} · 证据：${triage.evidence_level || '-'}`);
      if (triage.investment_target_path) lines.push(`建议 Wiki：${triage.investment_target_path}`);
      if (triage.investment_storage_path) lines.push(`证据归档：${triage.investment_storage_path}`);
      if (triage.symbol || triage.decision_month) lines.push(`标的/月度：${triage.symbol || '-'} · ${triage.decision_month || '-'}`);
      const flags = [];
      if (triage.preserve_original) flags.push('保留原件');
      if (triage.copy_only) flags.push('只复制/追加');
      if (triage.contains_trade_action) flags.push('含买卖动作');
      if (triage.contains_account_data) flags.push('含账户/交易数据');
      if (triage.contains_private_signal) flags.push('含私域线索');
      if (flags.length) lines.push(`边界：${flags.join(' · ')}`);
      if (triage.reason) lines.push(`原因：${triage.reason}`);
      const originalPaths = Array.isArray(triage.original_paths) ? triage.original_paths.filter(Boolean).slice(0, 2) : [];
      originalPaths.forEach(path => lines.push(`原始路径：${path}`));
      return `
        <div class="mt-2 rounded-md border border-[#e6dfcc] bg-[#f7f3ea] p-2 text-xs leading-5 text-[#5d554b]">
          ${lines.map(line => `<div class="break-all">${c.esc(line)}</div>`).join('')}
        </div>
      `;
    },

    wikiProposalPendingRows() {
      return (Array.isArray(this.wikiProposalLedger) ? this.wikiProposalLedger : []).filter(row => row.status === 'pending' || row.status === 'investment-wiki');
    },

    wikiProposalPaths(rows) {
      const seen = new Set();
      const paths = [];
      (rows || []).forEach(row => {
        (Array.isArray(row.paths) ? row.paths : []).forEach(path => {
          const value = String(path || '').trim();
          if (value && !seen.has(value)) {
            seen.add(value);
            paths.push(value);
          }
        });
      });
      return paths;
    },

    renderWikiProposalLedger() {
      const c = window.tmDashboard;
      const section = document.getElementById('wiki-proposal-ledger-section');
      const countNode = document.getElementById('wiki-proposal-ledger-count');
      const summary = document.getElementById('wiki-proposal-ledger-summary');
      const actions = document.getElementById('wiki-proposal-ledger-actions');
      const list = document.getElementById('wiki-proposal-ledger-list');
      if (!section) return;

      const ledger = Array.isArray(this.wikiProposalLedger) ? this.wikiProposalLedger : [];
      const total = ledger.reduce((sum, row) => sum + Number(row.count || 0), 0);
      const pendingRows = this.wikiProposalPendingRows();
      const pendingPaths = this.wikiProposalPaths(pendingRows);
      section.classList.toggle('hidden', total <= 0);
      if (countNode) countNode.textContent = `${total} 条 / ${ledger.length} 组`;
      if (!total) {
        if (summary) summary.textContent = '当前没有待归并的 wiki proposal。';
        if (actions) actions.innerHTML = '';
        if (list) list.innerHTML = '';
        return;
      }

      const investmentWikiGroups = ledger.filter(row => row.status === 'investment-wiki').length;
      const investmentArchiveGroups = ledger.filter(row => row.status === 'investment-thread').length;
      if (summary) {
        summary.textContent = investmentWikiGroups || investmentArchiveGroups
          ? `当前有 ${total} 条 Wiki 提案，聚合为 ${ledger.length} 组；${investmentWikiGroups} 组投研知识可直接写入，${investmentArchiveGroups} 组需先进入投资提案归档。`
          : `当前有 ${total} 条 wiki proposal，聚合为 ${ledger.length} 个目标页；优先由本线程归并成长期 Wiki。`;
      }
      if (actions) {
        actions.innerHTML = pendingPaths.length
          ? `
            <button type="button" data-wiki-ledger-action="approve-all" class="rounded-md bg-[#c8a560] px-3 py-1.5 text-xs font-semibold text-[#1f1d1b] hover:bg-[#b8932e]">批量写入 Wiki ${pendingPaths.length} 条</button>
            <span class="rounded-md border border-[#1f1d1b] bg-[#fbf8f1] px-3 py-1.5 text-xs text-[#1f1d1b]">投研可写 ${investmentWikiGroups} 组 · 归档 ${investmentArchiveGroups} 组</span>
          `
          : '<span class="rounded-md border border-[#e6dfcc] bg-[#f7f3ea] px-3 py-1.5 text-xs text-[#8a8275]">当前没有本线程可直接写入的提案</span>';
      }
      if (!list) return;

      list.innerHTML = ledger.map((row, index) => {
        const paths = Array.isArray(row.paths) ? row.paths : [];
        const samples = Array.isArray(row.sample_items) ? row.sample_items : [];
        const status = row.status || 'pending';
        const canWriteWiki = status === 'pending' || status === 'investment-wiki';
        const isInvestment = status === 'investment-thread' || status === 'investment-wiki';
        const title = this.wikiProposalCardTitle(row);
        const cardSummary = this.wikiProposalCardSummary(row);
        const recommendation = this.wikiProposalRecommendation(row);
        return `
          <article class="rounded-md border ${isInvestment ? 'border-[#1f1d1b] shadow-[inset_4px_0_0_#c8a560]' : 'border-[#e6dfcc]'} bg-[#fbf8f1] p-3" data-wiki-ledger-index="${index}">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <h3 class="text-base font-semibold text-[#1f1d1b]">${c.esc(title)}</h3>
              <div class="flex flex-wrap gap-2">
                <span class="rounded-full border border-[#e6dfcc] bg-[#f7f3ea] px-2 py-1 text-xs text-[#8a8275]">${c.esc(row.count || 0)} 条</span>
                ${isInvestment ? '' : `<span class="rounded-full border px-2 py-1 text-xs ${this.wikiProposalTrustClass(row.review_level)}">${c.esc(row.review_label || '未评分')}</span>`}
                <span class="rounded-full border px-2 py-1 text-xs ${this.wikiProposalStatusClass(status)}">${c.esc(this.wikiProposalStatusLabel(status))}</span>
              </div>
            </div>
            <div class="mt-2 text-sm leading-6 text-[#4a443d]">${c.esc(cardSummary)}</div>
            <div class="mt-3 rounded-md border ${isInvestment ? 'border-[#1f1d1b] bg-[#fbf8f1] text-[#1f1d1b] shadow-[inset_0_3px_0_#c8a560]' : 'border-[#d8cfba] bg-[#f7f3ea] text-[#52733a]'} px-3 py-2 text-sm leading-6">
              ${c.esc(recommendation)}
            </div>
            <div class="mt-3 flex flex-wrap gap-2">
              ${canWriteWiki ? `<button type="button" data-wiki-ledger-action="approve-one" data-index="${index}" class="rounded-md ${status === 'investment-wiki' ? 'border border-[#1f1d1b] bg-[#1f1d1b] text-[#f4d38a] hover:bg-[#2b2824]' : 'bg-[#c8a560] text-[#1f1d1b] hover:bg-[#b8932e]'} px-3 py-1.5 text-xs font-semibold">${status === 'investment-wiki' ? '写入投研 Wiki' : '写入 Wiki'}</button>` : ''}
              ${status === 'investment-thread' ? `<button type="button" data-wiki-ledger-action="investment-archive" data-index="${index}" class="rounded-md border border-[#1f1d1b] bg-[#1f1d1b] px-3 py-1.5 text-xs font-semibold text-[#f4d38a] hover:bg-[#2b2824]">移入投资提案归档</button><span class="rounded-md border border-[#c8a560] bg-[#fbf8f1] px-3 py-1.5 text-xs text-[#1f1d1b]">生成可检索摘要，不写正式投研页</span>` : ''}
            </div>
            <details class="mt-2 text-xs text-[#8a8275]">
              <summary class="cursor-pointer">查看技术详情</summary>
              <div class="mt-2 rounded-md border border-[#e6dfcc] bg-[#f7f3ea] p-2 leading-5">
                <div>目标页：<span class="font-mono">${c.esc(row.target || '(unknown target)')}</span></div>
                <div>首次/最新：${c.esc(row.first_date || '-')} / ${c.esc(row.newest_date || '-')}</div>
                <div>topic：${c.esc(row.topics || '-')} · agent：${c.esc(row.agents || '-')}</div>
                <div>评分：${c.esc(this.wikiProposalScoreText(row))} · 动作：${c.esc(row.wiki_actions || '-')}</div>
                ${this.wikiProposalInvestmentMeta(row)}
                ${samples.length ? `
                  <div class="mt-2 space-y-2">
                    ${samples.map(item => `
                      <div class="rounded-md border border-[#e6dfcc] bg-[#fbf8f1] p-2">
                        <div class="font-semibold text-[#4a443d]">${c.esc(this.cleanWikiProposalCopy(item.title || '未提供标题'))}</div>
                        <div class="mt-1 text-[#8a8275]">${c.esc(this.cleanWikiProposalCopy(item.preview || ''))}</div>
                      </div>
                    `).join('')}
                  </div>
                ` : ''}
                <div class="mt-2">inbox 路径：</div>
                <ul class="mt-1 list-disc space-y-1 pl-5">
                  ${paths.map(path => `<li class="font-mono">${c.esc(path)}</li>`).join('') || '<li>暂无样例路径</li>'}
                </ul>
              </div>
            </details>
            <div data-row-status></div>
          </article>
        `;
      }).join('');
      this.bindWikiProposalLedgerActions(section);
    },

    bindWikiProposalLedgerActions(section) {
      section.onclick = event => {
        const button = event.target.closest('[data-wiki-ledger-action]');
        if (!button || !section.contains(button)) return;
        this.runWikiProposalApproval(button);
      };
    },

    runWikiProposalApproval(button) {
      if (!button || button.disabled) return;
      const mode = button.dataset.wikiLedgerAction;
      const rows = mode === 'approve-all'
        ? this.wikiProposalPendingRows()
        : [this.wikiProposalLedger[Number(button.dataset.index)]].filter(Boolean);
      const paths = this.wikiProposalPaths(rows);
      if (!paths.length) {
        this.toast('没有可写入的 Wiki 提案', false);
        return;
      }
      const cards = rows
        .map(row => this.wikiProposalLedger.indexOf(row))
        .map(index => document.querySelector(`[data-wiki-ledger-index="${index}"]`))
        .filter(Boolean);
      if (mode === 'investment-archive') {
        this.enqueueWikiProposalInvestmentArchive({rows, paths, cards});
        return;
      }
      if (mode === 'approve-all') {
        this.openWikiProposalBatchModal(rows, cards);
        return;
      }
      const row = rows[0];
      const card = cards[0] || null;
      this.openWikiModal(
        {
          ...row,
          path: paths[0],
          title_cn: row.target || 'Wiki 提案',
          preview_cn: (Array.isArray(row.sample_items) && row.sample_items[0] && row.sample_items[0].preview) || '',
          wiki_target: this.wikiTargetFromLedgerRow(row)
        },
        card,
        {
          kind: 'wiki-ledger',
          paths,
          rows,
          cards,
          subtitle: `${row.target || 'Wiki 提案'} · ${paths.length} 条`
        }
      );
    },

    enqueueWikiProposalInvestmentArchive({rows = [], paths = [], cards = []}) {
      if (!paths.length) {
        this.toast('没有可归档的投资资料条目', false);
        return;
      }
      cards.forEach(card => this.setCardBusy(card, true, `正在写入投资归档并移出本页 ${paths.length} 条`));
      const queued = this.enqueueWriteJob({
        jobKey: `wiki-proposal:investment-archive:${paths.join('|')}`,
        label: '投资资料归档',
        detail: `${paths.length} 条投资资料`,
        runningDetail: `正在归档投资资料：${paths.length} 条...`,
        doneDetail: `投资资料已归档：${paths.length} 条`,
        timeoutMs: this.actionTimeoutMs('archive', paths.length),
        run: () => this.postJsonRaw('/api/inbox/batch-action', {paths, action: 'investment_archive'}),
        onSuccess: async ({data}) => {
          cards.forEach(card => {
            this.setCardBusy(card, true, `已归档，正在刷新列表 ${data.commit_sha || ''}`);
          });
          await this.refreshDigestThenCompleteCards(cards, `已归档投资资料 ${data.commit_sha || ''}`);
          this.toast(`已归档投资资料：${data.success_count || paths.length} 条 ${data.commit_sha || ''}`);
        },
        onError: async error => {
          const reconciled = [];
          for (const row of rows) {
            const rowPaths = this.wikiProposalPaths([row]);
            const card = document.querySelector(`[data-wiki-ledger-index="${this.wikiProposalLedger.indexOf(row)}"]`);
            for (const path of rowPaths) {
              if (await this.markCompletedIfPathGoneAfterError(error, path, card, '投资资料归档')) {
                reconciled.push(path);
              }
            }
          }
          if (reconciled.length === paths.length) return true;
          cards.forEach(card => this.setCardBusy(card, false));
          this.toast(`投资资料归档失败：${error.message}`, false);
          return false;
        }
      });
      if (!queued) cards.forEach(card => this.setCardBusy(card, false));
    },

    enqueueWikiProposalApproval({rows = [], paths = [], cards = [], target = null, batch = false}) {
      cards.forEach(card => this.setCardBusy(card, true, `已加入队列：写入 Wiki ${paths.length} 条`));
      const queued = this.enqueueWriteJob({
        jobKey: `wiki-proposal:${batch ? 'batch' : 'single'}:${target ? `${target.partition}:${target.slug}:` : ''}${paths.join('|')}`,
        label: 'Wiki 提案写入',
        detail: target ? `${target.partition}/${target.slug}` : `${paths.length} 条待写入`,
        runningDetail: `正在写入 Wiki：${paths.length} 条...`,
        doneDetail: `写入 Wiki 完成：${paths.length} 条`,
        timeoutMs: this.actionTimeoutMs('promote_wiki', paths.length),
        run: async () => {
          const body = target && paths.length === 1
            ? {path: paths[0], action: 'promote_wiki', partition: target.partition, slug: target.slug}
            : target
              ? {paths, action: 'promote_wiki', partition: target.partition, slug_prefix: target.slug}
              : {paths, action: 'promote_wiki'};
          const result = target && paths.length === 1
            ? {status: 200, data: await this.postJson('/api/inbox/action', body)}
            : await this.postJsonRaw('/api/inbox/batch-action', body);
          if (!result.data || !result.data.ok) {
            throw new Error((result.data && result.data.error) || `错误码 ${result.status}：写入失败`);
          }
          return result;
        },
        onSuccess: async ({data}) => {
          cards.forEach(card => {
            this.setCardBusy(card, true, `写入成功，正在刷新列表 ${data.commit_sha || ''}`);
          });
          await this.refreshDigestThenCompleteCards(cards, `协助成功：写入 Wiki ${data.commit_sha || ''}`);
          this.toast(`成功：写入 Wiki ${data.success_count || paths.length} 条 ${data.commit_sha || ''}`);
        },
        onError: async error => {
          const reconciled = [];
          for (const row of rows) {
            const rowPaths = this.wikiProposalPaths([row]);
            const card = document.querySelector(`[data-wiki-ledger-index="${this.wikiProposalLedger.indexOf(row)}"]`);
            for (const path of rowPaths) {
              if (await this.markCompletedIfPathGoneAfterError(error, path, card, '写入 Wiki')) {
                reconciled.push(path);
              }
            }
          }
          if (reconciled.length === paths.length) return true;
          cards.forEach(card => this.setCardBusy(card, false));
          this.toast(`写入 Wiki 失败：${error.message}`, false);
          return false;
        }
      });
      if (!queued) {
        cards.forEach(card => this.setCardBusy(card, false));
      }
    },

    hardRuleAllowsAction(row, action) {
      if (!(String(row.route_hard_rule) === 'true' || row.route_hard_rule === true)) return true;
      const target = String(row.route_target || '').trim();
      const allowed = {
        mem0: ['promote_mem0'],
        wiki: ['promote_wiki'],
        inbox: ['keep'],
        discard: ['archive']
      };
      if (!target || !allowed[target]) return true;
      return allowed[target].includes(action);
    },

    hardRuleRecommendedAction(row) {
      if (!(String(row.route_hard_rule) === 'true' || row.route_hard_rule === true)) return '';
      const target = String(row.route_target || '').trim();
      const allowed = {
        mem0: 'promote_mem0',
        wiki: 'promote_wiki',
        inbox: 'keep',
        discard: 'archive'
      };
      return allowed[target] || '';
    },

    hardRuleHint(row) {
      const action = this.hardRuleRecommendedAction(row);
      if (!action) return '';
      const label = this.actionLabels[action] || this.humanAction(action);
      return `硬性建议：仅可${label}；灰色按钮是不推荐路线。`;
    },

    actionDisabledAttr(row, action) {
      if (this.hardRuleAllowsAction(row, action)) return '';
      return ' disabled aria-disabled="true"';
    },

    actionDisabledClass(row, action) {
      return this.hardRuleAllowsAction(row, action) ? '' : ' opacity-45 cursor-not-allowed';
    },

    actionEmphasisClass(row, action) {
      return this.hardRuleRecommendedAction(row) === action ? ' ring-2 ring-[#c8a560] ring-offset-1 ring-offset-[#fbf8f1]' : '';
    },

    actionTitle(row, action, fallback) {
      if (this.hardRuleAllowsAction(row, action)) return fallback;
      return `${fallback}；审批建议硬性约束不推荐执行此动作。`;
    },

    actionButtons(row) {
      const c = window.tmDashboard;
      return `
        <button data-action="archive"${this.actionDisabledAttr(row, 'archive')} title="${c.esc(this.actionTitle(row, 'archive', this.actionHelps.archive))}" aria-label="${c.esc(this.actionTitle(row, 'archive', this.actionHelps.archive))}" class="rounded-md bg-[#8a3527] px-3 py-1.5 text-xs text-[#fbf8f1] hover:bg-[#6f2b20]${this.actionDisabledClass(row, 'archive')}${this.actionEmphasisClass(row, 'archive')}">归档</button>
        <button data-action="promote_mem0"${this.actionDisabledAttr(row, 'promote_mem0')} title="${c.esc(this.actionTitle(row, 'promote_mem0', this.actionHelps.promote_mem0))}" aria-label="${c.esc(this.actionTitle(row, 'promote_mem0', this.actionHelps.promote_mem0))}" class="rounded-md bg-[#8a6b1f] px-3 py-1.5 text-xs text-[#fbf8f1] hover:bg-[#6f5619]${this.actionDisabledClass(row, 'promote_mem0')}${this.actionEmphasisClass(row, 'promote_mem0')}">进入即时记忆</button>
        <button data-action="promote_wiki"${this.actionDisabledAttr(row, 'promote_wiki')} title="${c.esc(this.actionTitle(row, 'promote_wiki', this.actionHelps.promote_wiki))}" aria-label="${c.esc(this.actionTitle(row, 'promote_wiki', this.actionHelps.promote_wiki))}" class="rounded-md bg-[#c8a560] px-3 py-1.5 text-xs font-semibold text-[#1f1d1b] hover:bg-[#b8932e]${this.actionDisabledClass(row, 'promote_wiki')}${this.actionEmphasisClass(row, 'promote_wiki')}">写入 Wiki</button>
        <button data-action="keep"${this.actionDisabledAttr(row, 'keep')} title="${c.esc(this.actionTitle(row, 'keep', this.actionHelps.keep))}" aria-label="${c.esc(this.actionTitle(row, 'keep', this.actionHelps.keep))}" class="rounded-md bg-[#e8e0c8] px-3 py-1.5 text-xs text-[#4a443d] hover:bg-[#d8cfb5]${this.actionDisabledClass(row, 'keep')}${this.actionEmphasisClass(row, 'keep')}">保留观察</button>
      `;
    },

    hasMissingTitle(row) {
      return String(row.title_cn || row.cn_summary || '').startsWith('未提供中文摘要');
    },

    rowTitle(row) {
      if (!this.hasMissingTitle(row)) return this.humanizeCopy(row.title_cn || row.cn_summary || '');
      return '这条收件箱条目缺少中文标题';
    },

    rowPreview(row) {
      return this.humanizeCopy(row.preview_cn || row.cn_summary || row.raw_summary || row.summary || '');
    },

    rowReason(row) {
      return this.humanizeCopy(row.codex_recommended_reason || row.reason || '');
    },

    routeRecommendationLabel(row) {
      const routeLabel = String(row.route_label || '').trim();
      const fallbackLabel = String(row.codex_recommended_action || this.humanAction(row.action) || '').trim();
      const confidence = row.route_confidence;
      const confidenceText = confidence === 0 || confidence === '0'
        ? '0'
        : confidence || confidence === 0
          ? String(confidence)
          : '—';
      const reason = String(row.route_reason || row.codex_recommended_reason || row.reason || '').trim() || '无说明';
      const label = routeLabel || fallbackLabel || '未设置';
      return this.humanizeCopy(`审批建议：${this.humanizeCopy(label)} · 置信度 ${confidenceText} · ${this.humanizeCopy(reason)}`);
    },

    routeTargetText(row) {
      const target = row.route_target;
      if (typeof target === 'string') return target.trim();
      if (target && typeof target === 'object') {
        if (typeof target.path === 'string') return target.path.trim();
        if (typeof target.label === 'string') return target.label.trim();
        if (typeof target.partition === 'string') {
          const slug = typeof target.slug === 'string' ? target.slug.trim() : '';
          return `wiki/${target.partition}/${slug || 'inbox-review-note'}.md`;
        }
      }
      return '';
    },

    routeFlagsText(row) {
      const flags = Array.isArray(row.route_flags) ? row.route_flags : [];
      const visible = flags.map(item => String(item).trim()).filter(item => item);
      return visible.length ? visible.join(' / ') : '未设置';
    },

    partitionLabel(partition) {
      const hit = this.wikiPartitions.find(item => item[0] === partition);
      return hit ? hit[1] : partition;
    },

    normalizeWikiSlug(value, fallback = 'inbox-review-note') {
      const clean = String(value || '')
        .toLowerCase()
        .replace(/\.md$/i, '')
        .replace(/[^a-z0-9-]+/g, '-')
        .replace(/^-+|-+$/g, '')
        .slice(0, 80)
        .replace(/^-+|-+$/g, '');
      return clean || fallback;
    },

    wikiTargetPath(partition, slug) {
      return `wiki/${partition}/${this.normalizeWikiSlug(slug)}.md`;
    },

    parseWikiTargetPath(path) {
      const match = String(path || '').replaceAll('\\', '/').match(/^wiki\/([^/]+)\/(.+?)\.md$/);
      if (!match) return null;
      return {partition: match[1], slug: this.normalizeWikiSlug(match[2])};
    },

    wikiTargetFromLedgerRow(row) {
      const parsed = this.parseWikiTargetPath(row && row.target);
      const partition = String((row && row.target_partition) || (parsed && parsed.partition) || 'systems').trim();
      const slug = this.normalizeWikiSlug((row && row.target_slug) || (parsed && parsed.slug) || 'wiki-proposal');
      const alternatives = this.wikiPartitions.map(([part, label]) => ({
        partition: part,
        slug,
        label,
        path: this.wikiTargetPath(part, slug),
        recommended: part === partition,
        reason: part === partition
          ? '系统推荐落点。'
          : `改写到 ${label} 分区，页面名保持一致。`
      }));
      return {
        partition,
        slug,
        path: this.wikiTargetPath(partition, slug),
        reason: '根据 wiki proposal 的 frontmatter 推荐该目标页。',
        alternatives,
        similar: []
      };
    },

    wikiTarget(row) {
      return row.wiki_target || {partition: 'systems', slug: 'inbox-review-note', path: 'wiki/systems/inbox-review-note.md', reason: '缺少后端推荐，临时落到 systems 分区。', alternatives: [], similar: []};
    },

    renderWikiTargetCard(target, selected = false) {
      const c = window.tmDashboard;
      return `
        <button type="button" data-wiki-target data-partition="${c.esc(target.partition)}" data-slug="${c.esc(target.slug)}"
          class="w-full rounded-md border px-3 py-2 text-left text-sm ${selected ? 'border-[#b8932e] bg-[#f4e6c4] text-[#1f1d1b]' : 'border-[#e6dfcc] bg-[#fbf8f1] text-[#4a443d] hover:bg-[#f0e9d8]'}">
          <div class="flex flex-wrap items-center gap-2">
            <span class="font-semibold">${c.esc(target.label || target.partition)}</span>
            ${target.recommended ? '<span class="rounded-full bg-[#c8a560] px-2 py-0.5 text-xs text-[#1f1d1b]">默认推荐</span>' : ''}
          </div>
          <div class="mt-1 font-mono text-xs text-[#4a443d]">${c.esc(target.path || `wiki/${target.partition}/${target.slug}.md`)}</div>
          <div class="mt-1 text-xs text-[#8a8275]">${c.esc(this.humanizeCopy(target.reason || ''))}</div>
        </button>
      `;
    },

    openWikiModal(row, card, options = {}) {
      const c = window.tmDashboard;
      const target = this.wikiTarget(row);
      const alternatives = target.alternatives && target.alternatives.length ? target.alternatives : [target];
      this.wikiModalState = {
        kind: options.kind || 'inbox',
        row,
        card,
        paths: Array.isArray(options.paths) ? options.paths : (row.path ? [row.path] : []),
        cards: Array.isArray(options.cards) ? options.cards : (card ? [card] : []),
        rows: Array.isArray(options.rows) ? options.rows : [row],
        target: {partition: target.partition, slug: target.slug}
      };
      const modalTitle = document.getElementById('wiki-modal-title');
      if (modalTitle) {
        modalTitle.setAttribute('data-i18n', 'daily.wiki.modal.title');
        modalTitle.textContent = (window.tmI18n && window.tmI18n.get('daily.wiki.modal.title', '写入知识库推荐')) || '写入知识库推荐';
      }
      const modalSubtitle = document.getElementById('wiki-modal-subtitle');
      if (modalSubtitle) modalSubtitle.textContent = options.subtitle || this.rowTitle(row);
      const modalConfirm = document.getElementById('wiki-modal-confirm');
      if (modalConfirm) {
        modalConfirm.setAttribute('data-i18n', 'action.confirm_wiki');
        modalConfirm.textContent = (window.tmI18n && window.tmI18n.get('action.confirm_wiki', '确认写入知识库')) || '确认写入知识库';
        modalConfirm.className = 'rounded-md bg-[#c8a560] px-4 py-2 text-sm font-medium text-[#1f1d1b] hover:bg-[#b8932e] disabled:opacity-50';
      }

      const modalBody = document.getElementById('wiki-modal-body');
      if (modalBody) {
        modalBody.innerHTML = `
          <section class="rounded-md border border-[#c9bf9f] bg-[#f4e6c4] p-3">
            <div class="text-sm font-semibold text-[#1f1d1b]">Codex 推荐操作</div>
            <div class="mt-1 font-mono text-xs text-[#4a443d]">${c.esc(target.path)}</div>
            <div class="mt-2 text-sm leading-6 text-[#4a443d]">${c.esc(this.humanizeCopy(target.reason || ''))}</div>
          </section>
          <section>
            <div class="mb-2 text-sm font-semibold text-[#1f1d1b]">其他可选落点</div>
            <div class="grid gap-2 md:grid-cols-2">
              ${alternatives.map(item => this.renderWikiTargetCard(item, item.partition === target.partition && item.slug === target.slug)).join('')}
            </div>
          </section>
          <section>
            <div class="mb-2 text-sm font-semibold text-[#1f1d1b]">相似页面参考</div>
            ${(target.similar || []).length ? `
              <div class="space-y-2">
                ${(target.similar || []).map(item => `
                  <div class="rounded-md border border-[#e6dfcc] bg-[#f0e9d8] p-2 text-sm">
                    <div class="font-mono text-xs text-[#4a443d]">${c.esc(item.path)}</div>
                    <div class="mt-1 text-xs text-[#8a8275]">${c.esc(this.humanizeCopy(item.reason))}</div>
                  </div>
                `).join('')}
              </div>
            ` : '<p class="text-sm text-[#8a8275]">未找到明显相似页面，可以按默认推荐新建。</p>'}
          </section>
        `;
      }

      const wikiModal = document.getElementById('wiki-modal');
      if (wikiModal) {
        wikiModal.classList.remove('hidden');
        wikiModal.classList.add('flex');
      }

      document.querySelectorAll('[data-wiki-target]').forEach(button => {
        button.addEventListener('click', event => {
          document.querySelectorAll('[data-wiki-target]').forEach(node => {
            node.classList.remove('border-[#b8932e]', 'bg-[#f4e6c4]', 'text-[#1f1d1b]');
            node.classList.add('border-[#e6dfcc]', 'bg-[#fbf8f1]', 'text-[#4a443d]');
          });
          const current = event.currentTarget;
          current.classList.add('border-[#b8932e]', 'bg-[#f4e6c4]', 'text-[#1f1d1b]');
          current.classList.remove('border-[#e6dfcc]', 'bg-[#fbf8f1]', 'text-[#4a443d]');
          this.wikiModalState.target = {partition: current.dataset.partition, slug: current.dataset.slug};
        }, { signal: this.abortController.signal });
      });
    },

    openActionConfirmModal(row, card, action) {
      const c = window.tmDashboard;
      const label = this.actionLabels[action] || action;
      const impact = {
        archive: '确认后，这条内容会移到归档区，今天的待确认列表不再显示；不会写入即时记忆，也不会写入 Wiki。',
        promote_mem0: '确认后，这条内容会进入即时记忆，用于近期偏好、反馈和仍在变化的会话结论。',
        keep: '确认后，这条内容保留观察，今天的待确认列表不再显示，后续日报仍可复查。'
      };
      this.wikiModalState = {
        kind: 'action-confirm',
        row,
        card,
        action
      };
      const modalTitle = document.getElementById('wiki-modal-title');
      if (modalTitle) {
        modalTitle.removeAttribute('data-i18n');
        modalTitle.textContent = `${label}确认`;
      }
      const modalSubtitle = document.getElementById('wiki-modal-subtitle');
      if (modalSubtitle) modalSubtitle.textContent = this.rowTitle(row);
      const modalConfirm = document.getElementById('wiki-modal-confirm');
      if (modalConfirm) {
        modalConfirm.removeAttribute('data-i18n');
        modalConfirm.textContent = `确认${label}`;
        modalConfirm.className = 'rounded-md bg-[#1f1d1b] px-4 py-2 text-sm font-semibold text-[#f4e6c4] hover:bg-[#3a3328] disabled:opacity-50';
      }
      const modalBody = document.getElementById('wiki-modal-body');
      if (modalBody) {
        modalBody.innerHTML = `
          <section class="rounded-md border border-[#1f1d1b] bg-[#fbf8f1] p-3">
            <div class="text-sm font-semibold text-[#1f1d1b]">本次操作</div>
            <div class="mt-2 text-sm leading-6 text-[#4a443d]">${c.esc(impact[action] || this.actionHelps[action] || '')}</div>
          </section>
          <section class="rounded-md border border-[#c9bf9f] bg-[#f4e6c4] p-3">
            <div class="text-sm font-semibold text-[#1f1d1b]">系统建议</div>
            <div class="mt-2 text-sm leading-6 text-[#4a443d]">${c.esc(this.routeRecommendationLabel(row))}</div>
          </section>
          <section>
            <div class="mb-2 text-sm font-semibold text-[#1f1d1b]">内容摘要</div>
            <div class="rounded-md border border-[#e6dfcc] bg-[#f0e9d8] p-3 text-sm leading-6 text-[#4a443d]">${c.esc(this.rowPreview(row) || this.rowTitle(row))}</div>
          </section>
        `;
      }
      const wikiModal = document.getElementById('wiki-modal');
      if (wikiModal) {
        wikiModal.classList.remove('hidden');
        wikiModal.classList.add('flex');
      }
    },

    closeWikiModal() {
      const wikiModal = document.getElementById('wiki-modal');
      if (wikiModal) {
        wikiModal.classList.add('hidden');
        wikiModal.classList.remove('flex');
      }
      this.wikiModalState = null;
    },

    openWikiProposalBatchModal(rows, cards) {
      const c = window.tmDashboard;
      const paths = this.wikiProposalPaths(rows);
      if (!paths.length) {
        this.toast('没有可写入的 Wiki 提案', false);
        return;
      }
      this.wikiModalState = {
        kind: 'wiki-ledger-batch',
        row: {title_cn: `按推荐批量写入 ${paths.length} 条 Wiki 提案`},
        card: null,
        rows,
        cards,
        paths,
        target: null
      };
      const modalTitle = document.getElementById('wiki-modal-title');
      if (modalTitle) {
        modalTitle.setAttribute('data-i18n', 'daily.wiki.modal.title');
        modalTitle.textContent = (window.tmI18n && window.tmI18n.get('daily.wiki.modal.title', '写入知识库推荐')) || '写入知识库推荐';
      }
      const modalConfirm = document.getElementById('wiki-modal-confirm');
      if (modalConfirm) {
        modalConfirm.setAttribute('data-i18n', 'action.confirm_wiki');
        modalConfirm.textContent = (window.tmI18n && window.tmI18n.get('action.confirm_wiki', '确认写入知识库')) || '确认写入知识库';
        modalConfirm.className = 'rounded-md bg-[#c8a560] px-4 py-2 text-sm font-medium text-[#1f1d1b] hover:bg-[#b8932e] disabled:opacity-50';
      }
      const modalSubtitle = document.getElementById('wiki-modal-subtitle');
      if (modalSubtitle) modalSubtitle.textContent = `按每条提案自己的推荐目标写入，共 ${paths.length} 条`;
      const modalBody = document.getElementById('wiki-modal-body');
      if (modalBody) {
        modalBody.innerHTML = `
          <section class="rounded-md border border-[#c9bf9f] bg-[#f4e6c4] p-3">
            <div class="text-sm font-semibold text-[#1f1d1b]">Codex 推荐操作</div>
            <div class="mt-2 grid gap-2 text-xs leading-5 text-[#4a443d]">
              ${(rows || []).map(row => `
                <div class="rounded-md border border-[#e6dfcc] bg-[#fbf8f1] p-2">
                  <div class="font-mono">${c.esc(row.target || '(unknown target)')}</div>
                  <div class="mt-1 text-[#8a8275]">${c.esc(row.count || 0)} 条 · ${c.esc(row.review_label || '未评分')}</div>
                </div>
              `).join('')}
            </div>
          </section>
          <section>
            <div class="mb-2 text-sm font-semibold text-[#1f1d1b]">处理方式</div>
            <div class="rounded-md border border-[#e6dfcc] bg-[#f0e9d8] p-3 text-sm leading-6 text-[#4a443d]">本次批量按各提案自己的推荐目标页分别写入；单条改分类时不合并进批量操作。</div>
          </section>
        `;
      }
      const wikiModal = document.getElementById('wiki-modal');
      if (wikiModal) {
        wikiModal.classList.remove('hidden');
        wikiModal.classList.add('flex');
      }
    },

    setCardStatus(card, message, ok = true) {
      const node = card.querySelector('[data-row-status]');
      if (!node) return;
      node.textContent = message || '';
      node.className = `mt-2 text-xs ${ok ? 'text-[#52733a]' : 'text-[#8a3527]'}`;
    },

    setCardBusy(card, busy, message = '') {
      card.querySelectorAll('button, input[type="checkbox"]').forEach(node => node.disabled = busy);
      card.classList.toggle('tm-card-busy', busy);
      if (message) this.setCardStatus(card, message, true);
    },

    completeCard(card, message = '协助成功') {
      if (!card || !card.isConnected) return;
      this.setCardBusy(card, false);
      card.querySelectorAll('button, input[type="checkbox"]').forEach(node => node.disabled = true);
      card.classList.remove('line-through');
      card.classList.add('tm-card-complete');
      this.setCardStatus(card, message, true);
    },

    async refreshDigestThenCompleteCards(cards, message) {
      await this.fetchDigest({quiet: true});
      (cards || []).forEach(card => {
        if (card && card.isConnected) this.completeCard(card, message);
      });
    },

    setWriteControlsDisabled(disabled) {
      document.querySelectorAll('#inbox-list button[data-action], #inbox-list input[type="checkbox"], #batch-toolbar button, #wiki-modal-confirm').forEach(node => {
        node.disabled = disabled;
      });
    },

    queueStatusText(status, job = null) {
      if (status === 'running') return '处理中';
      if (status === 'done') return '完成';
      if (status === 'failed' && job && job.timeoutExpired) return '超时';
      if (status === 'failed') return '失败';
      return '排队中';
    },

    queueTimeoutText(ms) {
      return `任务超时 (${ms}ms)`;
    },

    queueProgressWidth(job) {
      if (!job) return 18;
      if (job.status === 'done' || job.status === 'failed') return 100;
      if (job.status === 'running') return Math.max(48, Math.min(90, Number(job.progress) || 64));
      return 18;
    },

    queueJobKey(job) {
      if (job.jobKey) return job.jobKey;
      if (job.batchAction && job.batchPath) return `batch:${job.batchAction}:${job.batchPath}`;
      if (job.batchAction && Array.isArray(job.batchPath)) return `batch:${job.batchAction}:${job.batchPath.join('|')}`;
      return `single:${job.label || ''}:${job.detail || ''}`;
    },

    findQueuedJobByKey(jobKey, statuses = ['queued', 'running']) {
      if (!this.writeQueue) return null;
      return this.writeQueue.find(item => statuses.includes(item.status) && item.jobKey === jobKey);
    },

    runWithTimeout(fn, timeoutMs, job) {
      const timeout = Number.isFinite(timeoutMs) ? timeoutMs : this.queueTimeoutMs;
      return Promise.race([
        Promise.resolve().then(fn),
        new Promise((_, reject) => {
          job.timeoutTimer = setTimeout(() => {
            reject(new Error(`任务超时 (${timeout}ms)`));
          }, timeout);
        })
      ]).finally(() => {
        if (job.timeoutTimer) {
          clearTimeout(job.timeoutTimer);
          job.timeoutTimer = null;
        }
      });
    },

    actionTimeoutMs(action, count = 1) {
      const itemCount = Math.max(1, Number(count) || 1);
      if (action === 'promote_mem0') return Math.min(180000, 45000 + itemCount * 30000);
      if (action === 'promote_wiki') return Math.min(360000, 120000 + itemCount * 30000);
      if (action === 'archive') return Math.min(120000, 30000 + itemCount * 10000);
      return this.queueTimeoutMs;
    },

    renderActionQueue() {
      const dock = document.getElementById('action-queue');
      if (!dock) return;
      const queue = this.writeQueue || [];
      const active = queue.filter(job => job.status === 'queued' || job.status === 'running').length;
      const failed = queue.filter(job => job.status === 'failed').length;
      const done = queue.filter(job => job.status === 'done').length;
      dock.classList.toggle('hidden', queue.length === 0);
      dock.classList.toggle('tm-action-queue--active', active > 0);
      dock.classList.toggle('tm-action-queue--failed', failed > 0);

      const summary = document.getElementById('action-queue-summary');
      if (summary) {
        const label = active > 0 ? `${active} 项正在处理` : failed > 0 ? `${failed} 项需要查看` : `${done} 项已完成`;
        summary.innerHTML = `
          <div class="tm-action-queue__pulse"></div>
          <div>
            <div class="tm-action-queue__title">处理队列</div>
            <div class="tm-action-queue__subtitle">${label}</div>
          </div>
          <div class="tm-action-queue__hint">悬停展开</div>
        `;
      }

      const list = document.getElementById('action-queue-list');
      if (list) {
        list.innerHTML = queue.slice(-6).map(job => `
          <div class="tm-action-queue__item tm-action-queue__item--${job.status}">
            <div class="min-w-0">
              <div class="truncate text-sm font-semibold">${window.tmDashboard.esc(job.label)}</div>
              <div class="truncate text-xs text-[#8a8275]">${window.tmDashboard.esc(job.detail || this.queueStatusText(job.status, job))}</div>
            </div>
            <div class="shrink-0 text-xs font-semibold">${this.queueStatusText(job.status, job)}</div>
            <div class="tm-action-queue__bar"><span style="width:${this.queueProgressWidth(job)}%"></span></div>
          </div>
        `).join('');
      }
    },

    clearSettledQueueJobs() {
      if (!this.writeQueue) return;
      this.writeQueue = this.writeQueue.filter(job => job.status === 'queued' || job.status === 'running');
      this.renderActionQueue();
    },

    scheduleWriteQueue(delay = 90) {
      if (this.queueProcessing) return;
      if (this.queueTimer) clearTimeout(this.queueTimer);
      this.queueTimer = setTimeout(() => {
        this.queueTimer = null;
        this.processWriteQueue();
      }, delay);
    },

    enqueueWriteJob(job) {
      if (!this.writeQueue) this.writeQueue = [];
      const next = {
        id: ++this.queueSeq,
        status: 'queued',
        progress: 0,
        jobKey: this.queueJobKey(job),
        timeoutMs: Number.isFinite(job.timeoutMs) ? job.timeoutMs : this.queueTimeoutMs,
        ...job
      };

      if (next.status !== 'queued') next.status = 'queued';
      const duplicate = this.findQueuedJobByKey(next.jobKey);
      if (duplicate) {
        duplicate.detail = `重复操作已合并`;
        this.renderActionQueue();
        this.toast(`相同操作已在队列中`);
        return null;
      }

      this.writeQueue.push(next);
      this.renderActionQueue();
      this.toast('已加入处理队列');
      this.scheduleWriteQueue();
      return next;
    },

    batchableQueuedJobs(firstJob) {
      if (!firstJob.batchAction || !firstJob.batchPath) return [firstJob];
      return this.writeQueue
        .filter(item => item.status === 'queued' && item.batchAction === firstJob.batchAction && item.batchPath)
        .slice(0, 20);
    },

    async processBatchedWriteJobs(jobs) {
      const action = jobs[0].batchAction;
      const timeoutMs = Math.max(
        ...jobs.map(job => Number.isFinite(job.timeoutMs) ? job.timeoutMs : this.queueTimeoutMs),
        this.actionTimeoutMs(action, jobs.length)
      );
      const requestId = ++this.queueRequestId;
      jobs.forEach(job => {
        job.status = 'running';
        job.progress = 58;
        job.detail = job.runningDetail || job.detail;
      });
      this.renderActionQueue();
      try {
        const {status, data} = await this.runWithTimeout(() => this.postJsonRaw('/api/inbox/batch-action', {
          paths: jobs.map(job => job.batchPath),
          action
        }), timeoutMs, jobs[0]);
        const resultByPath = new Map((data.results || []).map(item => [item.path, item]));
        const failureByPath = new Map((data.failures || []).map(item => [item.path, item]));
        const successCallbacks = [];
        jobs.forEach(job => {
          const result = resultByPath.get(job.batchPath);
          const failure = failureByPath.get(job.batchPath);
          if (this.isBatchResultOk(result)) {
            job.detail = '正在确认完成...';
            job.progress = 84;
            successCallbacks.push(Promise.resolve(job.onSuccess ? job.onSuccess({status, data, ...result, commit_sha: data.commit_sha, path: job.batchPath}) : null).then(() => {
              job.status = 'done';
              job.progress = 100;
              job.detail = job.doneDetail || '处理完成';
            }));
            return;
          }
          job.status = 'failed';
          job.detail = (failure && failure.error) || data.error || `错误码 ${status}：操作失败`;
          if (job.onError) job.onError(new Error(job.detail));
        });
        if (successCallbacks.length) {
          this.renderActionQueue();
          await Promise.all(successCallbacks);
        }
        if (data.ok) {
          this.toast(`批量${this.actionLabels[action] || action}成功：${data.success_count || jobs.length} 条 ${data.commit_sha || ''}`);
        } else {
          this.toast(`批量${this.actionLabels[action] || action}部分失败：成功 ${data.success_count || 0} 条，失败 ${data.failure_count || jobs.length} 条`, false);
        }
      } catch (error) {
        if (error.name === 'AbortError') throw error;
        jobs.forEach(job => {
          if (error.message && error.message.indexOf('任务超时') !== -1) {
            job.timeoutExpired = true;
            job.detail = '超时已中断，可稍后重试';
          } else {
            job.detail = error.message || '处理失败';
          }
          job.status = 'failed';
          if (job.onError) job.onError(error);
        });
      }
    },

    async processWriteQueue() {
      if (this.queueProcessing || !this.writeQueue) return;
      const job = this.writeQueue.find(item => item.status === 'queued');
      if (!job) {
        this.actionInFlight = false;
        this.renderActionQueue();
        return;
      }
      this.queueProcessing = true;
      this.actionInFlight = true;
      const batchedJobs = this.batchableQueuedJobs(job);
      if (batchedJobs.length > 1) {
        try {
          await this.processBatchedWriteJobs(batchedJobs);
          setTimeout(() => {
            if (!this.writeQueue) return;
            this.writeQueue = this.writeQueue.filter(item => item.status !== 'done');
            this.renderActionQueue();
          }, 7000);
        } catch (error) {
          if (error.name !== 'AbortError') console.error(error);
        } finally {
          this.queueProcessing = false;
          this.actionInFlight = Boolean(this.writeQueue.find(item => item.status === 'queued' || item.status === 'running'));
          this.renderActionQueue();
          if (this.writeQueue.find(item => item.status === 'queued')) this.processWriteQueue();
        }
        return;
      }
      job.status = 'running';
      job.progress = 58;
      job.detail = job.runningDetail || job.detail;
      this.renderActionQueue();
      try {
        const result = await this.runWithTimeout(() => job.run(), job.timeoutMs, job);
        job.detail = '正在确认完成...';
        job.progress = 84;
        this.renderActionQueue();
        if (job.onSuccess) await job.onSuccess(result);
        job.status = 'done';
        job.progress = 100;
        job.detail = job.doneDetail || '处理完成';
        setTimeout(() => {
          if (!this.writeQueue) return;
          this.writeQueue = this.writeQueue.filter(item => item.id !== job.id || item.status !== 'done');
          this.renderActionQueue();
        }, 7000);
      } catch (error) {
        if (error.name === 'AbortError') return;
        console.error(error);
        let recovered = false;
        if (error.message && error.message.indexOf('任务超时') !== -1) {
          job.timeoutExpired = true;
          job.detail = '超时已中断，可稍后重试';
        } else {
          job.detail = error.message || '处理失败';
        }
        job.status = 'failed';
        if (job.onError) recovered = await job.onError(error);
        if (recovered) {
          job.status = 'done';
          job.progress = 100;
          job.detail = job.doneDetail || '处理完成';
          setTimeout(() => {
            if (!this.writeQueue) return;
            this.writeQueue = this.writeQueue.filter(item => item.id !== job.id || item.status !== 'done');
            this.renderActionQueue();
          }, 7000);
        }
      } finally {
        this.queueProcessing = false;
        this.actionInFlight = Boolean(this.writeQueue.find(item => item.status === 'queued' || item.status === 'running'));
        this.renderActionQueue();
        if (this.writeQueue.find(item => item.status === 'queued')) this.processWriteQueue();
      }
    },

    hideReviewedCard(card, row) {
      if (this.selectedInbox) this.selectedInbox.delete(row.path);
      const checkbox = card.querySelector('input[type="checkbox"]');
      if (checkbox) checkbox.checked = false;
      card.remove();
      this.updateBatchToolbar();
      if (!document.querySelector('#inbox-list article')) {
        const inboxList = document.getElementById('inbox-list');
        if (inboxList) {
          const msg = (window.tmI18n && window.tmI18n.get('daily.inbox.no_pending', '当前没有需要你处理的内容。')) || '当前没有需要你处理的内容。';
          inboxList.innerHTML = `<p class="text-sm text-[#8a8275]">${msg}</p>`;
        }
      }
    },

    selectedPaths() {
      return this.selectedInbox ? Array.from(this.selectedInbox) : [];
    },

    digestHasInboxPath(path) {
      const target = String(path || '').replaceAll('\\', '/');
      if (!target) return false;
      const rows = [
        ...(Array.isArray(this.inboxRows) ? this.inboxRows : []),
        ...(Array.isArray(this.digest && this.digest.hidden_inbox_rows) ? this.digest.hidden_inbox_rows : [])
      ];
      if (rows.some(row => String(row.path || '').replaceAll('\\', '/') === target)) return true;
      return (Array.isArray(this.wikiProposalLedger) ? this.wikiProposalLedger : []).some(row =>
        (Array.isArray(row.paths) ? row.paths : []).some(item => String(item || '').replaceAll('\\', '/') === target)
      );
    },

    async markCompletedIfPathGone(path, card, label, waitMs = 0) {
      if (!path) return false;
      const deadline = Date.now() + Math.max(0, Number(waitMs) || 0);
      do {
        await this.fetchDigest({quiet: true});
        if (!this.digestHasInboxPath(path)) {
          if (card && card.isConnected) {
            this.completeCard(card, `协助成功：${label}。页面已复查确认该条已处理。`);
          }
          this.toast(`已完成：${label}。页面已复查确认该条已处理。`);
          return true;
        }
        if (Date.now() >= deadline) break;
        await new Promise(resolve => setTimeout(resolve, 2500));
      } while (true);
      return false;
    },

    async markCompletedIfPathGoneAfterError(error, path, card, label) {
      const waitMs = error && error.message && error.message.indexOf('任务超时') !== -1 ? 90000 : 0;
      return this.markCompletedIfPathGone(path, card, label, waitMs);
    },

    isBatchResultOk(result) {
      if (!result) return false;
      if (result.ok) return true;
      return Boolean(result.archive && result.archive.ok);
    },

    enqueueInboxRowAction(row, card, action) {
      if (!row || !card || !action) return null;
      const label = this.actionLabels[action] || action;
      this.setCardBusy(card, true, `已加入队列：${label}`);
      const queued = this.enqueueWriteJob({
        jobKey: `single:${action}:${this.digest.date || ''}:${row.path}`,
        timeoutMs: this.actionTimeoutMs(action, 1),
        label,
        detail: this.rowTitle(row),
        runningDetail: `正在${label}...`,
        doneDetail: `${label}完成`,
        batchAction: ['archive', 'promote_mem0'].includes(action) ? action : null,
        batchPath: ['archive', 'promote_mem0'].includes(action) ? row.path : null,
        run: () => this.postJson('/api/inbox/action', {path: row.path, action, date: this.digest.date}),
        onSuccess: async data => {
          const commitText = data && data.commit_sha ? ` ${data.commit_sha}` : '';
          if (this.selectedInbox) this.selectedInbox.delete(row.path);
          const checkbox = card.querySelector('input[type="checkbox"]');
          if (checkbox) checkbox.checked = false;
          this.updateBatchToolbar();
          if (action === 'keep') {
            this.setCardBusy(card, true, '已保留观察，正在刷新列表...');
            await this.refreshDigestThenCompleteCards([card], '已保留观察，今天的待确认列表不再显示。');
            if (card.isConnected) this.hideReviewedCard(card, row);
            this.toast('已保留观察：今天的待确认列表不再显示这条内容');
            return;
          }
          this.setCardBusy(card, true, `${label}成功，正在刷新列表${commitText}`);
          await this.refreshDigestThenCompleteCards([card], `成功：${label}${commitText}`);
          this.toast(`成功：${label}${commitText}`);
        },
        onError: async error => {
          if (await this.markCompletedIfPathGoneAfterError(error, row.path, card, label)) {
            return true;
          }
          this.setCardBusy(card, false);
          card.classList.remove('opacity-50', 'line-through');
          this.setCardStatus(card, `出错了：${error.message}`, false);
          this.toast(`出错了：${error.message}`, false);
          return false;
        }
      });
      if (!queued) {
        this.setCardBusy(card, false);
        this.setCardStatus(card, `已存在相同任务：${label}`, false);
      }
      return queued;
    },

    updateBatchToolbar() {
      if (!this.selectedInbox) return;
      const count = this.selectedInbox.size;
      const toolbar = document.getElementById('batch-toolbar');
      if (!toolbar) return;
      const selectedCount = document.getElementById('selected-count');
      if (selectedCount) selectedCount.textContent = `已选择 ${count} 条`;

      toolbar.classList.toggle('hidden', count === 0);
      toolbar.classList.toggle('flex', count > 0);
      ['batch-archive-selected', 'batch-mem0-selected', 'batch-wiki-selected'].forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.disabled = count === 0;
      });
    },

    async runBatchAction(action) {
      const paths = this.selectedPaths();
      if (!paths.length) return;
      const body = {paths, action};

      let btnId = '';
      let processingText = '';
      if (action === 'archive') {
        btnId = 'batch-archive-selected';
        processingText = (window.tmI18n && window.tmI18n.get('daily.processing.batch_archive', '正在批量归档中...')) || '正在批量归档中...';
      } else if (action === 'promote_mem0') {
        btnId = 'batch-mem0-selected';
        processingText = (window.tmI18n && window.tmI18n.get('daily.processing.batch_mem0', '正在批量进入即时记忆中...')) || '正在批量进入即时记忆中...';
      } else if (action === 'promote_wiki') {
        btnId = 'batch-wiki-selected';
        processingText = (window.tmI18n && window.tmI18n.get('daily.processing.batch_wiki', '正在批量写入 Wiki 中...')) || '正在批量写入 Wiki 中...';
      }

      const activeBtn = btnId ? document.getElementById(btnId) : null;
      let originalText = '';
      if (activeBtn) {
        originalText = activeBtn.textContent;
        activeBtn.setAttribute('aria-busy', 'true');
      }

      ['batch-archive-selected', 'batch-mem0-selected', 'batch-wiki-selected'].forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.disabled = true;
      });
      paths.forEach(path => {
        const card = document.querySelector(`article[data-path="${CSS.escape(path)}"]`);
        if (card) this.setCardBusy(card, true, `已加入队列：批量${this.actionLabels[action] || action}`);
      });

      const jobKey = `batch:${action}:${this.digest.date || ''}:${paths.join('|')}`;
      const queued = this.enqueueWriteJob({
        jobKey,
        label: `批量${this.actionLabels[action] || action}`,
        detail: `${paths.length} 条待处理`,
        runningDetail: processingText || '正在处理...',
        doneDetail: `${paths.length} 条处理完成`,
        timeoutMs: this.actionTimeoutMs(action, paths.length),
        run: () => this.postJsonRaw('/api/inbox/batch-action', body),
        onSuccess: async ({status, data}) => {
          const resultByPath = new Map((data.results || []).map(item => [item.path, item]));
          const failureByPath = new Map((data.failures || []).map(item => [item.path, item]));
          const completedCards = [];
          paths.forEach(path => {
            const card = document.querySelector(`article[data-path="${CSS.escape(path)}"]`);
            if (!card) return;
            const result = resultByPath.get(path);
            const failure = failureByPath.get(path);
            if (this.isBatchResultOk(result)) {
              card.classList.remove('opacity-50', 'line-through');
              this.setCardBusy(card, true, `批量${this.actionLabels[action] || action}成功，正在刷新列表 ${data.commit_sha || ''}`);
              completedCards.push(card);
              const checkbox = card.querySelector('input[type="checkbox"]');
              if (checkbox) checkbox.checked = false;
              if (this.selectedInbox) this.selectedInbox.delete(path);
            } else {
              card.classList.remove('opacity-50', 'line-through');
              this.setCardBusy(card, false);
              this.setCardStatus(card, `出错了：批量${this.actionLabels[action] || action}失败（错误码 ${status}）：${(failure && failure.error) || data.error || '操作失败'}`, false);
            }
          });
          if (!data.ok) {
            this.toast(`出错了：批量${this.actionLabels[action] || action}部分失败：成功 ${data.success_count || 0} 条，失败 ${data.failure_count || 0} 条（错误码 ${status}）${data.error ? `：${data.error}` : ''}`, false);
          } else {
            this.toast(`批量${this.actionLabels[action] || action}成功：${data.success_count || paths.length} 条 ${data.commit_sha || ''}`);
          }
          if (completedCards.length) {
            await this.refreshDigestThenCompleteCards(completedCards, `批量${this.actionLabels[action] || action}成功 ${data.commit_sha || ''}`);
          }
          if (activeBtn) {
            activeBtn.textContent = originalText;
            activeBtn.removeAttribute('aria-busy');
          }
          this.updateBatchToolbar();
        },
        onError: async error => {
          const reconciled = [];
          paths.forEach(path => {
            const card = document.querySelector(`article[data-path="${CSS.escape(path)}"]`);
            if (!card) return;
            this.setCardBusy(card, false);
            this.setCardStatus(card, `出错了：${error.message}`, false);
          });
          for (const path of paths) {
            const card = document.querySelector(`article[data-path="${CSS.escape(path)}"]`);
            if (await this.markCompletedIfPathGone(path, card, this.actionLabels[action] || action)) {
              reconciled.push(path);
            }
          }
          if (reconciled.length === paths.length) {
            if (activeBtn) {
              activeBtn.textContent = originalText;
              activeBtn.removeAttribute('aria-busy');
            }
            this.updateBatchToolbar();
            return;
          }
          if (activeBtn) {
            activeBtn.textContent = originalText;
            activeBtn.removeAttribute('aria-busy');
          }
          ['batch-archive-selected', 'batch-mem0-selected', 'batch-wiki-selected'].forEach(id => {
            const btn = document.getElementById(id);
            if (btn) btn.disabled = false;
          });
          this.updateBatchToolbar();
          this.toast(`出错了：${error.message}`, false);
        }
      });
      if (!queued) {
        paths.forEach(path => {
          const card = document.querySelector(`article[data-path="${CSS.escape(path)}"]`);
          if (!card) return;
          this.setCardBusy(card, false);
          this.setCardStatus(card, `已存在相同任务：批量${this.actionLabels[action] || action}`, false);
        });
        if (activeBtn) {
          activeBtn.textContent = originalText;
          activeBtn.removeAttribute('aria-busy');
        }
        ['batch-archive-selected', 'batch-mem0-selected', 'batch-wiki-selected'].forEach(id => {
          const btn = document.getElementById(id);
          if (btn) btn.disabled = false;
        });
      }
    },

    renderInbox() {
      const c = window.tmDashboard;
      const list = document.getElementById('inbox-list');
      if (!list) return;
      if (this.digest.loading) {
        list.innerHTML = Array.from({length: 3}).map(() => `
          <div class="rounded-md border border-[#e6dfcc] p-4 shimmer-loader" style="min-height: 70px">
            <div class="flex items-center justify-between gap-4">
              <div class="space-y-2 flex-1">
                <div class="h-4 w-1/3 rounded bg-[#e8e0c8]/60"></div>
                <div class="h-3 w-1/4 rounded bg-[#e8e0c8]/60"></div>
              </div>
              <div class="flex gap-2 shrink-0">
                <div class="h-8 w-14 rounded-md bg-[#e8e0c8]/60"></div>
                <div class="h-8 w-14 rounded-md bg-[#e8e0c8]/60"></div>
              </div>
            </div>
          </div>
        `).join('');
        return;
      }
      if (!this.inboxRows.length) {
        const msg = (window.tmI18n && window.tmI18n.get('daily.inbox.no_items', '当前没有待确认内容')) || '当前没有待确认内容';
        list.innerHTML = `
          <div class="rounded-md border border-[#e6dfcc] bg-[#f0e9d8] p-4">
            <div class="text-sm font-semibold text-[#1f1d1b]">${msg}</div>
            <p class="mt-1 text-sm leading-6 text-[#8a8275]">没有新收件箱时，这里不会展示归档或写入按钮；需要处理的内容出现后再操作。</p>
          </div>
        `;
        return;
      }
      list.innerHTML = this.inboxRows.map((row, index) => `
        <article class="rounded-md border border-l-4 ${row.stale_archive ? 'border-[#e6dfcc] border-l-[#8a3527] bg-[#fbf8f1]' : 'border-[#e6dfcc] border-l-transparent bg-[#fbf8f1]'} p-2" data-index="${index}" data-path="${c.esc(row.path)}">
          <div class="grid cursor-pointer gap-2 md:grid-cols-[auto_minmax(0,1fr)_auto_auto] md:items-center" data-toggle-details>
            <label class="flex items-center gap-2 text-xs text-[#8a8275]">
              <input type="checkbox" data-select-inbox class="h-4 w-4 rounded border-[#c9bf9f]">
              <span>选择</span>
            </label>
            <div class="min-w-0">
              <div class="truncate text-sm font-semibold text-[#1f1d1b]">${c.esc(this.rowTitle(row))}</div>
              <div class="mt-0.5 truncate text-xs text-[#52733a]">${c.esc(this.routeRecommendationLabel(row))}</div>
              ${this.hardRuleHint(row) ? `<div class="mt-1 inline-flex rounded-full border border-[#d8cfba] bg-[#f4e6c4] px-2 py-0.5 text-[11px] text-[#8a6b1f]">${c.esc(this.hardRuleHint(row))}</div>` : ''}
            </div>
            <div class="text-xs text-[#8a8275] md:text-right">
              <div>停留 ${c.esc(row.age_days || 0)} 天</div>
              <div>${c.esc(this.humanAction(row.action))}</div>
            </div>
            <div class="flex shrink-0 flex-wrap gap-2">${this.actionButtons(row)}</div>
          </div>
          <details class="mt-2 text-xs text-[#8a8275]" data-row-details>
            <summary class="cursor-pointer">展开详情</summary>
            <div class="mt-2 grid gap-2 border-t border-[#e6dfcc] pt-2">
              <div class="font-mono text-[11px] text-[#8a8275]">${c.esc(row.path)}</div>
              ${this.hasMissingTitle(row) ? '<div class="text-xs text-[#8a6b1f]">缺少中文标题，建议回补 title_cn。</div>' : ''}
              <div class="font-mono text-xs text-[#52733a]">审批建议目标：${c.esc(this.routeTargetText(row) || '未命中具体目标')}</div>
              <div class="font-mono text-xs text-[#8a8275]">审批建议标签：${c.esc(this.routeFlagsText(row))}</div>
              <div class="font-mono text-xs text-[#8a8275]">硬性约束：${c.esc(
                row.route_hard_rule === undefined || row.route_hard_rule === null
                  ? '未标记'
                  : String(row.route_hard_rule) === 'true' || row.route_hard_rule === true
                    ? '是'
                    : '否'
              )}</div>
              <div class="rounded border border-[#e6dfcc] bg-[#f0e9d8] p-2 text-sm leading-6 text-[#4a443d]" style="white-space: pre-wrap">${c.esc(this.rowPreview(row))}</div>
              <div class="rounded border border-[#e6dfcc] bg-[#fbf8f1] p-2 font-mono text-[11px] leading-5 text-[#8a8275]" style="white-space: pre-wrap">原始摘要：${c.esc(row.raw_summary || row.summary || '')}</div>
            </div>
          </details>
          <div data-row-status></div>
        </article>
      `).join('');

      list.querySelectorAll('input[data-select-inbox]').forEach(checkbox => {
        checkbox.addEventListener('change', event => {
          const card = event.target.closest('article');
          const row = this.inboxRows[Number(card.dataset.index)];
          if (event.target.checked) this.selectedInbox.add(row.path);
          else this.selectedInbox.delete(row.path);
          this.updateBatchToolbar();
        }, { signal: this.abortController.signal });
      });

      list.querySelectorAll('button[data-action]').forEach(button => {
        button.addEventListener('click', async event => {
          const actionButton = event.target.closest('button[data-action]');
          const card = actionButton.closest('article');
          const row = this.inboxRows[Number(card.dataset.index)];
          const action = actionButton.dataset.action;
          if (actionButton.disabled) {
            return;
          }
          if (action === 'archive') {
            this.openActionConfirmModal(row, card, action);
            return;
          }
          if (action === 'promote_wiki') {
            this.openWikiModal(row, card);
            return;
          }
          this.enqueueInboxRowAction(row, card, action);
        }, { signal: this.abortController.signal });
      });
      list.querySelectorAll('[data-toggle-details]').forEach(row => {
        row.addEventListener('click', event => {
          if (event.target.closest('button, input, label, a')) return;
          const details = event.currentTarget.closest('article')?.querySelector('[data-row-details]');
          if (details) details.open = !details.open;
        }, { signal: this.abortController.signal });
      });
      this.updateBatchToolbar();
    },

    proposalSpecCapsuleHtml(proposal) {
      const c = window.tmDashboard;
      const capsule = proposal && proposal.spec_capsule && typeof proposal.spec_capsule === 'object' ? proposal.spec_capsule : null;
      if (!capsule) {
        return `<div class="mt-3 rounded-md border border-[#e0c889] bg-[#f4e6c4] px-3 py-2 text-sm text-[#8a6b1f]">Spec Capsule：未提供。高风险提案建议先补齐问题、证据、验收和回滚，再决定是否应用。</div>`;
      }
      const items = capsule.items || {};
      const rows = [
        ['问题', items.problem],
        ['证据', items.evidence],
        ['约束', items.constraints],
        ['方案', items.solution],
        ['验收', items.acceptance],
        ['回滚', items.rollback],
        ['虎哥确认', items.needs_tiger_confirmation]
      ].filter(([, value]) => value);
      const missing = Array.isArray(capsule.missing) && capsule.missing.length
        ? `<div class="mt-2 text-xs text-[#8a3527]">缺失：${c.esc(capsule.missing.join('、'))}</div>`
        : '';
      const status = String(capsule.status || 'unknown');
      const ok = status === 'complete';
      return `
        <details class="mt-3 rounded-md border ${ok ? 'border-[#a0b889] bg-[#eef4e8]' : 'border-[#e0c889] bg-[#f4e6c4]'} px-3 py-2 text-sm text-[#4a443d]" open>
          <summary class="cursor-pointer font-semibold text-[#1f1d1b]">Spec Capsule：${c.esc(status)}</summary>
          <div class="mt-2 space-y-1">
            ${rows.length ? rows.map(([label, value]) => `<div><span class="font-semibold">${c.esc(label)}：</span>${c.esc(value)}</div>`).join('') : '<div class="text-[#8a8275]">暂无卡片摘要。</div>'}
            ${missing}
          </div>
        </details>
      `;
    },

    renderProposals() {
      const c = window.tmDashboard;
      const list = document.getElementById('proposal-list');
      if (!list) return;
      const section = document.getElementById('proposal-section');
      if (section) section.classList.toggle('hidden', !this.proposals.length);
      if (!this.proposals.length) {
        list.innerHTML = '';
        return;
      }
      list.innerHTML = this.proposals.map((proposal, index) => `
        <article class="rounded-md border border-[#e6dfcc] bg-[#fbf8f1] p-3" data-index="${index}">
          <div class="font-mono text-sm font-semibold">提案编号 ${c.esc(proposal.id)}</div>
          <div class="mt-1 text-xs text-[#8a8275]">类型：${c.esc(proposal.type)} · 触发：${c.esc(proposal.trigger || '')}</div>
          <div class="mt-2 text-sm leading-6 text-[#4a443d]">影响范围：${c.esc(proposal.impact || '未说明')}</div>
          ${this.proposalSpecCapsuleHtml(proposal)}
          <pre class="mt-3 max-h-48 overflow-auto rounded bg-[#f0e9d8] p-3 text-xs text-[#4a443d]">${c.esc((proposal.diff || []).join('\n'))}</pre>
          <div class="mt-3 flex flex-wrap gap-2">
            <button data-proposal-action="apply" class="rounded-md bg-[#52733a] px-3 py-1.5 text-xs text-[#fbf8f1] hover:bg-[#405b2d]">应用</button>
            <button data-proposal-action="reject" class="rounded-md bg-[#4a443d] px-3 py-1.5 text-xs text-[#fbf8f1] hover:bg-[#1f1d1b]">驳回</button>
          </div>
          <div data-row-status></div>
        </article>
      `).join('');

      list.querySelectorAll('button[data-proposal-action]').forEach(button => {
        button.addEventListener('click', async event => {
          const card = event.target.closest('article');
          const proposal = this.proposals[Number(card.dataset.index)];
          const action = event.target.dataset.proposalAction;
          const originalText = event.target.textContent;
          if (action === 'apply') {
            event.target.textContent = (window.tmI18n && window.tmI18n.get('daily.processing.applying', '正在应用...')) || '正在应用...';
          } else {
            event.target.textContent = (window.tmI18n && window.tmI18n.get('daily.processing.rejecting', '正在驳回...')) || '正在驳回...';
          }
          card.querySelectorAll('button').forEach(btn => btn.disabled = true);
          try {
            if (action === 'apply') {
              await this.postJson('/api/proposal/apply', {date: this.digest.date, proposal_id: proposal.id});
            } else {
              const reason = prompt('驳回理由') || '审阅界面手动驳回';
              await this.postJson('/api/proposal/reject', {date: this.digest.date, proposal_id: proposal.id, reason});
            }
            card.classList.add('opacity-50', 'line-through');
            const statusNode = card.querySelector('[data-row-status]');
            if (statusNode) {
              statusNode.textContent = `成功：AI 修改建议已${action === 'apply' ? '应用' : '驳回'}`;
              statusNode.className = `mt-2 text-xs text-[#52733a]`;
            }
            this.toast(`AI 修改建议${action === 'apply' ? '应用' : '驳回'}完成`);
          } catch (error) {
            if (error.name === 'AbortError') return;
            console.error(error);
            event.target.textContent = originalText;
            card.querySelectorAll('button').forEach(btn => btn.disabled = false);
            card.classList.remove('opacity-50', 'line-through');
            const statusNode = card.querySelector('[data-row-status]');
            if (statusNode) {
              statusNode.textContent = `出错了：${error.message}`;
              statusNode.className = `mt-2 text-xs text-[#8a3527]`;
            }
            this.toast(`出错了：${error.message}`, false);
          }
        }, { signal: this.abortController.signal });
      });
    },

    bindEvents() {
      const batchArchive = document.getElementById('batch-archive');
      if (batchArchive) {
        batchArchive.addEventListener('click', async event => {
          const originalText = event.target.textContent;
          event.target.disabled = true;
          event.target.setAttribute('aria-busy', 'true');
          const queued = this.enqueueWriteJob({
            jobKey: `batch-archive-all:${this.digest.date || ''}`,
            timeoutMs: this.actionTimeoutMs('archive', Math.max(1, Number(this.counts && this.counts.stale) || 10)),
            label: '一键归档全部',
            detail: '归档超过 14 天的内容',
            runningDetail: '正在归档全部...',
            doneDetail: '归档完成',
            run: () => this.postJson('/api/batch/archive-stale', {date: this.digest.date}),
            onSuccess: async data => {
              this.toast(`批量归档完成：${(data.archived || []).length} 条 ${data.commit_sha || ''}`);
              await this.fetchDigest();
              event.target.textContent = originalText;
              event.target.disabled = false;
              event.target.removeAttribute('aria-busy');
            },
            onError: error => {
              event.target.textContent = originalText;
              event.target.disabled = false;
              event.target.removeAttribute('aria-busy');
              this.toast(`出错了：${error.message}`, false);
            }
          });
          if (!queued) {
            event.target.textContent = originalText;
            event.target.disabled = false;
            event.target.removeAttribute('aria-busy');
          }
        }, { signal: this.abortController.signal });
      }

      const queueClear = document.getElementById('action-queue-clear');
      if (queueClear) {
        queueClear.addEventListener('click', event => {
          event.stopPropagation();
          this.clearSettledQueueJobs();
        }, { signal: this.abortController.signal });
      }

      const batchArchiveSelected = document.getElementById('batch-archive-selected');
      if (batchArchiveSelected) {
        batchArchiveSelected.addEventListener('click', () => this.runBatchAction('archive'), { signal: this.abortController.signal });
      }

      const batchMem0Selected = document.getElementById('batch-mem0-selected');
      if (batchMem0Selected) {
        batchMem0Selected.addEventListener('click', () => this.runBatchAction('promote_mem0'), { signal: this.abortController.signal });
      }

      const batchWikiSelected = document.getElementById('batch-wiki-selected');
      if (batchWikiSelected) {
        batchWikiSelected.addEventListener('click', () => this.runBatchAction('promote_wiki'), { signal: this.abortController.signal });
      }

      const batchClearSelected = document.getElementById('batch-clear-selected');
      if (batchClearSelected) {
        batchClearSelected.addEventListener('click', () => {
          if (this.selectedInbox) this.selectedInbox.clear();
          document.querySelectorAll('input[data-select-inbox]').forEach(node => node.checked = false);
          this.updateBatchToolbar();
        }, { signal: this.abortController.signal });
      }

      const wikiModalClose = document.getElementById('wiki-modal-close');
      if (wikiModalClose) {
        wikiModalClose.addEventListener('click', () => this.closeWikiModal(), { signal: this.abortController.signal });
      }

      const wikiModalCancel = document.getElementById('wiki-modal-cancel');
      if (wikiModalCancel) {
        wikiModalCancel.addEventListener('click', () => this.closeWikiModal(), { signal: this.abortController.signal });
      }

      const wikiModalConfirm = document.getElementById('wiki-modal-confirm');
      if (wikiModalConfirm) {
        wikiModalConfirm.addEventListener('click', async event => {
          if (!this.wikiModalState) return;
          const state = this.wikiModalState;
          const {row, card, target} = state;
          event.target.disabled = true;
          this.closeWikiModal();
          if (state.kind === 'action-confirm') {
            this.enqueueInboxRowAction(row, card, state.action);
            event.target.disabled = false;
            return;
          }
          if (state.kind === 'wiki-ledger' || state.kind === 'wiki-ledger-batch') {
            this.enqueueWikiProposalApproval({
              rows: state.rows || [],
              paths: state.paths || [],
              cards: state.cards || [],
              target: state.kind === 'wiki-ledger' ? target : null,
              batch: state.kind === 'wiki-ledger-batch'
            });
            event.target.disabled = false;
            return;
          }
          this.setCardBusy(card, true, `已加入队列：写入 Wiki ${target.partition}/${target.slug}`);
          const queued = this.enqueueWriteJob({
            jobKey: `wiki:${row.path}:${target.partition}:${target.slug}:${this.digest.date || ''}`,
            timeoutMs: this.actionTimeoutMs('promote_wiki', 1),
            label: '写入 Wiki',
            detail: `${target.partition}/${target.slug}`,
            runningDetail: `正在写入 Wiki：${target.partition}/${target.slug}...`,
            doneDetail: '写入 Wiki 完成',
            run: () => this.postJson('/api/inbox/action', {
              path: row.path,
              action: 'promote_wiki',
              partition: target.partition,
              slug: target.slug
            }),
            onSuccess: async data => {
              this.setCardBusy(card, true, `写入成功，正在刷新列表 ${data.commit_sha || ''}`);
              if (this.selectedInbox) this.selectedInbox.delete(row.path);
              const checkbox = card.querySelector('input[type="checkbox"]');
              if (checkbox) checkbox.checked = false;
              this.updateBatchToolbar();
              await this.refreshDigestThenCompleteCards([card], `协助成功：写入 Wiki ${data.commit_sha || ''}`);
              this.toast(`成功：写入 Wiki ${data.commit_sha || ''}`);
              event.target.disabled = false;
            },
            onError: async error => {
              if (await this.markCompletedIfPathGoneAfterError(error, row.path, card, '写入 Wiki')) {
                event.target.disabled = false;
                return true;
              }
              this.setCardBusy(card, false);
              card.classList.remove('opacity-50', 'line-through');
              this.setCardStatus(card, `出错了：${error.message}`, false);
              this.toast(`出错了：${error.message}`, false);
              event.target.disabled = false;
              return false;
            }
          });
          if (!queued) {
            this.setCardBusy(card, false);
            event.target.disabled = false;
          }
        }, { signal: this.abortController.signal });
      }
    },

    applyDigest(nextDigest) {
      this.digest = nextDigest || {};
      this.counts = this.digest.counts || {};
      this.inboxRows = this.digest.inbox_rows || [];
      this.wikiProposalLedger = this.digest.wiki_proposal_ledger || [];
      this.proposals = this.digest.proposals || [];
      if (this.selectedInbox) this.selectedInbox.clear();

      this.renderHeader();
      this.renderDecision();
      this.renderCronIntake();
      this.renderWikiProposalLedger();
      this.renderInbox();
      this.renderProposals();

      const metrics = document.getElementById('metrics');
      if (metrics) {
        metrics.textContent = this.digest.metrics || '';
        const section = metrics.closest('section');
        if (section) section.classList.toggle('hidden', !String(this.digest.metrics || '').trim());
      }

      const appendix = document.getElementById('appendix');
      if (appendix) {
        appendix.textContent = this.digest.appendix || '';
        const section = appendix.closest('section');
        if (section) section.classList.toggle('hidden', !String(this.digest.appendix || '').trim());
      }

      if (window.lucide) window.lucide.createIcons();
    },

    renderDigestError(message) {
      const c = window.tmDashboard;
      const decisionGrid = document.getElementById('decision-grid');
      if (decisionGrid) {
        decisionGrid.innerHTML = `<div class="md:col-span-4 rounded-md border border-[#d49a91] bg-[#f0d6d2] p-3 text-sm text-[#8a3527]">${c.esc(message)}</div>`;
      }
      const inboxList = document.getElementById('inbox-list');
      if (inboxList) {
        inboxList.innerHTML = `<div class="rounded-md border border-[#d49a91] bg-[#f0d6d2] p-3 text-sm text-[#8a3527]">${c.esc(message)}</div>`;
      }
      const proposalList = document.getElementById('proposal-list');
      if (proposalList) {
        proposalList.innerHTML = '<p class="text-sm text-[#8a8275]">数据加载失败，暂不显示 AI 修改建议。</p>';
      }
      const ledgerSection = document.getElementById('wiki-proposal-ledger-section');
      if (ledgerSection) ledgerSection.classList.add('hidden');
      const metrics = document.getElementById('metrics');
      if (metrics) metrics.textContent = message;
    },

    async fetchDigest(options = {}) {
      if (this.fetchDigestAbortController) this.fetchDigestAbortController.abort();
      const requestId = ++this.fetchDigestRequestId;
      this.fetchDigestAbortController = new AbortController();
      try {
        const date = this.digest.date || '';
        if (!date) return;
        const response = await fetch(`/api/digest/${encodeURIComponent(this.digest.date || '')}`, {
          signal: this.fetchDigestAbortController.signal
        });
        const data = await response.json();
        if (requestId !== this.fetchDigestRequestId) return;
        if (!data.ok) throw new Error(data.error || '今日数据加载失败');
        this.applyDigest(data.digest);
        this.fetchCronIntake({quiet: true});
      } catch (error) {
        if (error.name === 'AbortError') return;
        console.error(error);
        if (options.quiet) {
          this.toast(`数据刷新失败：${error.message}`, false);
          return;
        }
        this.renderDigestError(`数据暂时没取到，请稍后重试：${error.message}`);
      }
    },

    async fetchCronIntake(options = {}) {
      try {
        const date = this.digest && this.digest.date ? this.digest.date : '';
        if (!date) return;
        const response = await fetch(`/api/cron/intake/${encodeURIComponent(date)}`, {
          signal: this.abortController ? this.abortController.signal : null
        });
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || 'cron 承接卡加载失败');
        this.cronIntake = data.intake || {};
        this.renderCronIntake();
      } catch (error) {
        if (error.name === 'AbortError') return;
        if (!options.quiet) this.toast(`cron 承接卡加载失败：${error.message}`, false);
        this.cronIntake = {
          status: 'error',
          summary: `cron 承接卡加载失败：${error.message}`,
          reports: [],
          warnings: [error.message],
          action_items: []
        };
        this.renderCronIntake();
      }
    }
  };


  window.tmPages.settings = {
    root: null,
    data: null,
    prefs: null,
    abortController: null,
    toastTimeout: null,

    depthOptions: [
      ['A', 'A 极简', '只给中文', 'OAuth（账号授权）'],
      ['B', 'B 简短', '+ 类比', 'OAuth（账号授权，类似身份证）'],
      ['C', 'C 工程', '+ 上下文', 'OAuth（账号授权，tigermemory 用于连接器）'],
      ['D', 'D 全套', '三段都给', '账号授权 + 类比 + 系统上下文'],
    ],
    exemptionOptions: [
      ['git', 'git 操作'],
      ['ai', 'AI 通用词'],
      ['tigermemory', 'tigermemory 内核词'],
      ['agent', 'agent 名字'],
      ['data-format', '数据格式'],
    ],
    agentOptions: ['cascade', 'claude-code', 'codex', 'chatgpt', 'kimi', 'hermes', 'openclaw', 'deerflow'],

    init(root, data) {
      // Prevent duplicate initializations and clear any pre-existing timers/listeners
      this.destroy();

      this.root = root;
      this.data = data;
      this.prefs = JSON.parse(JSON.stringify(data.preferences || {}));
      this.abortController = new AbortController();

      // Render initial UI elements
      this.renderAll();

      // Hook reset button
      const resetBtn = document.getElementById('reset-button');
      if (resetBtn) {
        resetBtn.addEventListener('click', () => {
          if (!confirm('确定要将所有设置恢复为默认值吗？此操作将覆盖您当前的临时修改，需要点击“保存设置”才会写入数据库。')) {
            return;
          }
          this.prefs = JSON.parse(JSON.stringify(this.data.defaults || {}));
          this.setStatus('已恢复默认值，尚未保存。');
          this.renderAll();
        }, { signal: this.abortController.signal });
      }

      // Hook progressive-toggle strictly once at init (no inline onclick wrapper)
      const progressiveToggle = document.getElementById('progressive-toggle');
      if (progressiveToggle) {
        progressiveToggle.addEventListener('click', () => {
          this.prefs.progressive_term_frequency = !this.prefs.progressive_term_frequency;
          this.renderToggle();
        }, { signal: this.abortController.signal });
      }

      // Hook form submit
      const form = document.getElementById('settings-form');
      if (form) {
        form.addEventListener('submit', async event => {
          event.preventDefault();
          this.syncTextFields();
          const submit = event.submitter;
          if (submit) submit.disabled = true;
          this.setStatus('正在保存设置并同步到云端...');
          try {
            const response = await fetch('/api/settings/preferences', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                preferences: this.prefs,
                propose_wiki: document.getElementById('propose-wiki').checked
              }),
              signal: this.abortController.signal
            });
            const resData = await response.json();
            if (!resData.ok) throw new Error(resData.error || '保存失败');
            this.prefs = resData.preferences || this.prefs;
            const proposal = resData.wiki_proposal || {};
            const suffix = proposal.commit_sha ? `，已同步到云端 ${proposal.commit_sha}` : '，云端同步已提交或未启用';
            this.setStatus(`已保存到本地数据库${suffix}`);
            this.toast(`已保存到本地数据库${suffix}`);
            this.renderAll();
          } catch (error) {
            if (error.name === 'AbortError') return;
            console.error(error);
            this.setStatus(`出错了：${error.message}`, false);
            this.toast(`出错了：${error.message}`, false);
          } finally {
            if (submit) submit.disabled = false;
          }
        }, { signal: this.abortController.signal });
      }

      // Fetch dynamic state if page is currently loading shell
      if (this.data.loading) {
        this.fetchSettings();
      }
    },

    destroy() {
      // Abort all active fetch calls and remove all event listeners dynamically via signal
      if (this.abortController) {
        this.abortController.abort();
        this.abortController = null;
      }
      if (this.toastTimeout) {
        clearTimeout(this.toastTimeout);
        this.toastTimeout = null;
      }
    },

    toast(message, ok = true) {
      const node = document.getElementById('toast');
      if (!node) return;
      node.textContent = message;
      node.className = `fixed bottom-6 left-1/2 z-50 max-w-[min(92vw,36rem)] -translate-x-1/2 rounded-xl border px-4 py-3 text-sm shadow-lg ${ok ? 'border-[#a0b889] bg-[#dde8ce] text-[#52733a]' : 'border-[#d49a91] bg-[#f0d6d2] text-[#8a3527]'}`;
      node.classList.remove('hidden');
      if (this.toastTimeout) clearTimeout(this.toastTimeout);
      this.toastTimeout = setTimeout(() => node.classList.add('hidden'), 4500);
    },

    setStatus(message, ok = true) {
      const node = document.getElementById('save-status');
      if (!node) return;
      node.textContent = message || '';
      node.className = `mt-3 text-sm ${ok ? 'text-[#52733a]' : 'text-[#8a3527]'}`;
    },

    activeList(key) {
      return Array.isArray(this.prefs[key]) ? this.prefs[key] : [];
    },

    renderDepth() {
      const c = window.tmDashboard;
      const depthGrid = document.getElementById('depth-grid');
      if (!depthGrid) return;
      depthGrid.innerHTML = this.depthOptions.map(([value, title, subtitle, example]) => `
        <button type="button" data-depth="${value}" class="choice-card ${this.prefs.communication_depth === value ? 'active' : ''}">
          <div class="mb-2 text-lg font-extrabold">${this.prefs.communication_depth === value ? '◉' : '○'} ${c.esc(title)}</div>
          <div class="text-sm font-semibold">${c.esc(subtitle)}</div>
          <div class="mt-4 text-xs leading-5">${c.esc(example)}</div>
        </button>
      `).join('');

      document.querySelectorAll('[data-depth]').forEach(button => {
        button.addEventListener('click', () => {
          this.prefs.communication_depth = button.dataset.depth;
          this.renderAll();
        }, { signal: this.abortController.signal });
      });
    },

    renderChips(containerId, options, key) {
      const c = window.tmDashboard;
      const container = document.getElementById(containerId);
      if (!container) return;
      const active = new Set(this.activeList(key));
      container.innerHTML = options.map(item => {
        const value = Array.isArray(item) ? item[0] : item;
        const label = Array.isArray(item) ? item[1] : item;
        return `<button type="button" data-chip-key="${key}" data-chip-value="${c.esc(value)}" class="chip ${active.has(value) ? 'active' : ''}">${c.esc(label)}</button>`;
      }).join('');

      document.querySelectorAll(`[data-chip-key="${key}"]`).forEach(button => {
        button.addEventListener('click', () => {
          const values = new Set(this.activeList(key));
          if (values.has(button.dataset.chipValue)) {
            values.delete(button.dataset.chipValue);
          } else {
            values.add(button.dataset.chipValue);
          }
          this.prefs[key] = Array.from(values);
          this.renderAll();
        }, { signal: this.abortController.signal });
      });
    },

    renderToggle() {
      const toggle = document.getElementById('progressive-toggle');
      if (!toggle) return;
      toggle.classList.toggle('active', Boolean(this.prefs.progressive_term_frequency));
    },

    renderCustomTerms() {
      const container = document.getElementById('custom-terms-container');
      if (!container) return;

      const terms = this.activeList('custom_terms');
      let html = terms.map((term, index) => `
        <span class="tag-chip">
          <span>${window.tmDashboard.esc(term)}</span>
          <span class="tag-chip-remove" data-remove-term-index="${index}">&times;</span>
        </span>
      `).join('');

      html += `
        <input type="text" id="custom-term-input" class="tag-input-field" placeholder="输入词汇并回车..." />
      `;

      container.innerHTML = html;

      // Bind remove buttons
      container.querySelectorAll('[data-remove-term-index]').forEach(btn => {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          const idx = parseInt(btn.dataset.removeTermIndex);
          const list = [...this.activeList('custom_terms')];
          list.splice(idx, 1);
          this.prefs.custom_terms = list;
          this.renderAll();
        }, { signal: this.abortController.signal });
      });

      // Bind input enter / blur
      const input = document.getElementById('custom-term-input');
      if (input) {
        const addTerm = () => {
          const val = input.value.trim();
          if (val) {
            const list = [...this.activeList('custom_terms')];
            if (!list.includes(val)) {
              list.push(val);
              this.prefs.custom_terms = list;
              this.renderAll();
            } else {
              input.value = '';
              this.toast(`自定义词汇 “${val}” 已存在`, false);
            }
          }
        };

        input.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            addTerm();
          }
        }, { signal: this.abortController.signal });

        input.addEventListener('blur', () => {
          addTerm();
        }, { signal: this.abortController.signal });
      }
    },

    renderTimeoutSliders() {
      const container = document.getElementById('timeout-budget-sliders');
      if (!container) return;

      const budgetStr = this.prefs.command_timeout_budget || '10 / 30 / 60 / 120 秒';
      const parts = budgetStr.replace(/[^\d/]/g, '').split('/');
      const valLocal = parseInt(parts[0]) || 10;
      const valWsl = parseInt(parts[1]) || 30;
      const valNet = parseInt(parts[2]) || 60;
      const valLlm = parseInt(parts[3]) || 120;

      const slidersConfig = [
        ['local', '本地查询', valLocal, 5, 60],
        ['wsl', 'WSL 写入', valWsl, 10, 180],
        ['net', '网络调用', valNet, 10, 300],
        ['llm', 'LLM 调用', valLlm, 10, 600]
      ];

      container.innerHTML = slidersConfig.map(([id, label, val, min, max]) => `
        <div class="slider-row">
          <div>
            <div class="slider-label mb-1">${label}</div>
            <input type="range" id="timeout-slider-${id}" min="${min}" max="${max}" value="${val}" class="w-full" />
          </div>
          <span class="slider-value" id="timeout-value-${id}">${val} 秒</span>
        </div>
      `).join('');

      // Bind events for sliders
      const updateBudgetString = () => {
        const l = document.getElementById('timeout-slider-local').value;
        const w = document.getElementById('timeout-slider-wsl').value;
        const n = document.getElementById('timeout-slider-net').value;
        const lm = document.getElementById('timeout-slider-llm').value;
        this.prefs.command_timeout_budget = `${l} / ${w} / ${n} / ${lm} 秒`;
        
        const settingsJson = document.getElementById('settings-json');
        if (settingsJson) {
          settingsJson.textContent = JSON.stringify({
            path: this.data.path,
            preferences: this.prefs,
            defaults: this.data.defaults
          }, null, 2);
        }
      };

      slidersConfig.forEach(([id]) => {
        const slider = document.getElementById(`timeout-slider-${id}`);
        const valSpan = document.getElementById(`timeout-value-${id}`);
        if (slider && valSpan) {
          slider.addEventListener('input', () => {
            valSpan.textContent = `${slider.value} 秒`;
            updateBudgetString();
          }, { signal: this.abortController.signal });
        }
      });
    },

    renderTextFields() {
      const modelWorkflow = document.getElementById('model-workflow');
      if (modelWorkflow) modelWorkflow.value = this.prefs.model_workflow || '';

      const settingsJson = document.getElementById('settings-json');
      if (settingsJson) {
        settingsJson.textContent = JSON.stringify({
          path: this.data.path,
          preferences: this.prefs,
          defaults: this.data.defaults
        }, null, 2);
      }
    },

    syncTextFields() {
      const modelWorkflow = document.getElementById('model-workflow');
      if (modelWorkflow) {
        this.prefs.model_workflow = modelWorkflow.value.trim();
      }
    },

    renderAll() {
      this.renderDepth();
      this.renderChips('exemption-chips', this.exemptionOptions, 'exemptions');
      this.renderChips('agent-chips', this.agentOptions, 'agents');
      this.renderToggle();
      this.renderCustomTerms();
      this.renderTimeoutSliders();
      this.renderTextFields();
      if (window.lucide) window.lucide.createIcons();
    },

    async fetchSettings() {
      try {
        const response = await fetch('/api/settings/preferences', { signal: this.abortController.signal });
        const resData = await response.json();
        if (!resData.ok) throw new Error(resData.error || '偏好数据加载失败');
        this.data.path = resData.path || this.data.path;
        this.data.preferences = resData.preferences || this.data.preferences;
        this.data.defaults = resData.defaults || this.data.defaults;
        this.prefs = JSON.parse(JSON.stringify(resData.preferences || this.prefs));
        this.renderAll();
      } catch (error) {
        if (error.name === 'AbortError') return;
        console.error(error);
        this.setStatus(`出错了：${error.message}`, false);
      }
    }
  };

  window.tmPages.agentTools = {
    root: null,
    data: null,
    abortController: null,
    animateTimers: [],

    init(root, data) {
      this.destroy();
      this.root = root;
      this.data = data || {};
      this.abortController = new AbortController();
      this.animateTimers = [];

      this.bindEvents();
      this.checkAgentStatus();
      this.fetchRecentActivity();
      this.fetchInvestmentTradingNode();
      if (window.lucide) window.lucide.createIcons();
    },

    destroy() {
      if (this.abortController) {
         this.abortController.abort();
         this.abortController = null;
      }
      if (this.animateTimers) {
        this.animateTimers.forEach(timer => clearInterval(timer));
        this.animateTimers = [];
      }
    },

    bindEvents() {
      const doctorBtn = document.getElementById('doctor-btn');
      if (doctorBtn) {
        doctorBtn.addEventListener('click', () => this.runDoctor(), { signal: this.abortController.signal });
      }

      const evalBtn = document.getElementById('eval-btn');
      if (evalBtn) {
        evalBtn.addEventListener('click', () => this.runEval(), { signal: this.abortController.signal });
      }
    },

    async checkAgentStatus() {
      const signal = this.abortController ? this.abortController.signal : null;
      try {
        const res = await fetch('/api/agent/status', { signal });
        const data = await res.json();

        if (data.ok) {
          const t = (key, fallback, vars) => window.tmI18n ? window.tmI18n.t(key, fallback, vars) : fallback;
          const osTag = document.getElementById('os-tag');
          if (osTag) osTag.textContent = `${window.tmDashboard.esc(data.os)} OS`;

          const cur = data.cursor;
          const cursorPath = document.getElementById('cursor-path');
          const cursorBadge = document.getElementById('cursor-badge');
          if (cur.exists) {
            if (cursorPath) cursorPath.textContent = t('agent.path_detected', `检测路径：${cur.path}`, {path: cur.path});
            if (cursorBadge) {
              cursorBadge.innerHTML = window.tmDashboard.badge(
                cur.connected ? 'ok' : 'warn',
                t(cur.connected ? 'status.ok_custom' : 'status.warn_custom', cur.connected ? '已连接' : '未连接')
              );
            }
          } else {
            if (cursorPath) cursorPath.innerHTML = `<span class="text-[#8a3527]">${window.tmDashboard.esc(t('agent.cursor_not_found', '未检测到 Cursor 的配置文件 (mcp.json) 路径。'))}</span>`;
            if (cursorBadge) cursorBadge.innerHTML = window.tmDashboard.statusBadge('warn', t('status.not_found_custom', '未发现'));
          }

          const cld = data.claude;
          const claudePath = document.getElementById('claude-path');
          const claudeBadge = document.getElementById('claude-badge');
          if (cld.exists) {
            if (claudePath) claudePath.textContent = t('agent.path_detected', `检测路径：${cld.path}`, {path: cld.path});
            if (claudeBadge) {
              claudeBadge.innerHTML = window.tmDashboard.badge(
                cld.connected ? 'ok' : 'warn',
                t(cld.connected ? 'status.ok_custom' : 'status.warn_custom', cld.connected ? '已连接' : '未连接')
              );
            }
          } else {
            if (claudePath) claudePath.innerHTML = `<span class="text-[#8a3527]">${window.tmDashboard.esc(t('agent.claude_not_found', '未检测到 Claude Desktop 的连接文件路径。'))}</span>`;
            if (claudeBadge) claudeBadge.innerHTML = window.tmDashboard.statusBadge('warn', t('status.not_found_custom', '未发现'));
          }
        }
      } catch (err) {
        if (err.name === 'AbortError') return;
        console.error(err);
      }
    },

    async fetchRecentActivity() {
      const signal = this.abortController ? this.abortController.signal : null;
      const status = document.getElementById('recent-activity-status');
      const list = document.getElementById('recent-activity-list');
      try {
        const res = await fetch('/api/agent/recent-activity', { signal });
        const data = await res.json();
        if (status) status.textContent = data.ok ? `共 ${data.items.length} 条` : '加载失败';
        if (!list) return;
        if (!data.ok || !data.items.length) {
          list.innerHTML = '<div class="rounded-xl border border-[#e6dfcc] bg-[#fcfaf2] p-3 text-sm text-[#8a8275]">暂无最近活跃记录。</div>';
          return;
        }
        list.innerHTML = data.items.map(item => {
          const typeLabel = item.type === 'commit' ? '保存' : (item.type === 'handoff' ? '任务交接' : '记忆同步');
          return `
            <article class="rounded-xl border border-[#e6dfcc] bg-[#fcfaf2] p-3">
              <div class="flex items-center justify-between gap-2">
                <span class="text-sm font-semibold text-[#1f1d1b]">${window.tmDashboard.esc(item.agent || 'agent')}</span>
                <span class="rounded-full bg-[#f0e9d8] px-2 py-0.5 text-xs text-[#8a8275]">${window.tmDashboard.esc(typeLabel)}</span>
              </div>
              <div class="mt-2 truncate text-sm text-[#4a443d]">${window.tmDashboard.esc(item.title || '')}</div>
              <div class="mt-1 font-mono text-[11px] text-[#8a8275]">${window.tmDashboard.esc(item.created_at || item.sha || '')}</div>
            </article>
          `;
        }).join('');
        if (window.lucide) window.lucide.createIcons();
      } catch (err) {
        if (err.name === 'AbortError') return;
        if (status) status.textContent = '加载失败';
        if (list) list.innerHTML = '<div class="rounded-xl border border-[#d49a91] bg-[#f0d6d2] p-3 text-sm text-[#8a3527]">最近活跃加载失败。</div>';
      }
    },

    async fetchInvestmentTradingNode() {
      const card = document.getElementById('investment-trading-node-card');
      if (!card) return;
      card.innerHTML = '<div class="text-[#8a8275]">此公开仪表盘包不包含私有交易面板。</div>';
    },

    async runDoctor() {
      const signal = this.abortController ? this.abortController.signal : null;
      const btn = document.getElementById('doctor-btn');
      const resultsDiv = document.getElementById('doctor-results');
      const light = document.getElementById('doctor-light');
      const t = (key, fallback, vars) => window.tmI18n ? window.tmI18n.t(key, fallback, vars) : fallback;

      if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<i data-lucide="refresh-cw" class="h-4 w-4 animate-spin"></i> ${window.tmDashboard.esc(t('agent.doctor_scanning', '正在深度扫描工作区...'))}`;
      }
      if (window.lucide) window.lucide.createIcons();

      if (light) light.className = 'h-3 w-3 rounded-full glow-dot bg-amber-400';

      try {
        const res = await fetch('/api/agent/doctor?skip_l2=true', { signal });
        const data = await res.json();

        if (btn) {
          btn.disabled = false;
          btn.innerHTML = `<i data-lucide="play" class="h-4 w-4"></i> ${window.tmDashboard.esc(t('agent.rerun_doctor', '重新运行健康诊断'))}`;
        }
        if (window.lucide) window.lucide.createIcons();

        if (data.ok && data.report) {
          const rep = data.report;

          if (light) {
            light.className = `h-3 w-3 rounded-full glow-dot ${rep.status === 'ok' ? 'ok' : (rep.status === 'fail' ? 'fail' : 'warn')}`;
          }

          const adviceEl = document.getElementById('doctor-advice');
          if (adviceEl) {
            adviceEl.innerHTML = `${t('agent.doctor_advice_title', '💡 <strong>专家运维调优建议：</strong>')}${window.tmDashboard.esc(rep.recommended_action)}`;
          }

          if (resultsDiv) {
            resultsDiv.innerHTML = (rep.checks || []).map(check => {
              let detail = check.reason || check.error || '';
              if (check.name === 'worktree' && check.ok) {
                detail = t('agent.doctor_branch_info', `当前分支：{branch_html} · ahead={ahead} · behind={behind}`, {
                  branch_html: `<code>${window.tmDashboard.esc(check.branch)}</code>`,
                  ahead: window.tmDashboard.esc(check.ahead),
                  behind: window.tmDashboard.esc(check.behind)
                });
              } else if (check.name === 'lessons' && check.ok) {
                detail = t('agent.doctor_lessons_hit', `已为您拦截防御，命中 lessons 记录 {count} 个。`, {
                  count: window.tmDashboard.esc(check.hit_count)
                });
              } else if (check.name === 'public_ask_llm') {
                const state = check.llm_configured ? '在线问答已配置' : '在线问答未配置';
                const model = check.routine_model || '未记录模型';
                detail = `${state} · ${model}`;
              }
              return `
                <div class="flex items-center justify-between p-3 rounded-xl border border-[#e6dfcc] bg-[#fcfaf2]">
                  <div class="min-w-0">
                    <div class="text-sm font-semibold text-[#1f1d1b]">${window.tmDashboard.esc(check.name)}</div>
                    <div class="text-xs text-[#8a8275] truncate max-w-sm mt-0.5">${detail}</div>
                  </div>
                  ${window.tmDashboard.badge(check.status)}
                </div>
              `;
            }).join('');
            resultsDiv.classList.remove('hidden');
          }
        }
      } catch (err) {
        if (err.name === 'AbortError') return;
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = `<i data-lucide="alert-circle" class="h-4 w-4"></i> ${window.tmDashboard.esc(t('agent.doctor_fail', '诊断失败'))}`;
        }
        if (light) light.className = 'h-3 w-3 rounded-full glow-dot fail';
        console.error(err);
      }
    },

    async runEval() {
      const signal = this.abortController ? this.abortController.signal : null;
      const btn = document.getElementById('eval-btn');
      const detailsBox = document.getElementById('eval-details-box');
      const summaryBar = document.getElementById('eval-summary-bar');
      const suggestions = document.getElementById('eval-suggestions');
      const t = (key, fallback, vars) => window.tmI18n ? window.tmI18n.t(key, fallback, vars) : fallback;

      if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<i data-lucide="refresh-cw" class="h-4 w-4 animate-spin"></i> ${window.tmDashboard.esc(t('agent.eval_running', '正在检索评测中...'))}`;
      }
      if (window.lucide) window.lucide.createIcons();

      try {
        const res = await fetch('/api/agent/eval?skip_mem0=true', { signal });
        const data = await res.json();

        if (btn) {
          btn.disabled = false;
          btn.innerHTML = `<i data-lucide="refresh-cw" class="h-4 w-4"></i> ${window.tmDashboard.esc(t('agent.rerun_eval', '重新运行搜索测试'))}`;
        }
        if (window.lucide) window.lucide.createIcons();

        if (data.ok) {
          if (summaryBar) summaryBar.classList.remove('hidden');
          if (detailsBox) detailsBox.classList.remove('hidden');
          if (suggestions) suggestions.classList.remove('hidden');

          const w1 = Math.round(data.wiki.recall_1 * 100);
          const w3 = Math.round(data.wiki.recall_3 * 100);

          const wikiRecall1Circle = document.getElementById('wiki-recall-1-circle');
          if (wikiRecall1Circle) wikiRecall1Circle.setAttribute('stroke-dasharray', `${w1}, 100`);
          this.animateValue('wiki-recall-1-text', 0, w1, 1000, true);

          const wikiRecall3Circle = document.getElementById('wiki-recall-3-circle');
          if (wikiRecall3Circle) wikiRecall3Circle.setAttribute('stroke-dasharray', `${w3}, 100`);
          this.animateValue('wiki-recall-3-text', 0, w3, 1000, true);

          const wikiLatency = document.getElementById('wiki-latency');
          if (wikiLatency) wikiLatency.textContent = `${Math.round(data.wiki.avg_latency_ms)} ms`;

          const m = data.mem0;
          const mem0AccuracyVal = document.getElementById('mem0-accuracy-val');
          const mem0StatusText = document.getElementById('mem0-status-text');
          if (m.active) {
            if (mem0AccuracyVal) mem0AccuracyVal.textContent = `${Math.round(m.accuracy * 100)}%`;
            if (mem0StatusText) mem0StatusText.textContent = t('agent.eval_mem0_latency_info', `平均时延 {latency} ms`, {latency: Math.round(m.avg_latency_ms)});
          } else {
            if (mem0AccuracyVal) mem0AccuracyVal.textContent = 'OFFLINE';
            if (mem0StatusText) mem0StatusText.innerHTML = window.tmDashboard.statusBadge('warn', t('status.offline_custom', '离线降级'));
          }

          const tableBody = document.getElementById('eval-table-body');
          if (tableBody) {
            tableBody.innerHTML = (data.results || []).map(row => {
              const r_str = row.wiki_rank > 0 ? t('agent.eval_rank', `Rank ${row.wiki_rank}`, {rank: row.wiki_rank}) : t('status.not_found_custom', '未找到');
              const m_str = row.mem0_status === 'SUCCESS' ? t('status.ok', '正常') : (row.mem0_status === 'FAILED' ? t('status.fail', '故障') : t('status.offline_custom', '离线'));
              return `
                <tr class="hover:bg-[#f5eecb]">
                  <td class="p-3 font-semibold">${window.tmDashboard.esc(row.id)}</td>
                  <td class="p-3">${window.tmDashboard.esc(row.description)}</td>
                  <td class="p-3"><span class="${row.wiki_rank === 1 ? 'text-[#52733a] font-semibold' : (row.wiki_rank > 0 ? 'text-[#c8a560]' : 'text-[#8a3527]')}">${window.tmDashboard.esc(r_str)}</span></td>
                  <td class="p-3">${window.tmDashboard.esc(row.wiki_latency_ms)} ms</td>
                  <td class="p-3"><span class="text-xs font-semibold px-2 py-0.5 rounded-full ${row.mem0_match ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'}">${window.tmDashboard.esc(m_str)}</span></td>
                  <td class="p-3">${row.mem0_latency_ms ? window.tmDashboard.esc(row.mem0_latency_ms) + ' ms' : '--'}</td>
                </tr>
              `;
            }).join('');
          }

          const sugEl = document.getElementById('eval-suggestions-content');
          if (sugEl) {
            let sugKey = '';
            if (data.wiki.recall_1 < 0.6) {
              sugKey = 'agent.eval_sug_low';
            } else if (data.wiki.recall_1 < 0.8) {
              sugKey = 'agent.eval_sug_med';
            } else {
              sugKey = 'agent.eval_sug_high';
            }
            sugEl.setAttribute('data-i18n', sugKey);
            sugEl.innerHTML = t(sugKey);
          }
        } else {
          if (summaryBar) summaryBar.classList.add('hidden');
          if (detailsBox) detailsBox.classList.add('hidden');
          if (suggestions) suggestions.classList.remove('hidden');
          if (btn) {
            btn.innerHTML = `<i data-lucide="alert-circle" class="h-4 w-4"></i> ${window.tmDashboard.esc(data.error || t('agent.eval_fail', '评测失败'))}`;
          }
          const sugEl = document.getElementById('eval-suggestions-content');
          if (sugEl) {
            sugEl.removeAttribute('data-i18n');
            sugEl.textContent = data.hint || data.error || t('agent.eval_fail', '评测失败');
          }
          if (window.lucide) window.lucide.createIcons();
        }
      } catch (err) {
        if (err.name === 'AbortError') return;
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = `<i data-lucide="alert-circle" class="h-4 w-4"></i> ${window.tmDashboard.esc(t('agent.eval_fail', '评测失败'))}`;
        }
        console.error(err);
      }
    },

    animateValue(elementId, start, end, duration, isPercent = false) {
      const obj = document.getElementById(elementId);
      if (!obj) return;
      const range = end - start;
      let current = start;
      const increment = end > start ? 1 : -1;
      const stepTime = Math.abs(Math.floor(duration / (range || 1)));

      const timer = setInterval(() => {
        current += increment;
        if (current === end || (increment > 0 && current >= end) || (increment < 0 && current <= end)) {
          obj.textContent = end + (isPercent ? '%' : '');
          clearInterval(timer);
        } else {
          obj.textContent = current + (isPercent ? '%' : '');
        }
      }, Math.max(stepTime, 10));
      this.animateTimers.push(timer);
    }
  };

  // Health Page Controller
  window.tmPages.health = {
    root: null,
    data: null,
    refreshIntervalId: null,
    memoryOverviewIntervalId: null,
    _handleRefresh: null,
    fetchHealthRequestId: 0,
    fetchMemoryOverviewRequestId: 0,
    fetchHealthAbortController: null,
    fetchMemoryOverviewAbortController: null,

    init(root, data) {
      // Prevent duplicate initializations and clear any pre-existing timers/listeners
      this.destroy();

      this.root = root;
      this.data = data;

      // Initial render
      this.render(this.data);

      // Manual refresh button listener
      const refreshBtn = document.getElementById('refresh-button');
      if (refreshBtn) {
        this._handleRefresh = () => {
          this.fetchHealth();
          this.fetchMemoryOverview();
        };
        refreshBtn.addEventListener('click', this._handleRefresh);
      }

      // Check if loading flag is active
      if (this.data.loading) {
        this.fetchHealth();
      }
      this.fetchMemoryOverview();

      // Set up periodic auto refresh (30 seconds)
      this.refreshIntervalId = setInterval(() => runWhenVisible(() => this.fetchHealth()), 45000);
      this.memoryOverviewIntervalId = setInterval(() => runWhenVisible(() => this.fetchMemoryOverview()), 90000);
    },

    destroy() {
      // Clear refresh interval
      if (this.refreshIntervalId) {
        clearInterval(this.refreshIntervalId);
        this.refreshIntervalId = null;
      }
      if (this.memoryOverviewIntervalId) {
        clearInterval(this.memoryOverviewIntervalId);
        this.memoryOverviewIntervalId = null;
      }
      if (this.fetchHealthAbortController) {
        this.fetchHealthAbortController.abort();
        this.fetchHealthAbortController = null;
      }
      if (this.fetchMemoryOverviewAbortController) {
        this.fetchMemoryOverviewAbortController.abort();
        this.fetchMemoryOverviewAbortController = null;
      }

      // Remove refresh button listener
      const refreshBtn = document.getElementById('refresh-button');
      if (refreshBtn && this._handleRefresh) {
        refreshBtn.removeEventListener('click', this._handleRefresh);
        this._handleRefresh = null;
      }
    },

    render(report) {
      this.data = report;
      const c = window.tmDashboard;

      const generatedAtEl = document.getElementById('generated-at');
      if (generatedAtEl) generatedAtEl.textContent = report.generated_at || '-';

      const shaPill = document.getElementById('sha-pill');
      if (shaPill) shaPill.textContent = (report.dashboard && report.dashboard.git_sha) || '-';

      const serviceGrid = document.getElementById('service-grid');
      if (serviceGrid) {
        serviceGrid.innerHTML = this.renderServices(report);
      }

      this.renderWorktree(report);
      this.renderSourceUpdate(report.source_update || null);
      this.fetchSourceUpdate();
      this.renderDigest(report);
      if (Object.prototype.hasOwnProperty.call(report, 'memory_overview')) {
        this.renderMemoryOverview(report.memory_overview || {});
      }
      this.renderCommits(report);
      this.renderDoctor(report);
      this.renderSelfCheck(report);

      if (window.lucide) window.lucide.createIcons();
    },

    async renderSelfCheck(report) {
      const c = window.tmDashboard;
      const t = (key) => (window.tmI18n && window.tmI18n.get(key)) || key;

      // 1. Platform
      const platformEl = document.getElementById('self-check-platform');
      if (platformEl) {
        const isWsl = report.dashboard && report.dashboard.is_wsl;
        platformEl.innerHTML = isWsl 
          ? `<span class="text-[#52733a]">WSL2 (Linux)</span>` 
          : `<span class="text-[#8a6b1f]">Windows</span>`;
      }

      // 2. Dual Align
      const alignEl = document.getElementById('self-check-align');
      if (alignEl) {
        const localSha = (report.dashboard && report.dashboard.git_sha) || '';
        const oppositeSha = (report.dashboard && report.dashboard.opposite_sha) || '';

        let alignHtml = '';
        if (!oppositeSha) {
          alignHtml = `<span class="text-[#8a8275]">${t('health.self_check.no_opposite') || '未检测到对端'}</span>`;
        } else if (localSha.slice(0, 7) === oppositeSha.slice(0, 7)) {
          alignHtml = `<span class="text-[#52733a]">${t('health.self_check.aligned') || '双端一致'} (${localSha.slice(0, 7)})</span>`;
        } else {
          alignHtml = `<span class="text-[#8a3527] font-bold">${t('health.self_check.mismatch') || '双端未对齐'}</span><div class="text-[10px] text-[#8a8275] mt-0.5">WSL:${oppositeSha.slice(0, 7)} | Win:${localSha.slice(0, 7)}</div>`;
        }
        alignEl.innerHTML = alignHtml;
      }

      // 3. Cache Status
      const cacheEl = document.getElementById('self-check-cache');
      if (cacheEl) {
        const memCount = (window.tmDashboardRouter && window.tmDashboardRouter.cache) 
          ? Object.keys(window.tmDashboardRouter.cache).length 
          : 0;
        let pwaCount = 0;
        try {
          const keys = await caches.keys();
          pwaCount = keys.length;
        } catch (e) {
          console.warn('Caches not supported', e);
        }
        cacheEl.innerHTML = `<div>${t('health.self_check.mem') || '内存缓存'}: ${memCount}</div><div class="text-[11px] text-[#8a8275] mt-0.5">${t('health.self_check.pwa') || 'PWA缓存'}: ${pwaCount}</div>`;
      }

      // 4. Locale
      const localeEl = document.getElementById('self-check-locale');
      if (localeEl) {
        const currentLang = (window.tmI18n && window.tmI18n.lang && window.tmI18n.lang()) || localStorage.getItem('tm-lang') || 'zh';
        localeEl.innerHTML = currentLang === 'en' 
          ? `<span class="text-[#4a443d]">English (EN)</span>` 
          : `<span class="text-[#4a443d]">简体中文 (ZH)</span>`;
      }

      // Set global Self Check status badge next to title
      const selfCheckStatus = document.getElementById('self-check-status');
      if (selfCheckStatus) {
        const localSha = (report.dashboard && report.dashboard.git_sha) || '';
        const oppositeSha = (report.dashboard && report.dashboard.opposite_sha) || '';
        if (!oppositeSha) {
          selfCheckStatus.innerHTML = c.badge('warn', t('status.warn') || '注意');
        } else if (localSha.slice(0, 7) === oppositeSha.slice(0, 7)) {
          selfCheckStatus.innerHTML = c.badge('ok', t('status.ok') || '正常');
        } else {
          selfCheckStatus.innerHTML = c.badge('fail', t('status.fail') || '故障');
        }
      }
    },

    renderServices(report) {
      const c = window.tmDashboard;
      const services = report.services || [];
      const localProfile = (report.dashboard && report.dashboard.runtime_profile) === 'local';
      if (!localProfile) {
        return services.map(s => this.renderService(s)).join('');
      }

      const visibleServices = services.filter(service => service.status !== 'optional');
      const optionalServices = services.filter(service => service.status === 'optional');
      const cards = visibleServices.map(s => this.renderService(s));
      if (optionalServices.length) {
        const names = optionalServices.map(service => c.serviceName(service.name)).join(' / ');
        cards.push(`
          <article class="card col-span-full border-[#d8c99f] bg-[#f7f1e4] p-5">
            <div class="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
              <div>
                <div class="text-sm font-semibold text-[#1f1d1b]">高级连接未启用</div>
                <div class="mt-1 text-xs leading-5 text-[#6a6258]">基础模式不需要 ${c.esc(names)}；要做多设备同步或 IDE 自动记忆时再接入。</div>
              </div>
              ${c.badge('ok', '基础模式可用')}
            </div>
          </article>
        `);
      }
      return cards.join('');
    },

    renderService(service) {
      const c = window.tmDashboard;
      const metric = service.latency_ms == null
        ? (service.name === 'Dashboard' ? '运行中' : (service.status === 'ok' ? c.statusText(service.status, service.status_label) : '—'))
        : `${Math.round(Number(service.latency_ms))} ms`;

      let actionGuide = '';
      if (service.name === 'OpenClaw' && service.status === 'warn') {
        actionGuide = `
          <div class="mt-2 border-t border-[#e6dfcc] pt-2 text-[10px] text-[#8a8275]">
            <span class="font-semibold text-[#8a6b1f]">接入命令：</span>
            <code class="block font-mono bg-[#f0e9d8] p-1 rounded text-[9px] truncate select-all text-center mt-1">tm doctor</code>
          </div>
        `;
      }

      return `
        <article class="service-card card p-5">
          <div class="mb-3 flex items-center gap-2">
            <i data-lucide="${c.esc(service.icon || 'server')}" class="h-5 w-5 text-[#c8a560]"></i>
            <span class="text-sm font-semibold text-[#1f1d1b]">${c.esc(c.serviceName(service.name))}</span>
          </div>
          <div class="mb-2 flex items-baseline gap-2">
            <span class="text-2xl font-bold text-[#1f1d1b]">${c.esc(metric)}</span>
          </div>
          <div class="mb-3 truncate text-xs text-[#8a8275]">${c.esc(c.serviceDetail(service))}</div>
          <div class="flex items-center justify-between gap-2">
            <span class="text-xs text-[#8a8275]">${c.esc(service.port || '')}</span>
            ${c.badge(service.status, service.status_label)}
          </div>
          ${actionGuide}
        </article>
      `;
    },

    renderMemoryOverview(overview) {
      const c = window.tmDashboard;
      const grid = document.getElementById('memory-overview');
      const trend = document.getElementById('memory-trend');
      const status = document.getElementById('memory-overview-status');
      if (status) status.textContent = overview.ok === false ? '加载失败' : '实时估算';
      if (grid) {
        const mem0Available = overview.mem0_approximate !== null && overview.mem0_approximate !== undefined;
        const mem0Subline = mem0Available ? '即时记忆（在线）' : '即时记忆暂时无法连接';
        const mem0Status = mem0Available ? 'ok' : 'warn';
        grid.innerHTML = [
          c.kpiCard('library', '知识库页面', c.numberText(overview.wiki_pages), '永久保存的知识'),
          c.kpiCard('inbox', '待确认内容', c.numberText(overview.inbox_pending), '需要你来确认', overview.inbox_pending ? 'warn' : 'ok'),
          c.kpiCard('database', '即时记忆条数', c.numberText(overview.mem0_approximate), mem0Subline, mem0Status),
          c.kpiCard('bar-chart-3', '7 天日报', c.numberText((overview.trend_7d || []).filter(row => row.available).length), '已生成天数')
        ].join('');
      }
      if (trend) {
        const rows = overview.trend_7d || [];
        const maxValue = Math.max(1, ...rows.map(row => Number(row.mem0 || 0) + Number(row.inbox || 0) + Number(row.discard || 0)));
        trend.innerHTML = rows.map(row => {
          const total = Number(row.mem0 || 0) + Number(row.inbox || 0) + Number(row.discard || 0);
          const height = row.available ? Math.max(8, Math.round(total * 64 / maxValue)) : 8;
          return `
            <div class="rounded-xl border border-[#e6dfcc] bg-[#fcfaf2] p-2 text-center">
              <div class="mx-auto flex h-16 items-end justify-center">
                <div class="${row.available ? 'bg-[#c8a560]' : 'bg-[#e6dfcc]'} w-5 rounded-t" style="height:${height}px"></div>
              </div>
              <div class="mt-2 text-[11px] text-[#8a8275]">${c.esc(String(row.date || '').slice(5))}</div>
              <div class="text-xs font-semibold text-[#1f1d1b]">${row.available ? c.numberText(total) : '—'}</div>
            </div>
          `;
        }).join('');
      }
    },

    renderWorktree(report) {
      const c = window.tmDashboard;
      const check = (report.agent_doctor.checks || []).find(item => item.name === 'worktree') || {};
      const worktreeStatus = document.getElementById('worktree-status');
      if (worktreeStatus) worktreeStatus.innerHTML = c.badge(check.status || 'warn');

      const worktreeBody = document.getElementById('worktree-body');
      if (worktreeBody) {
        const paths = (check.paths || []).slice(0, 6).map(path => `<li class="truncate font-mono text-xs">${c.esc(path)}</li>`).join('');
        const sourceLabel = check.repo_root ? `
          <div class="mt-3 rounded-lg border border-[#e6dfcc] bg-[#fbf8f1] px-3 py-2 text-xs text-[#8a8275]">
            当前检查来源：<code id="worktree-root" class="break-all font-mono text-[#4a443d]">${c.esc(check.repo_root)}</code>
            ${check.runtime_side ? `<span class="ml-2 text-[#8a8275]">(${c.esc(check.runtime_side)})</span>` : ''}
          </div>
        ` : '';
        worktreeBody.innerHTML = `
          <div class="rounded-xl border border-[#e6dfcc] bg-[#f0e9d8] p-3">
            <div class="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-[#4a443d]">
              <span>分支 <code class="font-mono text-[#1f1d1b]">${c.esc(check.branch || '-')}</code></span>
              <span>↑ <code class="font-mono text-[#1f1d1b]">${c.esc(check.ahead ?? 0)}</code></span>
              <span>↓ <code class="font-mono text-[#1f1d1b]">${c.esc(check.behind ?? 0)}</code></span>
              <span class="break-all">HEAD <code class="font-mono text-[#1f1d1b]">${c.esc((check.head || '').slice(0, 12))}</code> → 上游 ${c.esc(check.upstream || '-')}</span>
            </div>
            ${sourceLabel}
          </div>
          ${(check.paths || []).length ? `
            <div>
              <div class="mb-2 text-xs font-medium text-[#8a3527]">未保存的文件：${c.esc(check.dirty_count || check.paths.length)} 个</div>
              <ul class="space-y-1 rounded-xl border border-[#d49a91] bg-[#f0d6d2] p-3 text-[#8a3527]">${paths}</ul>
            </div>
          ` : '<div class="rounded-xl border border-[#a0b889] bg-[#dde8ce] p-3 text-sm text-[#52733a]">工作区干净，可以继续操作。</div>'}
        `;
      }
    },

    renderSourceUpdate(update) {
      const c = window.tmDashboard;
      const statusEl = document.getElementById('source-update-status');
      const body = document.getElementById('source-update-body');
      if (!body && !statusEl) return;
      if (!update) {
        if (statusEl) statusEl.innerHTML = c.badge('warn', '读取中');
        if (body) body.innerHTML = '<div class="rounded-xl border border-[#e6dfcc] bg-[#f0e9d8] p-3 text-[#8a8275]">正在读取源码更新状态...</div>';
        return;
      }
      const ok = update.ok !== false;
      const status = !ok
        ? 'warn'
        : update.update_available
          ? (update.safe_to_apply ? 'warn' : 'fail')
          : 'ok';
      const label = !ok
        ? '不可判断'
        : update.update_available
          ? (update.safe_to_apply ? '有更新' : '需先处理')
          : '已是最新';
      if (statusEl) statusEl.innerHTML = c.badge(status, label);
      if (body) {
        const warnings = Array.isArray(update.warnings) ? update.warnings : [];
        body.innerHTML = `
          <div class="rounded-xl border border-[#e6dfcc] bg-[#f0e9d8] p-3">
            <div class="flex flex-wrap items-center gap-x-4 gap-y-2">
              <span>模式 <code class="font-mono text-[#1f1d1b]">${c.esc(update.source_mode || '-')}</code></span>
              <span>↓ <code class="font-mono text-[#1f1d1b]">${c.esc(update.behind ?? 0)}</code></span>
              <span>↑ <code class="font-mono text-[#1f1d1b]">${c.esc(update.ahead ?? 0)}</code></span>
              <span>可自动更新 <code class="font-mono text-[#1f1d1b]">${update.safe_to_apply ? 'yes' : 'no'}</code></span>
            </div>
            <div class="mt-2 break-all text-xs text-[#8a8275]">${c.esc(update.app_root || '')}</div>
          </div>
          <div class="rounded-xl border border-[#e6dfcc] bg-[#fbf8f1] p-3 text-sm leading-6">
            ${c.esc(update.recommended_action || '暂无建议。')}
          </div>
          ${warnings.length ? `<ul class="space-y-1 rounded-xl border border-[#e0c889] bg-[#f4e6c4] p-3 text-xs text-[#8a6b1f]">${warnings.slice(0, 3).map(item => `<li>${c.esc(item)}</li>`).join('')}</ul>` : ''}
        `;
      }
    },

    async fetchSourceUpdate() {
      try {
        const response = await fetch('/api/update/status');
        const data = await response.json();
        this.renderSourceUpdate(data);
      } catch (error) {
        this.renderSourceUpdate({
          ok: false,
          reason: 'fetch_failed',
          recommended_action: `读取源码更新状态失败：${error.message || error}`,
        });
      }
    },

    renderDigest(report) {
      const c = window.tmDashboard;
      const digest = report.daily_digest || {};
      const digestStatus = document.getElementById('digest-status');
      if (digestStatus) digestStatus.innerHTML = c.badge(digest.exists ? 'ok' : 'warn', digest.exists ? '已生成' : '未生成');

      const digestBody = document.getElementById('digest-body');
      if (digestBody) {
        digestBody.innerHTML = `
          <div class="text-3xl font-extrabold text-[#1f1d1b]">${c.esc(digest.date || '-')}</div>
          <div class="break-all rounded-xl border border-[#e6dfcc] bg-[#f0e9d8] p-3 font-mono text-xs">${c.esc(digest.path || '-')}</div>
          <button class="primary-button" type="button" disabled title="阶段 1 仅展示状态，生成按钮后续接 cron-daily-report">立即生成</button>
        `;
      }
    },

    renderCommits(report) {
      const c = window.tmDashboard;
      const commitList = document.getElementById('commit-list');
      if (commitList) {
        commitList.innerHTML = (report.recent_commits || []).map(line => {
          const parts = String(line).split(' ');
          const sha = parts.shift() || '';
          return `<div class="flex gap-3 rounded-xl border border-[#e6dfcc] bg-[#f0e9d8] px-3 py-2 text-sm">
            <span class="mt-2 h-2 w-2 shrink-0 rounded-full bg-[#c8a560]"></span>
            <code class="shrink-0 text-xs text-[#4a443d]">${c.esc(sha)}</code>
            <span class="min-w-0 truncate text-[#1f1d1b]">${c.esc(parts.join(' '))}</span>
          </div>`;
        }).join('');
      }
    },

    renderDoctor(report) {
      const c = window.tmDashboard;
      const summary = report.agent_doctor.summary || {};
      const doctorSummary = document.getElementById('doctor-summary');
      if (doctorSummary) {
        doctorSummary.textContent = `正常 ${summary.ok_count || 0} · 注意 ${summary.warn_count || 0} · 故障 ${summary.fail_count || 0}`;
      }

      const doctorDetails = document.getElementById('doctor-details');
      if (doctorDetails) {
        doctorDetails.innerHTML = (report.agent_doctor.checks || []).map(check => {
          let errHtml = '';
          if (check.error) {
            let errorText = check.error;
            let detailTrace = '';
            
            // Map typical errors
            if (check.error.includes('.env') && check.error.includes('missing')) {
              errorText = '短期记忆（Mem0）配置文件缺失，请先配置环境。';
              detailTrace = check.error;
            } else if (check.error.includes('timed out')) {
              errorText = '请求超时，目标记忆服务或数据库无响应。';
              detailTrace = check.error;
            } else if (check.error.includes('Connection refused')) {
              errorText = '连接被拒绝，服务未启动或端口被占用。';
              detailTrace = check.error;
            }

            if (detailTrace) {
              errHtml = `
                <div class="text-[#8a3527] font-semibold">${c.esc(errorText)}</div>
                <details class="mt-1">
                  <summary class="cursor-pointer text-[10px] text-[#8a8275] hover:text-[#8a3527] outline-none">展开详细调试日志</summary>
                  <pre class="mt-1 whitespace-pre-wrap rounded border border-[#d49a91] bg-[#fbf8f1] p-2 text-[10px] leading-4 text-[#8a3527] font-mono">${c.esc(detailTrace)}</pre>
                </details>
              `;
            } else {
              errHtml = `<div class="text-[#8a3527] font-semibold">错误：${c.esc(errorText)}</div>`;
            }
          }

          // Generate setup guidance for specific check names
          let guidanceHtml = '';
          if (check.status === 'warn' || check.status === 'fail') {
            if (check.name === 'worktree' && check.reason && check.reason.includes('dirty')) {
              guidanceHtml = `
                <div class="mt-2 rounded bg-[#fcfaf2] border border-[#e6dfcc] p-2 text-[11px] text-[#8a6b1f]">
                  <div class="font-semibold mb-1">💡 解决方案：工作区有未提交改动</div>
                  <div class="mb-1">请在终端中运行以下命令精准提交相关改动，或用 stash 暂存：</div>
                  <code class="block font-mono bg-[#f0e9d8] p-1 rounded select-all text-center">git add &lt;文件路径&gt; ; git commit -m "[human] fix: your message"</code>
                </div>
              `;
            } else if (check.name === 'mem0_api') {
              guidanceHtml = `
                <div class="mt-2 rounded bg-[#fcfaf2] border border-[#e6dfcc] p-2 text-[11px] text-[#8a6b1f]">
                  <div class="font-semibold mb-1">💡 解决方案：即时记忆服务未就绪</div>
                  <div class="mb-1">请运行以下命令检查端口，或在你的本地配置中设置 <code>MEM0_URL</code>：</div>
                  <code class="block font-mono bg-[#f0e9d8] p-1 rounded select-all text-center">tm doctor</code>
                </div>
              `;
            } else if (check.name === 'public_ask_llm') {
              guidanceHtml = `
                <div class="mt-2 rounded bg-[#fcfaf2] border border-[#e6dfcc] p-2 text-[11px] text-[#8a6b1f]">
                  <div class="font-semibold mb-1">💡 解决方案：在线问答模型未配置</div>
                  <div class="mb-1">设置 DeepSeek 或 OpenAI 兼容 Key 后，用下面命令确认不会泄露密钥：</div>
                  <code class="block font-mono bg-[#f0e9d8] p-1 rounded select-all text-center">${c.esc(check.verify_command || 'tm llm status --json')}</code>
                </div>
              `;
            }
          }

          let publicAskHtml = '';
          if (check.name === 'public_ask_llm') {
            const providers = (check.configured_providers || []).join(', ') || '未配置';
            publicAskHtml = `
              <div>在线问答：<code>${check.llm_configured ? '已配置' : '未配置'}</code></div>
              <div>推荐模型：<code>${c.esc(check.routine_model || '未记录')}</code> / 管理模型：<code>${c.esc(check.admin_model || '未记录')}</code></div>
              <div>已配置通道：<code>${c.esc(providers)}</code></div>
              <div>离线备用：<code>${c.esc(check.offline_fallback || 'tm ask --offline')}</code></div>
              <div>在线验证：<code>${c.esc(check.online_smoke || 'tm ask --query "TigerMemory 是什么？" --scope wiki')}</code></div>
            `;
          }

          return `
            <article class="rounded-xl border border-[#e6dfcc] bg-[#f0e9d8] p-3">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <div class="font-semibold text-[#1f1d1b]">${c.esc(check.name)}</div>
                ${c.badge(check.status)}
              </div>
              <div class="mt-2 grid gap-1 text-xs text-[#4a443d]">
                ${check.latency_ms == null ? '' : `<div>耗时：<code>${c.esc(Math.round(Number(check.latency_ms)))} ms</code></div>`}
                ${errHtml}
                ${check.reason ? `<div>原因：${c.esc(check.reason)}</div>` : ''}
                ${check.paths ? `<div class="break-all font-mono">路径：${c.esc((check.paths || []).slice(0, 3).join(' | '))}</div>` : ''}
                ${publicAskHtml}
                ${guidanceHtml}
              </div>
            </article>
          `;
        }).join('');
      }
    },

    renderFetchError(message) {
      const c = window.tmDashboard;
      const generatedAtEl = document.getElementById('generated-at');
      if (generatedAtEl) generatedAtEl.textContent = message;

      const serviceGrid = document.getElementById('service-grid');
      if (serviceGrid) {
        serviceGrid.innerHTML = `<div class="col-span-full rounded-2xl border border-[#d49a91] bg-[#f0d6d2] p-4 text-sm text-[#8a3527]">${c.esc(message)}</div>`;
      }
    },

    async fetchHealth() {
      if (this.fetchHealthAbortController) this.fetchHealthAbortController.abort();
      const requestId = ++this.fetchHealthRequestId;
      this.fetchHealthAbortController = new AbortController();
      try {
        const response = await fetch('/api/health/summary', { signal: this.fetchHealthAbortController.signal });
        const data = await response.json();
        if (requestId !== this.fetchHealthRequestId) return;
        this.render(data);
      } catch (error) {
        if (error.name === 'AbortError') return;
        console.error(error);
        this.renderFetchError('数据暂时没取到，请稍后重试');
      }
    },

    async fetchMemoryOverview() {
      const status = document.getElementById('memory-overview-status');
      if (this.fetchMemoryOverviewAbortController) this.fetchMemoryOverviewAbortController.abort();
      const requestId = ++this.fetchMemoryOverviewRequestId;
      this.fetchMemoryOverviewAbortController = new AbortController();
      try {
        const response = await fetch('/api/health/memory-overview', { signal: this.fetchMemoryOverviewAbortController.signal });
        const data = await response.json();
        if (requestId !== this.fetchMemoryOverviewRequestId) return;
        this.renderMemoryOverview(data);
      } catch (error) {
        if (error.name === 'AbortError') return;
        console.error(error);
        if (status) status.textContent = '加载失败';
      }
    }
  };

  // Quality Page Controller
  window.tmPages.quality = {
    root: null,
    data: null,
    refreshIntervalId: null,
    _handleRefresh: null,
    _tabListeners: [],
    _rangeListeners: [],
    rangeKey: 'today',
    fetchQualityRequestId: 0,
    fetchQualityAbortController: null,
    prefetchQualityAbortController: null,
    prefetchedQualityRanges: new Set(),
    qualityUpdating: false,

    init(root, data) {
      // Prevent duplicate initializations and clear any pre-existing timers/listeners
      this.destroy();

      this.root = root;
      this.data = data;
      this.rangeKey = (((data.memory || data || {}).range || {}).key) || this.rangeKey || 'today';
      this.prefetchedQualityRanges = new Set([this.rangeKey]);

      // Initial render
      this.render(this.data);

      // Setup tab switcher event listeners when future quality panels are present.
      this._handleTabClick = (button) => {
        document.querySelectorAll('[data-tab]').forEach(node => node.classList.toggle('active', node === button));
        document.querySelectorAll('[data-panel]').forEach(panel => panel.classList.toggle('hidden', panel.dataset.panel !== button.dataset.tab));
      };

      const tabButtons = document.querySelectorAll('[data-tab]');
      this._tabListeners = [];
      tabButtons.forEach(button => {
        const listener = () => this._handleTabClick(button);
        button.addEventListener('click', listener);
        this._tabListeners.push({ element: button, listener: listener });
      });

      // Manual refresh button listener
      const refreshBtn = document.getElementById('refresh-button');
      if (refreshBtn) {
        this._handleRefresh = this.fetchQuality.bind(this);
        refreshBtn.addEventListener('click', this._handleRefresh);
      }

      const rangeButtons = document.querySelectorAll('[data-quality-range]');
      this._rangeListeners = [];
      rangeButtons.forEach(button => {
        const listener = () => {
          const nextRange = button.dataset.qualityRange || 'today';
          if (nextRange === this.rangeKey) return;
          this.rangeKey = nextRange;
          this.renderRangeControls({ range: { key: nextRange } });
          this.setQualityUpdating(nextRange, true);
          this.fetchQuality();
        };
        button.addEventListener('click', listener);
        this._rangeListeners.push({ element: button, listener: listener });
      });

      // Check if loading flag is active
      if ((this.data.memory || {}).loading) {
        this.fetchQuality();
      } else {
        this.prefetchQualityRanges();
      }

      // Set up periodic auto refresh (30 seconds)
      this.refreshIntervalId = setInterval(() => runWhenVisible(() => this.fetchQuality()), 45000);
    },

    destroy() {
      // Clear refresh interval
      if (this.refreshIntervalId) {
        clearInterval(this.refreshIntervalId);
        this.refreshIntervalId = null;
      }

      // Clean up tab button event listeners
      if (this._tabListeners) {
        this._tabListeners.forEach(({ element, listener }) => {
          element.removeEventListener('click', listener);
        });
        this._tabListeners = [];
      }

      // Clean up refresh button event listener
      const refreshBtn = document.getElementById('refresh-button');
      if (refreshBtn && this._handleRefresh) {
        refreshBtn.removeEventListener('click', this._handleRefresh);
        this._handleRefresh = null;
      }

      if (this._rangeListeners) {
        this._rangeListeners.forEach(({ element, listener }) => {
          element.removeEventListener('click', listener);
        });
        this._rangeListeners = [];
      }

      if (this.fetchQualityAbortController) {
        this.fetchQualityAbortController.abort();
        this.fetchQualityAbortController = null;
      }
      if (this.prefetchQualityAbortController) {
        this.prefetchQualityAbortController.abort();
        this.prefetchQualityAbortController = null;
      }
      this.prefetchedQualityRanges = new Set();
    },

    render(data) {
      this.data = data;
      const c = window.tmDashboard;
      const memory = data.memory || data;
      const range = this.qualityRange(memory);
      this.rangeKey = range.key || this.rangeKey || 'today';
      this.clearQualityUpdating();

      const lastRefresh = document.getElementById('last-refresh');
      if (lastRefresh) lastRefresh.textContent = memory.date || '刚刚';

      const statusCounts = (memory.trace_summary || {}).status_counts || {};
      const traceTotal = Object.values(statusCounts).reduce((sum, value) => sum + Number(value || 0), 0);
      const tracePill = document.getElementById('trace-summary-pill');
      if (tracePill) {
        const hasTrace = memory.trace_latency_supported && traceTotal > 0;
        tracePill.className = `status-badge ${hasTrace ? 'status-ok' : 'status-warn'}`;
        tracePill.innerHTML = `<span class="status-dot"></span>${hasTrace ? '响应耗时已接入' : '等待真实回答记录'}`;
      }

      this.renderRangeControls(memory);
      this.renderKpis(memory);
      this.renderFlowPanel(memory);
      this.renderStatusBars(memory);
      this.renderRecommendationQuality(memory);
      this.renderRetrievalRelease(memory);
      this.renderFailures(memory);
      this.renderQualityEmptyState(memory);

      if (window.lucide) window.lucide.createIcons();
    },

    qualityRange(memory) {
      const range = (memory || {}).range || {};
      const fallback = {
        today: { key: 'today', label: '今日', trace_label: '近 24 小时' },
        '7d': { key: '7d', label: '近 7 天', trace_label: '近 7 天' },
        '30d': { key: '30d', label: '近 1 个月', trace_label: '近 30 天' },
      };
      return Object.assign({}, fallback[range.key || this.rangeKey] || fallback.today, range);
    },

    renderRangeControls(memory) {
      memory = memory || {};
      const c = window.tmDashboard;
      const range = this.qualityRange(memory);
      const title = document.getElementById('quality-flow-title');
      const subtitle = document.getElementById('quality-flow-subtitle');
      const statusTitle = document.getElementById('quality-status-title');
      const missing = Array.isArray(memory.missing_dates) ? memory.missing_dates.length : 0;
      const available = Array.isArray(memory.available_dates) ? memory.available_dates.length : null;
      if (title) title.textContent = `${range.label}记忆分流`;
      if (subtitle) {
        const coverage = available !== null
          ? `已纳入 ${available} 天数据${missing ? `，缺 ${missing} 天日报` : ''}`
          : '实时查看输入、分流规则和输出结果';
        subtitle.textContent = `${coverage}；mem0 / Wiki / inbox / discard 四条路线同时展示。`;
      }
      if (statusTitle) statusTitle.textContent = `回答状态分布（${range.trace_label || '近 7 天'}）`;
      document.querySelectorAll('[data-quality-range]').forEach(button => {
        const active = button.dataset.qualityRange === range.key;
        button.classList.toggle('bg-[#c8a560]', active);
        button.classList.toggle('text-[#1f1d1b]', active);
        button.classList.toggle('shadow-sm', active);
        button.classList.toggle('text-[#6f6258]', !active);
      });
    },

    setQualityUpdating(rangeKey, loading) {
      const c = window.tmDashboard;
      const range = this.qualityRange({ range: { key: rangeKey || this.rangeKey || 'today' } });
      this.qualityUpdating = Boolean(loading);
      const qualityAlert = document.getElementById('quality-alert');
      if (qualityAlert) {
        qualityAlert.dataset.qualityUpdating = loading ? '1' : '';
        qualityAlert.classList.toggle('hidden', !loading);
        if (loading) {
          qualityAlert.innerHTML = `<div class="card border-[#d8c99f] bg-[#f4e6c4] px-4 py-3 text-sm text-[#8a6b1f]">正在更新${c.esc(range.label)}数据；当前数字仍是上一范围，仅作参考。</div>`;
        }
      }
      ['kpi-grid', 'route-bars', 'status-bars', 'failure-list'].forEach(id => {
        const node = document.getElementById(id);
        if (node) node.classList.toggle('tm-quality-updating', Boolean(loading));
      });
    },

    clearQualityUpdating() {
      this.qualityUpdating = false;
      const qualityAlert = document.getElementById('quality-alert');
      if (qualityAlert && qualityAlert.dataset.qualityUpdating === '1') {
        qualityAlert.classList.add('hidden');
        qualityAlert.dataset.qualityUpdating = '';
      }
      ['kpi-grid', 'route-bars', 'status-bars', 'failure-list'].forEach(id => {
        const node = document.getElementById(id);
        if (node) node.classList.remove('tm-quality-updating');
      });
    },

    renderKpis(memory) {
      const c = window.tmDashboard;
      const counts = memory.counts || {};
      const range = this.qualityRange(memory);
      const duration = (memory.trace_summary || {}).duration_ms || {};
      const statusCounts = (memory.trace_summary || {}).status_counts || {};
      const answerFailureCount = Number(statusCounts.error || 0) + Number(statusCounts.fail || 0);
      const notFoundCount = Number(statusCounts.not_found || 0);
      const traceTotal = Object.values(statusCounts).reduce((sum, value) => sum + Number(value || 0), 0);
      const hasDuration = Number.isFinite(Number(duration.p95)) && traceTotal > 0;
      const pendingCount = Number(counts.inbox || 0);

      const kpiGrid = document.getElementById('kpi-grid');
      const qualityAlert = document.getElementById('quality-alert');
      const fallbackBanner = memory.fallback_mode
        ? `<div class="card border-[#e0c889] bg-[#f4e6c4] px-4 py-3 text-sm text-[#8a6b1f]">${c.esc(range.label)}实时模式：当前直接读取 Mem0、收件箱、回答轨迹和 discard 审计；日报只作为历史快照。</div>`
        : '';
      if (qualityAlert) {
        qualityAlert.innerHTML = fallbackBanner;
        qualityAlert.classList.toggle('hidden', !fallbackBanner);
      }
      if (kpiGrid) {
        const mem0Status = memory.mem0_status || {};
        const mem0CountKnown = Number.isFinite(Number(counts.mem0));
        const mem0Value = mem0CountKnown
          ? c.numberText(counts.mem0)
          : (Number.isFinite(Number(mem0Status.count)) ? c.numberText(mem0Status.count) : (mem0Status.ok === false ? '不可达' : '未接入'));
        const mem0Label = mem0CountKnown ? `${range.label}进入即时记忆` : '即时记忆连接';
        const rangeSpan = range.start_date && range.end_date ? `${range.start_date} 至 ${range.end_date}` : (memory.date || '-');
        const mem0Subline = mem0CountKnown
          ? `统计 ${rangeSpan}`
          : (mem0Status.ok === false ? (mem0Status.error || '服务暂时不可连接') : `当前总量${Number.isFinite(Number(mem0Status.count)) ? ` ${c.numberText(mem0Status.count)}` : '待同步'}`);
        const mem0CardStatus = mem0Status.ok === false ? 'fail' : 'ok';
        const answerValue = hasDuration ? c.numberText(Math.round(Number(duration.p95 || 0)), ' ms') : '无记录';
        const answerSubline = hasDuration
          ? `P50 ${c.numberText(Math.round(Number(duration.p50 || 0)), ' ms')} · 未命中 ${notFoundCount}`
          : '有回答记录后显示耗时';
        const issueCount = answerFailureCount;
        const issueValue = traceTotal ? c.numberText(issueCount) : '等待记录';
        const issueSubline = traceTotal
          ? (issueCount ? '模型或链路错误，需要复核' : (notFoundCount ? `未命中 ${notFoundCount} 条，属于证据不足拒答` : `${range.trace_label || '近 7 天'}未见失败项`))
          : '有回答记录后显示失败项';
        const reviewHidden = Number(counts.review_hidden || 0);
        const inboxSubline = reviewHidden ? `已折叠低价值 ${reviewHidden} 项` : `${range.label}审核队列`;
        kpiGrid.innerHTML = [
          c.kpiCard('inbox', '待确认内容', c.numberText(pendingCount), inboxSubline, pendingCount ? 'warn' : 'ok'),
          c.kpiCard('database', mem0Label, mem0Value, mem0Subline, mem0CardStatus),
          c.kpiCard('timer', '回答耗时 P95', answerValue, answerSubline, hasDuration && duration.p95 > 5000 ? 'warn' : 'ok'),
          c.kpiCard('triangle-alert', '回答失败', issueValue, issueSubline, issueCount ? 'warn' : 'ok'),
        ].join('');
      }
    },


    drawFlowLines() {
      const svg = document.getElementById('tm-flow-svg');
      if (!svg) return;
      
      const container = svg.parentElement;
      if (!container) return;
      
      const containerRect = container.getBoundingClientRect();
      const engineNode = container.querySelector('#tiger-icon') || container.querySelector('[data-flow-id="engine"]');
      if (!engineNode) return;
      
      const engineRect = engineNode.getBoundingClientRect();
      const engineX = engineRect.left - containerRect.left;
      const engineY = engineRect.top - containerRect.top;
      const engineW = engineRect.width;
      const engineH = engineRect.height;
      const engineCenterY = engineY + engineH / 2;
      const engineLeft = engineX;
      const engineRight = engineX + engineW;
      
      const sources = Array.from(container.querySelectorAll('[data-flow-id^="source-"]'));
      const outputs = Array.from(container.querySelectorAll('[data-flow-id^="output-"]'));
      
      let svgContent = '';
      
      const createPath = (x1, y1, x2, y2, flowId) => {
        // Curve to horizontal tangent
        const c1x = x1 + (x2 - x1) / 2;
        const c1y = y1;
        const c2x = x1 + (x2 - x1) / 2;
        const c2y = y2;
        const d = `M ${x1} ${y1} C ${c1x} ${c1y}, ${c2x} ${c2y}, ${x2} ${y2}`;
        
        // Randomize animation duration for a natural feel
        const dur = (Math.random() * 2 + 3).toFixed(1);
        const delay = (Math.random() * -3).toFixed(1);
        
        return `
          <path class="tm-flow-path" data-flow-path-for="${flowId}" d="${d}" />
          <circle class="tm-flow-dot-anim" r="3">
            <animateMotion dur="${dur}s" repeatCount="indefinite" begin="${delay}s">
              <mpath href="#path_${x1}_${y1}_${x2}_${y2}" />
            </animateMotion>
          </circle>
          <!-- hidden path for mpath reference -->
          <path id="path_${x1}_${y1}_${x2}_${y2}" d="${d}" fill="none" stroke="none" />
        `;
      };

      sources.forEach(source => {
        const rect = source.getBoundingClientRect();
        const x1 = rect.right - containerRect.left;
        const y1 = rect.top - containerRect.top + rect.height / 2;
        const flowId = source.dataset.flowId;
        svgContent += createPath(x1, y1, engineLeft + 10, engineCenterY, flowId);
      });

      outputs.forEach(output => {
        const rect = output.getBoundingClientRect();
        const x2 = rect.left - containerRect.left;
        const y2 = rect.top - containerRect.top + rect.height / 2;
        const flowId = output.dataset.flowId;
        svgContent += createPath(engineRight - 10, engineCenterY, x2, y2, flowId);
      });

      svg.innerHTML = svgContent;

      // Bind hover events to highlight paths
      const bindHover = (node) => {
        const flowId = node.dataset.flowId;
        if (!flowId) return;
        const path = svg.querySelector(`path[data-flow-path-for="${flowId}"]`);
        if (!path) return;
        const eventOptions = this.abortController ? { signal: this.abortController.signal } : undefined;

        node.addEventListener('mouseenter', () => {
          path.classList.add('flow-path-active');
        }, eventOptions);

        node.addEventListener('mouseleave', () => {
          path.classList.remove('flow-path-active');
        }, eventOptions);
      };

      sources.forEach(bindHover);
      outputs.forEach(bindHover);
    },

    renderFlowPanel(memory) {
      const c = window.tmDashboard;
      const range = this.qualityRange(memory);
      const routeSection = document.getElementById('route-section');
      const routeBars = document.getElementById('route-bars');
      if (!routeBars) return;

      const counts = memory.counts || {};
      const flowPayload = memory.route_flow || memory.flow || {};
      const traceCounts = (memory.trace_summary || {}).status_counts || {};
      const flowOutputs = Array.isArray(flowPayload.outputs) ? flowPayload.outputs : [];
      const routeOutputs = flowOutputs.filter(slot => !['issue', 'anomaly'].includes(String(slot.key || '').toLowerCase()));
      const flowSources = Array.isArray(flowPayload.sources) ? flowPayload.sources : [];
      const findSlot = (slots, keys) => {
        const normalized = keys.map(key => String(key).toLowerCase());
        return slots.find(slot => normalized.includes(String(slot.key || '').toLowerCase())) || {};
      };
      const numberOrNull = (value) => {
        if (value === null || value === undefined || value === '') return null;
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : null;
      };
      const issueFallback = Number(traceCounts.fail || 0) + Number(traceCounts.error || 0);
      const outputValues = {
        mem0: numberOrNull(findSlot(routeOutputs, ['mem0', 'instant']).value ?? counts.mem0),
        wiki: numberOrNull(findSlot(routeOutputs, ['wiki', 'long_term', 'long_term_knowledge']).value ?? counts.wiki),
        inbox: numberOrNull(findSlot(routeOutputs, ['inbox', 'manual_review']).value ?? counts.inbox_today ?? counts.inbox),
        discard: numberOrNull(findSlot(routeOutputs, ['discard']).value ?? counts.discard),
        issue: numberOrNull(findSlot(flowOutputs, ['issue', 'anomaly']).value ?? counts.issue ?? issueFallback),
      };
      const sourceValues = {
        daily: numberOrNull(findSlot(flowSources, ['daily']).value ?? outputValues.mem0),
        inbox: numberOrNull(findSlot(flowSources, ['inbox']).value ?? counts.inbox_today ?? counts.inbox),
        trace: numberOrNull(findSlot(flowSources, ['trace']).value ?? counts.trace_count),
      };
      const inputTotal = numberOrNull(flowPayload.input_total ?? flowPayload.today_total)
        ?? Object.values(sourceValues).reduce((sum, value) => sum + (Number.isFinite(value) ? value : 0), 0);
      const isLoggedFlow = flowPayload.flow_source === 'route_events';
      const loggedSourceValues = {
        ledger: inputTotal,
        backlog: numberOrNull(counts.inbox_pending ?? counts.inbox),
        trace: sourceValues.trace,
      };
      const visibleSourceValues = isLoggedFlow ? loggedSourceValues : sourceValues;
      const sourceTotal = Object.values(visibleSourceValues).reduce((sum, value) => sum + (Number.isFinite(value) ? value : 0), 0);
      const autoStored = [outputValues.mem0, outputValues.wiki, outputValues.discard]
        .reduce((sum, value) => sum + (Number.isFinite(value) ? value : 0), 0);
      const historyNote = flowPayload.history && flowPayload.history.note ? String(flowPayload.history.note) : '';
      const flowSummaryCards = [
        [isLoggedFlow ? `${range.label}流水` : `${range.label}候选`, inputTotal, isLoggedFlow ? '已记录路线' : '输入池'],
        ['自动处理', autoStored, '即时记忆 / Wiki 提案 / 忽略'],
        ['人工审核', outputValues.inbox, isLoggedFlow ? '真实退回人工' : (flowPayload.source_mode === 'range' ? '历史补算' : '需要确认的内容')],
        ['回答失败', outputValues.issue, '未找到另看状态分布'],
      ];
      const flowTotal = [outputValues.mem0, outputValues.wiki, outputValues.inbox, outputValues.discard]
        .reduce((sum, value) => sum + (Number.isFinite(value) ? value : 0), 0);
      const model = {
        sources: isLoggedFlow ? [
          ['路由流水', loggedSourceValues.ledger, '#dce8d3', '#52733a'],
          ['待审积压', loggedSourceValues.backlog, '#efe1b8', '#8a6b1f'],
          ['回答轨迹', loggedSourceValues.trace, '#dbe8ee', '#526f7a'],
        ] : [
          ['即时记忆', sourceValues.daily, '#dce8d3', '#52733a'],
          ['收件箱', sourceValues.inbox, '#efe1b8', '#8a6b1f'],
          ['回答轨迹', sourceValues.trace, '#dbe8ee', '#526f7a'],
        ],
        outputs: [
          { key: 'mem0', label: '即时记忆', description: isLoggedFlow ? '实际写入 Mem0' : '近期事实与偏好', value: outputValues.mem0, bg: '#dce8d3', stroke: '#8fae72', text: '#52733a' },
          { key: 'wiki', label: 'Wiki 提案', description: isLoggedFlow ? '实际进入候选' : '长期知识候选', value: outputValues.wiki, bg: '#efe1b8', stroke: '#c7af67', text: '#8a6b1f' },
          { key: 'inbox', label: '人工审核', description: isLoggedFlow ? '真实退回人工' : (flowPayload.source_mode === 'range' ? '历史补算' : '需要确认的内容'), value: outputValues.inbox, bg: '#dbe8ee', stroke: '#6f8ea0', text: '#526f7a' },
          { key: 'discard', label: '忽略归档', description: isLoggedFlow ? '实际忽略' : '重复或低价值内容', value: outputValues.discard, bg: '#e4ded5', stroke: '#8e8073', text: '#6f6258' },
        ],
      };
      const formatFlowValue = (value) => Number.isFinite(value) ? c.numberText(value) : '缺日志';
      const summaryCards = flowSummaryCards.map(([label, value, hint]) => `
        <article class="tm-flow-stage-card rounded-xl border border-[#dfd3bc] bg-[#fbf7ef] px-3 py-2.5">
          <div class="text-[11px] leading-4 text-[#6f6258]">${c.esc(label)}</div>
          <div class="mt-0.5 text-xl font-semibold leading-7 text-[#1f1d1b]">${formatFlowValue(value)}</div>
          <div class="text-[11px] leading-4 text-[#8a8275]">${c.esc(hint)}</div>
        </article>
      `).join('');
      const sourceCards = model.sources.map(([label, value, bg, text]) => `
        <article data-flow-id="source-${label}" class="tm-flow-stage-card relative z-10 rounded-xl border border-[#dfd3bc] px-3 py-3" style="background:${bg}; color:${text}">
          <div class="flex items-center justify-between gap-2">
            <span class="text-sm font-semibold leading-5">${c.esc(label)}</span>
            <span class="rounded-full bg-[#fbf8f1]/80 px-2 py-0.5 text-[11px] leading-4 text-[#4a443d]">${formatFlowValue(value)}</span>
          </div>
          <div class="mt-2 h-1.5 rounded-full bg-[#e7ddc8]">
            <div class="h-full rounded-full" style="width:${sourceTotal ? Math.min(100, Math.round((value || 0) * 100 / sourceTotal)) : 0}%; background:${text}"></div>
          </div>
        </article>
      `).join('');
      const outputCards = model.outputs.map((slot) => {
        const knownValue = Number.isFinite(slot.value);
        const pct = knownValue && flowTotal > 0 ? Math.round((slot.value || 0) * 100 / flowTotal) : (knownValue ? 0 : null);
        return `
          <article data-flow-id="output-${slot.key}" class="tm-flow-output-card relative z-10 rounded-xl border border-[#dacdb0] px-3 py-2.5" style="background:${slot.bg}">
            <div class="flex items-center justify-between gap-2">
              <div class="min-w-0">
                <div class="text-sm font-semibold leading-5" style="color:${slot.text}">${c.esc(slot.label)}</div>
                <div class="text-[10px] leading-3 mt-0.5" style="color:${slot.text}; opacity: 0.8">${c.esc(slot.description)}</div>
              </div>
              <div class="text-right shrink-0">
                <div class="text-base font-bold leading-5" style="color:${slot.text}">
                  ${formatFlowValue(slot.value)}
                </div>
                <div class="text-[10px] leading-3 mt-0.5" style="color:${slot.text}; opacity: 0.8">${pct === null ? '--' : `${pct}%`}</div>
              </div>
            </div>
            <div class="mt-2 h-1.5 rounded-full bg-[#e7ddc8] overflow-hidden">
              <div class="h-full rounded-full" style="width:${pct === null ? 0 : pct}%; background:${slot.stroke}"></div>
            </div>
          </article>
        `;
      }).join('');
      if (routeSection) routeSection.classList.remove('hidden');
      // schedule draw
      setTimeout(() => { if (window.tmPages && window.tmPages.quality && window.tmPages.quality.drawFlowLines) window.tmPages.quality.drawFlowLines(); }, 50);
      routeBars.innerHTML = `
        <div class="space-y-4 min-w-0 max-w-full">
          ${historyNote ? `<div class="rounded-xl border border-[#dfd3bc] bg-[#fbf7ef] px-3 py-2 text-xs leading-5 text-[#6f6258]">${c.esc(historyNote)}</div>` : ''}
          <div class="tm-flow-summary-grid">${summaryCards}</div>
          <div class="tm-flow-map relative">
            <svg id="tm-flow-svg" class="absolute inset-0 pointer-events-none z-0" style="width: 100%; height: 100%;"></svg>
            
            <section class="tm-flow-stack relative z-10">
              <div>
                <div class="text-base font-semibold text-[#1f1d1b]">输入池</div>
                <div class="text-xs leading-5 text-[#665646]">写入候选与回答质检来源</div>
              </div>
              ${sourceCards}
            </section>

            <section data-flow-id="engine" class="tm-flow-engine-card text-center relative z-10 flex flex-col items-center justify-center">
              <div id="tiger-icon" class="relative group">
                <img src="/static/cute_tiger_guard.png" alt="智能分流卫兵" class="w-32 h-32 object-contain drop-shadow-xl transition-transform hover:scale-110" />
              </div>
              <div class="mt-4">
                <div class="text-base font-bold text-[#1f1d1b]">记忆管理虎</div>
              </div>
            </section>

            <section class="tm-flow-stack relative z-10">
              <div>
                <div class="text-base font-semibold text-[#1f1d1b]">输出去向</div>
                <div class="text-xs leading-5 text-[#665646]">四条写入路线同时展示，不把 0 项隐藏</div>
              </div>
              ${outputCards}
            </section>
          </div>
        </div>
      `;
    },

    renderStatusBars(memory) {
      const c = window.tmDashboard;
      const range = this.qualityRange(memory);
      const counts = (memory.trace_summary || {}).status_counts || {};
      const rows = [
        ['成功', Number(counts.ok || 0), 'ok'],
        ['未找到', Number(counts.not_found || 0), 'warn'],
        ['冲突', Number(counts.conflict || 0), 'warn'],
        ['错误', Number(counts.error || 0), 'fail'],
      ];
      const total = rows.reduce((sum, item) => sum + item[1], 0);
      const statusSection = document.getElementById('status-section');
      const statusBars = document.getElementById('status-bars');
      if (statusBars) {
        if (!total) {
          if (statusSection) statusSection.classList.remove('hidden');
          statusBars.innerHTML = `<div class="rounded-xl border border-[#d8c99f] bg-[#fbf7ef] p-4 text-sm leading-6 text-[#6f6258]">${c.esc(range.trace_label || '近 7 天')}回答轨迹还没有可统计状态；有新回答后这里显示成功、未找到、冲突和错误占比。</div>`;
          return;
        }
        if (statusSection) statusSection.classList.remove('hidden');
        statusBars.innerHTML = rows
          .filter(([, value]) => value > 0)
          .map(([label, value, status]) => {
            const pct = Math.round(value * 100 / total);
            const fillColor = status === 'ok' ? '#7fa06b' : status === 'fail' ? '#c9796f' : '#c8a560';
            return `
              <div>
                <div class="mb-2 flex items-center justify-between text-sm">
                  <span class="font-semibold text-[#1f1d1b]">${c.esc(label)}</span>
                  <span class="text-[#4a443d]">${value} / ${pct}%</span>
                </div>
                <div class="bar-track"><div class="bar-fill" style="width:${pct}%; background:${fillColor}"></div></div>
              </div>
            `;
          }).join('');
      }
    },

    renderFailures(memory) {
      const c = window.tmDashboard;
      const range = this.qualityRange(memory);
      const trace = memory.trace_summary || {};
      const intake = trace.real_failure_intake || {};
      const statusCounts = trace.status_counts || {};
      const traceTotal = Object.values(statusCounts).reduce((sum, value) => sum + Number(value || 0), 0);
      const intakeLatest = Array.isArray(intake.latest) ? intake.latest : [];
      const latest = (intakeLatest.length ? intakeLatest : (trace.latest || []).filter(item => item.status && item.status !== 'ok')).slice(0, 6);
      const candidateCount = Number(intake.candidate_count || latest.length || 0);
      const intakeStatusCounts = intake.status_counts || {};
      const sourceKindCounts = intake.source_kind_counts || {};
      const nextActions = Array.isArray(intake.next_actions) ? intake.next_actions.slice(0, 3) : [];
      const section = document.getElementById('failure-section');
      const list = document.getElementById('failure-list');
      if (!list) return;

      if (!traceTotal) {
        if (section) section.classList.remove('hidden');
        list.innerHTML = '<div class="rounded-xl border border-[#d8c99f] bg-[#fbf7ef] p-4 text-sm leading-6 text-[#6f6258]">P5 真实失败池暂无样本；有未找到、冲突、错误或失败记录后会进入这里。</div>';
        return;
      }
      if (section) section.classList.remove('hidden');
      if (!latest.length) {
        list.innerHTML = `<div class="rounded-2xl border border-[#a0b889] bg-[#dde8ce] p-4 text-sm text-[#52733a]">${c.esc(range.trace_label || '近 7 天')}未发现新的 P5 真实失败候选。</div>`;
        return;
      }
      const statusText = Object.entries(intakeStatusCounts)
        .filter(([, value]) => Number(value || 0) > 0)
        .map(([key, value]) => `${key}: ${value}`)
        .join(' / ') || '暂无分类';
      const sourceText = Object.entries(sourceKindCounts)
        .filter(([, value]) => Number(value || 0) > 0)
        .map(([key, value]) => `${key}: ${value}`)
        .join(' / ') || '暂无来源分类';
      const header = `
        <div class="rounded-2xl border border-[#d8c99f] bg-[#fbf7ef] p-4 text-sm leading-6 text-[#4a443d]">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <span class="font-semibold text-[#1f1d1b]">真实失败候选 ${c.numberText(candidateCount)}</span>
            <span class="text-xs text-[#8a8275]">${c.esc(range.trace_label || '近 7 天')}</span>
          </div>
          <div class="mt-1 text-xs text-[#6f6258]">状态分布：${c.esc(statusText)}</div>
          <div class="mt-1 text-xs text-[#6f6258]">来源分布：${c.esc(sourceText)}</div>
          ${nextActions.length ? `<ul class="mt-2 list-disc space-y-1 pl-5 text-xs text-[#6f6258]">${nextActions.map(action => `<li>${c.esc(action)}</li>`).join('')}</ul>` : ''}
        </div>
      `;
      const rows = latest.map(item => `
        <article class="rounded-2xl border border-[#e6dfcc] bg-[#f0e9d8] p-4">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="flex items-center gap-2">${c.statusBadge(item.status === 'error' ? 'fail' : 'warn', item.status)}<code class="text-xs text-[#4a443d]">${c.esc(item.trace_id || '未记录')}</code></div>
            <span class="text-xs text-[#8a8275]">${c.esc(item.ts || '')}</span>
          </div>
          <div class="mt-2 grid gap-1 text-xs text-[#4a443d] md:grid-cols-3">
            <div>问题类别 <code>${c.esc(item.query_class || '未记录')}</code></div>
            <div>耗时 <code>${Number.isFinite(Number(item.duration_ms)) ? `${c.esc(item.duration_ms)} ms` : '未记录'}</code></div>
            <div>模型 <code>${c.esc(item.llm || '未记录')}</code></div>
          </div>
          <div class="mt-2 text-xs text-[#8a8275]">query hash <code>${c.esc(item.query_hash || '未记录')}</code> · 来源 <code>${c.esc(item.source_kind || '未记录')}</code></div>
        </article>
      `).join('');
      list.innerHTML = header + rows;
    },

    renderRecommendationQuality(memory) {
      const c = window.tmDashboard;
      const range = this.qualityRange(memory);
      const quality = (memory.trace_summary || {}).recommendation_quality || {};
      const section = document.getElementById('recommendation-quality-section');
      const list = document.getElementById('recommendation-quality-list');
      if (!list) return;

      const summary = memory.trace_summary || {};
      const traceRows = Number(summary.row_count || 0);
      const shownCount = Number(quality.recommendation_shown_count || 0);
      const candidateCount = Number(quality.recommendation_candidate_count || 0);
      const boostCount = Number(quality.recommendation_boost_attempted_count || 0);
      const usedCount = Number(quality.recommendation_used_as_evidence_count || 0);
      const blockedCount = Number(quality.recommendation_blocked_by_gate_count || 0);
      const feedbackSummary = quality.feedback_summary || {};
      const feedbackActionCounts = feedbackSummary.action_counts || {};
      const clickedCount = Number(feedbackActionCounts.clicked || 0);
      const ignoredCount = Number(feedbackActionCounts.ignored || 0);
      const selectedCount = Number(feedbackActionCounts.selected || 0);
      const feedbackHasCounts = Number(feedbackSummary.event_count || 0) > 0 || Object.keys(feedbackActionCounts).length > 0;
      const statusCounts = quality.status_counts || {};
      const sidecarStatus = statusCounts.sidecar || {};
      const boostStatus = statusCounts.boost || {};
      const topNoisy = Array.isArray(quality.top_noisy_reasons) ? quality.top_noisy_reasons : [];

      if (section) section.classList.remove('hidden');

      if (
        !traceRows && !candidateCount && !boostCount && !shownCount && !usedCount && !blockedCount
        && !feedbackHasCounts && !Object.keys(sidecarStatus || {}).length && !Object.keys(boostStatus || {}).length && !topNoisy.length
      ) {
        list.innerHTML = `<div class="rounded-xl border border-[#d8c99f] bg-[#fbf7ef] p-4 text-sm leading-6 text-[#6f6258]">${c.esc(range.trace_label || '近 7 天')}暂无可聚合推荐指标；有新回答后展示侧栏建议与门禁分布。</div>`;
        return;
      }

      const formatStatusCounts = (label, items) => {
        const pairs = Object.entries(items)
          .sort(([, lhs], [, rhs]) => Number(rhs) - Number(lhs));
        if (!pairs.length) {
          return `<div class="text-xs text-[#8a8275]">${c.esc(label)}：暂无记录</div>`;
        }
        return `<div class="text-xs text-[#8a8275]">${c.esc(label)}：${pairs.map(([key, value]) => `${c.esc(key)}${Number(value) ? ` ${c.esc(String(value))}` : ''}`).join(' / ')}</div>`;
      };

      const noisyRows = topNoisy.length
        ? topNoisy.map(row => `<li class="rounded-md border border-[#d8c99f] bg-[#f9f2dc] px-2 py-1.5 text-xs">${c.esc(String(row.reason_category || '-'))} × ${c.esc(String(row.count || 0))}</li>`).join('')
        : `<li class="rounded-md border border-[#e6dfcc] bg-[#f0e9d8] px-2 py-1.5 text-xs text-[#8a8275]">最近未出现门禁拦截噪音。</li>`;
      const metricCards = [
        ['sidecar已展示', shownCount, `${c.esc(range.trace_label || '近 7 天')}有 sidecar候选`, 'warn'],
        ['related 推荐', candidateCount, `共发现 ${c.esc(c.numberText(candidateCount))} 条`, 'ok'],
        ['boost 尝试', boostCount, `已评估 ${c.esc(c.numberText(boostCount))} 行`, 'warn'],
        ['已用于证据', usedCount, '通过证据门禁后接入回答', usedCount ? 'ok' : 'warn'],
        ['被门禁拦截', blockedCount, '仅作降噪信号', blockedCount ? 'warn' : 'ok'],
      ];
      const feedbackCards = feedbackHasCounts
        ? [
            ['clicked', clickedCount, '人工点开推荐', clickedCount ? 'ok' : 'warn'],
            ['ignored', ignoredCount, '人工忽略推荐', ignoredCount ? 'warn' : 'ok'],
            ['selected', selectedCount, '人工选中推荐', selectedCount ? 'ok' : 'warn'],
          ]
        : [];
      const metricHtml = metricCards.map(([label, value, subtitle, status]) => `
        <article class="rounded-xl border border-[#dfd3bc] bg-[#fbf7ef] px-3 py-2.5">
          <div class="text-xs leading-4 text-[#6f6258]">${c.esc(label)}</div>
          <div class="mt-0.5 text-2xl font-semibold leading-7 text-[#1f1d1b]">${c.numberText(Number(value))}</div>
          <div class="mt-1 text-xs leading-4 ${status === 'warn' ? 'text-[#8a6b1f]' : 'text-[#52733a]'}">${c.esc(subtitle)}</div>
        </article>
      `).join('');
      const feedbackHtml = feedbackCards.length
        ? `
          <article class="rounded-xl border border-[#dfd3bc] bg-[#fbf7ef] p-3">
            <div class="text-sm font-semibold text-[#1f1d1b]">显式反馈</div>
            <div class="mt-2 grid gap-2 md:grid-cols-3">
              ${feedbackCards.map(([label, value, subtitle, status]) => `
                <div class="rounded-lg border border-[#e4d7bc] bg-[#fffaf0] px-2.5 py-2">
                  <div class="text-xs leading-4 text-[#6f6258]">${c.esc(label)}</div>
                  <div class="mt-0.5 text-xl font-semibold leading-6 text-[#1f1d1b]">${c.numberText(Number(value))}</div>
                  <div class="mt-1 text-xs leading-4 ${status === 'warn' ? 'text-[#8a6b1f]' : 'text-[#52733a]'}">${c.esc(subtitle)}</div>
                </div>
              `).join('')}
            </div>
          </article>
        `
        : '';
      const statusHtml = [
        formatStatusCounts('Sidecar 状态', sidecarStatus),
        formatStatusCounts('Boost 状态', boostStatus),
      ].join('');

      list.innerHTML = `
        <div class="space-y-3">
          <div class="grid gap-2 md:grid-cols-2">
            ${metricHtml}
          </div>
          ${feedbackHtml}
          <article class="rounded-xl border border-[#dfd3bc] bg-[#fbf7ef] p-3">
            <div class="text-sm font-semibold text-[#1f1d1b]">状态分布</div>
            <div class="mt-2 space-y-1">${statusHtml}</div>
          </article>
          <article class="rounded-xl border border-[#dfd3bc] bg-[#fbf7ef] p-3">
            <div class="text-sm font-semibold text-[#1f1d1b]">高频拦截原因</div>
            <ul class="mt-2 space-y-1">${noisyRows}</ul>
          </article>
        </div>
      `;
    },

    renderRetrievalRelease(memory) {
      const c = window.tmDashboard;
      const section = document.getElementById('retrieval-release-section');
      const list = document.getElementById('retrieval-release-list');
      if (!list) return;

      const payload = memory.retrieval_release || {};
      if (!payload.schema_version) {
        if (window.console && console.warn) console.warn('retrieval-release: schema_version missing');
        if (section) section.classList.add('hidden');
        return;
      }
      if (section) section.classList.remove('hidden');

      const latest = payload.latest || {};
      const production = latest.production || {};
      const mapArm = latest.map_arm || {};
      const deltas = payload.deltas || {};
      const flags = ((payload.flags || {}).flags) || {};
      const flagState = Boolean(payload.default_enabled);
      const decisionMap = {
        service_default_enabled: ['服务默认已启用', '当前运行服务已开启 map arm，并有最近 holdout 收益。', 'ok'],
        default_candidate: ['可作为默认候选', '最近 holdout 有净收益，但仍需要发布动作和回退确认。', 'ok'],
        candidate_with_leak_risk: ['候选但需复查', '答案命中提升了，但漏点没有同步下降。', 'warn'],
        keep_opt_in: ['继续实验开关', '最近 holdout 没有稳定净收益。', 'warn'],
        needs_production_baseline: ['缺生产对照', '只有 map arm 结果，缺最近 production 基线。', 'warn'],
        no_recent_map_arm_evidence: ['缺最近证据', '还没有最近 map arm holdout 结果。', 'warn'],
        artifact_error: ['证据读取失败', '最近 holdout 证据文件损坏或无法读取。', 'warn'],
      };
      const [decisionLabel, decisionHint, decisionStatus] = decisionMap[payload.decision] || ['待判断', payload.summary || '等待下一次 holdout 证据。', 'warn'];
      const statusColor = decisionStatus === 'ok' ? 'text-[#52733a]' : 'text-[#8a6b1f]';
      const answerDelta = Number.isFinite(Number(deltas.answer_evidence_hit)) ? Number(deltas.answer_evidence_hit) : null;
      const leakDelta = Number.isFinite(Number(deltas.map_hit_but_evidence_miss)) ? Number(deltas.map_hit_but_evidence_miss) : null;
      const truthyFlag = (value) => ['1', 'true', 'on', 'enabled', 'yes', 'force'].includes(String(value || '').trim().toLowerCase());
      const ratio = (row, key) => {
        const value = Number(row[key]);
        const total = Number(row.expected_path_case_count);
        if (!Number.isFinite(value)) return '缺数据';
        return Number.isFinite(total) && total > 0 ? `${c.numberText(value)} / ${c.numberText(total)}` : c.numberText(value);
      };
      const artifactName = (row) => {
        const artifact = String(row.artifact || '');
        return artifact ? artifact.split(/[\\/]/).slice(-2).join('/') : '未记录';
      };
      const flagHtml = [
        ['map arm', flagState ? '已启用' : '未启用', flagState ? 'ok' : 'warn'],
        ['summary', flags.TM_EMBED_SUMMARY_WEIGHT || '0', Number(flags.TM_EMBED_SUMMARY_WEIGHT || 0) > 0 ? 'warn' : 'ok'],
        ['bridge', flags.TM_ANSWER_WIKI_MAP_BRIDGE || '0', truthyFlag(flags.TM_ANSWER_WIKI_MAP_BRIDGE) ? 'warn' : 'ok'],
        ['旧 map', flags.TM_ANSWER_WIKI_MAP || '0', truthyFlag(flags.TM_ANSWER_WIKI_MAP) ? 'warn' : 'ok'],
      ].map(([label, value, status]) => `
        <span class="rounded-full border ${status === 'ok' ? 'border-[#a0b889] bg-[#dde8ce] text-[#52733a]' : 'border-[#d8c99f] bg-[#f4e6c4] text-[#8a6b1f]'} px-2 py-1 text-[11px] leading-4">
          ${c.esc(label)}：${c.esc(value)}
        </span>
      `).join('');
      const warnings = Array.isArray(payload.warnings) ? payload.warnings : [];
      const warningsHtml = warnings.length
        ? `<article class="rounded-xl border border-[#d49a91] bg-[#f0d6d2] p-3 text-xs leading-5 text-[#8a3527]">
            <div class="font-semibold">证据读取提醒</div>
            <ul class="mt-1 list-disc space-y-1 pl-4">${warnings.map(item => `<li>${c.esc(String(item))}</li>`).join('')}</ul>
          </article>`
        : '';

      list.innerHTML = `
        <article class="rounded-xl border border-[#dfd3bc] bg-[#fbf7ef] p-3">
          <div class="flex flex-wrap items-start justify-between gap-2">
            <div>
              <div class="text-sm font-semibold text-[#1f1d1b]">${c.esc(decisionLabel)}</div>
              <div class="mt-1 text-xs leading-5 ${statusColor}">${c.esc(payload.summary || decisionHint)}</div>
            </div>
            <span class="status-badge ${decisionStatus === 'ok' ? 'status-ok' : 'status-warn'}"><span class="status-dot"></span>${flagState ? '运行中' : '未默认'}</span>
          </div>
          <div class="mt-3 flex flex-wrap gap-2">${flagHtml}</div>
        </article>
        <article class="rounded-xl border border-[#dfd3bc] bg-[#fbf7ef] p-3">
          <div class="grid gap-2 md:grid-cols-2">
            <div class="rounded-lg border border-[#e4d7bc] bg-[#fffaf0] p-2">
              <div class="text-xs text-[#6f6258]">production 证据命中</div>
              <div class="mt-1 text-xl font-semibold text-[#1f1d1b]">${c.esc(ratio(production, 'answer_evidence_hit'))}</div>
              <div class="mt-1 break-words text-[11px] leading-4 text-[#8a8275]">${c.esc(artifactName(production))}</div>
            </div>
            <div class="rounded-lg border border-[#e4d7bc] bg-[#fffaf0] p-2">
              <div class="text-xs text-[#6f6258]">map arm 证据命中</div>
              <div class="mt-1 text-xl font-semibold text-[#1f1d1b]">${c.esc(ratio(mapArm, 'answer_evidence_hit'))}</div>
              <div class="mt-1 break-words text-[11px] leading-4 text-[#8a8275]">${c.esc(artifactName(mapArm))}</div>
            </div>
          </div>
          <div class="mt-3 grid gap-2 md:grid-cols-2">
            <div class="rounded-lg border border-[#e4d7bc] bg-[#fffaf0] px-2.5 py-2">
              <div class="text-xs text-[#6f6258]">证据命中变化</div>
              <div class="mt-1 text-lg font-semibold ${answerDelta !== null && answerDelta > 0 ? 'text-[#52733a]' : 'text-[#8a6b1f]'}">${answerDelta === null ? '缺数据' : `${answerDelta > 0 ? '+' : ''}${c.numberText(answerDelta)}`}</div>
            </div>
            <div class="rounded-lg border border-[#e4d7bc] bg-[#fffaf0] px-2.5 py-2">
              <div class="text-xs text-[#6f6258]">漏点变化</div>
              <div class="mt-1 text-lg font-semibold ${leakDelta !== null && leakDelta < 0 ? 'text-[#52733a]' : 'text-[#8a6b1f]'}">${leakDelta === null ? '缺数据' : `${leakDelta > 0 ? '+' : ''}${c.numberText(leakDelta)}`}</div>
            </div>
          </div>
        </article>
        <article class="rounded-xl border border-[#dfd3bc] bg-[#fbf7ef] p-3 text-xs leading-5 text-[#6f6258]">
          <div class="font-semibold text-[#1f1d1b]">回退方式</div>
          <div class="mt-1">${c.esc(payload.rollback || '关闭 TM_HYBRID_MAP_ARM 后重启服务。')}</div>
        </article>
        ${warningsHtml}
      `;
    },

    renderQualityEmptyState(memory) {
      const emptyState = document.getElementById('quality-empty-state');
      if (!emptyState) return;
      const range = this.qualityRange(memory);
      const counts = memory.counts || {};
      const statusCounts = (memory.trace_summary || {}).status_counts || {};
      const traceTotal = Object.values(statusCounts).reduce((sum, value) => sum + Number(value || 0), 0);
      const hasRouteCounts = Number.isFinite(Number(counts.mem0)) || Number(counts.inbox || 0) > 0 || Number(counts.discard || 0) > 0;
      const shouldShow = Boolean(memory.fallback_mode && !hasRouteCounts && traceTotal <= 0);
      emptyState.classList.toggle('hidden', !shouldShow);
      emptyState.textContent = shouldShow
        ? `${range.label}还没有可用于质量判断的实时写入或回答记录；页面先保留待确认数量和服务连接，等真实写入或审核发生后再展开明细。`
        : '';
    },

    renderFetchError(message) {
      const c = window.tmDashboard;
      const fallback = `<div class="rounded-2xl border border-[#d49a91] bg-[#f0d6d2] p-4 text-sm text-[#8a3527]">${c.esc(message)}</div>`;

      const kpiGrid = document.getElementById('kpi-grid');
      if (kpiGrid) kpiGrid.innerHTML = `<div class="card p-5 md:col-span-2 xl:col-span-4">${fallback}</div>`;

      const routeBars = document.getElementById('route-bars');
      if (routeBars) routeBars.innerHTML = fallback;

      const statusBars = document.getElementById('status-bars');
      if (statusBars) statusBars.innerHTML = fallback;

      const failureList = document.getElementById('failure-list');
      if (failureList) failureList.innerHTML = fallback;

      const tracePill = document.getElementById('trace-summary-pill');
      if (tracePill) {
        tracePill.className = 'status-badge status-warn';
        tracePill.innerHTML = `<span class="status-dot"></span>${c.esc(message)}`;
      }
    },

    async fetchQuality() {
      if (this.fetchQualityAbortController) this.fetchQualityAbortController.abort();
      if (this.prefetchQualityAbortController) this.prefetchQualityAbortController.abort();
      const requestId = ++this.fetchQualityRequestId;
      this.fetchQualityAbortController = new AbortController();
      try {
        const params = new URLSearchParams({ range: this.rangeKey || 'today' });
        const memoryResponse = await fetch(`/api/quality/memory?${params.toString()}`, { signal: this.fetchQualityAbortController.signal });
        if (requestId !== this.fetchQualityRequestId) return;
        const memory = await memoryResponse.json();
        this.prefetchedQualityRanges.add(((memory.range || {}).key) || this.rangeKey || 'today');
        this.render({ memory });
        this.prefetchQualityRanges();
      } catch (error) {
        if (error.name === 'AbortError') return;
        console.error(error);
        this.renderFetchError('数据暂时没取到，请稍后重试');
      }
    },

    async prefetchQualityRanges() {
      if (this.prefetchQualityAbortController) this.prefetchQualityAbortController.abort();
      const controller = new AbortController();
      this.prefetchQualityAbortController = controller;
      for (const rangeKey of ['7d', '30d']) {
        if (controller.signal.aborted) return;
        if (rangeKey === this.rangeKey || this.prefetchedQualityRanges.has(rangeKey)) continue;
        try {
          const params = new URLSearchParams({ range: rangeKey });
          const response = await fetch(`/api/quality/memory?${params.toString()}`, { signal: controller.signal });
          if (response.ok) {
            await response.json();
            this.prefetchedQualityRanges.add(rangeKey);
          }
        } catch (error) {
          if (error.name === 'AbortError') return;
          console.warn('quality range prefetch failed', rangeKey, error);
        }
      }
      if (this.prefetchQualityAbortController === controller) {
        this.prefetchQualityAbortController = null;
      }
    }
  };

  // Self-Evolution Page Controller
  window.tmPages.selfEvolution = {
    root: null,
    data: null,
    abortController: null,
    fetchRequestId: 0,
    fetchAbortController: null,

    init(root, data) {
      this.destroy();
      this.root = root;
      this.data = data || {};
      this.abortController = new AbortController();
      this.bindEvents();
      this.render(this.data);
      if (this.data.loading) {
        this.fetchData();
      }
    },

    destroy() {
      if (this.abortController) {
        this.abortController.abort();
        this.abortController = null;
      }
      if (this.fetchAbortController) {
        this.fetchAbortController.abort();
        this.fetchAbortController = null;
      }
      this.data = null;
    },

    bindEvents() {
      const refresh = document.getElementById('self-evolution-refresh');
      if (refresh) {
        refresh.addEventListener('click', () => this.fetchData({force: true}), {signal: this.abortController.signal});
      }
    },

    i18n(key, fallback) {
      return (window.tmI18n && window.tmI18n.t) ? window.tmI18n.t(key, fallback) : fallback;
    },

    badge(status, label) {
      return window.tmDashboard.statusBadge(status, label);
    },

    render(data) {
      this.data = data || {};
      const c = window.tmDashboard;
      const summary = this.data.summary || {};
      const proposals = Array.isArray(this.data.proposals) ? this.data.proposals : [];
      const proposalSummary = this.data.proposal_summary || {};
      const baseline = this.data.baseline || {};
      const refresh = document.getElementById('last-refresh');
      if (refresh) refresh.textContent = this.data.generated_at || this.data.date || '刚刚';
      const mode = document.getElementById('self-evolution-mode');
      if (mode) mode.textContent = this.data.mode || 'propose_only';
      const date = document.getElementById('self-evolution-date');
      if (date) date.textContent = this.data.date || '-';

      const notice = document.getElementById('self-evolution-notice');
      const warnings = [...(this.data.warnings || []), ...(this.data.errors || [])].filter(Boolean);
      if (notice) {
        const loadingMessage = this.data.loading ? '正在汇总自我进化证据。首次计算可能需要几十秒；缓存命中后会直接显示最近一次结果。' : '';
        notice.classList.toggle('hidden', !loadingMessage && warnings.length === 0);
        notice.textContent = loadingMessage || warnings.join('；');
      }

      if (this.data.loading) {
        this.renderLoadingState();
        if (window.lucide) window.lucide.createIcons();
        return;
      }

      const kpiGrid = document.getElementById('self-evolution-kpis');
      if (kpiGrid) {
        const eventCount = Number(summary.event_count || 0);
        const counts = summary.counts || {};
        const outcomePending = Number(summary.outcome_pending || 0);
        const eligible = Number(proposalSummary.eligible || 0);
        const status = baseline.status || 'unknown';
        kpiGrid.innerHTML = [
          c.kpiCard('database', '证据事件', c.numberText(eventCount), '来自 hook 证据日志，只读统计', eventCount ? 'ok' : 'warn'),
          c.kpiCard('repeat-2', '重复提案', c.numberText(eligible), `阈值 ${proposalSummary.min_repeats || 3} 次 / 置信 ${proposalSummary.min_confidence || 0.75}`, eligible ? 'warn' : 'ok'),
          c.kpiCard('hourglass', '待回填结果', c.numberText(outcomePending), 'helped / friction 等结果需后续证据确认', outcomePending ? 'warn' : 'ok'),
          c.kpiCard('shield-check', '基线状态', this.baselineLabel(status), `hook 阻塞 ${counts.hook_blocked || 0} / 交接缺失 ${counts.handoff_missing || 0}`, status === 'ok' ? 'ok' : 'warn'),
        ].join('');
      }

      this.renderSources(this.data.evidence_sources || {});
      this.renderProposals(proposals, proposalSummary);
      this.renderBaseline(baseline);
      this.renderSamples(summary.samples || []);
      if (window.lucide) window.lucide.createIcons();
    },

    renderLoadingState() {
      const kpiGrid = document.getElementById('self-evolution-kpis');
      if (kpiGrid) {
        kpiGrid.innerHTML = `
          <div class="rounded-lg border border-[#d8c99f] bg-[#fbf8f1] p-5 text-sm text-[#4a443d] md:col-span-2 xl:col-span-4">
            <div class="flex items-center gap-2 font-semibold text-[#1f1d1b]">
              <i data-lucide="loader-circle" class="h-4 w-4 text-[#c8a560]"></i>
              <span>正在准备自我进化报告</span>
            </div>
            <p class="mt-2 leading-6">后端正在读取 hook 证据、重复提案和遥测基线。首次冷启动会慢一些；返回前不会把空壳当作真实数据展示。</p>
          </div>
        `;
      }
      const sources = document.getElementById('self-evolution-sources');
      if (sources) sources.innerHTML = '';
      const proposals = document.getElementById('self-evolution-proposals');
      if (proposals) proposals.innerHTML = '';
      const proposalPill = document.getElementById('self-evolution-proposal-pill');
      if (proposalPill) {
        proposalPill.className = 'status-badge status-warn';
        proposalPill.innerHTML = '<span class="status-dot"></span>计算中';
      }
      const baseline = document.getElementById('self-evolution-baseline');
      if (baseline) baseline.innerHTML = '';
      const baselineStatus = document.getElementById('self-evolution-baseline-status');
      if (baselineStatus) baselineStatus.innerHTML = this.badge('warn', '计算中');
      const samples = document.getElementById('self-evolution-samples');
      if (samples) samples.innerHTML = '';
    },

    renderErrorState(message) {
      const c = window.tmDashboard;
      const kpiGrid = document.getElementById('self-evolution-kpis');
      if (kpiGrid) {
        kpiGrid.innerHTML = `
          <div class="rounded-lg border border-[#d49a91] bg-[#f0d6d2] p-5 text-sm text-[#8a3527] md:col-span-2 xl:col-span-4">
            <div class="flex items-center gap-2 font-semibold">
              <i data-lucide="circle-alert" class="h-4 w-4"></i>
              <span>自我进化数据暂时没取到</span>
            </div>
            <p class="mt-2 leading-6">${c.esc(message || '请稍后手动刷新。')}</p>
          </div>
        `;
      }
      const sources = document.getElementById('self-evolution-sources');
      if (sources) sources.innerHTML = '';
      const proposals = document.getElementById('self-evolution-proposals');
      if (proposals) proposals.innerHTML = '';
      const proposalPill = document.getElementById('self-evolution-proposal-pill');
      if (proposalPill) {
        proposalPill.className = 'status-badge status-warn';
        proposalPill.innerHTML = '<span class="status-dot"></span>数据未取到';
      }
      const baseline = document.getElementById('self-evolution-baseline');
      if (baseline) baseline.innerHTML = '';
      const baselineStatus = document.getElementById('self-evolution-baseline-status');
      if (baselineStatus) baselineStatus.innerHTML = this.badge('warn', '数据未取到');
      const samples = document.getElementById('self-evolution-samples');
      if (samples) samples.innerHTML = '';
      if (window.lucide) window.lucide.createIcons();
    },

    renderSources(sources) {
      const c = window.tmDashboard;
      const target = document.getElementById('self-evolution-sources');
      if (!target) return;
      const eventSources = Array.isArray(sources.events) ? sources.events : [];
      const telemetrySources = Array.isArray(sources.telemetry) ? sources.telemetry : [];
      const rows = [
        {
          title: '事件来源',
          items: eventSources,
          metric: item => `${c.numberText(item.event_count || 0)} 条事件`,
        },
        {
          title: '遥测来源',
          items: telemetrySources,
          metric: item => `${c.numberText(item.tool_calls || 0)} 次工具 / ${c.numberText(item.session_closes || 0)} 次收工`,
        },
      ].filter(group => group.items.length);
      if (!rows.length) {
        target.innerHTML = '';
        return;
      }
      target.innerHTML = rows.map(group => `
        <article class="rounded-lg border border-[#e6dfcc] bg-[#fbf8f1] p-4 text-sm">
          <div class="mb-3 flex items-center gap-2 font-semibold text-[#1f1d1b]">
            <i data-lucide="database" class="h-4 w-4 text-[#c8a560]"></i>
            <span>${c.esc(group.title)}</span>
          </div>
          <div class="space-y-2">
            ${group.items.map(item => `
              <div class="rounded-md border border-[#e6dfcc] bg-[#f0e9d8] px-3 py-2">
                <div class="flex flex-wrap items-center justify-between gap-2">
                  <b class="text-[#1f1d1b]">${c.esc(item.label || 'source')}</b>
                  ${this.badge(item.exists ? 'ok' : 'warn', item.exists ? group.metric(item) : '路径不可读')}
                </div>
                <div class="mt-1 break-all text-xs text-[#8a8275]">${c.esc(item.root || '')}</div>
              </div>
            `).join('')}
          </div>
        </article>
      `).join('');
    },

    baselineLabel(status) {
      const labels = {
        ok: '稳定',
        insufficient_tool_calls: '样本不足',
        insufficient_session_closes: '收工样本不足',
        'insufficient_tool_calls,insufficient_session_closes': '样本不足',
        loading: '加载中',
      };
      return labels[status] || status || '待观察';
    },

    renderProposals(proposals, summary) {
      const c = window.tmDashboard;
      const list = document.getElementById('self-evolution-proposals');
      const pill = document.getElementById('self-evolution-proposal-pill');
      const eligible = Number(summary && summary.eligible || 0);
      if (pill) {
        pill.className = `status-badge ${eligible ? 'status-warn' : 'status-ok'}`;
        pill.innerHTML = `<span class="status-dot"></span>${eligible ? `${eligible} 条待提案` : '暂无高频问题'}`;
      }
      if (!list) return;
      if (!proposals.length) {
        list.innerHTML = `<div class="rounded-md border border-[#e6dfcc] bg-[#fbf8f1] p-4 text-sm text-[#8a8275]">${c.esc(this.i18n('self_evolution.empty.proposals', '暂未发现需要提案的重复问题。'))}</div>`;
        return;
      }
      list.innerHTML = proposals.map(item => {
        const status = item.eligible_for_inbox ? 'warn' : 'ok';
        const key = item.key || {};
        const eventType = key.event_type || item.event_type || 'event';
        const source = key.source || item.source || 'unknown';
        const confidence = Math.round(Number(item.confidence || 0) * 100);
        const refs = (item.evidence_refs || []).slice(0, 4);
        return `
          <article class="rounded-lg border border-[#e6dfcc] bg-[#fbf8f1] p-4">
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div class="text-sm font-bold text-[#1f1d1b]">${c.esc(eventType)} · ${c.esc(source)}</div>
                <div class="mt-1 text-xs leading-5 text-[#8a8275]">${c.esc(item.reason || '重复证据达到提案阈值')}</div>
              </div>
              ${this.badge(status, item.eligible_for_inbox ? '建议进 inbox' : '观察')}
            </div>
            <div class="mt-3 grid gap-2 text-xs text-[#4a443d] sm:grid-cols-3">
              <div class="rounded border border-[#e6dfcc] bg-[#f0e9d8] p-2">次数：<b>${c.esc(item.repeat_count || item.count || 0)}</b></div>
              <div class="rounded border border-[#e6dfcc] bg-[#f0e9d8] p-2">置信：<b>${confidence}%</b></div>
              <div class="rounded border border-[#e6dfcc] bg-[#f0e9d8] p-2">动作：<b>${c.esc(item.recommended_action || 'propose')}</b></div>
            </div>
            ${refs.length ? `<div class="mt-3 flex flex-wrap gap-2">${refs.map(ref => `<code class="rounded bg-[#f0e9d8] px-2 py-1 text-[11px] text-[#6f5619]">${c.esc(ref)}</code>`).join('')}</div>` : ''}
          </article>
        `;
      }).join('');
    },

    renderBaseline(baseline) {
      const c = window.tmDashboard;
      const target = document.getElementById('self-evolution-baseline');
      const statusNode = document.getElementById('self-evolution-baseline-status');
      if (statusNode) {
        const status = baseline.status === 'ok' ? 'ok' : 'warn';
        statusNode.innerHTML = this.badge(status, this.baselineLabel(baseline.status));
      }
      if (!target) return;
      const counts = baseline.counts || {};
      const rates = baseline.rates || {};
      const rows = [
        ['总事件', counts.total_events],
        ['hook 阻塞', counts.hook_blocked],
        ['lesson 检索', counts.lesson_searched],
        ['交接缺失', counts.handoff_missing],
        ['工具调用', counts.tool_calls],
        ['会话收工', counts.session_closes],
        ['阻塞率', rates.hook_block_rate == null ? null : `${Math.round(Number(rates.hook_block_rate) * 1000) / 10}%`],
        ['交接缺失率', rates.handoff_missing_rate == null ? null : `${Math.round(Number(rates.handoff_missing_rate) * 1000) / 10}%`],
      ];
      target.innerHTML = rows.map(([label, value]) => `
        <div class="flex items-center justify-between gap-4 rounded-md border border-[#e6dfcc] bg-[#fbf8f1] px-3 py-2">
          <span class="text-[#8a8275]">${c.esc(label)}</span>
          <b class="text-[#1f1d1b]">${c.esc(value ?? '暂无')}</b>
        </div>
      `).join('');
    },

    renderSamples(samples) {
      const c = window.tmDashboard;
      const target = document.getElementById('self-evolution-samples');
      if (!target) return;
      if (!samples.length) {
        target.innerHTML = `<div class="rounded-md border border-[#e6dfcc] bg-[#fbf8f1] p-4 text-sm text-[#8a8275]">${c.esc(this.i18n('self_evolution.empty.samples', '暂无可展示的证据样本。'))}</div>`;
        return;
      }
      target.innerHTML = samples.slice(0, 8).map(item => `
        <article class="rounded-lg border border-[#e6dfcc] bg-[#fbf8f1] p-4 text-sm">
          <div class="mb-2 flex items-center justify-between gap-2">
            <b class="text-[#1f1d1b]">${c.esc(item.event_type || 'event')}</b>
            <span class="rounded-full border border-[#c9bf9f] bg-[#f0e9d8] px-2 py-1 text-xs text-[#4a443d]">${c.esc(item.source || 'unknown')}</span>
          </div>
          <div class="text-xs leading-5 text-[#8a8275]">${c.esc(item.created_at || item.timestamp || '')}</div>
          <div class="mt-2 text-[#4a443d]">${c.esc(item.summary || item.reason || item.message || '已记录证据')}</div>
        </article>
      `).join('');
    },

    async fetchData() {
      const requestId = ++this.fetchRequestId;
      if (this.fetchAbortController) this.fetchAbortController.abort();
      this.fetchAbortController = new AbortController();
      const timeout = setTimeout(() => this.fetchAbortController && this.fetchAbortController.abort(), 45000);
      try {
        const date = this.data && this.data.date ? this.data.date : new Date().toISOString().slice(0, 10);
        const response = await fetch(`/api/self-evolution/${encodeURIComponent(date)}`, {
          signal: this.fetchAbortController.signal,
          headers: {'Accept': 'application/json'}
        });
        const data = await response.json();
        if (requestId !== this.fetchRequestId) return;
        if (!data.ok) throw new Error(data.error || 'self-evolution data unavailable');
        this.render(data);
      } catch (error) {
        if (error.name === 'AbortError' && requestId !== this.fetchRequestId) return;
        const message = error.name === 'AbortError' ? '首次计算超过 45 秒，已停止等待；可以稍后手动刷新。' : error.message;
        const notice = document.getElementById('self-evolution-notice');
        if (notice) {
          notice.classList.remove('hidden');
          notice.textContent = `自我进化数据暂时没取到：${message}`;
        }
        this.renderErrorState(message);
      } finally {
        clearTimeout(timeout);
      }
    },
  };

  // Canvas Page Controller
  window.tmPages.canvas = {
    root: null,
    data: null,
    renderIndex: 0,
    mermaidLoadPromise: null,
    abortController: null,
    animationTimer: null,
    renderToken: 0,
    activeModules: [],
    mermaidEntries: [],
    roadmapStages: [],
    graphBounds: null,
    graphModel: null,
    technicalRendered: false,
    graphState: {
      x: 0,
      y: 0,
      scale: 1,
      targetX: 0,
      targetY: 0,
      targetScale: 1,
      vx: 0,
      vy: 0,
      vs: 0,
      dragging: false,
      lastX: 0,
      lastY: 0,
      moved: false,
      snapToTarget: false,
      rafId: 0,
    },
    graphWorld: {width: 1680, height: 1080},
    activeModuleIndex: null,
    canvasCandidates: [],
    currentMode: 'overview',

    init(root, data) {
      this.destroy();
      this.root = root;
      this.data = data || {};
      this.renderIndex += 1;
      this.renderToken = this.renderIndex;
      this.activeModules = Array.isArray(this.data && this.data.active_modules) ? this.data.active_modules : [];
      this.canvasCandidates = Array.isArray(this.data && this.data.canvas_candidates) ? this.data.canvas_candidates : [];
      this.mermaidEntries = this.extractMermaidEntries(this.data && this.data.mermaid_src);
      this.roadmapStages = this.extractRoadmapStages(this.data && this.data.mermaid_src);
      this.graphBounds = null;
      this.graphModel = null;
      this.technicalRendered = false;
      this.graphState = this.createGraphState();
      this.activeModuleIndex = null;
      this.currentMode = 'overview';
      this._viewportRect = null;
      this._viewportDirty = true;
      this._reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      this.abortController = new AbortController();
      this.bindEvents();
      this.render(this.data, this.renderToken);
    },

    destroy() {
      if (this.animationTimer) {
        clearTimeout(this.animationTimer);
        this.animationTimer = null;
      }
      if (this.abortController) {
        this.abortController.abort();
        this.abortController = null;
      }
      if (this.graphState && this.graphState.transformTimer) {
        clearTimeout(this.graphState.transformTimer);
      }
      if (this._resizeObserver) {
        this._resizeObserver.disconnect();
        this._resizeObserver = null;
      }
      const container = document.getElementById('canvas-mermaid');
      if (container) {
        container.innerHTML = '';
      }
      const roadmap = document.getElementById('canvas-roadmap');
      if (roadmap) {
        roadmap.innerHTML = '';
      }
      const moduleDetail = document.getElementById('canvas-module-detail');
      if (moduleDetail) {
        moduleDetail.innerHTML = '';
      }
      const candidates = document.getElementById('canvas-candidates');
      if (candidates) {
        candidates.innerHTML = '';
      }
      const modulePanel = document.getElementById('canvas-module-panel');
      if (modulePanel) {
        modulePanel.classList.add('hidden');
      }
      const technicalPanel = document.getElementById('canvas-technical-panel');
      if (technicalPanel) {
        technicalPanel.classList.add('hidden');
      }
      const overviewPanel = document.getElementById('canvas-overview-panel');
      if (overviewPanel) {
        overviewPanel.classList.remove('hidden');
      }
      this.activeModules = [];
      this.mermaidEntries = [];
      this.roadmapStages = [];
      this.graphBounds = null;
      this.graphModel = null;
      this.technicalRendered = false;
      this.graphState = this.createGraphState();
      this.activeModuleIndex = null;
      this.canvasCandidates = [];
      this.currentMode = 'overview';
      this.renderToken = 0;
      this.mermaidLoadPromise = null;
      this.data = null;
    },

    createGraphState() {
      return {
        x: 0,
        y: 0,
        scale: 1,
        transformTimer: null,
        wheelTimer: null,
        dragging: false,
        renderImmediate: false,
        lastX: 0,
        lastY: 0,
        moved: false,
      };
    },

    i18n(key, fallback, vars) {
      const raw = (window.tmI18n && window.tmI18n.t) ? window.tmI18n.t(key, fallback) : fallback;
      if (!vars || typeof raw !== 'string') return raw;
      return raw.replace(/\{([^}]+)\}/g, (match, name) => (
        Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : match
      ));
    },

    normalizeToken(value) {
      return String(value || '')
        .replace(/([a-z])([A-Z])/g, '$1 $2')
        .toLowerCase()
        .normalize('NFKC')
        .replace(/[\u200B-\u200D\uFEFF]/g, '')
        .replace(/[^a-zA-Z0-9\u4e00-\u9fff]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
    },

    extractMermaidEntries(source) {
      const entries = [];
      if (!source) return entries;

      const lines = String(source).split(/\r?\n/);
      const stateOpen = /^\s*state\s+([^\s{]+)\s*\{/;
      const entryLine = /^\s*([^:]+):\s*(.+)\s*$/;
      let inState = false;
      let currentState = '';

      for (const raw of lines) {
        const trimmed = String(raw || '').trim();
        if (!trimmed) continue;

        const open = trimmed.match(stateOpen);
        if (open) {
          inState = true;
          currentState = open[1] || '';
          continue;
        }
        if (!inState) continue;
        if (trimmed === '}') {
          inState = false;
          currentState = '';
          continue;
        }

        const row = trimmed.match(entryLine);
        if (!row) continue;
        const label = String(row[1] || '').trim();
        const text = String(row[2] || '').trim();
        if (!label || !text) continue;

        entries.push({
          state: currentState,
          label,
          text,
          raw: `${label}: ${text}`,
        });
      }

      return entries;
    },

    extractRoadmapStages(source) {
      if (!source) return [];
      const lines = String(source).split(/\r?\n/);
      const transition = /^\s*(?:\[\*\]|([A-Za-z0-9_]+))\s*-->\s*([A-Za-z0-9_]+)\s*:\s*(.+)\s*$/;
      const stages = [];
      const seen = new Set();

      for (const raw of lines) {
        const row = String(raw || '').match(transition);
        if (!row) continue;
        const id = row[2];
        if (!id || seen.has(id)) continue;
        seen.add(id);
        stages.push({
          id,
          status: String(row[3] || '').trim(),
          entries: [],
        });
      }

      const byId = new Map(stages.map(stage => [stage.id, stage]));
      for (const entry of this.mermaidEntries) {
        if (!byId.has(entry.state)) continue;
        byId.get(entry.state).entries.push(entry);
      }

      for (const entry of this.mermaidEntries) {
        if (byId.has(entry.state)) continue;
        if (seen.has(entry.state)) continue;
        seen.add(entry.state);
        const stage = {id: entry.state, status: '', entries: []};
        stage.entries.push(entry);
        stages.push(stage);
        byId.set(entry.state, stage);
      }

      return stages;
    },

    stageDisplayName(id) {
      return String(id || '')
        .replace(/^P(\d+)_/, 'P$1 · ')
        .replace(/_/g, ' ')
        .replace(/([a-z])([A-Z])/g, '$1 $2')
        .trim();
    },

    statusKind(value) {
      const text = String(value || '');
      if (text.includes('✅')) return 'done';
      if (text.includes('🟡') || text.includes('进行') || text.includes('审核')) return 'current';
      if (text.includes('🔴') || text.includes('阻塞')) return 'blocked';
      return 'pending';
    },

    stageForModuleName(name) {
      const normalized = this.normalizeToken(name);
      const aliases = [
        ['session handoff', 'P0_SessionHandoff'],
        ['handoff protocol', 'P0_SessionHandoff'],
        ['auto fallback', 'P1_AutoFallback'],
        ['rolling summary', 'P2_RollingSummary'],
        ['multi ide', 'P3_MultiIDE'],
        ['canonical policy', 'P3_MultiIDE'],
        ['openclaw ce', 'P3_MultiIDE'],
        ['hermes session', 'P3_MultiIDE'],
        ['mermaid task canvas', 'P3_MultiIDE'],
        ['dashboard project canvas', 'P3_MultiIDE'],
        ['runtime simplification', 'P4_RuntimeSimplification'],
        ['open source', 'P4_RuntimeSimplification'],
      ];
      const alias = aliases.find(([token]) => normalized.includes(token));
      return alias ? alias[1] : '';
    },

    collectModuleEntries(name) {
      const normalized = this.normalizeToken(name);
      if (!normalized) return [];

      const stageId = this.stageForModuleName(name);
      if (stageId) {
        return this.mermaidEntries
          .filter(entry => entry.state === stageId)
          .slice(0, 8);
      }

      const tokens = normalized.split(' ').filter(token => token.length > 1);
      const joined = tokens.join('');
      const result = [];
      const seen = new Set();
      for (const entry of this.mermaidEntries) {
        const hay = this.normalizeToken(`${entry.state} ${entry.label} ${entry.text}`);
        const hayJoined = hay.replace(/\s+/g, '');
        let score = 0;
        for (const token of tokens) {
          if (hay.includes(token) || hayJoined.includes(token)) score += 1;
        }
        if (joined && hayJoined.includes(joined)) score += 2;
        const threshold = Math.min(2, Math.max(1, Math.ceil(tokens.length / 3)));
        if (score < threshold && !hay.includes(normalized)) continue;
        const key = `${entry.state}::${entry.label}`;
        if (seen.has(key)) continue;
        seen.add(key);
        result.push({...entry, score});
      }

      return result
        .sort((a, b) => b.score - a.score || String(a.state).localeCompare(String(b.state)))
        .slice(0, 8);
    },

    bindEvents() {
      window.addEventListener('resize', () => {
        this._viewportDirty = true;
      }, { signal: this.abortController.signal });

      const overviewBtn = document.getElementById('canvas-view-overview');
      const modulesBtn = document.getElementById('canvas-view-modules');
      const technicalBtn = document.getElementById('canvas-view-technical');
      const backBtn = document.getElementById('canvas-back-overview');
      const modulesList = document.getElementById('canvas-modules');
      const roadmap = document.getElementById('canvas-roadmap');

      if (overviewBtn) {
        overviewBtn.addEventListener('click', () => this.switchMode('overview'), { signal: this.abortController.signal });
      }
      if (modulesBtn) {
        modulesBtn.addEventListener('click', () => this.switchMode('module'), { signal: this.abortController.signal });
      }
      if (technicalBtn) {
        technicalBtn.addEventListener('click', () => this.switchMode('technical'), { signal: this.abortController.signal });
      }
      if (backBtn) {
        backBtn.addEventListener('click', () => this.switchMode('overview'), { signal: this.abortController.signal });
      }
      if (modulesList) {
        modulesList.addEventListener('click', (event) => {
          const target = event.target.closest('[data-module-index]');
          if (!target) return;
          const index = Number.parseInt(target.dataset.moduleIndex || '', 10);
          if (Number.isNaN(index)) return;
          this.openModule(index);
        }, { signal: this.abortController.signal });
      }
      if (roadmap) {
        roadmap.addEventListener('click', (event) => {
          const action = event.target.closest('[data-graph-action]');
          if (action) {
            this.handleGraphAction(action.dataset.graphAction);
            return;
          }

          if (!event.target.closest('#canvas-graph-viewport')) return;
          if (this.graphState && this.graphState.moved) return;

          // 优先通过 DOM 元素识别被点击节点，比 hitGraphNode 精确坐标计算更稳定
          const nodeEl = event.target.closest('.canvas-graph-node');
          if (nodeEl) {
            const type = nodeEl.dataset.nodeType;
            const index = Number.parseInt(nodeEl.dataset.nodeIndex || '', 10);
            if (type === 'center') {
              this.fitGraph();
            } else if (type === 'stage') {
              const stageNode = this.graphModel && this.graphModel.stageNodes[index];
              if (stageNode) this.openGraphHit(stageNode);
            } else if (type === 'module') {
              const moduleNode = this.graphModel && this.graphModel.moduleNodes[index];
              if (moduleNode) this.openGraphHit(moduleNode);
            }
            return;
          }

          const hit = this.hitGraphNode(event.clientX, event.clientY);
          if (!hit) return;
          this.openGraphHit(hit);
        }, { signal: this.abortController.signal });

        roadmap.addEventListener('wheel', (event) => {
          const viewport = event.target.closest('#canvas-graph-viewport');
          if (!viewport) return;
          if (event.cancelable) event.preventDefault();
          event.stopPropagation();
          
          viewport.classList.add('is-transforming');
          if (this.graphState.wheelTimer) clearTimeout(this.graphState.wheelTimer);
          this.graphState.wheelTimer = setTimeout(() => {
            viewport.classList.remove('is-transforming');
          }, 150);

          // 极致优化: 如果是触控板双指缩放 (ctrlKey=true) 或是高频小刻度平滑滚动 (< 50)，
          // 代表输入源自带极高平滑度，无需套用任何 CSS 过渡，使用 immediate 1:1 响应。
          // 只有遇到传统的单格卡顿滚轮 (deltaY = 100+)，才使用 0.15s CSS 曲线进行插值补帧。
          const isSmoothTracking = event.ctrlKey || Math.abs(event.deltaY) < 50;
          const factor = Math.min(1.18, Math.max(0.84, Math.exp(-event.deltaY * 0.00075)));
          this.zoomGraph(factor, event.clientX, event.clientY, { immediate: isSmoothTracking });
        }, { signal: this.abortController.signal, passive: false, capture: true });

        roadmap.addEventListener('pointerdown', (event) => {
          const viewport = event.target.closest('#canvas-graph-viewport');
          if (!viewport || event.target.closest('.canvas-graph-controls')) return;
          this.graphState.dragging = true;
          this.graphState.moved = false;
          this.graphState.lastX = event.clientX;
          this.graphState.lastY = event.clientY;
          viewport.classList.add('is-dragging');
          viewport.setPointerCapture(event.pointerId);
        }, { signal: this.abortController.signal });

        roadmap.addEventListener('pointermove', (event) => {
          if (!this.graphState.dragging) return;
          const dx = event.clientX - this.graphState.lastX;
          const dy = event.clientY - this.graphState.lastY;
          if (Math.abs(dx) + Math.abs(dy) > 1) this.graphState.moved = true;
          const nextX = this.graphState.targetX + event.clientX - this.graphState.lastX;
          const nextY = this.graphState.targetY + event.clientY - this.graphState.lastY;
          this.graphState.lastX = event.clientX;
          this.graphState.lastY = event.clientY;
          this.setGraphTarget({x: nextX, y: nextY}, {snap: true});
        }, { signal: this.abortController.signal });

        const stopDrag = (event) => {
          if (!this.graphState.dragging) return;
          this.graphState.dragging = false;
          requestAnimationFrame(() => {
            if (this.graphState) this.graphState.moved = false;
          });
          const viewport = document.getElementById('canvas-graph-viewport');
          if (viewport) {
            viewport.classList.remove('is-dragging');
            try { viewport.releasePointerCapture(event.pointerId); } catch (_err) {}
          }
        };
        roadmap.addEventListener('pointerup', stopDrag, { signal: this.abortController.signal });
        roadmap.addEventListener('pointercancel', stopDrag, { signal: this.abortController.signal });
      }

      document.addEventListener('tm-lang-change', () => {
        this.renderRoadmap();
        this.renderModules(this.data, this.renderToken);
        this.renderModulePanel();
        this.renderModeUI();
      }, { signal: this.abortController.signal });
    },

    switchMode(mode) {
      const nextMode = mode === 'module' || mode === 'technical' ? mode : 'overview';
      if (nextMode === 'module' && this.activeModuleIndex == null && this.activeModules.length) {
        this.activeModuleIndex = 0;
      }
      this.currentMode = nextMode;
      this.renderModeUI();
      this.renderModulePanel();
      this.animateViewMode(nextMode);
      this.ensureTechnicalRendered();
    },

    openModule(index) {
      if (!this.activeModules[index]) return;
      this.activeModuleIndex = index;
      this.currentMode = 'module';
      this.renderModules(this.data, this.renderToken);
      this.renderModeUI();
      this.renderModulePanel();
      this.renderGraphDomOverlay();
      this.animateViewMode('module');
    },

    ensureTechnicalRendered() {
      if (this.currentMode !== 'technical' || this.technicalRendered) return;
      this.renderMermaid(this.data || {}, this.renderToken);
    },

    getActiveModule() {
      if (this.activeModuleIndex == null) return null;
      return this.activeModules[this.activeModuleIndex] || null;
    },

    renderModeUI() {
      const overviewBtn = document.getElementById('canvas-view-overview');
      const modulesBtn = document.getElementById('canvas-view-modules');
      const technicalBtn = document.getElementById('canvas-view-technical');
      const shell = document.getElementById('canvas-view-shell');
      const moduleMode = this.currentMode === 'module';
      const technicalMode = this.currentMode === 'technical';

      if (overviewBtn) {
        overviewBtn.classList.toggle('active', !moduleMode && !technicalMode);
        overviewBtn.setAttribute('aria-selected', String(!moduleMode && !technicalMode));
      }
      if (modulesBtn) {
        modulesBtn.classList.toggle('active', moduleMode);
        modulesBtn.setAttribute('aria-selected', String(moduleMode));
      }
      if (technicalBtn) {
        technicalBtn.classList.toggle('active', technicalMode);
        technicalBtn.setAttribute('aria-selected', String(technicalMode));
      }
      if (shell) {
        shell.setAttribute('data-mode', this.currentMode);
      }
    },

    animateViewMode(mode) {
      const overviewPanel = document.getElementById('canvas-overview-panel');
      const modulePanel = document.getElementById('canvas-module-panel');
      const technicalPanel = document.getElementById('canvas-technical-panel');
      if (!overviewPanel || !modulePanel || !technicalPanel) return;

      const panels = {overview: overviewPanel, module: modulePanel, technical: technicalPanel};
      const nextPanel = panels[mode] || overviewPanel;
      const prevPanel = [overviewPanel, modulePanel, technicalPanel].find(panel => !panel.classList.contains('hidden') && panel !== nextPanel) || overviewPanel;
      if (this.animationTimer) {
        clearTimeout(this.animationTimer);
        this.animationTimer = null;
      }

      if (prevPanel.classList.contains('hidden') || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        prevPanel.classList.add('hidden');
        prevPanel.classList.remove('canvas-view-panel-entering', 'canvas-view-panel-exiting');
        nextPanel.classList.remove('canvas-view-panel-entering', 'canvas-view-panel-exiting');
        nextPanel.classList.remove('hidden');
        return;
      }

      nextPanel.classList.add('canvas-view-panel-entering');
      nextPanel.classList.remove('hidden');
      requestAnimationFrame(() => {
        nextPanel.classList.remove('canvas-view-panel-entering');
        prevPanel.classList.add('canvas-view-panel-exiting');
      });
      this.animationTimer = setTimeout(() => {
        [overviewPanel, modulePanel, technicalPanel].forEach(panel => {
          if (panel !== nextPanel) panel.classList.add('hidden');
        });
        prevPanel.classList.remove('canvas-view-panel-exiting');
        nextPanel.classList.remove('canvas-view-panel-entering');
        this.animationTimer = null;
      }, 240);
    },

    renderState(data) {
      const statusEl = document.getElementById('canvas-status');
      const updatedEl = document.getElementById('canvas-updated');
      const sourceEl = document.getElementById('canvas-source');
      const infoEl = document.getElementById('canvas-info');

      if (statusEl) {
        if (data && data.ok) {
          statusEl.textContent = '已加载';
          statusEl.className = 'status-badge status-ok';
          statusEl.innerHTML = '<span class="status-dot"></span>已加载';
        } else {
          statusEl.textContent = '不可用';
          statusEl.className = 'status-badge status-warn';
          statusEl.innerHTML = '<span class="status-dot"></span>不可用';
        }
      }
      if (updatedEl) {
        updatedEl.textContent = (data && data.updated) || '-';
      }
      if (sourceEl) {
        sourceEl.textContent = (data && data.source_path) || '';
      }
      if (infoEl) {
        infoEl.textContent = (data && data.error) || (data && data.ok ? '' : '请检查源文件是否包含 mermaid 区块');
      }
    },

    clampGraphScale(value) {
      return Math.min(5, Math.max(0.12, value));
    },

    setGraphTarget(next, options) {
      const state = this.graphState;
      if (!state) return;
      const immediate = Boolean(options && options.immediate);
      
      if (Number.isFinite(next && next.scale)) {
        state.targetScale = this.clampGraphScale(next.scale);
      }
      if (Number.isFinite(next && next.x)) state.targetX = next.x;
      if (Number.isFinite(next && next.y)) state.targetY = next.y;

      // 视口边界 Clamp 限制，防止平移超出丢失星图
      const viewport = document.getElementById('canvas-graph-viewport');
      if (viewport && this.graphBounds) {
        const rect = this.getViewportRect(viewport);
        const bounds = this.graphBounds;
        const scale = state.targetScale;
        const padding = 100; // 预留 100px 边缘留在视口中

        const minX = padding - (bounds.x + bounds.width) * scale;
        const maxX = rect.width - padding - bounds.x * scale;
        const minY = padding - (bounds.y + bounds.height) * scale;
        const maxY = rect.height - padding - bounds.y * scale;

        if (minX < maxX) {
          state.targetX = Math.min(maxX, Math.max(minX, state.targetX));
        } else {
          state.targetX = Math.min(minX, Math.max(maxX, state.targetX));
        }

        if (minY < maxY) {
          state.targetY = Math.min(maxY, Math.max(minY, state.targetY));
        } else {
          state.targetY = Math.min(minY, Math.max(maxY, state.targetY));
        }
      }

      this.applyGraphTransform(immediate);
    },

    applyGraphTransform(immediate) {
      if (immediate && this.graphState) {
        this.graphState.renderImmediate = true;
      }
      if (this.graphState.renderPending) return;
      this.graphState.renderPending = true;

      requestAnimationFrame(() => {
        if (!this.graphState) return;
        this.graphState.renderPending = false;
        
        let needsNextFrame = false;

        // 极致平滑滤镜 (Lerp Filter): 隔离鼠标硬件刷新率与显示器刷新率
        // 拖拽时，不使用 CSS 缓动，而是使用紧致的高频插值 (0.65)，
        // 消除由于输入采样率不稳定引起的“瞬移/抖动”感，实现丝滑纯净的 1:1 跟随。
        if (this.graphState.dragging) {
          const dx = this.graphState.targetX - this.graphState.x;
          const dy = this.graphState.targetY - this.graphState.y;
          
          if (Math.abs(dx) > 0.05 || Math.abs(dy) > 0.05) {
            this.graphState.x += dx * 0.65;
            this.graphState.y += dy * 0.65;
            needsNextFrame = true;
          } else {
            this.graphState.x = this.graphState.targetX;
            this.graphState.y = this.graphState.targetY;
          }
        } else {
          this.graphState.x = this.graphState.targetX;
          this.graphState.y = this.graphState.targetY;
          this.graphState.scale = this.graphState.targetScale;
        }
        
        const isImmediate = this.graphState.renderImmediate || this.graphState.dragging;
        this.graphState.renderImmediate = false;

        const world = document.getElementById('canvas-graph-world');
        if (!world) return;

        const zoomLabel = document.getElementById('canvas-graph-zoom');
        if (zoomLabel) {
          const newText = `${Math.round(this.graphState.scale * 100)}%`;
          if (zoomLabel.textContent !== newText) {
            zoomLabel.textContent = newText;
          }
        }

        if (isImmediate || this._reducedMotion) {
          world.style.transition = 'none';
        } else {
          world.style.transition = 'transform 0.15s cubic-bezier(0.2, 0.8, 0.2, 1)';
        }

        world.style.transform = `translate3d(${this.graphState.x}px, ${this.graphState.y}px, 0) scale(${this.graphState.scale})`;
        
        if (needsNextFrame) {
          this.applyGraphTransform(true);
        }
      });
    },

    updateGraphTransform() {
      this.applyGraphTransform();
    },

    fitGraph(options) {
      const viewport = document.getElementById('canvas-graph-viewport');
      if (!viewport) return;
      const rect = this.getViewportRect(viewport);
      const world = this.graphWorld || {width: 1680, height: 1080};
      const bounds = this.graphBounds || {x: 0, y: 0, width: world.width, height: world.height};
      const rawScale = Math.min((rect.width - 48) / bounds.width, (rect.height - 48) / bounds.height);
      const minFitScale = rect.width < 560 ? 0.18 : 0.34;
      const scale = this.clampGraphScale(Math.min(1.08, Math.max(rawScale, minFitScale)));
      this.setGraphTarget({
        scale,
        x: (rect.width - bounds.width * scale) / 2 - bounds.x * scale,
        y: (rect.height - bounds.height * scale) / 2 - bounds.y * scale,
      }, {immediate: Boolean(options && options.immediate)});
    },

    resetGraph() {
      this.fitGraph();
    },

    zoomGraph(factor, clientX, clientY, options) {
      const viewport = document.getElementById('canvas-graph-viewport');
      if (!viewport) return;
      const rect = this.getViewportRect(viewport);
      const oldScale = this.graphState.targetScale || this.graphState.scale;
      const nextScale = this.clampGraphScale(oldScale * factor);
      if (Math.abs(nextScale - oldScale) < 0.0001) return;
      const focusX = Number.isFinite(clientX) ? clientX - rect.left : rect.width / 2;
      const focusY = Number.isFinite(clientY) ? clientY - rect.top : rect.height / 2;
      const baseX = Number.isFinite(this.graphState.targetX) ? this.graphState.targetX : this.graphState.x;
      const baseY = Number.isFinite(this.graphState.targetY) ? this.graphState.targetY : this.graphState.y;
      const localX = (focusX - baseX) / oldScale;
      const localY = (focusY - baseY) / oldScale;
      this.setGraphTarget({
        scale: nextScale,
        x: focusX - localX * nextScale,
        y: focusY - localY * nextScale,
      }, options);
    },

    handleGraphAction(action) {
      if (action === 'zoom-in') {
        this.zoomGraph(1.18);
      } else if (action === 'zoom-out') {
        this.zoomGraph(0.84);
      } else if (action === 'reset') {
        this.resetGraph();
      } else if (action === 'fit') {
        this.fitGraph();
      }
    },

    renderRoadmap() {
      const root = document.getElementById('canvas-roadmap');
      if (!root) return;
      const stages = this.roadmapStages || [];
      if (!stages.length) {
        root.innerHTML = `<div class="rounded-xl border border-[#e6dfcc] bg-[#f0e9d8] p-4 text-sm text-[#8a8275]">${this.i18n('page.canvas.roadmap_empty', '暂未解析到项目阶段。')}</div>`;
        return;
      }

      this.graphModel = this.buildGraphModel(stages);
      this.graphBounds = this.graphModel.bounds;
      root.innerHTML = `
        <div class="canvas-graph-toolbar">
          <div class="canvas-graph-hint text-sm text-[#8a8275]">${this.i18n('page.canvas.graph_hint', '拖拽空白处移动，滚轮缩放；点击节点查看详情。')}</div>
          <div class="canvas-graph-controls" aria-label="${window.tmDashboard.esc(this.i18n('page.canvas.graph_controls', '画布缩放控制'))}">
            <button type="button" class="canvas-graph-control" data-graph-action="zoom-out">${this.i18n('page.canvas.graph_zoom_out', '缩小')}</button>
            <span id="canvas-graph-zoom" class="px-2 text-xs font-bold text-[#8a8275]">100%</span>
            <button type="button" class="canvas-graph-control" data-graph-action="zoom-in">${this.i18n('page.canvas.graph_zoom_in', '放大')}</button>
            <button type="button" class="canvas-graph-control" data-graph-action="fit">${this.i18n('page.canvas.graph_fit', '适配')}</button>
            <button type="button" class="canvas-graph-control" data-graph-action="reset">${this.i18n('page.canvas.graph_reset', '复位')}</button>
          </div>
        </div>
        <div id="canvas-graph-viewport" class="canvas-graph-viewport">
          <div id="canvas-graph-world" class="canvas-graph-world">
            <canvas id="canvas-graph-canvas" class="canvas-graph-canvas" role="img" aria-label="${window.tmDashboard.esc(this.i18n('page.canvas.diagram_title', '项目状态图'))}"></canvas>
            <div id="canvas-graph-dom-overlay" class="canvas-graph-dom-overlay" aria-hidden="true"></div>
          </div>
        </div>
      `;
      this.renderGraphDomOverlay();
      this.drawGraphCanvas(); // 初次加载模型时绘制一次静态 Canvas 连线背景
      const viewport = document.getElementById('canvas-graph-viewport');
      if (viewport) {
        if (this._resizeObserver) {
          this._resizeObserver.disconnect();
        }
        this._resizeObserver = new ResizeObserver(() => {
          this._viewportDirty = true;
          // 仅在视口尺寸改变时重新适配，不重绘 canvas 像素，因为 canvas 像素是静态世界的
          this.fitGraph({immediate: true});
        });
        this._resizeObserver.observe(viewport);
      }

      const waitAndFit = () => {
        const vp = document.getElementById('canvas-graph-viewport');
        if (!vp) return;
        const rect = vp.getBoundingClientRect();
        if (rect.width < 10 || rect.height < 10) {
          requestAnimationFrame(waitAndFit);
          return;
        }
        this._viewportDirty = true;
        this.fitGraph({immediate: true});
      };
      requestAnimationFrame(waitAndFit);
    },

    renderGraphDomOverlay() {
      const overlay = document.getElementById('canvas-graph-dom-overlay');
      const worldEl = document.getElementById('canvas-graph-world');
      const model = this.graphModel;
      if (!overlay || !model) return;
      const c = window.tmDashboard;
      const statusText = {
        center: this.i18n('page.canvas.graph_center', '项目主画布'),
        done: '已完成',
        current: '进行中',
        blocked: '阻塞',
        pending: '待开工',
      };
      if (worldEl) {
        worldEl.style.width = `${model.world.width}px`;
        worldEl.style.height = `${model.world.height}px`;
      }
      overlay.style.width = `${model.world.width}px`;
      overlay.style.height = `${model.world.height}px`;
      overlay.innerHTML = model.nodes.map((node) => {
        const role = node.type === 'module' ? 'entry' : node.type;
        const title = (node.titleLines || []).join(' ');
        const meta = (node.subLines || []).join(' / ');
        const status = statusText[node.kind] || statusText.pending;
        return `
          <div class="canvas-graph-node ${role} is-${c.esc(node.kind || 'pending')}" style="left:${Math.round(node.x)}px;top:${Math.round(node.y)}px;width:${Math.round(node.box.width)}px;min-height:${Math.round(node.box.height)}px">
            <div class="canvas-graph-kicker">${c.esc(role === 'center' ? statusText.center : role === 'stage' ? '阶段' : '模块')}</div>
            <div class="canvas-graph-title">${c.esc(title || '-')}</div>
            <div class="canvas-graph-meta">${c.esc(meta || '-')}</div>
            <div class="canvas-graph-status"><span class="canvas-graph-status-mark"></span>${c.esc(status)}</div>
          </div>
        `;
      }).join('');
      this.updateGraphDomOverlay();
    },

    updateGraphDomOverlay() {
      const world = document.getElementById('canvas-graph-world');
      if (!world || !this.graphState) return;
      world.style.transform = `translate3d(${this.graphState.x}px, ${this.graphState.y}px, 0) scale(${this.graphState.scale})`;
    },

    buildGraphModel(stages) {
      const world = this.graphWorld || {width: 1680, height: 1080};
      const center = {x: world.width / 2, y: world.height / 2};
      const stageRadius = 275;
      const moduleRadius = 445;
      const totals = stages.reduce((acc, stage) => {
        const kind = this.statusKind(stage.status);
        acc.total += 1;
        acc[kind] = (acc[kind] || 0) + 1;
        return acc;
      }, {total: 0, done: 0, current: 0, pending: 0, blocked: 0});

      const textUnits = (value) => Array.from(String(value || '')).reduce((total, char) => {
        if (/[\u4e00-\u9fff]/.test(char)) return total + 1.05;
        if (/[A-Z0-9]/.test(char)) return total + 0.72;
        if (/\s/.test(char)) return total + 0.35;
        return total + 0.58;
      }, 0);
      const labelLines = (value, max = 18, maxLines = 2) => {
        const text = String(value || '').trim();
        if (!text) return ['-'];
        if (text.length <= max) return [text];
        const parts = text.split(/\s+/).filter(Boolean);
        if (parts.length > 1) {
          const lines = [];
          let current = '';
          for (const part of parts) {
            const next = current ? `${current} ${part}` : part;
            if (next.length > max && current) {
              lines.push(current);
              current = part;
            } else {
              current = next;
            }
            if (lines.length >= maxLines) break;
          }
          if (current) lines.push(current);
          const clipped = lines.slice(0, maxLines);
          if (clipped.join(' ').length < text.length && clipped.length) {
            clipped[clipped.length - 1] = `${clipped[clipped.length - 1].slice(0, Math.max(1, max - 1))}…`;
          }
          return clipped;
        }
        const lines = [];
        for (let index = 0; index < text.length && lines.length < maxLines; index += max) {
          lines.push(text.slice(index, index + max));
        }
        if (lines.join('').length < text.length && lines.length) {
          lines[lines.length - 1] = `${lines[lines.length - 1].slice(0, Math.max(1, max - 1))}…`;
        }
        return lines;
      };
      const nodeBox = (titleLines, subLines, variant) => {
        const settings = {
          center: {minW: 244, maxW: 310, minH: 118, padX: 28, padY: 22, titlePx: 14, subPx: 8, titleGap: 24, subGap: 16, radius: 22},
          stage: {minW: 188, maxW: 252, minH: 112, padX: 24, padY: 20, titlePx: 13.4, subPx: 7.6, titleGap: 19, subGap: 15, radius: 18},
          module: {minW: 166, maxW: 224, minH: 96, padX: 20, padY: 18, titlePx: 12.5, subPx: 7, titleGap: 17, subGap: 14, radius: 16},
        }[variant];
        const longestTitle = Math.max(...titleLines.map(textUnits), 1) * settings.titlePx;
        const longestSub = Math.max(...subLines.map(textUnits), 0) * settings.subPx;
        const width = Math.min(settings.maxW, Math.max(settings.minW, Math.max(longestTitle, longestSub) + settings.padX * 2));
        const titleHeight = Math.max(18, 18 + (titleLines.length - 1) * settings.titleGap);
        const subHeight = subLines.length ? 13 + (subLines.length - 1) * settings.subGap : 0;
        const contentHeight = titleHeight + (subLines.length ? 12 + subHeight : 0);
        const height = Math.max(settings.minH, contentHeight + settings.padY * 2);
        return {
          width,
          height,
          radius: settings.radius,
          titleY: -contentHeight / 2 + 15,
          subY: -contentHeight / 2 + 15 + titleHeight + 12,
          titleGap: settings.titleGap,
          subGap: settings.subGap,
        };
      };

      const stageNodes = stages.map((stage, index) => {
        const angle = -Math.PI / 2 + (Math.PI * 2 * index) / Math.max(stages.length, 1);
        const entries = stage.entries || [];
        const titleLines = labelLines(this.stageDisplayName(stage.id), 15, 2);
        const subLines = labelLines(entries.length ? `${entries.length} 项` : (stage.status || '-'), 22, 1);
        return {
          type: 'stage',
          index,
          stage,
          kind: this.statusKind(stage.status),
          x: center.x + Math.cos(angle) * stageRadius,
          y: center.y + Math.sin(angle) * stageRadius,
          angle,
          titleLines,
          subLines,
          box: nodeBox(titleLines, subLines, 'stage'),
        };
      });

      const stageIndexById = new Map(stageNodes.map(node => [node.stage.id, node.index]));
      const moduleBuckets = new Map();
      const moduleNodes = (this.activeModules || []).map((item, index) => {
        const stageId = this.stageForModuleName(item.module || '');
        const stageIndex = stageIndexById.has(stageId) ? stageIndexById.get(stageId) : index % Math.max(stageNodes.length, 1);
        const bucket = moduleBuckets.get(stageIndex) || [];
        bucket.push(index);
        moduleBuckets.set(stageIndex, bucket);
        return {item, index, stageIndex, bucketIndex: bucket.length - 1};
      }).map(point => {
        const bucket = moduleBuckets.get(point.stageIndex) || [];
        const stageNode = stageNodes[point.stageIndex] || stageNodes[0];
        const spread = Math.min(1.75, 0.55 * Math.max(bucket.length - 1, 0));
        const offset = bucket.length <= 1 ? 0 : -spread / 2 + (spread * point.bucketIndex) / (bucket.length - 1);
        const angle = stageNode.angle + offset;
        const cleanStatus = String(point.item.status || '').replace(/^[✅🟡⚪🔴]\s*/, '').trim();
        const titleLines = labelLines(point.item.module || '-', 14, 2);
        const subLines = labelLines(cleanStatus || point.item.owner || '-', 20, 1);
        return {
          type: 'module',
          index: point.index,
          stageIndex: point.stageIndex,
          item: point.item,
          kind: this.statusKind(point.item.status),
          x: center.x + Math.cos(angle) * moduleRadius,
          y: center.y + Math.sin(angle) * moduleRadius,
          titleLines,
          subLines,
          box: nodeBox(titleLines, subLines, 'module'),
        };
      });

      const centerSubLines = [
        this.i18n('page.canvas.graph_center', '项目主画布'),
        this.i18n('page.canvas.graph_summary', '{done} 已完成 / {active} 推进中 / {pending} 待开工', {done: totals.done || 0, active: (totals.current || 0) + (totals.blocked || 0), pending: totals.pending || 0}),
      ];
      const centerNode = {
        type: 'center',
        kind: 'center',
        x: center.x,
        y: center.y,
        titleLines: ['TigerMemory'],
        subLines: centerSubLines,
        box: nodeBox(['TigerMemory'], centerSubLines, 'center'),
      };

      const allNodes = [centerNode, ...stageNodes, ...moduleNodes];

      // 物理碰撞松弛算法 (Relaxation Algorithm for AABB collision)
      // 避免任何项目模块/卡片之间产生视觉重叠，并保持优雅的“呼吸感”间距
      const spacing = 64;
      const iterations = 96;

      for (let iter = 0; iter < iterations; iter++) {
        let moved = false;
        for (let i = 0; i < allNodes.length; i++) {
          const nodeA = allNodes[i];
          for (let j = i + 1; j < allNodes.length; j++) {
            const nodeB = allNodes[j];

            const halfW_A = nodeA.box.width / 2;
            const halfH_A = nodeA.box.height / 2;
            const halfW_B = nodeB.box.width / 2;
            const halfH_B = nodeB.box.height / 2;

            const min_dx = halfW_A + halfW_B + spacing;
            const min_dy = halfH_A + halfH_B + spacing;

            let dx = nodeB.x - nodeA.x;
            let dy = nodeB.y - nodeA.y;
            if (dx === 0 && dy === 0) {
              const angle = ((j + 1) * 2.399963229728653) % (Math.PI * 2);
              nodeB.x += Math.cos(angle) * 0.75;
              nodeB.y += Math.sin(angle) * 0.75;
              dx = nodeB.x - nodeA.x;
              dy = nodeB.y - nodeA.y;
            }

            const abs_dx = Math.abs(dx);
            const abs_dy = Math.abs(dy);

            if (abs_dx < min_dx && abs_dy < min_dy) {
              const overlapX = min_dx - abs_dx;
              const overlapY = min_dy - abs_dy;

              let pushX = 0;
              let pushY = 0;

              if (overlapX < overlapY) {
                pushX = overlapX * (dx >= 0 ? 1 : -1);
              } else {
                pushY = overlapY * (dy >= 0 ? 1 : -1);
              }

              if (nodeA.type === 'center') {
                nodeB.x += pushX;
                nodeB.y += pushY;
              } else if (nodeB.type === 'center') {
                nodeA.x -= pushX;
                nodeA.y -= pushY;
              } else {
                nodeA.x -= pushX * 0.5;
                nodeA.y -= pushY * 0.5;
                nodeB.x += pushX * 0.5;
                nodeB.y += pushY * 0.5;
              }
              moved = true;
            }
          }
        }
        if (!moved) break;
      }

      const minX = Math.min(...allNodes.map(node => node.x - node.box.width / 2)) - 54;
      const maxX = Math.max(...allNodes.map(node => node.x + node.box.width / 2)) + 54;
      const minY = Math.min(...allNodes.map(node => node.y - node.box.height / 2)) - 54;
      const maxY = Math.max(...allNodes.map(node => node.y + node.box.height / 2)) + 54;

      const shiftX = minX < 0 ? Math.abs(minX) + 50 : 0;
      const shiftY = minY < 0 ? Math.abs(minY) + 50 : 0;

      if (shiftX > 0 || shiftY > 0) {
        for (const node of allNodes) {
          node.x += shiftX;
          node.y += shiftY;
        }
        center.x += shiftX;
        center.y += shiftY;
      }

      world.width = Math.max(1680, maxX + shiftX + 50);
      world.height = Math.max(1080, maxY + shiftY + 50);

      return {
        world,
        center,
        stageRadius,
        moduleRadius,
        nodes: allNodes,
        stageNodes,
        moduleNodes,
        bounds: {x: 0, y: 0, width: world.width, height: world.height},
      };
    },

    getViewportRect(viewport) {
      if (!this._viewportRect || this._viewportDirty) {
        this._viewportRect = viewport.getBoundingClientRect();
        this._viewportDirty = false;
      }
      return this._viewportRect;
    },

    drawGraphCanvas() {
      const canvas = document.getElementById('canvas-graph-canvas');
      const model = this.graphModel;
      if (!canvas || !model) return;

      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const width = model.world.width;
      const height = model.world.height;

      if (canvas.width !== width * dpr || canvas.height !== height * dpr) {
        canvas.width = width * dpr;
        canvas.height = height * dpr;
        canvas.style.width = `${width}px`;
        canvas.style.height = `${height}px`;
      }

      const ctx = canvas.getContext('2d', {alpha: true});
      if (!ctx) return;

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);
      ctx.save();

      // 在 1:1 静态世界坐标下绘制背景线和圈
      this.drawGraphHalo(ctx, model.center.x, model.center.y, model.stageRadius);
      this.drawGraphHalo(ctx, model.center.x, model.center.y, model.moduleRadius);
      for (const node of model.stageNodes) {
        this.drawGraphCurve(ctx, model.center, node, true);
      }
      for (const node of model.moduleNodes) {
        const stage = model.stageNodes[node.stageIndex] || model.stageNodes[0];
        if (stage) this.drawGraphCurve(ctx, stage, node, false);
      }
      ctx.restore();
    },

    drawGraphHalo(ctx, x, y, radius) {
      ctx.save();
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.setLineDash([8, 12]);
      ctx.lineWidth = 1.2;
      ctx.strokeStyle = 'rgba(200, 165, 96, .18)';
      ctx.stroke();
      ctx.restore();
    },

    drawGraphCurve(ctx, from, to, strong) {
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(from.x, from.y);
      ctx.bezierCurveTo(from.x, to.y, to.x, from.y, to.x, to.y);
      ctx.lineWidth = strong ? 2.1 : 1.35;
      ctx.strokeStyle = strong ? 'rgba(200, 165, 96, .42)' : 'rgba(138, 130, 117, .22)';
      ctx.stroke();
      ctx.restore();
    },

    roundRectPath(ctx, x, y, width, height, radius) {
      const r = Math.min(radius, width / 2, height / 2);
      ctx.beginPath();
      ctx.moveTo(x + r, y);
      ctx.arcTo(x + width, y, x + width, y + height, r);
      ctx.arcTo(x + width, y + height, x, y + height, r);
      ctx.arcTo(x, y + height, x, y, r);
      ctx.arcTo(x, y, x + r, y, r);
      ctx.closePath();
    },

    hitGraphNode(clientX, clientY) {
      const viewport = document.getElementById('canvas-graph-viewport');
      const model = this.graphModel;
      if (!viewport || !model || !Number.isFinite(clientX) || !Number.isFinite(clientY)) return null;
      const rect = viewport.getBoundingClientRect();
      const scale = this.graphState.scale || 1;
      const x = (clientX - rect.left - this.graphState.x) / scale;
      const y = (clientY - rect.top - this.graphState.y) / scale;
      for (let index = model.nodes.length - 1; index >= 0; index -= 1) {
        const node = model.nodes[index];
        if (
          x >= node.x - node.box.width / 2 &&
          x <= node.x + node.box.width / 2 &&
          y >= node.y - node.box.height / 2 &&
          y <= node.y + node.box.height / 2
        ) {
          return node;
        }
      }
      return null;
    },

    openGraphHit(node) {
      if (!node) return;
      if (node.type === 'center') {
        this.fitGraph();
        return;
      }
      if (node.type === 'module') {
        this.openModule(node.index);
        return;
      }
      if (node.type === 'stage') {
        this.activeModuleIndex = null;
        this.currentMode = 'module';
        this.renderModules(this.data, this.renderToken);
        this.renderModeUI();
        this.renderStagePanel(node.stage);
        this.renderGraphDomOverlay();
        this.animateViewMode('module');
      }
    },

    renderModules(data) {
      const listEl = document.getElementById('canvas-modules');
      const countEl = document.getElementById('canvas-module-count');
      const modules = Array.isArray(data && data.active_modules) ? data.active_modules : [];
      if (countEl) {
        countEl.textContent = this.i18n('page.canvas.module_count_value', `共 ${modules.length} 项`, {count: modules.length});
      }
      if (!listEl) return;

      if (!modules.length) {
        listEl.innerHTML = `<div class="rounded-xl border border-[#e6dfcc] bg-[#f0e9d8] p-3 text-sm text-[#8a8275]">${this.i18n('page.canvas.modules_empty', '暂无活跃模块摘要，请先更新画布。')}</div>`;
        return;
      }

      listEl.innerHTML = modules.map((item, index) => `
        <button type="button" data-module-index="${index}" class="canvas-module-item ${index === this.activeModuleIndex && this.currentMode === 'module' ? 'active' : ''}" aria-pressed="${index === this.activeModuleIndex && this.currentMode === 'module' ? 'true' : 'false'}">
          <div class="canvas-module-heading text-sm text-[#1f1d1b]">
            <span class="canvas-module-name font-semibold">${window.tmDashboard.esc(item.module || '-')}</span>
            <span class="canvas-module-status rounded-full border border-[#e6dfcc] px-2 py-0.5 text-[11px] text-[#8a8275]">${window.tmDashboard.esc(item.status || '-')}</span>
          </div>
          <div class="mt-1 text-xs text-[#8a8275]">${this.i18n('page.canvas.module_owner', '负责人')}：${window.tmDashboard.esc(item.owner || '-')}</div>
          <div class="mt-1 text-xs text-[#8a8275]">${this.i18n('page.canvas.module_updated', '最近更新')}：${window.tmDashboard.esc(item.updated || '-')}</div>
        </button>
      `).join('');
    },

    renderCandidates(data) {
      const listEl = document.getElementById('canvas-candidates');
      const countEl = document.getElementById('canvas-candidate-count');
      const candidates = Array.isArray(data && data.canvas_candidates) ? data.canvas_candidates : [];
      const warnings = Array.isArray(data && data.candidate_warnings) ? data.candidate_warnings : [];
      if (countEl) {
        countEl.textContent = `${candidates.length} 项`;
      }
      if (!listEl) return;

      const warningHtml = warnings.length
        ? warnings.map((warning) => `<div class="rounded-lg border border-[#d8cfba] bg-[#fffaf0] px-3 py-2 text-xs leading-5 text-[#8a6b1f]">${window.tmDashboard.esc(warning)}</div>`).join('')
        : '';

      if (!candidates.length) {
        listEl.innerHTML = [
          warningHtml,
          `<div class="rounded-xl border border-dashed border-[#d8cfba] bg-[#f7f3ea] p-3 text-xs leading-5 text-[#8a8275]">暂无待纳入候选。</div>`,
        ].filter(Boolean).join('');
        return;
      }

      const cards = candidates.map((item) => {
        const state = item.review_state || '建议纳入';
        const name = item.name || item.target_module || item.summary || 'project-canvas';
        const summary = item.summary || item.reason || '-';
        const targetModule = item.target_module || 'project-canvas';
        const reason = item.reason || '-';
        const evidenceCount = Number.isFinite(Number(item.evidence_count)) ? Number(item.evidence_count) : 0;
        const source = item.source || item.candidate_source || '-';
        const confidence = item.confidence || '-';
        return `
          <article class="min-w-0 rounded-xl border border-dashed border-[#d8cfba] bg-[#f7f3ea]/80 p-3 text-left shadow-none">
            <div class="mb-2 flex min-w-0 flex-wrap items-start justify-between gap-2">
              <div class="min-w-0">
                <div class="break-words text-sm font-semibold leading-5 text-[#4a443d]">${window.tmDashboard.esc(name)}</div>
                <div class="mt-1 break-words text-xs leading-5 text-[#8a8275]">${window.tmDashboard.esc(summary)}</div>
              </div>
              <span class="shrink-0 rounded-full border border-[#d8cfba] bg-[#fffaf0] px-2 py-0.5 text-[11px] font-semibold text-[#8a6b1f]">${window.tmDashboard.esc(state)}</span>
            </div>
            <div class="grid gap-1 text-[11px] leading-5 text-[#8a8275]">
              <div class="break-words"><span class="font-semibold text-[#6f665a]">目标模块：</span>${window.tmDashboard.esc(targetModule)}</div>
              <div class="break-words"><span class="font-semibold text-[#6f665a]">原因：</span>${window.tmDashboard.esc(reason)}</div>
              <div class="flex min-w-0 flex-wrap gap-x-3 gap-y-1">
                <span>证据 ${window.tmDashboard.esc(evidenceCount)} 条</span>
                <span class="break-words">来源 ${window.tmDashboard.esc(source)}</span>
                <span>置信度 ${window.tmDashboard.esc(confidence)}</span>
              </div>
            </div>
          </article>
        `;
      }).join('');

      listEl.innerHTML = [warningHtml, cards].filter(Boolean).join('');
    },

    renderStagePanel(stage) {
      const detail = document.getElementById('canvas-module-detail');
      if (!detail || !stage) return;
      const entries = (stage.entries || []).slice(0, 10);
      const entryNodes = entries.length
        ? entries.map(entry => `
            <li class="rounded-md border border-[#e6dfcc] bg-[#f0e9d8] px-3 py-2 text-sm text-[#4a443d]">
              <div class="font-semibold text-[#1f1d1b]">${window.tmDashboard.esc(entry.label || '-')}</div>
              <div class="mt-1">${window.tmDashboard.esc(entry.text || '')}</div>
            </li>
          `).join('')
        : `<li class="rounded-md border border-[#e6dfcc] bg-[#f0e9d8] px-3 py-2 text-sm text-[#4a443d]">${this.i18n('page.canvas.stage_no_items', '暂无细分条目')}</li>`;
      detail.innerHTML = `
        <section class="rounded-xl border border-[#e6dfcc] bg-[#fbf8f1] p-4">
          <div class="mb-2 text-sm text-[#1f1d1b]">
            <span class="font-semibold">${this.i18n('page.canvas.stage_name', '阶段')}：</span>
            <span>${window.tmDashboard.esc(this.stageDisplayName(stage.id))}</span>
          </div>
          <div class="text-xs text-[#8a8275]">${this.i18n('page.canvas.module_status_label', '状态')}：${window.tmDashboard.esc(stage.status || '-')}</div>
        </section>
        <section class="rounded-xl border border-[#e6dfcc] bg-[#fbf8f1] p-4">
          <div class="text-sm font-semibold text-[#1f1d1b]">${this.i18n('page.canvas.module_items', '关键条目')}</div>
          <ul class="mt-2 space-y-2">${entryNodes}</ul>
        </section>
      `;
    },

    renderModulePanel() {
      const detail = document.getElementById('canvas-module-detail');
      if (!detail) return;

      const item = this.getActiveModule();
      if (!item) {
        detail.innerHTML = `<p class="text-sm text-[#8a8275]">${this.i18n('page.canvas.module_no_selection', '请先从右侧“活跃模块”点击任一模块查看。')}</p>`;
        return;
      }

      const moduleName = item.module || '-';
      const status = item.status || '-';
      const owner = item.owner || '-';
      const updated = item.updated || '-';
      const detailItems = this.collectModuleEntries(moduleName);
      const entryNodes = detailItems.length
        ? detailItems.map(entry => `
            <li class="rounded-md border border-[#e6dfcc] bg-[#f0e9d8] px-3 py-2 text-sm text-[#4a443d]">
              <div class="font-semibold text-[#1f1d1b]">${window.tmDashboard.esc(entry.label || '-')}</div>
              <div class="mt-1">${window.tmDashboard.esc(entry.text || '')}</div>
              <div class="mt-1 text-xs text-[#8a8275]">${window.tmDashboard.esc(entry.state || '')}</div>
            </li>
          `).join('')
        : `
            <li class="rounded-md border border-[#e6dfcc] bg-[#f0e9d8] px-3 py-2 text-sm text-[#4a443d]">${this.i18n('page.canvas.module_status_label', '状态')}：${window.tmDashboard.esc(status)}</li>
            <li class="rounded-md border border-[#e6dfcc] bg-[#f0e9d8] px-3 py-2 text-sm text-[#4a443d]">${this.i18n('page.canvas.module_owner', '负责人')}：${window.tmDashboard.esc(owner)}</li>
            <li class="rounded-md border border-[#e6dfcc] bg-[#f0e9d8] px-3 py-2 text-sm text-[#4a443d]">${this.i18n('page.canvas.module_updated', '最近更新')}：${window.tmDashboard.esc(updated)}</li>
          `;

      detail.innerHTML = `
        <section class="rounded-xl border border-[#e6dfcc] bg-[#fbf8f1] p-4">
          <div class="mb-2 text-sm text-[#1f1d1b]">
            <span class="font-semibold">${this.i18n('page.canvas.module_name', '模块名')}：</span>
            <span>${window.tmDashboard.esc(moduleName)}</span>
          </div>
          <div class="grid gap-2 text-xs text-[#8a8275] sm:grid-cols-2">
            <div>${this.i18n('page.canvas.module_status_label', '状态')}：${window.tmDashboard.esc(status)}</div>
            <div>${this.i18n('page.canvas.module_owner', '负责人')}：${window.tmDashboard.esc(owner)}</div>
            <div>${this.i18n('page.canvas.module_updated', '最近更新')}：${window.tmDashboard.esc(updated)}</div>
          </div>
        </section>
        <section class="rounded-xl border border-[#e6dfcc] bg-[#fbf8f1] p-4">
          <div class="text-sm font-semibold text-[#1f1d1b]">${this.i18n('page.canvas.module_items', '关键条目')}</div>
          <ul class="mt-2 space-y-2">${entryNodes}</ul>
        </section>
      `;
    },

    async loadMermaid() {
      if (window.mermaid) return;
      if (this.mermaidLoadPromise) return this.mermaidLoadPromise;

      this.mermaidLoadPromise = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = '/static/assets/mermaid.min.js';
        script.async = true;
        script.crossOrigin = 'anonymous';
        script.onload = () => resolve();
        script.onerror = () => reject(new Error('Mermaid 脚本加载失败'));
        document.head.appendChild(script);
      });
      await this.mermaidLoadPromise;
    },

    async renderMermaid(data, token) {
      const container = document.getElementById('canvas-mermaid');
      const emptyEl = document.getElementById('canvas-empty');
      if (!container || !emptyEl) return;
      container.innerHTML = '';

      if (!data.ok || !data.mermaid_src) {
        emptyEl.classList.remove('hidden');
        container.innerHTML = `<div class="rounded-xl border border-[#d49a91] bg-[#f0d6d2] p-4 text-sm text-[#8a3527]">${this.i18n('page.canvas.render_missing', '未读取到可用的 Mermaid 代码块。')}</div>`;
        return;
      }

      try {
        await this.loadMermaid();
        if (!window.mermaid || token !== this.renderToken) return;
        window.mermaid.initialize({ startOnLoad: false, securityLevel: 'strict' });
        const renderId = `tm-canvas-${token}-${Date.now()}`;
        const result = await window.mermaid.render(renderId, data.mermaid_src, container);
        if (token !== this.renderToken) return;

        const svg = typeof result === 'string' ? result : (result && result.svg);
        if (!svg) throw new Error('Mermaid 渲染返回空');
        this.technicalRendered = true;
        emptyEl.classList.add('hidden');
        container.innerHTML = `<section class="relative" id="${window.tmDashboard.esc(renderId)}-container">${svg}</section>`;
        const svgNode = container.querySelector('svg');
        if (svgNode) {
          const width = Number.parseFloat(svgNode.getAttribute('width') || '');
          const height = Number.parseFloat(svgNode.getAttribute('height') || '');
          if (Number.isFinite(width)) {
            svgNode.style.maxWidth = '100%';
            svgNode.style.width = '100%';
            svgNode.style.minWidth = 'min(100%, 960px)';
          }
          if (Number.isFinite(height)) {
            svgNode.style.height = 'auto';
          }
          if (!svgNode.getAttribute('viewBox') && Number.isFinite(width) && Number.isFinite(height)) {
            svgNode.setAttribute('viewBox', `0 0 ${width} ${height}`);
          }
          svgNode.querySelectorAll('text, tspan, .label').forEach(node => {
            node.style.fontSize = '20px';
          });
        }
      } catch (err) {
        if (token !== this.renderToken) return;
        console.error('Failed to render mermaid canvas', err);
        container.innerHTML = `<div class="rounded-xl border border-[#d49a91] bg-[#f0d6d2] p-4 text-sm text-[#8a3527]">Mermaid 渲染失败：${window.tmDashboard.esc(err?.message || 'unknown')}</div>`;
      }
    },

    render(data, token = this.renderToken) {
      this.renderState(data);
      this.renderRoadmap();
      this.renderModules(data);
      this.renderCandidates(data);
      this.renderModulePanel();
      this.renderModeUI();
      this.ensureTechnicalRendered();
      this.animateViewMode(this.currentMode);
    },

    // Canvas page is server-rendered in current implementation; no polling fetch required.
  };
})();

window.addEventListener('resize', () => { if (window.tmPages && window.tmPages.quality && window.tmPages.quality.drawFlowLines) window.tmPages.quality.drawFlowLines(); });
