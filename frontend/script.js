const { createApp } = Vue;

createApp({
    data() {
        return {
            activeNav: localStorage.getItem('lynx_activeNav') || 'news',
            token: localStorage.getItem('accessToken') || '',
            currentUser: null,
            authMode: 'login',
            authLoading: false,
            authForm: { username: '', password: '' },

            newsItems: [],
            newsSources: [],
            availableDates: [],
            newsTotal: 0,
            newsLoading: false,
            selectedNewsDetail: null,
            filters: { source: '', date: '' },
            floatingLogos: [
                { name: 'OpenAI', url: 'https://api.iconify.design/simple-icons/openai.svg?color=%23000000', link: 'https://openai.com' },
                { name: 'Anthropic', url: 'https://api.iconify.design/simple-icons/anthropic.svg?color=%23D97757', link: 'https://www.anthropic.com' },
                { name: 'Google', url: 'https://api.iconify.design/simple-icons/google.svg?color=%234285F4', link: 'https://ai.google' },
                { name: 'Meta', url: 'https://api.iconify.design/simple-icons/meta.svg?color=%230468FF', link: 'https://ai.meta.com' },
                { name: 'Microsoft', url: 'https://api.iconify.design/simple-icons/microsoft.svg?color=%2300A4EF', link: 'https://www.microsoft.com/en-us/ai' },
                { name: 'NVIDIA', url: 'https://api.iconify.design/simple-icons/nvidia.svg?color=%2376B900', link: 'https://www.nvidia.com/en-us/ai-data-science/' },
                { name: 'Hugging Face', url: 'https://api.iconify.design/simple-icons/huggingface.svg?color=%23FFD21E', link: 'https://huggingface.co' },
                { name: 'Apple', url: 'https://api.iconify.design/simple-icons/apple.svg?color=%23000000', link: 'https://machinelearning.apple.com' },
                { name: 'AWS', url: 'https://api.iconify.design/simple-icons/amazonaws.svg?color=%23FF9900', link: 'https://aws.amazon.com/ai/' },
                { name: 'xAI', url: 'https://api.iconify.design/simple-icons/x.svg?color=%23000000', link: 'https://x.ai' },
                { name: 'DeepSeek', url: 'https://api.iconify.design/simple-icons/deepseek.svg?color=%234D6BFE', link: 'https://www.deepseek.com' },
                { name: 'ByteDance', url: 'https://api.iconify.design/simple-icons/bytedance.svg?color=%23325AB4', link: 'https://www.bytedance.com' },
                { name: 'Baidu', url: 'https://api.iconify.design/simple-icons/baidu.svg?color=%232932E1', link: 'https://cloud.baidu.com/product/wenxin.html' },
                { name: 'Alibaba Cloud', url: 'https://api.iconify.design/simple-icons/alibabacloud.svg?color=%23FF6A00', link: 'https://tongyi.aliyun.com' },
                { name: 'Tencent', url: 'https://api.iconify.design/simple-icons/tencentqq.svg?color=%2300A4CE', link: 'https://hunyuan.tencent.com' },
                { name: 'Moonshot AI', url: 'https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@latest/icons/moonshot.svg', link: 'https://www.moonshot.cn' },
                { name: 'Zhipu AI', url: 'https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@latest/icons/zhipu.svg', link: 'https://www.zhipuai.cn' },
                { name: 'MiniMax', url: 'https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@latest/icons/minimax.svg', link: 'https://www.minimaxi.com' },
                { name: 'Baichuan', url: 'https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@latest/icons/baichuan.svg', link: 'https://www.baichuan-ai.com' },
                { name: '01.AI', url: 'https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@latest/icons/yi.svg', link: 'https://www.01.ai' },
                { name: 'SenseTime', url: 'https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@latest/icons/sensenova.svg', link: 'https://www.sensetime.com' },
                { name: 'iFlytek', url: 'https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@latest/icons/spark.svg', link: 'https://xinghuo.xfyun.cn' },
            ],

            messages: [],
            userInput: '',
            isLoading: false,
            abortController: null,
            sessionId: `session_${Date.now()}`,
            sessions: [],
            chatAttachments: [],
            isParsingAttachment: false,
            currentNewsContext: null,
            _pickerTimer: null,

            // Admin
            scrapingJob: null,
            scrapingEventSource: null,
            documents: [],
            docsLoading: false,
            docUploading: false,
            cardForm: { title: '', source_slug: '', published_at: '', url: '', text: '' },
            adminItems: [],
            adminItemsLoading: false,
            adminSearch: '',
            parsingUrl: false,
            creating: false,
            editingItem: null,
            adminSort: { field: 'published_at', order: 'desc' },
        };
    },
    computed: {
        marqueeRow1() {
            const half = Math.ceil(this.floatingLogos.length / 2);
            return this.floatingLogos.slice(0, half);
        },
        marqueeRow2() {
            const half = Math.ceil(this.floatingLogos.length / 2);
            return this.floatingLogos.slice(half);
        },
        isAuthenticated() { return !!this.token && !!this.currentUser; },
        isAdmin() { return this.isAuthenticated && this.currentUser?.role === 'admin'; },
        scrapingActive() { return this.scrapingJob?.status === 'running'; },
        scrapingProgress() { return this.scrapingJob?.details_json?.progress ?? 0; },
        scrapingTotal() { return this.scrapingJob?.details_json?.total ?? 0; },
        scrapingCurrent() { return this.scrapingJob?.details_json?.current ?? ''; },
        scrapingProgressPercent() {
            const total = this.scrapingTotal;
            if (!total) return 0;
            return Math.min(100, Math.round((this.scrapingProgress / total) * 100));
        },
        adminSources() {
            if (this.newsSources.length) return this.newsSources;
            return this.floatingLogos.map(l => ({ slug: l.name.toLowerCase().replace(/\s+/g, '_'), name: l.name }));
        },
        sortedAdminItems() {
            let items = [...this.adminItems];
            const q = this.adminSearch.trim().toLowerCase();
            if (q) {
                items = items.filter(item =>
                    (item.title || '').toLowerCase().includes(q) ||
                    (item.source_name || '').toLowerCase().includes(q)
                );
            }
            const { field, order } = this.adminSort;
            items.sort((a, b) => {
                let va = a[field] || '';
                let vb = b[field] || '';
                if (field === 'published_at') {
                    va = va ? new Date(va).getTime() : 0;
                    vb = vb ? new Date(vb).getTime() : 0;
                } else {
                    va = String(va).toLowerCase();
                    vb = String(vb).toLowerCase();
                }
                return va < vb ? -1 : va > vb ? 1 : 0;
            });
            return order === 'desc' ? items.reverse() : items;
        },
        detailParagraphs() {
            const body = this.selectedNewsDetail?.body || '';
            return String(body).split(/\n{2,}/).map(i => i.trim()).filter(Boolean).slice(0, 20);
        },
    },
    watch: {
        activeNav: {
            immediate: true,
            handler(nav) {
                localStorage.setItem('lynx_activeNav', nav);
                if (nav === 'admin') {
                    this.loadDocuments();
                    this.loadAdminItems();
                    this.fetchLatestJob().then(latest => {
                        if (latest?.status === 'running') {
                            this.scrapingJob = latest;
                            this.connectIngestStream(latest.id);
                        } else if (latest) {
                            this.scrapingJob = latest;
                        }
                    });
                } else if (nav === 'chat') {
                    this.loadSessions(true);
                    this.startPickerScroll();
                } else {
                    this.disconnectIngestStream();
                }
            },
        },
    },
    async mounted() {
        this.configureMarked();
        await this.loadNews();
        if (this.token) {
            try {
                await this.fetchMe();
                if (this.isAdmin) await this.loadDocuments();
                if (this.activeNav === 'chat') await this.loadSessions(true);
            }
            catch (_) { this.handleLogout(); }
        }
        this.startPickerScroll();
    },
    beforeUnmount() {
        this.stopPickerScroll();
    },
    methods: {
        configureMarked() {
            marked.setOptions({
                gfm: true, breaks: true,
                highlight(code, lang) {
                    const language = hljs.getLanguage(lang) ? lang : 'plaintext';
                    return hljs.highlight(code, { language }).value;
                },
            });
        },
        parseMarkdown(text) { return marked.parse(String(text || '')); },

        formatDateLabel(raw) {
            if (!raw) return '未知时间';
            let normalized = raw;
            if (typeof normalized === 'string') {
                const hasTz = /(?:Z|[+-]\d{2}:\d{2})$/i.test(normalized);
                if (!hasTz) normalized = `${normalized}Z`;
            }
            const date = new Date(normalized);
            if (Number.isNaN(date.getTime())) return raw;
            return date.toLocaleString('zh-CN', {
                timeZone: 'Asia/Shanghai',
                year: 'numeric', month: '2-digit', day: '2-digit',
            });
        },
        formatDateShort(raw) {
            if (!raw) return '';
            let normalized = raw;
            if (typeof normalized === 'string') {
                const hasTz = /(?:Z|[+-]\d{2}:\d{2})$/i.test(normalized);
                if (!hasTz) normalized = `${normalized}Z`;
            }
            const date = new Date(normalized);
            if (Number.isNaN(date.getTime())) return '';
            return date.toLocaleString('zh-CN', {
                timeZone: 'Asia/Shanghai',
                month: '2-digit', day: '2-digit',
            });
        },
        formatTimeShort(raw) {
            if (!raw) return '';
            let normalized = raw;
            if (typeof normalized === 'string') {
                const hasTz = /(?:Z|[+-]\d{2}:\d{2})$/i.test(normalized);
                if (!hasTz) normalized = `${normalized}Z`;
            }
            const date = new Date(normalized);
            if (Number.isNaN(date.getTime())) return '';
            return date.toLocaleString('zh-CN', {
                timeZone: 'Asia/Shanghai',
                month: '2-digit', day: '2-digit',
                hour: '2-digit', minute: '2-digit',
            });
        },
        cardVariant(index) { return `card-var-${index % 5}`; },

        sourceLogo(sourceName) {
            if (!sourceName) return null;
            const s = sourceName.toLowerCase();
            const match = this.floatingLogos.find(logo => {
                const l = logo.name.toLowerCase();
                return s === l || s.startsWith(l) || l.startsWith(s);
            });
            return match || null;
        },

        // === Auth ===

        authHeaders(extra = {}) {
            const h = { ...extra };
            if (this.token) h.Authorization = `Bearer ${this.token}`;
            return h;
        },
        async authFetch(url, options = {}) {
            const opts = { ...options, headers: this.authHeaders(options.headers || {}) };
            const r = await fetch(url, opts);
            if (r.status === 401) { this.handleLogout(); throw new Error('登录状态已失效'); }
            return r;
        },
        async fetchMe() {
            const r = await fetch('/auth/me', { headers: this.authHeaders() });
            if (r.status === 401) { this.handleLogout(); throw new Error('登录状态已失效'); }
            if (!r.ok) throw new Error('Failed to fetch current user');
            this.currentUser = await r.json();
        },
        async handleAuthSubmit() {
            this.authLoading = true;
            try {
                const endpoint = this.authMode === 'login' ? '/auth/login' : '/auth/register';
                const payload = { username: this.authForm.username, password: this.authForm.password };
                const r = await fetch(endpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                const data = await r.json().catch(() => ({}));
                if (!r.ok) throw new Error(data.detail || 'Authentication failed');
                this.token = data.access_token;
                this.currentUser = { username: data.username, role: data.role };
                localStorage.setItem('accessToken', this.token);
                this.authForm.password = '';
                this.activeNav = 'news';
                await this.loadSessions(true);
            } catch (e) { alert(e.message); }
            finally { this.authLoading = false; }
        },
        toggleAuthMode() { this.authMode = this.authMode === 'login' ? 'register' : 'login'; },
        handleLogout() {
            this.token = '';
            this.currentUser = null;
            localStorage.removeItem('accessToken');
            this.messages = [];
            this.sessions = [];
            this.currentNewsContext = null;
            this.selectedNewsDetail = null;
            this.activeNav = 'news';
        },

        // === News ===

        async loadNews() {
            this.newsLoading = true;
            try {
                const p = new URLSearchParams();
                if (this.filters.source) p.set('source', this.filters.source);
                if (this.filters.date) p.set('date', this.filters.date);
                p.set('page', '1');
                p.set('page_size', '50');
                const r = await fetch(`/news?${p.toString()}`);
                if (!r.ok) throw new Error('Failed to load news');
                const data = await r.json();
                this.newsItems = data.items || [];
                this.newsSources = data.sources || [];
                this.availableDates = data.available_dates || [];
                this.newsTotal = data.total || 0;
            } catch (e) { alert(`资讯加载失败: ${e.message}`); }
            finally { this.newsLoading = false; }
        },
        async openNewsDetail(id) {
            try {
                const r = await fetch(`/news/${id}`);
                if (!r.ok) throw new Error('Failed to load detail');
                this.selectedNewsDetail = await r.json();
            } catch (e) { alert(`详情加载失败: ${e.message}`); }
        },
        openNewsChat(item) {
            this.currentNewsContext = item;
            this.activeNav = 'chat';
            this.selectedNewsDetail = null;
            if (!this.isAuthenticated) return;
            this.handleNewChat();
        },
        clearNewsContext() { this.currentNewsContext = null; },

        // === Sessions ===

        async loadSessions(silent = false) {
            if (!this.isAuthenticated) { this.sessions = []; return; }
            try {
                const r = await fetch('/sessions', { headers: this.authHeaders() });
                if (r.status === 401) { this.sessions = []; return; }
                if (!r.ok) throw new Error('Failed to load sessions');
                const data = await r.json();
                this.sessions = data.sessions || [];
            } catch (e) {
                this.sessions = [];
                if (!silent) alert(`历史会话加载失败: ${e.message}`);
            }
        },

        async loadSession(sessionId) {
            if (!this.isAuthenticated) return;
            this.activeNav = 'chat';
            this.sessionId = sessionId;
            this.currentNewsContext = null; // 切换时先清空

            try {
                const r = await this.authFetch(`/sessions/${encodeURIComponent(sessionId)}`);
                if (!r.ok) throw new Error('Failed to load session');
                const data = await r.json();

                // 1. 恢复聊天消息流
                this.messages = (data.messages || []).map(msg => {
                    if (msg.type === 'human' || msg.type === 'user') {
                        const parsed = this.parseUserMessageDisplay(msg.content);
                        return { text: parsed.text, attachments: parsed.attachments, isUser: true };
                    }
                    return { text: msg.content, isUser: false, ragTrace: msg.rag_trace || null };
                });

                // 🌟 2. 【终极恢复方案】：先查浏览器的本地小本本，直接恢复！
                let localMap = JSON.parse(localStorage.getItem('lynx_session_news') || '{}');
                if (localMap[sessionId]) {
                    // 如果本地记住了这个关联，气泡瞬间复活
                    this.currentNewsContext = localMap[sessionId];
                }
                // 3. 备用逻辑（如果以后你修好了后端，或者换了电脑登录）
                else if (data.news_id) {
                    try {
                        const nr = await fetch(`/news/${data.news_id}`);
                        if (nr.ok) {
                            const detail = await nr.json();
                            this.currentNewsContext = detail.item || detail;
                        }
                    } catch (e) { /* 忽略错误 */ }
                }

                this.$nextTick(() => this.scrollToBottom());
            } catch (e) { alert(`会话加载失败: ${e.message}`); }
        },
        handleNewChat() {
            this.messages = [];
            this.sessionId = `session_${Date.now()}`;
            this.chatAttachments = [];
            if (this.isAuthenticated) {
                this.activeNav = 'chat';
            }
            this.$nextTick(() => this.startPickerScroll());
        },
        async deleteSession(sessionId) {
            if (!confirm('确定要删除此对话吗？')) return;
            try {
                await this.authFetch(`/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
                if (this.sessionId === sessionId) {
                    this.handleNewChat();
                }
                await this.loadSessions(true);
            } catch (e) { alert(`删除失败: ${e.message}`); }
        },
        parseUserMessageDisplay(content) {
            let text = String(content || '');
            let attachments = [];

            // Strip [当前资讯上下文] block appended for the model
            text = text.replace(/\n?\n?\[当前资讯上下文\]\n[\s\S]*$/, '').trim();

            // Strip [附件内容] block appended for the model
            text = text.replace(/\n?\n?\[附件内容\]\n[\s\S]*$/, '').trim();

            // Extract [附件(N):filename] suffix (display-only indicator)
            const fileMatch = text.match(/\n?\n?\[附件\((\d+)\):([^\]]+)\]\s*$/);
            if (fileMatch) {
                attachments = fileMatch[2].split(',').map(i => i.trim()).filter(Boolean);
                text = text.slice(0, fileMatch.index).trim();
            }

            return { text, attachments };
        },

        // === Chat ===

        async sendMessage() {
            if (!this.isAuthenticated || !this.userInput.trim() || this.isLoading) return;
            const text = this.userInput.trim();
            const attachmentContext = this.chatAttachments.map(a => `# ${a.name}\n${a.text}`).join('\n\n');
            const attachmentFiles = this.chatAttachments.map(a => a.name);

            this.messages.push({ text, isUser: true, attachments: [...attachmentFiles] });
            const botIdx = this.messages.push({ text: '', isUser: false, isThinking: true, ragSteps: [] }) - 1;
            
            if (!this.sessions.find(s => s.session_id === this.sessionId)) {
                this.sessions.unshift({
                    session_id: this.sessionId,
                    title: text.length > 25 ? text.slice(0, 25) + '...' : text, // 暂时用用户输入的前几个字作为标题
                    updated_at: new Date().toISOString()
                });
            }

            if (this.currentNewsContext) {
                let localMap = JSON.parse(localStorage.getItem('lynx_session_news') || '{}');
                localMap[this.sessionId] = this.currentNewsContext;
                localStorage.setItem('lynx_session_news', JSON.stringify(localMap));
            }

            this.userInput = '';
            this.resetTextareaHeight();
            this.isLoading = true;
            this.abortController = new AbortController();
            this.$nextTick(() => this.scrollToBottom());

            try {
                const r = await this.authFetch('/chat/stream', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        message: text, session_id: this.sessionId,
                        attachment_context: attachmentContext || null,
                        attachment_files: attachmentFiles.length ? attachmentFiles : null,
                        news_id: this.currentNewsContext?.id || null,
                    }),
                    signal: this.abortController.signal,
                });
                if (!r.ok || !r.body) {
                    const err = await r.json().catch(() => ({}));
                    throw new Error(err.detail || 'Streaming failed');
                }
                const reader = r.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    const chunks = buffer.split('\n\n');
                    buffer = chunks.pop() || '';
                    for (const chunk of chunks) {
                        const line = chunk.trim();
                        if (!line.startsWith('data: ')) continue;
                        const payload = line.slice(6).trim();
                        if (payload === '[DONE]') continue;
                        const data = JSON.parse(payload);
                        if (data.type === 'content') {
                            this.messages[botIdx].isThinking = false;
                            this.messages[botIdx].text += data.content;
                        } else if (data.type === 'rag_step') {
                            this.messages[botIdx].ragSteps.push(data.step);
                        } else if (data.type === 'error') {
                            throw new Error(data.content || 'Unknown stream error');
                        } else if (data.type === 'done') {
                            // Stream finished cleanly — close thinking state
                            if (this.messages[botIdx]?.isThinking) {
                                this.messages[botIdx].isThinking = false;
                                if (!this.messages[botIdx].text) {
                                    this.messages[botIdx].text = '(空响应)';
                                }
                            }
                        }
                        this.$nextTick(() => this.scrollToBottom());
                    }
                }
                // Fallback: if stream ended without content, close thinking state
                if (this.messages[botIdx]?.isThinking) {
                    this.messages[botIdx].isThinking = false;
                    if (!this.messages[botIdx].text) {
                        this.messages[botIdx].text = '(空响应)';
                    }
                }
                this.chatAttachments = [];
            } catch (e) {
                if (this.messages[botIdx]) {
                    this.messages[botIdx].isThinking = false;
                    this.messages[botIdx].text = `生成失败: ${e.message}`;
                }
            } finally {
                this.isLoading = false;
                this.abortController = null;
                await this.loadSessions(true);
                this.$nextTick(() => this.scrollToBottom());
            }
        },
        autoResize(e) {
            const ta = e.target;
            ta.style.height = 'auto';
            ta.style.height = `${ta.scrollHeight}px`;
        },
        resetTextareaHeight() {
            if (this.$refs.textarea) this.$refs.textarea.style.height = 'auto';
        },
        scrollToBottom() {
            if (this.$refs.chatContainer) {
                this.$refs.chatContainer.scrollTop = this.$refs.chatContainer.scrollHeight;
            }
        },
        async handleChatFileSelect(e) {
            const files = e.target.files ? Array.from(e.target.files) : [];
            if (!files.length || !this.isAuthenticated) {
                if (this.$refs.chatFileInput) this.$refs.chatFileInput.value = '';
                return;
            }
            this.isParsingAttachment = true;
            try {
                for (const file of files) {
                    const fd = new FormData();
                    fd.append('file', file);
                    const r = await this.authFetch('/chat/attachments/parse', { method: 'POST', body: fd });
                    const data = await r.json().catch(() => ({}));
                    if (!r.ok) throw new Error(data.detail || `${file.name} parse failed`);
                    this.chatAttachments.push({ name: data.filename || file.name, text: data.extracted_text || '', size: file.size || 0 });
                }
            } catch (e) { alert(`附件解析失败: ${e.message}`); }
            finally {
                this.isParsingAttachment = false;
                if (this.$refs.chatFileInput) this.$refs.chatFileInput.value = '';
            }
        },
        removeChatAttachment(idx) { this.chatAttachments.splice(idx, 1); },

        // === Admin helpers ===

        async fetchLatestJob() {
            try {
                const r = await this.authFetch('/admin/news/jobs');
                if (!r.ok) return null;
                const data = await r.json();
                return (data.jobs || [])[0] || null;
            } catch { return null; }
        },

        // === Admin: Scraping ===

        async startScraping() {
            this.disconnectIngestStream();
            this.scrapingJob = null;
            try {
                const r = await this.authFetch('/admin/news/ingest', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ force: false }),
                });
                if (!r.ok) throw new Error('启动抓取失败');
                const data = await r.json();
                const job = data.job || data;
                this.scrapingJob = job;
                this.connectIngestStream(job.id);
            } catch (e) {
                alert(`抓取启动失败: ${e.message}`);
            }
        },

        connectIngestStream(jobId) {
            this.disconnectIngestStream();
            this._connectIngestStreamWithFetch(jobId);
        },

        async _connectIngestStreamWithFetch(jobId) {
            try {
                const r = await this.authFetch(`/admin/news/ingest/${jobId}/stream`);
                if (!r.ok || !r.body) throw new Error('SSE connection failed');
                const reader = r.body.getReader();
                const decoder = new TextDecoder();
                let buf = '';
                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    buf += decoder.decode(value, { stream: true });
                    const parts = buf.split('\n\n');
                    buf = parts.pop() || '';
                    for (const part of parts) {
                        const line = part.trim();
                        if (!line.startsWith('data: ')) continue;
                        const payload = line.slice(6).trim();
                        let data;
                        try { data = JSON.parse(payload); } catch { continue; }
                        this._handleIngestEvent(data);
                    }
                }
            } catch (_) {
                // Stream ended or connection closed
            }
            // Fallback: if the job is still stuck as "running" after stream closes,
            // fetch the actual status from the API once.
            if (this.scrapingJob?.status === 'running') {
                const latest = await this.fetchLatestJob();
                if (latest) this.scrapingJob = latest;
            }
        },

        _handleIngestEvent(data) {
            if (data.type === 'progress') {
                this.scrapingJob = {
                    ...(this.scrapingJob || {}),
                    status: data.status,
                    details_json: {
                        current: data.current || '',
                        progress: data.progress || 0,
                        total: data.total || 0,
                    },
                };
            } else if (data.type === 'success' || data.type === 'failed') {
                this.scrapingJob = {
                    ...(this.scrapingJob || {}),
                    status: data.status,
                    imported_count: data.imported_count || 0,
                    skipped_count: data.skipped_count || 0,
                    error_message: data.error_message || '',
                    details_json: {
                        current: '',
                        progress: data.progress || 0,
                        total: data.total || 0,
                    },
                };
            }
        },

        disconnectIngestStream() {
            // With the fetch-based approach the connection auto-closes
        },

        // === Admin: Documents ===

        async loadDocuments() {
            this.docsLoading = true;
            try {
                const r = await this.authFetch('/documents');
                if (!r.ok) throw new Error('获取文档列表失败');
                const data = await r.json();
                this.documents = data.documents || [];
            } catch (e) {
                if (this.activeNav === 'admin') alert(`文档列表加载失败: ${e.message}`);
            } finally {
                this.docsLoading = false;
            }
        },

        async handleDocUpload(e) {
            const file = e.target?.files?.[0];
            if (!file) return;
            this.docUploading = true;
            try {
                const fd = new FormData();
                fd.append('file', file);
                const r = await this.authFetch('/documents/upload', { method: 'POST', body: fd });
                if (!r.ok) {
                    const err = await r.json().catch(() => ({}));
                    throw new Error(err.detail || '上传失败');
                }
                await this.loadDocuments();
            } catch (e) {
                alert(`文档上传失败: ${e.message}`);
            } finally {
                this.docUploading = false;
                if (this.$refs.docFileInput) this.$refs.docFileInput.value = '';
            }
        },

        async deleteDocument(filename) {
            if (!confirm(`确定要删除文档 "${filename}" 吗？此操作不可恢复。`)) return;
            try {
                const r = await this.authFetch(`/documents/${encodeURIComponent(filename)}`, { method: 'DELETE' });
                if (!r.ok) {
                    const err = await r.json().catch(() => ({}));
                    throw new Error(err.detail || '删除失败');
                }
                await this.loadDocuments();
            } catch (e) {
                alert(`删除失败: ${e.message}`);
            }
        },

        async deleteAllNews() {
            if (!confirm('确定要删除所有资讯卡片吗？此操作不可恢复。')) return;
            try {
                const r = await this.authFetch('/admin/news/items', { method: 'DELETE' });
                if (!r.ok) throw new Error('删除失败');
                const data = await r.json();
                this.newsItems = [];
                this.newsTotal = 0;
                this.scrapingJob = null;
                alert(data.message || '已删除所有资讯');
            } catch (e) {
                alert(`删除失败: ${e.message}`);
            }
        },

        // === Admin: Card CRUD ===

        async loadAdminItems() {
            this.adminItemsLoading = true;
            try {
                const r = await this.authFetch('/admin/items?page_size=100');
                if (!r.ok) throw new Error('加载失败');
                const data = await r.json();
                this.adminItems = data.items || [];
            } catch (e) {
                if (this.activeNav === 'admin') alert(`卡片加载失败: ${e.message}`);
            } finally {
                this.adminItemsLoading = false;
            }
        },

        resetCardForm() {
            this.cardForm = { title: '', source_slug: '', published_at: '', url: '', text: '' };
            this.editingItem = null;
        },

        _utcToLocalForm(utcIso) {
            if (!utcIso) return '';
            const d = new Date(utcIso);
            if (Number.isNaN(d.getTime())) return '';
            const pad = n => String(n).padStart(2, '0');
            return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
        },

        _matchSourceFromUrl(url) {
            if (!url) return '';
            const host = new URL(url).hostname.toLowerCase();
            const domainMap = {
                openai: 'openai', anthropic: 'anthropic', google: 'google',
                meta: 'meta', xai: 'xai', microsoft: 'microsoft',
                nvidia: 'nvidia', 'huggingface': 'huggingface', deepseek: 'deepseek',
                'aws': 'aws', apple: 'apple', 'alibabacloud': 'alibaba',
                baidu: 'baidu', bytedance: 'bytedance', tencent: 'tencent',
                'moonshot': 'moonshot', 'zhipuai': 'zhipu', 'minimaxi': 'minimax',
                'baichuan': 'baichuan', '01.ai': '01ai', 'sensetime': 'sensetime',
                'iflytek': 'iflytek',
            };
            for (const [domain, slug] of Object.entries(domainMap)) {
                if (host.includes(domain)) return slug;
            }
            return '';
        },

        async createCard() {
            const { title, source_slug, published_at, text } = this.cardForm;
            if (!title || !source_slug || !text) return;
            this.creating = true;
            try {
                const isEdit = !!this.editingItem;
                const url = isEdit ? `/admin/items/${this.editingItem.id}` : '/admin/items';
                const r = await this.authFetch(url, {
                    method: isEdit ? 'PUT' : 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        title: title.trim(),
                        source_slug,
                        text: text.trim(),
                        url: this.cardForm.url.trim() || null,
                        published_at: published_at ? published_at + 'T12:00:00' : null,
                    }),
                });
                if (!r.ok) {
                    const err = await r.json().catch(() => ({}));
                    throw new Error(err.detail || (isEdit ? '保存失败' : '创建失败'));
                }
                this.resetCardForm();
                await this.loadAdminItems();
            } catch (e) {
                alert(`操作失败: ${e.message}`);
            } finally {
                this.creating = false;
            }
        },

        async parseUrl() {
            const url = this.cardForm.url?.trim();
            if (!url) return;
            this.parsingUrl = true;
            try {
                const r = await this.authFetch('/admin/items/parse-url', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url }),
                });
                if (!r.ok) {
                    const err = await r.json().catch(() => ({}));
                    throw new Error(err.detail || '解析失败');
                }
                const data = await r.json();
                if (data.title) this.cardForm.title = data.title;
                if (data.text) this.cardForm.text = data.text;
                if (data.published_at) {
                    this.cardForm.published_at = this._utcToLocalForm(data.published_at);
                }
                // Auto-select source from URL domain
                const matched = this._matchSourceFromUrl(url);
                if (matched && this.adminSources.some(s => s.slug === matched)) {
                    this.cardForm.source_slug = matched;
                }
            } catch (e) {
                alert(`解析失败: ${e.message}`);
            } finally {
                this.parsingUrl = false;
            }
        },

        async deleteAdminItem(id) {
            if (!confirm('确定要删除此卡片吗？')) return;
            try {
                const r = await this.authFetch(`/admin/items/${id}`, { method: 'DELETE' });
                if (!r.ok) throw new Error('删除失败');
                await this.loadAdminItems();
            } catch (e) {
                alert(`删除失败: ${e.message}`);
            }
        },

        startEdit(item) {
            this.cardForm = {
                title: item.title,
                source_slug: item.source_slug,
                url: item.url || '',
                published_at: this._utcToLocalForm(item.published_at),
                text: item.body || '',
            };
            this.editingItem = item;
            this.$nextTick(() => {
                this.$refs.cardMgmt?.scrollIntoView({ behavior: 'smooth', block: 'start' });
            });
        },

        cancelEdit() {
            this.resetCardForm();
        },

        setSort(field) {
            if (this.adminSort.field === field) {
                this.adminSort.order = this.adminSort.order === 'asc' ? 'desc' : 'asc';
            } else {
                this.adminSort = { field, order: 'desc' };
            }
        },

        // === Picker auto-scroll ===

        startPickerScroll() {
            this.stopPickerScroll();
            // Wake up the scroll container — ensures scrollWidth is computed
            const rail = document.querySelector('.news-picker-rail');
            if (rail) {
                void rail.offsetHeight;
                rail.scrollLeft = 1;
                rail.scrollLeft = 0;
            }
            this._pickerTimer = setInterval(() => {
                const el = document.querySelector('.news-picker-rail');
                if (!el || el.matches(':hover')) return;
                el.scrollLeft += 0.4;
                if (el.scrollLeft >= el.scrollWidth / 2) {
                    el.scrollLeft = 0;
                }
            }, 30);
        },
        stopPickerScroll() {
            if (this._pickerTimer) {
                clearInterval(this._pickerTimer);
                this._pickerTimer = null;
            }
        },
    },
}).mount('#app');
