(() => {
  "use strict";

  const state = {
    genres: [],
    works: [],
    selectedDrawerId: "",
    projects: [],
    currentProjectId: "",
    graph: { project: null, nodes: [], choices: [], diagnostics: {} },
    selectedNodeId: "",
    reader: {
      workId: "",
      chapterNum: 1,
      lineStart: 1,
      lineEnd: 80,
      highlightStart: null,
      highlightEnd: null,
    },
    pendingRatings: {},
  };

  // ---------- Helpers ----------
  function $(id) {
    return document.getElementById(id);
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function toast(msg, type = "info") {
    const box = $("toast-container");
    const el = document.createElement("div");
    el.className = `toast ${type === "error" ? "error" : type === "success" ? "success" : ""}`;
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(() => {
      el.remove();
    }, 3400);
  }

  async function api(url, options = {}) {
    const opts = {
      headers: { "Content-Type": "application/json" },
      ...options,
    };
    const resp = await fetch(url, opts);
    let data = {};
    try {
      data = await resp.json();
    } catch {
      data = {};
    }
    if (!resp.ok) {
      throw new Error(data.detail || `请求失败 (${resp.status})`);
    }
    return data;
  }

  function genreNameById(gid) {
    const found = state.genres.find((g) => g.id === gid);
    return found ? found.name : gid || "未分类";
  }

  function openModal(id) {
    $(id).classList.add("open");
  }

  function closeModal(id) {
    $(id).classList.remove("open");
  }

  function closeAllModals() {
    document.querySelectorAll(".modal-backdrop.open").forEach((m) => m.classList.remove("open"));
  }

  function nodeTypeLabel(t) {
    const map = {
      start: "起始幕",
      scene: "普通戏",
      climax: "高潮戏",
      good_ending: "好结局",
      bad_ending: "坏结局",
      hidden_ending: "隐藏结局",
    };
    return map[t] || t || "普通戏";
  }

  function formatDelta(delta) {
    if (!delta || typeof delta !== "object") return "";
    const parts = [];
    for (const [k, v] of Object.entries(delta)) {
      const num = Number(v);
      const sign = !Number.isNaN(num) && num > 0 ? `+${num}` : `${v}`;
      parts.push(`${k} ${sign}`);
    }
    return parts.join(" · ");
  }

  // ---------- Tabs ----------
  function initTabs() {
    document.querySelectorAll(".nav-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = btn.dataset.tab;
        document.querySelectorAll(".nav-tab").forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        $(target).classList.add("active");
      });
    });

    document.querySelectorAll(".sub-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = btn.dataset.subtab;
        document.querySelectorAll(".sub-tab").forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".sub-panel").forEach((p) => (p.style.display = "none"));
        btn.classList.add("active");
        $(target).style.display = "block";
      });
    });

    document.querySelectorAll(".modal-close").forEach((btn) => {
      btn.addEventListener("click", () => closeAllModals());
    });
    document.querySelectorAll(".modal-backdrop").forEach((bg) => {
      bg.addEventListener("click", (e) => {
        if (e.target === bg) bg.classList.remove("open");
      });
    });
  }

  function switchMainTab(tabId) {
    document.querySelectorAll(".nav-tab").forEach((b) => {
      b.classList.toggle("active", b.dataset.tab === tabId);
    });
    document.querySelectorAll(".tab-panel").forEach((p) => {
      p.classList.toggle("active", p.id === tabId);
    });
  }

  // ---------- Dashboard & Genres ----------
  async function loadDashboard() {
    const data = await api("/api/dashboard");
    state.genres = data.genres || [];
    const stats = data.stats || {};

    $("data-dir-badge").textContent = `数据保险箱：${data.data_dir || "D:\\ScreenwriterKB"}`;
    $("stat-genres").textContent = state.genres.length;
    $("stat-works").textContent = stats.total_works ?? stats.works_count ?? 0;
    $("stat-pending").textContent = stats.pending_taste_count ?? stats.pending_snippets ?? 0;
    $("stat-approved").textContent = stats.total_approved_dialogues ?? stats.approved_snippets ?? 0;
    $("stat-rules").textContent = stats.enabled_jinghua_rules ?? stats.total_jinghua_rules ?? 0;
    $("stat-projects").textContent = stats.total_script_projects ?? stats.script_projects ?? 0;

    renderDrawers();
    populateGenreSelects();
  }

  function renderDrawers() {
    const grid = $("drawer-grid");
    if (!state.genres.length) {
      grid.innerHTML = `<div class="empty-state">暂无类型抽屉，请点击右上角新建。</div>`;
      return;
    }
    grid.innerHTML = state.genres
      .map((g) => {
        const selected = state.selectedDrawerId === g.id ? "selected" : "";
        const workCount = g.work_count ?? g.works_count ?? 0;
        const snippetCount = g.approved_dialogue_count ?? g.snippet_count ?? 0;
        const charCount = g.character_count ?? 0;
        return `
          <button type="button" class="drawer-card ${selected}" data-genre-id="${escapeHtml(g.id)}">
            <div style="display:flex; justify-content:space-between; align-items:center; width:100%;">
              <span class="drawer-name">${escapeHtml(g.name)}</span>
              <span class="pill-badge">${escapeHtml(g.id)}</span>
            </div>
            <div class="drawer-desc">${escapeHtml(g.description || "独立存放该题材原话与人物卡")}</div>
            ${g.channel_hint ? `<div style="font-size:11.5px; color:var(--accent);">渠道：${escapeHtml(g.channel_hint)}</div>` : ""}
            <div class="drawer-stats">
              <span>书籍 ${workCount}</span>
              <span>· 好梗 ${snippetCount}</span>
              <span>· 人物 ${charCount}</span>
            </div>
          </button>
        `;
      })
      .join("");

    grid.querySelectorAll(".drawer-card").forEach((card) => {
      card.addEventListener("click", async () => {
        const gid = card.dataset.genreId;
        state.selectedDrawerId = state.selectedDrawerId === gid ? "" : gid;
        if (state.selectedDrawerId) {
          $("import-genre-id").value = state.selectedDrawerId;
        }
        renderDrawers();
        await loadWorks();
      });
    });
  }

  function populateGenreSelects() {
    const selectIds = [
      "import-genre-id",
      "add-snippet-genre",
      "char-genre",
      "hl-genre",
      "proj-genre",
    ];
    for (const sid of selectIds) {
      const el = $(sid);
      if (!el) continue;
      const prev = el.value;
      el.innerHTML = state.genres
        .map((g) => `<option value="${escapeHtml(g.id)}">${escapeHtml(g.name)}</option>`)
        .join("");
      if (prev && state.genres.some((g) => g.id === prev)) {
        el.value = prev;
      } else if (sid === "proj-genre" && state.genres.some((g) => g.id === "hudong")) {
        el.value = "hudong";
      }
    }

    const filterIds = ["taste-genre-filter", "kb-genre-filter"];
    for (const fid of filterIds) {
      const el = $(fid);
      if (!el) continue;
      const prev = el.value;
      el.innerHTML =
        `<option value="">全部类型抽屉</option>` +
        state.genres
          .map((g) => `<option value="${escapeHtml(g.id)}">${escapeHtml(g.name)}</option>`)
          .join("");
      if (prev) el.value = prev;
    }
  }

  // ---------- Works & Reader ----------
  async function loadWorks() {
    const q = state.selectedDrawerId ? `?genre_id=${encodeURIComponent(state.selectedDrawerId)}` : "";
    const data = await api(`/api/works${q}`);
    state.works = data.items || [];
    renderWorks();
    populateWorkFilter();
  }

  function populateWorkFilter() {
    const el = $("taste-work-filter");
    if (!el) return;
    const prev = el.value;
    el.innerHTML =
      `<option value="">全部小说来源</option>` +
      state.works.map((w) => `<option value="${escapeHtml(w.id)}">《${escapeHtml(w.title)}》</option>`).join("");
    if (prev) el.value = prev;
  }

  function renderWorks() {
    const list = $("works-list");
    if (!state.works.length) {
      list.innerHTML = `
        <div class="empty-state" style="grid-column:1/-1;">
          ${state.selectedDrawerId ? "这个类型抽屉里还没有导书，可以在右侧粘贴本地小说路径一键导入。" : "书架目前是空的，你可以在右侧填入本地小说路径一键喂书。"}
        </div>`;
      return;
    }
    list.innerHTML = state.works
      .map((w) => {
        const total = Number(w.total_chapters || 0);
        const clean = Number(w.clean_chapters || 0);
        const bad = Number(w.corrupted_chapters || 0);
        const ratio = total > 0 ? Math.round((clean / total) * 100) : 100;
        return `
          <div class="book-card">
            <div class="book-header">
              <div>
                <h3 class="book-title">《${escapeHtml(w.title)}》</h3>
                <div style="font-size:12.5px; color:var(--ink-muted);">
                  ${escapeHtml(w.author || "佚名")} · ${(Number(w.total_chars || 0) / 10000).toFixed(1)} 万字
                </div>
              </div>
              <span class="pill-badge cobalt">${escapeHtml(genreNameById(w.genre_id))}</span>
            </div>
            <div>
              <div style="display:flex; justify-content:space-between; font-size:12.5px; margin-bottom:5px;">
                <span style="color:var(--jade); font-weight:600;">完好可用：${clean} 章</span>
                <span style="color:${bad > 0 ? "var(--crimson)" : "var(--ink-muted)"}; font-weight:600;">
                  已隔离防盗坏章：${bad} 章
                </span>
              </div>
              <div class="health-bar"><div class="health-bar-fill" style="width:${ratio}%;"></div></div>
            </div>
            <div style="font-size:12.5px; color:var(--ink-secondary); min-height:20px;">
              ${w.summary_one_page ? escapeHtml(w.summary_one_page).slice(0, 72) + "…" : "暂未填写一页纸核心卖点摘要"}
            </div>
            <div style="display:flex; gap:8px; margin-top:auto;">
              <button class="btn btn-sm btn-primary" type="button" data-open-book="${escapeHtml(w.id)}" style="flex:1;">
                查看章节健康度 &amp; 读原文
              </button>
              <button class="btn btn-sm" type="button" data-taste-book="${escapeHtml(w.id)}">
                去试菜
              </button>
            </div>
          </div>
        `;
      })
      .join("");

    list.querySelectorAll("[data-open-book]").forEach((btn) => {
      btn.addEventListener("click", () => openBookReaderModal(btn.dataset.openBook));
    });
    list.querySelectorAll("[data-taste-book]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        $("taste-work-filter").value = btn.dataset.tasteBook;
        switchMainTab("tab-taste");
        await loadPendingSnippets();
      });
    });
  }

  async function openBookReaderModal(workId, jumpChapter = null, hlStart = null, hlEnd = null) {
    try {
      const data = await api(`/api/works/${encodeURIComponent(workId)}`);
      const w = data.work;
      state.reader.workId = workId;
      state.reader.highlightStart = hlStart;
      state.reader.highlightEnd = hlEnd;

      $("reader-modal-title").textContent = `《${w.title}》 · 章节体检与原文查看`;
      $("reader-modal-sub").textContent = `所属抽屉：${genreNameById(w.genre_id)} · 共 ${w.total_chapters} 章（完好 ${w.clean_chapters} 章 / 已隔离坏章 ${w.corrupted_chapters} 章）`;

      $("reader-book-meta").innerHTML = `
        <div style="background:var(--bg-subtle); border:1px solid var(--border-soft); border-radius:10px; padding:12px;">
          <div class="form-label" style="margin-bottom:4px;">本书一页纸卖点与风格备注</div>
          <div style="display:flex; gap:8px; flex-wrap:wrap;">
            <input id="reader-summary-input" class="input" type="text" value="${escapeHtml(w.summary_one_page || "")}" placeholder="写一句本书最大看点/风格提醒" style="flex:1;" />
            <button id="btn-save-book-summary" class="btn btn-sm btn-primary" type="button">保存备注</button>
          </div>
        </div>
      `;

      $("btn-save-book-summary").addEventListener("click", async () => {
        try {
          await api(`/api/works/${encodeURIComponent(workId)}/summary`, {
            method: "PUT",
            body: JSON.stringify({
              summary_one_page: $("reader-summary-input").value,
              style_notes: w.style_notes || "",
            }),
          });
          toast("已更新书籍备注", "success");
          await loadWorks();
        } catch (err) {
          toast(err.message, "error");
        }
      });

      const chapters = w.chapters || [];
      if (!chapters.length) {
        $("reader-chapters-area").innerHTML = `<div class="empty-state">这本书暂无章节切片记录。</div>`;
      } else {
        $("reader-chapters-area").innerHTML = `
          <div class="form-label" style="margin-bottom:6px;">点任意章节即可查看原文（红字标出的是系统自动隔离的防盗乱码章）：</div>
          <div class="chapter-pills">
            ${chapters
              .map((ch) => {
                const bad = Number(ch.is_corrupted || 0) === 1;
                const label = `第${ch.chapter_num}章 ${ch.title || ""}`.trim();
                const hint = bad ? ` [已隔离: ${ch.corruption_reason || "异常"}]` : "";
                return `<button type="button" class="chapter-pill ${bad ? "corrupted" : ""}" data-ch="${ch.chapter_num}">${escapeHtml(label + hint)}</button>`;
              })
              .join("")}
          </div>
        `;
        $("reader-chapters-area").querySelectorAll("[data-ch]").forEach((btn) => {
          btn.addEventListener("click", () => {
            const cnum = Number(btn.dataset.ch);
            loadChapterSpan(workId, cnum, 1, 80);
          });
        });
      }

      if (jumpChapter) {
        const s = Math.max(1, (hlStart || 1) - 10);
        const e = s + 79;
        await loadChapterSpan(workId, jumpChapter, s, e);
      } else {
        $("reader-span-viewer").style.display = "none";
      }

      openModal("modal-book-reader");
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function loadChapterSpan(workId, chapterNum, lineStart, lineEnd) {
    try {
      const data = await api(
        `/api/works/${encodeURIComponent(workId)}/span?chapter_num=${chapterNum}&line_start=${lineStart}&line_end=${lineEnd}`
      );
      const span = data.span || {};
      state.reader.chapterNum = chapterNum;
      state.reader.lineStart = span.line_start || lineStart;
      state.reader.lineEnd = span.line_end || lineEnd;

      $("reader-span-viewer").style.display = "block";
      const chTitle = span.chapter_title ? `第${chapterNum}章 ${span.chapter_title}` : `第${chapterNum}章`;
      const badTag = span.is_corrupted ? ` ⚠️ [已隔离防盗/坏章：${span.corruption_reason || "异常"}]` : "";
      $("reader-span-title").textContent = `${chTitle}（第 ${state.reader.lineStart} ~ ${state.reader.lineEnd} 行 / 共 ${span.total_chapter_lines || "?"} 行）${badTag}`;
      $("btn-span-prev").disabled = state.reader.lineStart <= 1;
      $("btn-span-next").disabled = Boolean(span.total_chapter_lines && state.reader.lineEnd >= span.total_chapter_lines);

      let lines = span.lines;
      if (!Array.isArray(lines)) {
        const raw = String(span.text || span.content || "");
        lines = raw ? raw.split(/\r?\n/).map((t, idx) => ({ line_num: state.reader.lineStart + idx, text: t })) : [];
      }

      $("reader-span-lines").innerHTML = lines.length
        ? lines
            .map((item, idx) => {
              const ln = typeof item === "object" ? item.line_num ?? item.line ?? state.reader.lineStart + idx : state.reader.lineStart + idx;
              const txt = typeof item === "object" ? item.text ?? "" : String(item);
              const isHl =
                state.reader.highlightStart &&
                ln >= state.reader.highlightStart &&
                ln <= (state.reader.highlightEnd || state.reader.highlightStart);
              return `<div class="span-line ${isHl ? "highlight" : ""}"><span class="span-ln">L${ln}</span><span>${escapeHtml(txt || " ")}</span></div>`;
            })
            .join("")
        : `<div class="empty-state">这一页没有文本内容。</div>`;
    } catch (err) {
      toast(err.message, "error");
    }
  }

  // ---------- Tab 2: Taste Check & Knowledge Base ----------
  async function loadPendingSnippets() {
    const gid = $("taste-genre-filter").value;
    const wid = $("taste-work-filter").value;
    const params = new URLSearchParams({ status: "pending", limit: "30" });
    if (gid) params.set("genre_id", gid);
    if (wid) params.set("work_id", wid);

    const data = await api(`/api/snippets?${params.toString()}`);
    const items = data.items || [];
    const list = $("pending-snippets-list");

    if (!items.length) {
      list.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">目前没有待试菜的新对白啦！你可以先去「类型抽屉 &amp; 书架」导入一本小说，系统会自动挑出候选题材对白放这里。</div>`;
      return;
    }

    list.innerHTML = items
      .map((s) => {
        const curRating = state.pendingRatings[s.id] || s.rating || 4;
        const stars = [1, 2, 3, 4, 5]
          .map(
            (n) =>
              `<button type="button" class="star-btn ${n <= curRating ? "active" : ""}" data-rate-snippet="${escapeHtml(s.id)}" data-star="${n}">★</button>`
          )
          .join("");
        const sourceInfo = s.chapter_num
          ? `第${s.chapter_num}章 ${s.line_start ? `L${s.line_start}` : ""}`
          : "本地导入";
        return `
          <div class="snippet-card pending" data-snippet-card="${escapeHtml(s.id)}">
            <div style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
              <span class="pill-badge amber">${escapeHtml(genreNameById(s.genre_id))} · ${escapeHtml(sourceInfo)}</span>
              <div class="star-row">${stars}</div>
            </div>
            <div class="snippet-quote">${escapeHtml(s.raw_text)}</div>
            ${s.trigger_context ? `<div style="font-size:12.5px; color:var(--ink-secondary);">语境：${escapeHtml(s.trigger_context)}</div>` : ""}
            <div class="form-row" style="margin-bottom:0;">
              <input class="input" type="text" data-tag-input="${escapeHtml(s.id)}" value="${escapeHtml(s.scene_tag || "")}" placeholder="顺手打个场景标签（如：装傻反呛）" style="padding:6px 10px; font-size:13px;" />
              ${
                s.work_id && s.chapter_num
                  ? `<button type="button" class="btn btn-sm" data-jump-source="${escapeHtml(s.work_id)}" data-ch="${s.chapter_num}" data-ls="${s.line_start || 1}" data-le="${s.line_end || s.line_start || 1}">回看原文</button>`
                  : ""
              }
            </div>
            <div class="snippet-actions">
              <button type="button" class="btn btn-jade" data-review-id="${escapeHtml(s.id)}" data-decision="approved">✓ 对味留用</button>
              <button type="button" class="btn btn-crimson" data-review-id="${escapeHtml(s.id)}" data-decision="rejected">✗ 无感淘汰</button>
            </div>
          </div>
        `;
      })
      .join("");

    list.querySelectorAll("[data-rate-snippet]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const sid = btn.dataset.rateSnippet;
        const star = Number(btn.dataset.star);
        state.pendingRatings[sid] = star;
        const row = btn.parentElement;
        row.querySelectorAll(".star-btn").forEach((b) => {
          b.classList.toggle("active", Number(b.dataset.star) <= star);
        });
      });
    });

    list.querySelectorAll("[data-review-id]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const sid = btn.dataset.reviewId;
        const decision = btn.dataset.decision;
        const tagInput = list.querySelector(`[data-tag-input="${CSS.escape(sid)}"]`);
        const rating = state.pendingRatings[sid] || 4;
        try {
          await api(`/api/snippets/${encodeURIComponent(sid)}/review`, {
            method: "POST",
            body: JSON.stringify({
              status: decision,
              rating: decision === "approved" ? rating : 0,
              scene_tag: tagInput ? tagInput.value.trim() : "",
            }),
          });
          toast(decision === "approved" ? "已收进你的好梗库！" : "已淘汰这条", "success");
          await Promise.all([loadDashboard(), loadPendingSnippets(), loadApprovedSnippets()]);
        } catch (err) {
          toast(err.message, "error");
        }
      });
    });

    bindSourceJumpButtons(list);
  }

  function bindSourceJumpButtons(container) {
    container.querySelectorAll("[data-jump-source]").forEach((btn) => {
      btn.addEventListener("click", () => {
        openBookReaderModal(
          btn.dataset.jumpSource,
          Number(btn.dataset.ch || 1),
          Number(btn.dataset.ls || 1),
          Number(btn.dataset.le || 1)
        );
      });
    });
  }

  async function loadApprovedSnippets() {
    const gid = $("kb-genre-filter").value;
    const tag = $("kb-scene-tag-filter").value.trim();
    const q = $("kb-search-query").value.trim();
    const params = new URLSearchParams({ status: "approved", limit: "60" });
    if (gid) params.set("genre_id", gid);
    if (tag) params.set("scene_tag", tag);
    if (q) params.set("query", q);

    const data = await api(`/api/snippets?${params.toString()}`);
    const items = data.items || [];
    const list = $("approved-snippets-list");

    if (!items.length) {
      list.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">这个抽屉下暂无已入库好梗，试试清空筛选条件或去上方试菜台勾几条，也可点击右上角手动记一条。</div>`;
      return;
    }

    list.innerHTML = items
      .map((s) => {
        const stars = s.rating ? "★".repeat(Number(s.rating)) : "未打星";
        return `
          <div class="snippet-card">
            <div style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
              <div style="display:flex; gap:6px; flex-wrap:wrap;">
                <span class="pill-badge jade">${escapeHtml(genreNameById(s.genre_id))}</span>
                ${s.scene_tag ? `<span class="pill-badge cobalt">${escapeHtml(s.scene_tag)}</span>` : ""}
              </div>
              <span style="color:#e0961c; font-size:13px;">${escapeHtml(stars)}</span>
            </div>
            <div class="snippet-quote">${escapeHtml(s.raw_text)}</div>
            ${s.trigger_context ? `<div style="font-size:12.5px; color:var(--ink-secondary);">触发情境：${escapeHtml(s.trigger_context)}</div>` : ""}
            ${s.why_funny_or_effective ? `<div style="font-size:12.5px; color:var(--ink-muted);">好在哪里：${escapeHtml(s.why_funny_or_effective)}</div>` : ""}
            ${
              s.work_id && s.chapter_num
                ? `<div style="margin-top:auto;"><button type="button" class="btn btn-sm" data-jump-source="${escapeHtml(s.work_id)}" data-ch="${s.chapter_num}" data-ls="${s.line_start || 1}" data-le="${s.line_end || s.line_start || 1}">查看原书出处（第${s.chapter_num}章）</button></div>`
                : ""
            }
          </div>
        `;
      })
      .join("");

    bindSourceJumpButtons(list);
  }

  async function loadCharacters() {
    const gid = $("kb-genre-filter").value;
    const params = new URLSearchParams();
    if (gid) params.set("genre_id", gid);
    const data = await api(`/api/characters?${params.toString()}`);
    const items = data.items || [];
    const list = $("characters-list");

    if (!items.length) {
      list.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">暂无人物专属声音卡，点击右上角「＋ 新建人物声音卡」记录主角口癖与说话忌口。</div>`;
      return;
    }

    list.innerHTML = items
      .map((c) => {
        const samples = Array.isArray(c.sample_lines) ? c.sample_lines : [];
        return `
          <div class="snippet-card">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <strong style="font-size:16px;">${escapeHtml(c.name)}</strong>
              <span class="pill-badge cobalt">${escapeHtml(genreNameById(c.genre_id))}${c.role_type ? ` · ${escapeHtml(c.role_type)}` : ""}</span>
            </div>
            <div style="font-size:13.5px;"><strong>性格底色：</strong>${escapeHtml(c.traits)}</div>
            ${c.speech_style ? `<div style="font-size:13px; color:var(--ink-secondary);"><strong>说话习惯：</strong>${escapeHtml(c.speech_style)}</div>` : ""}
            ${c.catchphrases ? `<div style="font-size:13px; color:var(--jade);"><strong>常挂嘴边：</strong>${escapeHtml(c.catchphrases)}</div>` : ""}
            ${c.forbidden_words ? `<div style="font-size:13px; color:var(--crimson);"><strong>绝对忌口：</strong>${escapeHtml(c.forbidden_words)}</div>` : ""}
            ${samples.length ? `<div class="snippet-quote" style="font-size:13px;">${samples.map((l) => escapeHtml(l)).join("\n")}</div>` : ""}
          </div>
        `;
      })
      .join("");
  }

  async function loadHighlights() {
    const gid = $("kb-genre-filter").value;
    const params = new URLSearchParams();
    if (gid) params.set("genre_id", gid);
    const data = await api(`/api/highlights?${params.toString()}`);
    const items = data.items || [];
    const list = $("highlights-list");

    if (!items.length) {
      list.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">暂无名场面钩子卡，点击右上角「＋ 记一个名场面」沉淀高潮反转套路。</div>`;
      return;
    }

    list.innerHTML = items
      .map(
        (h) => `
          <div class="snippet-card">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <span class="pill-badge plum">${escapeHtml(h.hook_type || "名场面钩子")}</span>
              <span class="pill-badge">${escapeHtml(genreNameById(h.genre_id))}${h.chapter_range ? ` · ${escapeHtml(h.chapter_range)}` : ""}</span>
            </div>
            <div style="font-size:14px;">${escapeHtml(h.plain_summary)}</div>
            ${h.why_it_hooks ? `<div style="font-size:12.5px; color:var(--ink-secondary);">抓人原因：${escapeHtml(h.why_it_hooks)}</div>` : ""}
            ${h.raw_excerpt ? `<div class="snippet-quote" style="font-size:13px;">${escapeHtml(h.raw_excerpt)}</div>` : ""}
          </div>
        `
      )
      .join("");
  }

  // ---------- Tab 3: Jinghua Lock ----------
  async function loadJinghuaRules() {
    const data = await api("/api/jinghua/rules?include_disabled=true");
    const items = data.items || [];
    const list = $("jinghua-rules-list");

    if (!items.length) {
      list.innerHTML = `<div class="empty-state">暂无黑名单规则。</div>`;
      return;
    }

    list.innerHTML = items
      .map((r) => {
        const enabled = Boolean(r.enabled);
        return `
          <div class="rule-item ${enabled ? "" : "disabled"}">
            <div style="flex:1;">
              <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin-bottom:4px;">
                <strong style="color:var(--crimson); font-size:14.5px;">${escapeHtml(r.bad_pattern)}</strong>
                <span class="pill-badge">${escapeHtml(r.category)}</span>
                <span class="pill-badge ${enabled ? "jade" : ""}">${enabled ? "拦截中" : "已暂停"}</span>
                ${r.hit_count ? `<span style="font-size:11.5px; color:var(--ink-muted);">已拦截 ${r.hit_count} 次</span>` : ""}
              </div>
              ${r.reason ? `<div style="font-size:12.5px; color:var(--ink-secondary);">原因：${escapeHtml(r.reason)}</div>` : ""}
              ${r.good_replacement_example ? `<div style="font-size:12.5px; color:var(--jade);">平实改写：${escapeHtml(r.good_replacement_example)}</div>` : ""}
            </div>
            <div style="display:flex; gap:6px;">
              <button type="button" class="btn btn-sm" data-toggle-rule="${escapeHtml(r.id)}" data-next-enabled="${enabled ? "0" : "1"}">${enabled ? "暂停" : "启用"}</button>
              <button type="button" class="btn btn-sm btn-crimson" data-del-rule="${escapeHtml(r.id)}">删除</button>
            </div>
          </div>
        `;
      })
      .join("");

    list.querySelectorAll("[data-toggle-rule]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const rid = btn.dataset.toggleRule;
        const nextEnabled = btn.dataset.nextEnabled === "1";
        try {
          await api(`/api/jinghua/rules/${encodeURIComponent(rid)}`, {
            method: "PATCH",
            body: JSON.stringify({ enabled: nextEnabled }),
          });
          await Promise.all([loadJinghuaRules(), loadDashboard()]);
        } catch (err) {
          toast(err.message, "error");
        }
      });
    });

    list.querySelectorAll("[data-del-rule]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const rid = btn.dataset.delRule;
        try {
          await api(`/api/jinghua/rules/${encodeURIComponent(rid)}`, { method: "DELETE" });
          toast("已删除该规则", "success");
          await Promise.all([loadJinghuaRules(), loadDashboard()]);
        } catch (err) {
          toast(err.message, "error");
        }
      });
    });
  }

  function renderHighlightedOilyText(rawText, hits) {
    const patterns = Array.from(
      new Set(
        (hits || [])
          .map((h) => h.matched_text || h.pattern || h.bad_pattern || "")
          .filter(Boolean)
      )
    ).sort((a, b) => b.length - a.length);

    let html = escapeHtml(rawText);
    for (const pat of patterns) {
      const safePat = escapeHtml(pat).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      if (!safePat) continue;
      const re = new RegExp(safePat, "g");
      html = html.replace(re, (m) => `<mark class="oily-mark">${m}</mark>`);
    }
    return html;
  }

  async function runJinghuaTester(text) {
    const data = await api("/api/jinghua/check", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    const res = data.result || {};
    $("jinghua-check-result").style.display = "block";

    const score = Number(res.score ?? 100);
    const passed = Boolean(res.passed);
    const badge = $("jinghua-score-badge");
    badge.textContent = `精话锁得分：${score} 分`;
    badge.className = `pill-badge ${passed ? "jade" : "crimson"}`;
    $("jinghua-pass-text").textContent = passed ? "通过！旁白干净平实" : `发现 ${(res.hits || []).length} 处AI油腻旁白，建议洗稿`;
    $("jinghua-pass-text").style.color = passed ? "var(--jade)" : "var(--crimson)";

    $("jinghua-highlight-preview").innerHTML = renderHighlightedOilyText(text, res.hits || []);
    $("jinghua-rewrite-guide").value = res.rewrite_guide || "旁白保持干净克制，无需修改。";

    const hits = res.hits || [];
    $("jinghua-hits-list").innerHTML = hits.length
      ? hits
          .map((h) => {
            const pat = h.pattern || h.bad_pattern || h.matched_text || "夸张旁白";
            const ln = h.line_num ? `第 ${h.line_num} 行` : "";
            const lineTxt = h.line_text || h.sentence || "";
            const reason = h.reason || "";
            const repl = h.good_replacement_example || h.suggestion || "";
            return `
              <div style="background:var(--crimson-soft); border:1px solid #ebb5be; border-radius:8px; padding:10px 12px; margin-bottom:8px; font-size:13px;">
                <div><strong style="color:var(--crimson);">命中「${escapeHtml(pat)}」</strong> ${ln ? `<span class="pill-badge">${escapeHtml(ln)}</span>` : ""}</div>
                ${lineTxt ? `<div style="margin-top:4px; color:var(--ink-main);">原句：${escapeHtml(lineTxt)}</div>` : ""}
                ${reason ? `<div style="margin-top:3px; color:var(--ink-secondary);">问题：${escapeHtml(reason)}</div>` : ""}
                ${repl ? `<div style="margin-top:3px; color:var(--jade); font-weight:600;">建议改为：${escapeHtml(repl)}</div>` : ""}
              </div>
            `;
          })
          .join("")
      : `<div class="diagnostics-banner ok" style="margin-bottom:0;">未命中任何禁用油腻词，台词与动作描写很舒服！</div>`;
  }

  // ---------- Tab 4: Interactive Drama Graph ----------
  async function loadProjects() {
    const data = await api("/api/projects");
    state.projects = data.items || [];
    const sel = $("drama-project-select");
    if (!state.projects.length) {
      sel.innerHTML = `<option value="">暂无剧本，请先新建</option>`;
      state.currentProjectId = "";
      renderEmptyGraph();
      return;
    }

    sel.innerHTML = state.projects
      .map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.title)}（${p.project_mode === "short_drama" ? "单线短剧" : "多支线互动"}）</option>`)
      .join("");

    if (!state.currentProjectId || !state.projects.some((p) => p.id === state.currentProjectId)) {
      state.currentProjectId = state.projects[0].id;
    }
    sel.value = state.currentProjectId;
    await loadProjectGraph(state.currentProjectId);
  }

  function renderEmptyGraph() {
    $("drama-diagnostics-banner").className = "diagnostics-banner warn";
    $("drama-diagnostics-banner").textContent = "还没有互动剧本，点击上方「＋ 新建互动剧本」开始创作。";
    $("graph-nodes-layer").innerHTML = "";
    $("graph-edges-layer").innerHTML = "";
    $("graph-svg").querySelectorAll("path").forEach((p) => p.remove());
  }

  async function loadProjectGraph(projectId) {
    if (!projectId) return;
    const graph = await api(`/api/projects/${encodeURIComponent(projectId)}/graph`);
    state.graph = graph;
    renderDiagnosticsBanner(graph);
    renderGraphCanvas();

    const nodes = graph.nodes || [];
    if (nodes.length) {
      if (!state.selectedNodeId || !nodes.some((n) => n.id === state.selectedNodeId)) {
        selectNode(nodes[0].id);
      } else {
        selectNode(state.selectedNodeId);
      }
    } else {
      clearNodeEditor();
    }
  }

  function renderDiagnosticsBanner(graph) {
    const d = graph.diagnostics || {};
    const nodes = graph.nodes || [];
    const deadEnds = d.dead_end_nodes || d.dead_ends || [];
    const unreachable = d.unreachable_nodes || d.unreachable || [];
    const endingsCount =
      d.endings?.total ??
      d.ending_count ??
      d.endings_count ??
      nodes.filter((n) => String(n.node_type || "").endsWith("_ending")).length;
    const avgScore = d.avg_jinghua_score ?? 100;

    const hasProblem = deadEnds.length > 0 || unreachable.length > 0 || endingsCount === 0;
    const banner = $("drama-diagnostics-banner");
    banner.className = `diagnostics-banner ${hasProblem ? "warn" : "ok"}`;

    const issues = [];
    if (deadEnds.length) {
      const names = deadEnds.map((x) => (typeof x === "object" ? `${x.node_code || ""} ${x.title || ""}` : x)).join("、");
      issues.push(`发现 ${deadEnds.length} 个断头死胡同（非结局但没接后续选项）：${names}`);
    }
    if (unreachable.length) {
      const names = unreachable.map((x) => (typeof x === "object" ? `${x.node_code || ""} ${x.title || ""}` : x)).join("、");
      issues.push(`发现 ${unreachable.length} 个孤立不可达场次：${names}`);
    }
    if (endingsCount === 0 && nodes.length > 0) {
      issues.push("还没有设置任何结局场次（好结局/坏结局/隐藏结局）");
    }

    const summary = `全剧共 ${nodes.length} 场戏 · ${(graph.choices || []).length} 条分支选项 · ${endingsCount} 个结局 · 精话锁均分 ${avgScore}`;
    banner.innerHTML = `
      <span>${hasProblem ? `⚠️ 结构提醒：${escapeHtml(issues.join("；"))}` : "✅ 结构体检健康：所有支线畅通，无断头死胡同！"}</span>
      <span style="font-weight:500;">${escapeHtml(summary)}</span>
    `;
  }

  function renderGraphCanvas() {
    const nodes = state.graph.nodes || [];
    const choices = state.graph.choices || [];
    const nodesLayer = $("graph-nodes-layer");
    const edgesLayer = $("graph-edges-layer");
    const svg = $("graph-svg");

    svg.querySelectorAll("path").forEach((p) => p.remove());
    edgesLayer.innerHTML = "";
    nodesLayer.innerHTML = "";

    const nodeMap = new Map(nodes.map((n) => [n.id, n]));
    const NODE_W = 210;
    const NODE_H = 100;

    // Group edges sharing same (from, to) to slightly offset curved paths
    const pairCounts = new Map();
    choices.forEach((c) => {
      const key = `${c.from_node_id}->${c.to_node_id}`;
      pairCounts.set(key, (pairCounts.get(key) || 0) + 1);
    });
    const pairIdx = new Map();

    for (const c of choices) {
      const from = nodeMap.get(c.from_node_id);
      const to = nodeMap.get(c.to_node_id);
      if (!from || !to) continue;

      const key = `${c.from_node_id}->${c.to_node_id}`;
      const idx = pairIdx.get(key) || 0;
      pairIdx.set(key, idx + 1);
      const totalSame = pairCounts.get(key) || 1;
      const offset = (idx - (totalSame - 1) / 2) * 34;

      const x1 = Number(from.pos_x || 120) + NODE_W / 2;
      const y1 = Number(from.pos_y || 120) + NODE_H / 2;
      const x2 = Number(to.pos_x || 120) + NODE_W / 2;
      const y2 = Number(to.pos_y || 120) + NODE_H / 2;

      const mx = (x1 + x2) / 2;
      const my = (y1 + y2) / 2;
      const dx = x2 - x1;
      const dy = y2 - y1;
      const len = Math.hypot(dx, dy) || 1;
      const nx = -dy / len;
      const ny = dx / len;
      const cx = mx + nx * (26 + offset);
      const cy = my + ny * (26 + offset);

      // End arrow near target card edge
      const tx = x2 - (dx / len) * 65;
      const ty = y2 - (dy / len) * 45;

      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", `M ${x1} ${y1} Q ${cx} ${cy} ${tx} ${ty}`);
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", "#677080");
      path.setAttribute("stroke-width", "2.2");
      path.setAttribute("marker-end", "url(#arrow-head)");
      svg.appendChild(path);

      // Midpoint on quadratic bezier t=0.5
      const lx = 0.25 * x1 + 0.5 * cx + 0.25 * tx;
      const ly = 0.25 * y1 + 0.5 * cy + 0.25 * ty;

      const deltaText = formatDelta(c.state_delta);
      const lbl = document.createElement("div");
      lbl.className = "graph-edge-label";
      lbl.style.left = `${lx}px`;
      lbl.style.top = `${ly}px`;
      lbl.innerHTML = `
        <div style="font-weight:700; color:var(--ink-main);">${escapeHtml(c.choice_label)}</div>
        ${c.choice_tag ? `<div style="color:var(--cobalt); font-size:11px;">${escapeHtml(c.choice_tag)}</div>` : ""}
        ${deltaText ? `<div style="color:var(--jade); font-size:11px; font-weight:600;">${escapeHtml(deltaText)}</div>` : ""}
      `;
      lbl.addEventListener("click", () => openChoiceModal(c));
      edgesLayer.appendChild(lbl);
    }

    for (const n of nodes) {
      const card = document.createElement("div");
      const ntype = n.node_type || "scene";
      card.className = `story-node-card type-${ntype} ${state.selectedNodeId === n.id ? "selected" : ""}`;
      card.style.left = `${Number(n.pos_x || 120)}px`;
      card.style.top = `${Number(n.pos_y || 120)}px`;
      card.dataset.nodeId = n.id;

      const score = Number(n.jinghua_score ?? 100);
      card.innerHTML = `
        <div class="node-top">
          <span class="node-code">${escapeHtml(n.node_code || "S?")} · 第${n.episode_num || 1}集</span>
          <span class="pill-badge" style="font-size:11px; padding:2px 6px;">${escapeHtml(nodeTypeLabel(ntype))}</span>
        </div>
        <div class="node-title">${escapeHtml(n.title)}</div>
        <div class="node-meta">
          ${n.speaker_summary ? escapeHtml(n.speaker_summary) : "点击右侧写台词"} · 精话 ${score}分
        </div>
      `;

      // Drag & click handling
      card.addEventListener("mousedown", (ev) => {
        if (ev.button !== 0) return;
        const startMouseX = ev.clientX;
        const startMouseY = ev.clientY;
        const origX = Number(n.pos_x || 120);
        const origY = Number(n.pos_y || 120);
        let moved = false;

        function onMove(moveEv) {
          const dx = moveEv.clientX - startMouseX;
          const dy = moveEv.clientY - startMouseY;
          if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved = true;
          if (!moved) return;
          const nx = Math.max(16, Math.round(origX + dx));
          const ny = Math.max(16, Math.round(origY + dy));
          n.pos_x = nx;
          n.pos_y = ny;
          card.style.left = `${nx}px`;
          card.style.top = `${ny}px`;
          // Fast redraw edges while dragging
          redrawEdgesOnly(nodes, choices);
        }

        async function onUp() {
          window.removeEventListener("mousemove", onMove);
          window.removeEventListener("mouseup", onUp);
          if (!moved) {
            selectNode(n.id);
          } else {
            try {
              await api(`/api/nodes/${encodeURIComponent(n.id)}/position`, {
                method: "PATCH",
                body: JSON.stringify({ pos_x: n.pos_x, pos_y: n.pos_y }),
              });
            } catch (err) {
              toast(`保存坐标失败：${err.message}`, "error");
            }
          }
        }

        window.addEventListener("mousemove", onMove);
        window.addEventListener("mouseup", onUp);
      });

      nodesLayer.appendChild(card);
    }
  }

  function redrawEdgesOnly(nodes, choices) {
    // Re-render only edges & SVG paths smoothly during drag
    const svg = $("graph-svg");
    const edgesLayer = $("graph-edges-layer");
    svg.querySelectorAll("path").forEach((p) => p.remove());
    edgesLayer.innerHTML = "";
    const nodeMap = new Map(nodes.map((n) => [n.id, n]));
    const NODE_W = 210;
    const NODE_H = 100;

    for (const c of choices) {
      const from = nodeMap.get(c.from_node_id);
      const to = nodeMap.get(c.to_node_id);
      if (!from || !to) continue;
      const x1 = Number(from.pos_x || 120) + NODE_W / 2;
      const y1 = Number(from.pos_y || 120) + NODE_H / 2;
      const x2 = Number(to.pos_x || 120) + NODE_W / 2;
      const y2 = Number(to.pos_y || 120) + NODE_H / 2;
      const dx = x2 - x1;
      const dy = y2 - y1;
      const len = Math.hypot(dx, dy) || 1;
      const cx = (x1 + x2) / 2 + (-dy / len) * 26;
      const cy = (y1 + y2) / 2 + (dx / len) * 26;
      const tx = x2 - (dx / len) * 65;
      const ty = y2 - (dy / len) * 45;

      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", `M ${x1} ${y1} Q ${cx} ${cy} ${tx} ${ty}`);
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", "#677080");
      path.setAttribute("stroke-width", "2.2");
      path.setAttribute("marker-end", "url(#arrow-head)");
      svg.appendChild(path);

      const lx = 0.25 * x1 + 0.5 * cx + 0.25 * tx;
      const ly = 0.25 * y1 + 0.5 * cy + 0.25 * ty;
      const lbl = document.createElement("div");
      lbl.className = "graph-edge-label";
      lbl.style.left = `${lx}px`;
      lbl.style.top = `${ly}px`;
      lbl.innerHTML = `<div style="font-weight:700;">${escapeHtml(c.choice_label)}</div>`;
      edgesLayer.appendChild(lbl);
    }
  }

  function selectNode(nodeId) {
    state.selectedNodeId = nodeId;
    document.querySelectorAll(".story-node-card").forEach((el) => {
      el.classList.toggle("selected", el.dataset.nodeId === nodeId);
    });

    const node = (state.graph.nodes || []).find((n) => n.id === nodeId);
    if (!node) return;

    $("editor-heading").textContent = `编辑场次：${node.node_code || ""} ${node.title}`;
    $("editor-sub").textContent = `当前精话锁得分：${node.jinghua_score ?? 100} 分 · 支持改后自动存档对比`;
    $("edit-node-id").value = node.id;
    $("edit-node-code").value = node.node_code || "S1";
    $("edit-node-title").value = node.title || "";
    $("edit-node-episode").value = node.episode_num || 1;
    $("edit-node-type").value = node.node_type || "scene";
    $("edit-node-rating").value = String(node.user_rating || 0);
    $("edit-node-speakers").value = node.speaker_summary || "";
    $("edit-node-cue").value = node.camera_or_acting_cue || "";
    $("edit-node-script").value = node.script_text || "";
    $("edit-node-note").value = node.revision_note || "";
    $("node-check-feedback").innerHTML = "";

    renderSelectedNodeChoices(node.id);
  }

  function clearNodeEditor() {
    state.selectedNodeId = "";
    $("editor-heading").textContent = "选中场次台词编辑器";
    $("editor-sub").textContent = "请在左侧点选或新增一个场次卡片";
    $("form-node-editor").reset();
    $("edit-node-id").value = "";
    $("selected-node-choices").innerHTML = "";
  }

  function renderSelectedNodeChoices(nodeId) {
    const outgoing = (state.graph.choices || []).filter((c) => c.from_node_id === nodeId);
    const nodeMap = new Map((state.graph.nodes || []).map((n) => [n.id, n]));
    const box = $("selected-node-choices");

    if (!outgoing.length) {
      box.innerHTML = `<div style="font-size:13px; color:var(--ink-muted);">本场目前没有往外连出的玩家选项。如果是结局请保持“好/坏/隐藏结局”属性；如果是过场戏请点击上方「＋ 从本场加选项」。</div>`;
      return;
    }

    box.innerHTML = outgoing
      .map((c) => {
        const target = nodeMap.get(c.to_node_id);
        const targetLabel = target ? `${target.node_code} ${target.title}` : c.to_node_id;
        const deltaStr = formatDelta(c.state_delta);
        return `
          <div style="background:#fff; border:1px solid var(--border-soft); border-radius:8px; padding:8px 10px; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center; gap:8px;">
            <div style="font-size:13px;">
              <div><strong>${escapeHtml(c.choice_label)}</strong> → <span style="color:var(--cobalt);">${escapeHtml(targetLabel)}</span></div>
              <div style="font-size:12px; color:var(--ink-muted);">
                ${c.choice_tag ? `标签：${escapeHtml(c.choice_tag)} · ` : ""}
                ${c.condition_expr ? `条件：${escapeHtml(c.condition_expr)} · ` : ""}
                ${deltaStr ? `数值：${escapeHtml(deltaStr)}` : ""}
              </div>
            </div>
            <div style="display:flex; gap:6px;">
              <button type="button" class="btn btn-sm" data-edit-choice="${escapeHtml(c.id)}">编辑</button>
              <button type="button" class="btn btn-sm btn-crimson" data-del-choice="${escapeHtml(c.id)}">删</button>
            </div>
          </div>
        `;
      })
      .join("");

    box.querySelectorAll("[data-edit-choice]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const ch = (state.graph.choices || []).find((x) => x.id === btn.dataset.editChoice);
        if (ch) openChoiceModal(ch);
      });
    });

    box.querySelectorAll("[data-del-choice]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          await api(`/api/choices/${encodeURIComponent(btn.dataset.delChoice)}`, { method: "DELETE" });
          toast("已删除分支选项", "success");
          await loadProjectGraph(state.currentProjectId);
        } catch (err) {
          toast(err.message, "error");
        }
      });
    });
  }

  function openChoiceModal(existingChoice = null, defaultFromId = null) {
    const nodes = state.graph.nodes || [];
    if (nodes.length < 2) {
      toast("至少需要 2 个剧情场次才能连线哦", "error");
      return;
    }
    const opts = nodes.map((n) => `<option value="${escapeHtml(n.id)}">${escapeHtml(n.node_code)} · ${escapeHtml(n.title)}</option>`).join("");
    $("choice-from-node").innerHTML = opts;
    $("choice-to-node").innerHTML = opts;

    if (existingChoice) {
      $("choice-edit-id").value = existingChoice.id;
      $("choice-from-node").value = existingChoice.from_node_id;
      $("choice-to-node").value = existingChoice.to_node_id;
      $("choice-label-input").value = existingChoice.choice_label || "";
      $("choice-tag-input").value = existingChoice.choice_tag || "";
      $("choice-condition-input").value = existingChoice.condition_expr || "";
      $("choice-delta-input").value = JSON.stringify(existingChoice.state_delta || {});
      $("btn-delete-choice-modal").style.display = "inline-flex";
    } else {
      $("choice-edit-id").value = "";
      $("choice-from-node").value = defaultFromId || state.selectedNodeId || nodes[0].id;
      $("choice-to-node").value = nodes.find((n) => n.id !== $("choice-from-node").value)?.id || nodes[0].id;
      $("choice-label-input").value = "";
      $("choice-tag-input").value = "";
      $("choice-condition-input").value = "";
      $("choice-delta-input").value = '{"好感度": 10}';
      $("btn-delete-choice-modal").style.display = "none";
    }
    openModal("modal-choice-editor");
  }

  // ---------- Event Bindings ----------
  function bindEvents() {
    $("btn-refresh-all").addEventListener("click", async () => {
      await refreshAll();
      toast("已刷新全部最新数据", "success");
    });

    // Tab 1
    $("btn-show-all-drawers").addEventListener("click", async () => {
      state.selectedDrawerId = "";
      renderDrawers();
      await loadWorks();
    });

    $("btn-open-new-drawer").addEventListener("click", () => openModal("modal-new-drawer"));

    $("form-new-drawer").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api("/api/genres", {
          method: "POST",
          body: JSON.stringify({
            name: $("drawer-name-input").value.trim(),
            genre_id: $("drawer-id-input").value.trim() || null,
            description: $("drawer-desc-input").value.trim(),
            channel_hint: $("drawer-hint-input").value.trim(),
          }),
        });
        $("form-new-drawer").reset();
        closeAllModals();
        toast("新类型抽屉已创建", "success");
        await loadDashboard();
      } catch (err) {
        toast(err.message, "error");
      }
    });

    $("form-import-novel").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = $("btn-import-submit");
      btn.disabled = true;
      btn.textContent = "正在读取分章并检测防盗乱码……";
      try {
        const data = await api("/api/works/import", {
          method: "POST",
          body: JSON.stringify({
            file_path: $("import-file-path").value.trim(),
            genre_id: $("import-genre-id").value,
            title: $("import-title").value.trim() || null,
            author: $("import-author").value.trim(),
          }),
        });
        const r = data.result || {};
        $("import-result-box").innerHTML = `
          <div style="background:var(--jade-soft); border:1px solid #b8e3d1; border-radius:10px; padding:12px; font-size:13.5px;">
            <div style="font-weight:700; color:var(--jade); margin-bottom:4px;">${r.already_exists ? "该书已在书架中：" : "导入成功："}《${escapeHtml(r.title || "新书")}》</div>
            <div>共切出 ${r.total_chapters ?? 0} 章 · 完好可用 ${r.clean_chapters ?? 0} 章 · 自动隔离坏章 ${r.corrupted_chapters ?? 0} 章</div>
            <div>已抽出 ${r.pending_snippets_count ?? r.extracted_snippets ?? 0} 段候选对白放进「试菜台」</div>
            <button id="btn-go-taste-after-import" class="btn btn-sm btn-primary" type="button" style="margin-top:8px;">马上去试菜打勾 ✓/✗</button>
          </div>
        `;
        $("btn-go-taste-after-import").addEventListener("click", async () => {
          $("taste-work-filter").value = r.work_id || "";
          switchMainTab("tab-taste");
          await loadPendingSnippets();
        });
        toast("小说导入完成！", "success");
        await Promise.all([loadDashboard(), loadWorks(), loadPendingSnippets()]);
      } catch (err) {
        $("import-result-box").innerHTML = `<div style="background:var(--crimson-soft); color:var(--crimson); padding:10px; border-radius:8px; font-size:13px;">${escapeHtml(err.message)}</div>`;
        toast(err.message, "error");
      } finally {
        btn.disabled = false;
        btn.textContent = "一键导入并挑出试菜对白";
      }
    });

    // Reader pagination
    $("btn-span-prev").addEventListener("click", () => {
      const nextStart = Math.max(1, state.reader.lineStart - 80);
      loadChapterSpan(state.reader.workId, state.reader.chapterNum, nextStart, nextStart + 79);
    });
    $("btn-span-next").addEventListener("click", () => {
      const nextStart = state.reader.lineEnd + 1;
      loadChapterSpan(state.reader.workId, state.reader.chapterNum, nextStart, nextStart + 79);
    });

    // Tab 2
    $("taste-genre-filter").addEventListener("change", () => loadPendingSnippets());
    $("taste-work-filter").addEventListener("change", () => loadPendingSnippets());
    $("btn-reload-taste").addEventListener("click", () => loadPendingSnippets());

    $("kb-genre-filter").addEventListener("change", () => {
      loadApprovedSnippets();
      loadCharacters();
      loadHighlights();
    });
    $("btn-kb-search").addEventListener("click", () => loadApprovedSnippets());
    $("kb-search-query").addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        loadApprovedSnippets();
      }
    });

    $("btn-open-add-snippet").addEventListener("click", () => openModal("modal-add-snippet"));
    $("btn-open-add-char").addEventListener("click", () => openModal("modal-add-char"));
    $("btn-open-add-highlight").addEventListener("click", () => openModal("modal-add-highlight"));

    $("form-add-snippet").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api("/api/snippets", {
          method: "POST",
          body: JSON.stringify({
            genre_id: $("add-snippet-genre").value,
            scene_tag: $("add-snippet-tag").value.trim(),
            raw_text: $("add-snippet-raw").value.trim(),
            trigger_context: $("add-snippet-context").value.trim(),
            why_funny_or_effective: $("add-snippet-why").value.trim(),
            status: "approved",
          }),
        });
        $("form-add-snippet").reset();
        closeAllModals();
        toast("好梗已直接收进抽屉！", "success");
        await Promise.all([loadDashboard(), loadApprovedSnippets()]);
      } catch (err) {
        toast(err.message, "error");
      }
    });

    $("form-add-char").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const samples = $("char-samples")
          .value.split(/\r?\n/)
          .map((s) => s.trim())
          .filter(Boolean);
        await api("/api/characters", {
          method: "POST",
          body: JSON.stringify({
            genre_id: $("char-genre").value,
            name: $("char-name").value.trim(),
            role_type: $("char-role").value.trim(),
            traits: $("char-traits").value.trim(),
            speech_style: $("char-speech").value.trim(),
            forbidden_words: $("char-forbidden").value.trim(),
            sample_lines: samples,
          }),
        });
        $("form-add-char").reset();
        closeAllModals();
        toast("人物声音卡已保存！", "success");
        await Promise.all([loadDashboard(), loadCharacters()]);
      } catch (err) {
        toast(err.message, "error");
      }
    });

    $("form-add-highlight").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api("/api/highlights", {
          method: "POST",
          body: JSON.stringify({
            genre_id: $("hl-genre").value,
            hook_type: $("hl-hook-type").value.trim(),
            plain_summary: $("hl-summary").value.trim(),
            why_it_hooks: $("hl-why").value.trim(),
          }),
        });
        $("form-add-highlight").reset();
        closeAllModals();
        toast("名场面钩子卡已保存！", "success");
        await loadHighlights();
      } catch (err) {
        toast(err.message, "error");
      }
    });

    // Tab 3
    $("btn-fill-oily-demo").addEventListener("click", () => {
      $("jinghua-test-input").value =
        '陆总推门进来，满屋杀气瞬间卡壳，他粗嗓门硬生生夹细了八度，老脸涨成猪肝色，空气突然安静得可怕。\n旁边的助理石化在原地，嘴角抽搐了一下，眼底闪过一丝不易察觉的三分薄凉。\n“你别乱动，先听我说完。”';
    });

    $("btn-run-jinghua-check").addEventListener("click", async () => {
      const txt = $("jinghua-test-input").value.trim();
      if (!txt) {
        toast("请先粘贴一段需要体检的文本", "error");
        return;
      }
      try {
        await runJinghuaTester(txt);
      } catch (err) {
        toast(err.message, "error");
      }
    });

    $("form-add-rule").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api("/api/jinghua/rules", {
          method: "POST",
          body: JSON.stringify({
            bad_pattern: $("rule-bad-pattern").value.trim(),
            category: $("rule-category").value,
            reason: $("rule-reason").value.trim(),
            good_replacement_example: $("rule-replacement").value.trim(),
          }),
        });
        $("form-add-rule").reset();
        toast("已永久锁死这个讨厌表达！", "success");
        await Promise.all([loadJinghuaRules(), loadDashboard()]);
      } catch (err) {
        toast(err.message, "error");
      }
    });

    // Tab 4
    $("drama-project-select").addEventListener("change", async () => {
      state.currentProjectId = $("drama-project-select").value;
      state.selectedNodeId = "";
      await loadProjectGraph(state.currentProjectId);
    });

    $("btn-open-new-project").addEventListener("click", () => openModal("modal-new-project"));

    $("form-new-project").addEventListener("submit", async (e) => {
      e.preventDefault();
      let vars = {};
      try {
        vars = JSON.parse($("proj-vars").value.trim() || "{}");
      } catch {
        toast("初始数值变量必须是合法 JSON，例如 {\"好感度\": 0}", "error");
        return;
      }
      try {
        const data = await api("/api/projects", {
          method: "POST",
          body: JSON.stringify({
            title: $("proj-title").value.trim(),
            genre_id: $("proj-genre").value,
            project_mode: $("proj-mode").value,
            synopsis: $("proj-synopsis").value.trim(),
            state_vars: vars,
          }),
        });
        $("form-new-project").reset();
        closeAllModals();
        state.currentProjectId = data.item.id;
        state.selectedNodeId = "";
        toast("新互动剧本已创建", "success");
        await Promise.all([loadProjects(), loadDashboard()]);
      } catch (err) {
        toast(err.message, "error");
      }
    });

    $("btn-add-node").addEventListener("click", async () => {
      if (!state.currentProjectId) {
        toast("请先新建一个互动剧本", "error");
        return;
      }
      const existingCount = (state.graph.nodes || []).length;
      const code = `S${existingCount + 1}`;
      const px = 80 + ((existingCount * 240) % 760);
      const py = 80 + Math.floor((existingCount * 240) / 760) * 160;
      try {
        const data = await api(`/api/projects/${encodeURIComponent(state.currentProjectId)}/nodes`, {
          method: "POST",
          body: JSON.stringify({
            title: `新场次 ${code}`,
            node_code: code,
            episode_num: 1,
            node_type: existingCount === 0 ? "start" : "scene",
            pos_x: px,
            pos_y: py,
          }),
        });
        state.selectedNodeId = data.item.id;
        toast("已新增剧情场次", "success");
        await loadProjectGraph(state.currentProjectId);
      } catch (err) {
        toast(err.message, "error");
      }
    });

    $("btn-open-add-choice").addEventListener("click", () => openChoiceModal(null));
    $("btn-add-choice-from-selected").addEventListener("click", () =>
      openChoiceModal(null, state.selectedNodeId)
    );

    $("form-choice-editor").addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!state.currentProjectId) return;
      let delta = {};
      try {
        delta = JSON.parse($("choice-delta-input").value.trim() || "{}");
      } catch {
        toast("数值变化必须是合法 JSON，例如 {\"好感度\": 10}", "error");
        return;
      }
      try {
        await api(`/api/projects/${encodeURIComponent(state.currentProjectId)}/choices`, {
          method: "POST",
          body: JSON.stringify({
            choice_id: $("choice-edit-id").value || null,
            from_node_id: $("choice-from-node").value,
            to_node_id: $("choice-to-node").value,
            choice_label: $("choice-label-input").value.trim(),
            choice_tag: $("choice-tag-input").value.trim(),
            condition_expr: $("choice-condition-input").value.trim(),
            state_delta: delta,
          }),
        });
        closeAllModals();
        toast("分支连线已保存", "success");
        await loadProjectGraph(state.currentProjectId);
      } catch (err) {
        toast(err.message, "error");
      }
    });

    $("btn-delete-choice-modal").addEventListener("click", async () => {
      const cid = $("choice-edit-id").value;
      if (!cid) return;
      try {
        await api(`/api/choices/${encodeURIComponent(cid)}`, { method: "DELETE" });
        closeAllModals();
        toast("已删除分支连线", "success");
        await loadProjectGraph(state.currentProjectId);
      } catch (err) {
        toast(err.message, "error");
      }
    });

    $("form-node-editor").addEventListener("submit", async (e) => {
      e.preventDefault();
      const nid = $("edit-node-id").value;
      if (!nid) {
        toast("请先在左侧点选一个场次卡片", "error");
        return;
      }
      const curNode = (state.graph.nodes || []).find((n) => n.id === nid);
      try {
        await api(`/api/nodes/${encodeURIComponent(nid)}`, {
          method: "PUT",
          body: JSON.stringify({
            project_id: state.currentProjectId,
            node_code: $("edit-node-code").value.trim(),
            title: $("edit-node-title").value.trim(),
            episode_num: Number($("edit-node-episode").value || 1),
            node_type: $("edit-node-type").value,
            user_rating: Number($("edit-node-rating").value || 0),
            speaker_summary: $("edit-node-speakers").value.trim(),
            camera_or_acting_cue: $("edit-node-cue").value.trim(),
            script_text: $("edit-node-script").value,
            revision_note: $("edit-node-note").value.trim(),
            pos_x: curNode ? curNode.pos_x : 120,
            pos_y: curNode ? curNode.pos_y : 120,
          }),
        });
        toast("场次台词已保存（若有改动已留存历史版本）", "success");
        await loadProjectGraph(state.currentProjectId);
      } catch (err) {
        toast(err.message, "error");
      }
    });

    $("btn-node-jinghua").addEventListener("click", async () => {
      const txt = $("edit-node-script").value.trim();
      if (!txt) {
        toast("请先输入本场剧本正文再体检", "error");
        return;
      }
      try {
        const data = await api("/api/jinghua/check", {
          method: "POST",
          body: JSON.stringify({ text: txt }),
        });
        const r = data.result || {};
        const hits = r.hits || [];
        $("node-check-feedback").innerHTML = `
          <div style="background:${r.passed ? "var(--jade-soft)" : "var(--crimson-soft)"}; border-radius:8px; padding:10px; font-size:13px;">
            <strong>精话锁得分：${r.score ?? 100} 分</strong> · ${r.passed ? "干净过关！" : `发现 ${hits.length} 处油腻表达：${hits.map((h) => h.pattern || h.bad_pattern).join("、")}`}
            ${r.rewrite_guide && !r.passed ? `<div style="margin-top:4px; color:var(--ink-secondary);">${escapeHtml(r.rewrite_guide)}</div>` : ""}
          </div>
        `;
      } catch (err) {
        toast(err.message, "error");
      }
    });

    $("btn-node-originality").addEventListener("click", async () => {
      const txt = $("edit-node-script").value.trim();
      if (!txt) {
        toast("请先输入本场剧本正文再查重", "error");
        return;
      }
      const proj = state.graph.project || {};
      try {
        const data = await api("/api/originality/check", {
          method: "POST",
          body: JSON.stringify({
            draft_text: txt,
            genre_id: proj.genre_id || null,
          }),
        });
        const r = data.result || {};
        const passed = r.passed !== false && !r.is_plagiarized;
        const maxSim = r.max_similarity ?? r.similarity ?? 0;
        const matches = r.matches || r.warnings || [];
        $("node-check-feedback").innerHTML = `
          <div style="background:${passed ? "var(--jade-soft)" : "var(--amber-soft)"}; border-radius:8px; padding:10px; font-size:13px;">
            <strong>${escapeHtml(r.summary || (passed ? "通过，安全原创" : "存在高相似句子，建议改写"))}</strong>
            <div style="margin-top:2px;">最高局部相似度：${(Number(maxSim) * 100).toFixed(1)}%</div>
            ${matches.length ? `<div style="margin-top:4px; color:var(--ink-secondary);">最接近出处：《${escapeHtml(matches[0].work_title || "")}》${matches[0].chapter_num ? ` 第${matches[0].chapter_num}章` : ""}</div>` : ""}
          </div>
        `;
      } catch (err) {
        toast(err.message, "error");
      }
    });

    $("btn-node-revisions").addEventListener("click", async () => {
      const nid = $("edit-node-id").value;
      if (!nid) return;
      try {
        const data = await api(`/api/nodes/${encodeURIComponent(nid)}/revisions`);
        const items = data.items || [];
        $("text-viewer-title").textContent = "本场历史修改记录对比";
        $("text-viewer-body").innerHTML = items.length
          ? items
              .map(
                (rev) => `
                  <div style="border:1px solid var(--border-soft); border-radius:10px; padding:12px; margin-bottom:12px;">
                    <div style="display:flex; justify-content:space-between; font-size:12.5px; color:var(--ink-muted);">
                      <span>修改时间：${escapeHtml(rev.created_at || "")} · 评分：${rev.user_rating || 0} 星</span>
                      <span>说明：${escapeHtml(rev.note || "手动修改")}</span>
                    </div>
                    <div class="revision-diff">
                      <div class="revision-pane"><strong>改前：</strong>\n${escapeHtml(rev.old_text || "（空）")}</div>
                      <div class="revision-pane" style="background:#f5fbf7;"><strong>改后：</strong>\n${escapeHtml(rev.new_text || "（空）")}</div>
                    </div>
                  </div>
                `
              )
              .join("")
          : `<div class="empty-state">本场还没有产生历史修改记录（每次修改台词并点「保存本场剧本」后会自动留底）。</div>`;
        openModal("modal-text-viewer");
      } catch (err) {
        toast(err.message, "error");
      }
    });

    $("btn-node-delete").addEventListener("click", async () => {
      const nid = $("edit-node-id").value;
      if (!nid) return;
      try {
        await api(`/api/nodes/${encodeURIComponent(nid)}`, { method: "DELETE" });
        state.selectedNodeId = "";
        toast("已删除该剧情场次", "success");
        await loadProjectGraph(state.currentProjectId);
      } catch (err) {
        toast(err.message, "error");
      }
    });

    $("btn-export-markdown").addEventListener("click", async () => {
      if (!state.currentProjectId) return;
      try {
        const data = await api(`/api/projects/${encodeURIComponent(state.currentProjectId)}/export`, {
          method: "POST",
        });
        const md = data.markdown || "";
        $("text-viewer-title").textContent = "导出的互动影游完整台本 (Markdown，已自动存一份到 exports/ 目录)";
        $("text-viewer-body").innerHTML = `
          <textarea class="textarea" rows="18" readonly style="font-family: ui-monospace, Consolas, monospace;">${escapeHtml(md)}</textarea>
        `;
        openModal("modal-text-viewer");
      } catch (err) {
        toast(err.message, "error");
      }
    });
  }

  async function refreshAll() {
    await loadDashboard();
    await Promise.all([
      loadWorks(),
      loadPendingSnippets(),
      loadApprovedSnippets(),
      loadCharacters(),
      loadHighlights(),
      loadJinghuaRules(),
      loadProjects(),
    ]);
  }

  // ---------- Boot ----------
  document.addEventListener("DOMContentLoaded", async () => {
    initTabs();
    bindEvents();
    try {
      await refreshAll();
    } catch (err) {
      toast(`初始化加载失败：${err.message}`, "error");
    }
  });
})();
