(() => {
  'use strict';

  // -------------------------------------------------------------------------
  // 工具函数 & API 封装
  // -------------------------------------------------------------------------
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function escHtml(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function svgEl(tag, attrs = {}) {
    const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v !== undefined && v !== null) el.setAttribute(k, String(v));
    }
    return el;
  }

  async function api(url, options = {}) {
    const opts = { ...options };
    if (opts.body && !(opts.body instanceof FormData) && typeof opts.body !== 'string') {
      opts.headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
      opts.body = JSON.stringify(opts.body);
    }
    const res = await fetch(url, opts);
    let data = null;
    const ct = res.headers.get('content-type') || '';
    if (ct.includes('application/json')) {
      data = await res.json();
    } else {
      const text = await res.text();
      try { data = JSON.parse(text); } catch { data = { detail: text }; }
    }
    if (!res.ok) {
      throw new Error((data && (data.detail || data.message)) || `请求失败 (${res.status})`);
    }
    return data;
  }

  function toast(msg, type = 'info') {
    const stack = $('#toast-stack');
    const div = document.createElement('div');
    div.className = `toast ${type}`;
    div.textContent = msg;
    stack.appendChild(div);
    setTimeout(() => {
      div.style.opacity = '0';
      div.style.transition = 'opacity 0.25s ease';
      setTimeout(() => div.remove(), 260);
    }, 2800);
  }

  // -------------------------------------------------------------------------
  // 全局状态
  // -------------------------------------------------------------------------
  const CHANNEL_COLORS = {
    '女频': '#f472b6',
    '男频': '#60a5fa',
    '通用': '#34d399',
  };

  const NODE_TYPE_LABELS = {
    start: '起始幕',
    scene: '普通剧情',
    climax: '高潮反转',
    good_ending: '好结局',
    bad_ending: '坏结局',
    hidden_ending: '隐藏结局',
  };

  const state = {
    mode: 'universe', // 'universe' | 'branching'
    universe: {
      root: { total_works: 0, total_approved: 0, total_pending: 0, total_jinghua_rules: 0 },
      channels: [],
      tags: [],
      works: [],
      work_tag_links: [],
    },
    expandedChannels: new Set(['女频', '男频', '通用']),
    expandedTagIds: new Set(),
    onlyPopulated: false,
    searchQuery: '',
    searchHitGenreIds: new Set(),
    searchHitWorkIds: new Set(),

    selectedNodeId: null,
    hoveredNodeId: null,

    // 互动支线状态
    projects: [],
    currentProjectId: null,
    branchGraph: null,

    // 精话锁规则
    jinghuaRules: [],

    // 待上传文件与多标签草稿（从顶部按钮、抽屉按钮或拖放到标签上时暂存）
    pendingUploadFile: null,
    feedTargetGenreId: null,
    uploadDraft: {
      primaryTagId: null,
      extraTagIds: [],
      autoDetect: true,
    },

    // 视图平移缩放
    view: { x: 0, y: 0, scale: 1 },
  };

  function getTagById(tagId) {
    if (!tagId) return null;
    return (state.universe.tags || []).find((t) => t.id === tagId) || null;
  }

  function getTagName(tagId) {
    return getTagById(tagId)?.name || tagId || '未分类';
  }

  function getTagColor(tagId) {
    const t = getTagById(tagId);
    return t ? (CHANNEL_COLORS[t.channel] || '#7dd3fc') : '#7dd3fc';
  }

  function getWorkTagIds(work) {
    if (!work) return [];
    const ordered = [];
    const seen = new Set();
    const pushId = (id) => {
      if (id && !seen.has(id)) {
        seen.add(id);
        ordered.push(id);
      }
    };
    if (work.genre_id) pushId(work.genre_id);
    if (Array.isArray(work.tag_ids)) {
      work.tag_ids.forEach(pushId);
    }
    for (const link of state.universe.work_tag_links || []) {
      if (link.work_id === work.id && link.tag_id) {
        if (link.is_primary) {
          // ensure primary stays first if work.genre_id was absent
          if (!ordered.includes(link.tag_id)) ordered.unshift(link.tag_id);
          seen.add(link.tag_id);
        } else {
          pushId(link.tag_id);
        }
      }
    }
    return ordered;
  }

  // -------------------------------------------------------------------------
  // 物理弹簧模拟 & SVG 图谱渲染引擎
  // -------------------------------------------------------------------------
  const simNodes = new Map(); // id -> { id, x, y, tx, ty, vx, vy, width, height, pinned, data }
  let simEdges = [];          // [{ id, from, to, weight, label, kind, dashed, isGood, isBad }]
  let simGroupBoxes = [];     // [{ x, y, width, height, label }]
  const nodeDomMap = new Map(); // id -> SVGGElement
  const edgeDomMap = new Map(); // id -> { group, path, hit, badgeBg, badgeText }

  let rafId = null;
  let particleRafId = null;
  let activeParticles = [];   // [{ edgeId, t, speed, el }]
  let didInitialFit = false;

  const viewportEl = $('#graph-viewport');
  const layerGroups = $('#layer-groups');
  const layerEdges = $('#layer-edges');
  const layerParticles = $('#layer-particles');
  const layerNodes = $('#layer-nodes');
  const svgElRoot = $('#graph-svg');

  function measurePillWidth(title, badge = '', hasHalo = false) {
    // 估算中文字符 12px，ASCII 7px，含左侧圆点与右侧计数角标
    let titleW = 0;
    for (const ch of String(title || '')) {
      titleW += ch.charCodeAt(0) > 255 ? 12.2 : 7.2;
    }
    let badgeW = badge ? String(badge).length * 7 + 12 : 0;
    const base = 32 + titleW + badgeW + (hasHalo ? 8 : 0);
    return Math.max(96, Math.min(270, Math.round(base)));
  }

  function truncatePillText(text, maxChars = 15) {
    const s = String(text || '');
    return s.length > maxChars ? s.slice(0, maxChars - 1) + '…' : s;
  }

  function computeUniverseLayout() {
    const { root, channels, tags, works } = state.universe;
    const nextNodes = new Map();
    const nextEdges = [];
    const nextBoxes = [];

    const q = state.searchQuery.trim().toLowerCase();

    // 规范化 channels 顺序：女频 / 男频 / 通用
    const defaultChannels = [
      { id: 'female', name: '女频' },
      { id: 'male', name: '男频' },
      { id: 'common', name: '通用' },
    ];
    const channelList = defaultChannels.map((dc) => {
      const found = (channels || []).find((c) => c.name === dc.name || c.id === dc.id);
      return found ? { ...dc, ...found } : dc;
    });

    // 统计各标签关联的所有书籍（含主标签与副标签，用于计数与搜索命中）
    const worksByAnyTag = new Map();
    const workTagIdsMap = new Map();
    for (const w of works || []) {
      const allIds = getWorkTagIds(w);
      if (allIds.length === 0) allIds.push(w.genre_id || 'common');
      workTagIdsMap.set(w.id, allIds);
      for (const gid of allIds) {
        if (!worksByAnyTag.has(gid)) worksByAnyTag.set(gid, []);
        worksByAnyTag.get(gid).push(w);
      }
    }

    const totalWorks = root?.total_works ?? (works || []).length;
    const totalApproved = root?.approved_count ?? root?.total_approved ?? (tags || []).reduce((s, t) => s + (t.approved_count || 0), 0);
    const totalPending = root?.pending_count ?? root?.total_pending ?? (tags || []).reduce((s, t) => s + (t.pending_count || 0), 0);

    // 0 层：总库根节点
    const rootId = 'root:kb';
    const rootBadge = totalWorks > 0 || totalApproved > 0 ? `${totalWorks}书·${totalApproved}梗` : '总库';
    nextNodes.set(rootId, {
      id: rootId,
      kind: 'root',
      title: '编剧总库',
      badge: rootBadge,
      tx: 0,
      ty: 70,
      width: measurePillWidth('编剧总库', rootBadge, true),
      height: 38,
      populated: true,
      hasHalo: true,
      dotColor: '#93c5fd',
      pendingCount: totalPending,
      searchHit: q ? '编剧总库'.includes(q) : false,
      raw: root,
    });

    // 1 层 & 2 层：按三大频道分列，保证信息量大而不乱成毛线团
    const colWidth = 430;
    const colGap = 44;
    const totalSpan = channelList.length * colWidth + (channelList.length - 1) * colGap;
    const startX = -totalSpan / 2 + colWidth / 2;

    channelList.forEach((ch, colIdx) => {
      const chName = ch.name;
      const chCenterX = startX + colIdx * (colWidth + colGap);
      const chTagsAll = (tags || []).filter((t) => (t.channel || '通用') === chName);

      // 过滤“仅看有货”与搜索命中
      const chTags = chTagsAll.filter((t) => {
        const hasStock = (t.works_count || 0) > 0 || (t.approved_count || 0) > 0 || (t.pending_count || 0) > 0 || (t.characters_count || 0) > 0;
        if (state.onlyPopulated && !hasStock) return false;
        return true;
      });

      const chWorksCount = ch.works_count ?? chTagsAll.reduce((s, t) => s + (t.works_count || 0), 0);
      const chApproved = ch.approved_count ?? chTagsAll.reduce((s, t) => s + (t.approved_count || 0), 0);
      const chPending = ch.pending_count ?? chTagsAll.reduce((s, t) => s + (t.pending_count || 0), 0);
      const chPopulated = chWorksCount > 0 || chApproved > 0 || chPending > 0;

      const chId = `ch:${chName}`;
      const chBadge = chPopulated ? `${chWorksCount}书·${chApproved}梗` : `${chTagsAll.length}标签`;
      nextNodes.set(chId, {
        id: chId,
        kind: 'channel',
        channelName: chName,
        title: chName,
        badge: chBadge,
        tx: chCenterX,
        ty: 185,
        width: measurePillWidth(chName, chBadge, false),
        height: 36,
        populated: chPopulated,
        hasHalo: false,
        dotColor: CHANNEL_COLORS[chName] || '#7dd3fc',
        pendingCount: chPending,
        searchHit: q ? chName.toLowerCase().includes(q) : false,
        raw: ch,
      });

      nextEdges.push({
        id: `e:${rootId}->${chId}`,
        from: rootId,
        to: chId,
        weight: chApproved + chWorksCount * 2,
        weightLabel: chApproved > 0 ? String(chApproved) : (chWorksCount > 0 ? String(chWorksCount) : ''),
        dashed: !chPopulated,
        curveBias: (colIdx - 1) * 18,
      });

      if (!state.expandedChannels.has(chName) || chTags.length === 0) return;

      // 将标签按 sub_group 分组（题材主干 / 爆款设定梗）整齐分排
      const groupOrder = [];
      const bySub = new Map();
      for (const t of chTags) {
        const sg = t.sub_group || '题材主干';
        if (!bySub.has(sg)) {
          bySub.set(sg, []);
          groupOrder.push(sg);
        }
        bySub.get(sg).push(t);
      }

      let curY = 278;
      const colsPerRow = 3;
      const cellW = 140;
      const rowH = 54;

      groupOrder.forEach((sgName) => {
        const items = bySub.get(sgName);
        const rowsCount = Math.ceil(items.length / colsPerRow);
        const boxTop = curY - 26;
        const boxH = rowsCount * rowH + 34;

        nextBoxes.push({
          x: chCenterX - colWidth / 2 + 4,
          y: boxTop,
          width: colWidth - 8,
          height: boxH,
          label: `${chName} · ${sgName}`,
        });

        items.forEach((tag, idx) => {
          const r = Math.floor(idx / colsPerRow);
          const c = idx % colsPerRow;
          const itemsInThisRow = Math.min(colsPerRow, items.length - r * colsPerRow);
          const rowSpan = (itemsInThisRow - 1) * cellW;
          const tx = chCenterX - rowSpan / 2 + c * cellW + (r % 2 === 1 ? 8 : -8);
          const ty = curY + r * rowH + 14;

          const tagWorks = worksByAnyTag.get(tag.id) || [];
          const worksCnt = Math.max(tag.works_count || 0, tagWorks.length);
          const approvedCnt = tag.approved_count || 0;
          const pendingCnt = tag.pending_count || 0;
          const populated = worksCnt > 0 || approvedCnt > 0 || pendingCnt > 0 || (tag.characters_count || 0) > 0;
          const badge = approvedCnt > 0
            ? `${approvedCnt}`
            : (pendingCnt > 0 ? `待${pendingCnt}` : (worksCnt > 0 ? `${worksCnt}书` : ''));

          const hitSelf = q && (
            state.searchHitGenreIds.has(tag.id) ||
            (tag.name || '').toLowerCase().includes(q) ||
            (tag.description || '').toLowerCase().includes(q) ||
            (tag.platforms || '').toLowerCase().includes(q) ||
            tagWorks.some((w) => (w.title || '').toLowerCase().includes(q) || state.searchHitWorkIds.has(w.id))
          );

          const tagNodeId = `tag:${tag.id}`;
          const pillTitle = truncatePillText(tag.name, 7);
          nextNodes.set(tagNodeId, {
            id: tagNodeId,
            kind: 'tag',
            genreId: tag.id,
            channelName: chName,
            title: pillTitle,
            fullTitle: tag.name,
            badge,
            tx,
            ty,
            width: measurePillWidth(pillTitle, badge, false),
            height: 32,
            populated,
            hasHalo: false,
            dotColor: CHANNEL_COLORS[chName] || '#7dd3fc',
            pendingCount: pendingCnt,
            searchHit: Boolean(hitSelf),
            parentId: chId,
            raw: tag,
          });

          const edgeWeight = approvedCnt + worksCnt * 2;
          nextEdges.push({
            id: `e:${chId}->${tagNodeId}`,
            from: chId,
            to: tagNodeId,
            weight: edgeWeight,
            weightLabel: approvedCnt > 0 ? String(approvedCnt) : (worksCnt > 0 ? String(worksCnt) : ''),
            dashed: !populated,
            curveBias: (c - 1) * 14,
          });
        });

        curY += rowsCount * rowH + 46;
      });
    });

    // 3 层：一书多标签动态渲染 —— 书本胶囊位于主标签下方，主标签实线弧线 + 副标签柔和半透明虚线跨列弧线
    const worksByAnchor = new Map();
    for (const w of works || []) {
      const allIds = workTagIdsMap.get(w.id) || [w.genre_id || 'common'];
      const primaryId = w.genre_id || allIds[0];
      const workHit = q && ((w.title || '').toLowerCase().includes(q) || state.searchHitWorkIds.has(w.id));
      const anyTagExpanded = allIds.some((tid) => state.expandedTagIds.has(tid));
      if (!anyTagExpanded && !workHit) continue;

      let anchorTagId = null;
      if (nextNodes.has(`tag:${primaryId}`)) {
        anchorTagId = primaryId;
      } else {
        anchorTagId = allIds.find((tid) => nextNodes.has(`tag:${tid}`) && (state.expandedTagIds.has(tid) || workHit))
          || allIds.find((tid) => nextNodes.has(`tag:${tid}`))
          || null;
      }
      if (!anchorTagId) continue;

      if (!worksByAnchor.has(anchorTagId)) worksByAnchor.set(anchorTagId, []);
      worksByAnchor.get(anchorTagId).push({ work: w, allIds, primaryId });
    }

    for (const [anchorTagId, entries] of worksByAnchor.entries()) {
      const anchorNode = nextNodes.get(`tag:${anchorTagId}`);
      if (!anchorNode) continue;
      const wGap = 166;
      const wSpan = (entries.length - 1) * wGap;

      entries.forEach(({ work: w, allIds, primaryId }, wIdx) => {
        const wx = anchorNode.tx - wSpan / 2 + wIdx * wGap;
        const wy = anchorNode.ty + 78 + (wIdx % 2) * 18;
        const wId = `work:${w.id}`;
        const wTitle = `《${truncatePillText(w.title, 8)}》`;
        const cleanCh = w.clean_chapters ?? 0;
        const corrCh = w.corrupted_chapters ?? 0;
        const multiSuffix = allIds.length > 1 ? `·${allIds.length}签` : '';
        const wBadge = (corrCh > 0 ? `${cleanCh}净/${corrCh}防` : `${cleanCh}章`) + multiSuffix;

        nextNodes.set(wId, {
          id: wId,
          kind: 'work',
          workId: w.id,
          genreId: primaryId,
          tagIds: allIds,
          title: wTitle,
          fullTitle: w.title,
          badge: wBadge,
          tx: wx,
          ty: wy,
          width: measurePillWidth(wTitle, wBadge, false),
          height: 32,
          populated: true,
          hasHalo: false,
          dotColor: corrCh > 0 ? '#fbbf24' : '#7dd3fc',
          pendingCount: w.pending_count || 0,
          searchHit: q ? ((w.title || '').toLowerCase().includes(q) || state.searchHitWorkIds.has(w.id)) : false,
          parentId: `tag:${anchorTagId}`,
          raw: w,
        });

        // 为该书所有可见的归属标签绘制连线：主标签实线发光，副标签柔和半透明虚线跨列弧线
        allIds.forEach((tid, tIdx) => {
          const tNodeId = `tag:${tid}`;
          const tNode = nextNodes.get(tNodeId);
          if (!tNode) return;
          const isPrimary = tid === primaryId;
          const dxSign = tNode.tx === wx ? 0 : (wx > tNode.tx ? -1 : 1);
          nextEdges.push({
            id: isPrimary ? `e:${tNodeId}->${wId}` : `e:sec:${tNodeId}->${wId}`,
            from: tNodeId,
            to: wId,
            weight: isPrimary ? Math.max(2, Math.min(8, Math.round(cleanCh / 3))) : 1,
            weightLabel: isPrimary && cleanCh > 0 ? String(cleanCh) : '',
            dashed: !isPrimary,
            isPrimaryWorkEdge: isPrimary,
            isSecondaryWorkEdge: !isPrimary,
            curveBias: isPrimary
              ? (wIdx - (entries.length - 1) / 2) * 12
              : dxSign * (32 + (tIdx % 3) * 18),
          });
        });
      });
    }

    applyLayoutTargets(nextNodes, nextEdges, nextBoxes);
  }

  function computeBranchingLayout() {
    const graph = state.branchGraph;
    const nextNodes = new Map();
    const nextEdges = [];
    const nextBoxes = [];

    if (!graph || !graph.nodes || graph.nodes.length === 0) {
      applyLayoutTargets(nextNodes, nextEdges, nextBoxes);
      return;
    }

    const deadSet = new Set(graph.diagnostics?.dead_end_node_ids || []);
    const unreachableSet = new Set(graph.diagnostics?.unreachable_node_ids || []);

    for (const n of graph.nodes) {
      const nid = `bnode:${n.id}`;
      const isGood = n.node_type === 'good_ending';
      const isBad = n.node_type === 'bad_ending';
      const isHidden = n.node_type === 'hidden_ending';
      const isStart = n.node_type === 'start';
      const isDead = deadSet.has(n.id) || unreachableSet.has(n.id);

      const label = `${n.node_code || 'S'} · ${truncatePillText(n.title, 10)}`;
      const badge = NODE_TYPE_LABELS[n.node_type] || '';
      let dotColor = '#7dd3fc';
      if (isGood) dotColor = '#fbbf24';
      else if (isBad) dotColor = '#f87171';
      else if (isHidden) dotColor = '#a78bfa';
      else if (n.node_type === 'climax') dotColor = '#f472b6';
      else if (isDead) dotColor = '#94a3b8';

      nextNodes.set(nid, {
        id: nid,
        kind: 'branch_node',
        storyNodeId: n.id,
        title: label,
        fullTitle: n.title,
        badge,
        tx: (n.pos_x ?? 120) * 1.45,
        ty: (n.pos_y ?? 120) * 1.25,
        width: measurePillWidth(label, badge, isStart || isGood || isBad || isHidden),
        height: 36,
        populated: !isDead && Boolean((n.script_text || '').trim() || isGood || isBad || isHidden),
        hasHalo: isStart || isGood || isBad || isHidden,
        haloKind: isGood ? 'good' : isBad ? 'bad' : isHidden ? 'hidden' : 'start',
        dotColor,
        pendingCount: isDead ? 1 : 0,
        searchHit: false,
        raw: n,
      });
    }

    for (const ch of graph.choices || []) {
      const fromId = `bnode:${ch.from_node_id}`;
      const toId = `bnode:${ch.to_node_id}`;
      const targetNode = nextNodes.get(toId);
      const isGood = targetNode?.haloKind === 'good';
      const isBad = targetNode?.haloKind === 'bad';
      nextEdges.push({
        id: `bchoice:${ch.id}`,
        choiceId: ch.id,
        from: fromId,
        to: toId,
        weight: 3,
        choiceLabel: truncatePillText(ch.choice_label, 18),
        dashed: false,
        isGood,
        isBad,
        curveBias: 16,
      });
    }

    applyLayoutTargets(nextNodes, nextEdges, nextBoxes);
  }

  function applyLayoutTargets(nextNodes, nextEdges, nextBoxes) {
    // 保留已有节点的当前坐标与速度，新增节点从父节点平滑弹簧长出
    for (const [id, spec] of nextNodes.entries()) {
      const prev = simNodes.get(id);
      if (prev) {
        prev.title = spec.title;
        prev.fullTitle = spec.fullTitle;
        prev.badge = spec.badge;
        prev.width = spec.width;
        prev.height = spec.height;
        prev.populated = spec.populated;
        prev.hasHalo = spec.hasHalo;
        prev.haloKind = spec.haloKind;
        prev.dotColor = spec.dotColor;
        prev.pendingCount = spec.pendingCount;
        prev.searchHit = spec.searchHit;
        prev.raw = spec.raw;
        prev.kind = spec.kind;
        prev.genreId = spec.genreId;
        prev.tagIds = spec.tagIds;
        prev.workId = spec.workId;
        prev.channelName = spec.channelName;
        prev.storyNodeId = spec.storyNodeId;
        prev.parentId = spec.parentId;
        if (!prev.pinned) {
          prev.tx = spec.tx;
          prev.ty = spec.ty;
        }
      } else {
        const parent = spec.parentId ? (simNodes.get(spec.parentId) || nextNodes.get(spec.parentId)) : null;
        simNodes.set(id, {
          ...spec,
          x: parent ? (parent.x ?? parent.tx) : spec.tx,
          y: parent ? (parent.y ?? parent.ty) : spec.ty - 14,
          vx: 0,
          vy: 0,
          pinned: false,
        });
      }
    }

    // 删除已不在当前视图的节点
    for (const id of Array.from(simNodes.keys())) {
      if (!nextNodes.has(id)) {
        simNodes.delete(id);
      }
    }

    simEdges = nextEdges;
    simGroupBoxes = nextBoxes;

    rebuildSvgDom();
    startPhysicsLoop();
  }

  function rebuildSvgDom() {
    // 1. 分组框与微标题
    layerGroups.innerHTML = '';
    for (const box of simGroupBoxes) {
      const rect = svgEl('rect', {
        class: 'group-box-frame',
        x: box.x,
        y: box.y,
        width: box.width,
        height: box.height,
      });
      const label = svgEl('text', {
        class: 'group-header-label',
        x: box.x + 12,
        y: box.y + 15,
      });
      label.textContent = box.label;
      layerGroups.appendChild(rect);
      layerGroups.appendChild(label);
    }

    // 2. 连线
    layerEdges.innerHTML = '';
    edgeDomMap.clear();
    for (const edge of simEdges) {
      const g = svgEl('g', { class: 'edge-group', 'data-edge-id': edge.id });
      const strokeW = edge.isSecondaryWorkEdge
        ? 1.25
        : edge.isPrimaryWorkEdge
          ? 1.85
          : (edge.dashed ? 1.15 : (1.35 + Math.min(3.3, Math.log2((edge.weight || 1) + 1) * 0.95)));
      const markerEnd = edge.isSecondaryWorkEdge
        ? 'none'
        : edge.isGood
          ? 'url(#arrow-good)'
          : edge.isBad
            ? 'url(#arrow-bad)'
            : 'url(#arrow-muted)';

      const workEdgeClass = edge.isPrimaryWorkEdge
        ? 'is-work-primary'
        : (edge.isSecondaryWorkEdge ? 'is-work-secondary' : '');
      const path = svgEl('path', {
        class: `edge-path ${edge.dashed ? 'is-dashed' : 'is-populated'} ${workEdgeClass} ${edge.isGood ? 'is-good' : ''} ${edge.isBad ? 'is-bad' : ''}`,
        'stroke-width': strokeW.toFixed(2),
        'marker-end': markerEnd,
      });
      const hit = svgEl('path', { class: 'edge-hit' });
      g.appendChild(path);
      g.appendChild(hit);

      let badgeBg = null;
      let badgeText = null;
      if (edge.choiceLabel) {
        badgeBg = svgEl('rect', { class: 'edge-choice-pill', height: 18 });
        badgeText = svgEl('text', { class: 'edge-choice-text' });
        badgeText.textContent = edge.choiceLabel;
        g.appendChild(badgeBg);
        g.appendChild(badgeText);
      } else if (edge.weightLabel) {
        badgeBg = svgEl('rect', { class: 'edge-weight-bg', rx: 6, height: 14 });
        badgeText = svgEl('text', { class: 'edge-weight-text' });
        badgeText.textContent = edge.weightLabel;
        g.appendChild(badgeBg);
        g.appendChild(badgeText);
      }

      layerEdges.appendChild(g);
      edgeDomMap.set(edge.id, { group: g, path, hit, badgeBg, badgeText, edge });
    }

    // 3. 胶囊节点
    layerNodes.innerHTML = '';
    nodeDomMap.clear();
    for (const node of simNodes.values()) {
      const g = svgEl('g', {
        class: `node-group ${node.populated ? '' : 'is-empty-node'}`,
        'data-node-id': node.id,
        transform: `translate(${node.x}, ${node.y})`,
      });

      const hw = node.width / 2;
      const hh = node.height / 2;

      // 双层外环光晕（Root / Selected / Ending）
      const halo = svgEl('rect', {
        class: `node-halo ${node.haloKind ? `halo-${node.haloKind}` : ''}`,
        x: -hw - 4.5,
        y: -hh - 4.5,
        width: node.width + 9,
        height: node.height + 9,
        rx: (node.height + 9) / 2,
        display: node.hasHalo || state.selectedNodeId === node.id ? 'block' : 'none',
      });
      g.appendChild(halo);

      // 主胶囊体
      const pill = svgEl('rect', {
        class: `node-pill ${node.populated ? '' : 'is-empty'}`,
        x: -hw,
        y: -hh,
        width: node.width,
        height: node.height,
        rx: hh,
      });
      g.appendChild(pill);

      // 左侧状态圆点（空节点为参考图同款空心环 o，有货为呼吸亮色圆点 ●）
      if (node.populated) {
        const dot = svgEl('circle', {
          class: 'node-dot-solid node-dot-breathing',
          cx: -hw + 14,
          cy: 0,
          r: 3.8,
          fill: node.dotColor || '#7dd3fc',
        });
        g.appendChild(dot);
      } else {
        const ring = svgEl('circle', {
          class: 'node-dot-hollow',
          cx: -hw + 14,
          cy: 0,
          r: 3.4,
        });
        g.appendChild(ring);
      }

      // 节点标题
      const titleEl = svgEl('text', {
        class: 'node-title',
        x: -hw + 24,
        y: 0.5,
      });
      titleEl.textContent = node.title;
      g.appendChild(titleEl);

      // 右侧精炼计数角标
      if (node.badge) {
        const badgeEl = svgEl('text', {
          class: 'node-badge-text',
          x: hw - 11,
          y: 0.5,
        });
        badgeEl.textContent = node.badge;
        g.appendChild(badgeEl);
      }

      // 待试菜 / 待补提醒微光点
      if (node.pendingCount > 0) {
        const pip = svgEl('circle', {
          class: 'node-pending-pip',
          cx: hw - 4,
          cy: -hh + 4,
          r: 3.2,
        });
        g.appendChild(pip);
      }

      // 拖拽文件悬停提示气泡（默认隐藏）
      const tipG = svgEl('g', { class: 'node-drop-tip hidden', transform: `translate(0, ${-hh - 18})` });
      const tipLabel = `松手喂入【${node.fullTitle || node.title}】`;
      const tipW = Math.max(116, tipLabel.length * 11 + 18);
      const tipBg = svgEl('rect', { class: 'node-drop-tip-bg', x: -tipW / 2, y: -11, width: tipW, height: 22 });
      const tipTx = svgEl('text', { class: 'node-drop-tip-text', x: 0, y: 0.5 });
      tipTx.textContent = tipLabel;
      tipG.appendChild(tipBg);
      tipG.appendChild(tipTx);
      g.appendChild(tipG);

      bindNodeInteractions(g, node);
      layerNodes.appendChild(g);
      nodeDomMap.set(node.id, g);
    }

    refreshHighlightClasses();
    updateEdgePositions();
  }

  function computeBezier(a, b, edge) {
    const dx = b.x - a.x;
    const dy = b.y - a.y;

    let sx, sy, ex, ey, c1x, c1y, c2x, c2y;
    if (edge.isSecondaryWorkEdge) {
      const bias = edge.curveBias || 0;
      if (dy >= 16) {
        sx = a.x;
        sy = a.y + a.height / 2 + 1;
        ex = b.x;
        ey = b.y - b.height / 2 - 4;
        const arcY = Math.max(58, Math.abs(ey - sy) * 0.55);
        c1x = sx + bias;
        c1y = sy + arcY;
        c2x = ex - bias * 0.5;
        c2y = ey - arcY * 0.85;
      } else if (Math.abs(dx) < 60) {
        const side = (bias >= 0 ? 1 : -1);
        sx = a.x + side * (a.width / 2 + 1);
        sy = a.y;
        ex = b.x + side * (b.width / 2 + 4);
        ey = b.y;
        const bulge = 64 + Math.min(40, Math.abs(bias));
        c1x = sx + side * bulge;
        c1y = sy;
        c2x = ex + side * bulge;
        c2y = ey;
      } else {
        const signX = dx >= 0 ? 1 : -1;
        sx = a.x + signX * (a.width / 2 + 1);
        sy = a.y;
        ex = b.x - signX * (b.width / 2 + 4);
        ey = b.y;
        const arch = 56 + Math.abs(bias);
        c1x = sx + signX * Math.max(45, Math.abs(ex - sx) * 0.35);
        c1y = sy + arch;
        c2x = ex - signX * Math.max(45, Math.abs(ex - sx) * 0.35);
        c2y = ey + arch * 0.7;
      }
    } else {
      const vertical = Math.abs(dy) >= Math.abs(dx) * 0.45;
      if (vertical) {
        const sign = dy >= 0 ? 1 : -1;
        sx = a.x;
        sy = a.y + sign * (a.height / 2 + 1);
        ex = b.x;
        ey = b.y - sign * (b.height / 2 + 4);
        const spanY = Math.max(36, Math.abs(ey - sy) * 0.52);
        const bias = edge.curveBias || 0;
        c1x = sx + bias;
        c1y = sy + sign * spanY;
        c2x = ex - bias * 0.5;
        c2y = ey - sign * spanY;
      } else {
        const sign = dx >= 0 ? 1 : -1;
        sx = a.x + sign * (a.width / 2 + 1);
        sy = a.y;
        ex = b.x - sign * (b.width / 2 + 4);
        ey = b.y;
        const spanX = Math.max(40, Math.abs(ex - sx) * 0.5);
        c1x = sx + sign * spanX;
        c1y = sy + (edge.curveBias || 0);
        c2x = ex - sign * spanX;
        c2y = ey - (edge.curveBias || 0) * 0.4;
      }
    }

    const d = `M ${sx.toFixed(1)} ${sy.toFixed(1)} C ${c1x.toFixed(1)} ${c1y.toFixed(1)}, ${c2x.toFixed(1)} ${c2y.toFixed(1)}, ${ex.toFixed(1)} ${ey.toFixed(1)}`;
    // t=0.5 的三次贝塞尔中点
    const mx = 0.125 * sx + 0.375 * c1x + 0.375 * c2x + 0.125 * ex;
    const my = 0.125 * sy + 0.375 * c1y + 0.375 * c2y + 0.125 * ey;
    return { d, mx, my };
  }

  function updateEdgePositions() {
    for (const item of edgeDomMap.values()) {
      const a = simNodes.get(item.edge.from);
      const b = simNodes.get(item.edge.to);
      if (!a || !b) continue;
      const { d, mx, my } = computeBezier(a, b, item.edge);
      item.path.setAttribute('d', d);
      item.hit.setAttribute('d', d);

      if (item.badgeBg && item.badgeText) {
        if (item.edge.choiceLabel) {
          const bw = Math.max(54, item.edge.choiceLabel.length * 10.5 + 14);
          item.badgeBg.setAttribute('x', (mx - bw / 2).toFixed(1));
          item.badgeBg.setAttribute('y', (my - 9).toFixed(1));
          item.badgeBg.setAttribute('width', bw.toFixed(1));
          item.badgeText.setAttribute('x', mx.toFixed(1));
          item.badgeText.setAttribute('y', my.toFixed(1));
        } else {
          const bw = Math.max(16, (item.edge.weightLabel || '').length * 7 + 8);
          item.badgeBg.setAttribute('x', (mx - bw / 2).toFixed(1));
          item.badgeBg.setAttribute('y', (my - 7).toFixed(1));
          item.badgeBg.setAttribute('width', bw.toFixed(1));
          item.badgeText.setAttribute('x', mx.toFixed(1));
          item.badgeText.setAttribute('y', my.toFixed(1));
        }
      }
    }
  }

  function startPhysicsLoop() {
    if (rafId !== null) return;
    const step = () => {
      let moving = false;
      for (const node of simNodes.values()) {
        if (node.isDragging) continue;
        const dx = node.tx - node.x;
        const dy = node.ty - node.y;
        if (Math.abs(dx) > 0.25 || Math.abs(dy) > 0.25 || Math.abs(node.vx) > 0.05 || Math.abs(node.vy) > 0.05) {
          node.vx = (node.vx + dx * 0.17) * 0.72;
          node.vy = (node.vy + dy * 0.17) * 0.72;
          node.x += node.vx;
          node.y += node.vy;
          moving = true;
          const g = nodeDomMap.get(node.id);
          if (g) g.setAttribute('transform', `translate(${node.x.toFixed(1)}, ${node.y.toFixed(1)})`);
        } else if (node.x !== node.tx || node.y !== node.ty) {
          node.x = node.tx;
          node.y = node.ty;
          node.vx = 0;
          node.vy = 0;
          const g = nodeDomMap.get(node.id);
          if (g) g.setAttribute('transform', `translate(${node.x.toFixed(1)}, ${node.y.toFixed(1)})`);
        }
      }

      if (moving) {
        updateEdgePositions();
        rafId = requestAnimationFrame(step);
      } else {
        updateEdgePositions();
        rafId = null;
      }
    };
    rafId = requestAnimationFrame(step);
  }

  // -------------------------------------------------------------------------
  // 悬停聚光灯（上下游血脉高亮 + 流动微光粒子）
  // -------------------------------------------------------------------------
  function getLineageSet(startId) {
    const activeNodes = new Set([startId]);
    const activeEdges = new Set();

    // 向上游溯源
    const upQueue = [startId];
    const upSeen = new Set([startId]);
    while (upQueue.length) {
      const cur = upQueue.shift();
      for (const e of simEdges) {
        if (e.to === cur) {
          activeEdges.add(e.id);
          if (!upSeen.has(e.from)) {
            upSeen.add(e.from);
            activeNodes.add(e.from);
            upQueue.push(e.from);
          }
        }
      }
    }

    // 向下游追踪
    const downQueue = [startId];
    const downSeen = new Set([startId]);
    while (downQueue.length) {
      const cur = downQueue.shift();
      for (const e of simEdges) {
        if (e.from === cur) {
          activeEdges.add(e.id);
          if (!downSeen.has(e.to)) {
            downSeen.add(e.to);
            activeNodes.add(e.to);
            downQueue.push(e.to);
          }
        }
      }
    }

    return { activeNodes, activeEdges };
  }

  function refreshHighlightClasses() {
    const focusId = state.hoveredNodeId || (state.selectedNodeId && state.selectedNodeId.startsWith('work:') ? state.selectedNodeId : null);
    const hasSearch = state.searchQuery.trim().length > 0;
    const lineage = focusId ? getLineageSet(focusId) : null;

    for (const [id, g] of nodeDomMap.entries()) {
      const node = simNodes.get(id);
      const isSelected = state.selectedNodeId === id;
      const isLit = Boolean(lineage && lineage.activeNodes.has(id) && id !== focusId);
      g.classList.toggle('is-selected', isSelected);
      g.classList.toggle('is-lineage-lit', isLit);
      g.classList.toggle('is-search-hit', Boolean(node?.searchHit));

      const halo = g.querySelector('.node-halo');
      if (halo) {
        halo.setAttribute('display', node?.hasHalo || isSelected ? 'block' : 'none');
        halo.classList.toggle('halo-selected', isSelected && !node?.haloKind);
      }

      let dimmed = false;
      if (lineage && !lineage.activeNodes.has(id)) {
        dimmed = true;
      } else if (!lineage && hasSearch && !node?.searchHit) {
        dimmed = true;
      }
      g.classList.toggle('is-dimmed', dimmed);
    }

    for (const [eid, item] of edgeDomMap.entries()) {
      const active = lineage ? lineage.activeEdges.has(eid) : false;
      item.group.classList.toggle('is-dimmed', Boolean(lineage && !active));
      item.path.classList.toggle('is-highlight', active);
      if (active) {
        item.path.setAttribute('marker-end', 'url(#arrow-active)');
      } else if (item.edge.isSecondaryWorkEdge) {
        item.path.setAttribute('marker-end', 'none');
      } else {
        const m = item.edge.isGood ? 'url(#arrow-good)' : item.edge.isBad ? 'url(#arrow-bad)' : 'url(#arrow-muted)';
        item.path.setAttribute('marker-end', m);
      }
    }

    syncFlowParticles(lineage ? lineage.activeEdges : null);
  }

  function syncFlowParticles(activeEdgeIds) {
    layerParticles.innerHTML = '';
    activeParticles = [];
    if (particleRafId !== null) {
      cancelAnimationFrame(particleRafId);
      particleRafId = null;
    }
    if (!activeEdgeIds || activeEdgeIds.size === 0) return;

    for (const eid of activeEdgeIds) {
      const dom = edgeDomMap.get(eid);
      if (!dom) continue;
      for (let i = 0; i < 2; i++) {
        const c = svgEl('circle', { class: 'flow-particle', r: 2.3 });
        layerParticles.appendChild(c);
        activeParticles.push({ path: dom.path, t: i * 0.5, speed: 0.014, el: c });
      }
    }

    const tick = () => {
      if (!activeParticles.length) {
        particleRafId = null;
        return;
      }
      for (const p of activeParticles) {
        p.t = (p.t + p.speed) % 1;
        try {
          const len = p.path.getTotalLength();
          if (len > 1) {
            const pt = p.path.getPointAtLength(p.t * len);
            p.el.setAttribute('cx', pt.x.toFixed(1));
            p.el.setAttribute('cy', pt.y.toFixed(1));
          }
        } catch { /* ignore detached path */ }
      }
      particleRafId = requestAnimationFrame(tick);
    };
    particleRafId = requestAnimationFrame(tick);
  }

  // -------------------------------------------------------------------------
  // 节点交互：点击选中 / 拖拽物理手感 / 悬停聚光灯
  // -------------------------------------------------------------------------
  function bindNodeInteractions(g, node) {
    g.addEventListener('mouseenter', () => {
      state.hoveredNodeId = node.id;
      refreshHighlightClasses();
    });

    g.addEventListener('mouseleave', () => {
      if (state.hoveredNodeId === node.id) {
        state.hoveredNodeId = null;
        refreshHighlightClasses();
      }
    });

    g.addEventListener('mousedown', (ev) => {
      if (ev.button !== 0) return;
      ev.stopPropagation();

      const startClientX = ev.clientX;
      const startClientY = ev.clientY;
      const startNodeX = node.x;
      const startNodeY = node.y;
      let moved = false;

      const onMove = (mev) => {
        const dx = (mev.clientX - startClientX) / state.view.scale;
        const dy = (mev.clientY - startClientY) / state.view.scale;
        if (!moved && Math.hypot(mev.clientX - startClientX, mev.clientY - startClientY) > 4) {
          moved = true;
          node.isDragging = true;
          g.classList.add('is-dragging');
        }
        if (moved) {
          node.x = startNodeX + dx;
          node.y = startNodeY + dy;
          node.tx = node.x;
          node.ty = node.y;
          node.pinned = true;
          g.setAttribute('transform', `translate(${node.x.toFixed(1)}, ${node.y.toFixed(1)})`);
          updateEdgePositions();
        }
      };

      const onUp = () => {
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        if (moved) {
          node.isDragging = false;
          g.classList.remove('is-dragging');
          // 在知识星图模式释放后轻微回弹归位（或保持位置），在互动支线模式持久化坐标
          if (state.mode === 'branching' && node.storyNodeId) {
            const px = Math.round(node.x / 1.45);
            const py = Math.round(node.y / 1.25);
            api(`/api/nodes/${node.storyNodeId}/position`, {
              method: 'PATCH',
              body: { pos_x: px, pos_y: py },
            }).catch(() => {});
          } else {
            // 释放时微幅物理阻尼回弹感
            node.vx = (node.tx - node.x) * 0.08;
            node.vy = (node.ty - node.y) * 0.08;
          }
        } else {
          handleNodeClick(node);
        }
      };

      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    });
  }

  async function handleNodeClick(node) {
    state.selectedNodeId = node.id;
    refreshHighlightClasses();

    if (node.kind === 'root') {
      await openRootDrawer();
      return;
    }

    if (node.kind === 'channel') {
      // 点击三大主干：平滑展开/收起其子标签
      if (state.expandedChannels.has(node.channelName)) {
        state.expandedChannels.delete(node.channelName);
      } else {
        state.expandedChannels.add(node.channelName);
      }
      computeUniverseLayout();
      return;
    }

    if (node.kind === 'tag') {
      // 若旗下有书，平滑向下长出《书名》节点，同时滑出右侧极简抽屉
      if (state.expandedTagIds.has(node.genreId)) {
        state.expandedTagIds.delete(node.genreId);
      } else {
        state.expandedTagIds.add(node.genreId);
      }
      computeUniverseLayout();
      await openTagDrawer(node.raw);
      return;
    }

    if (node.kind === 'work') {
      await openWorkDrawer(node.workId);
      return;
    }

    if (node.kind === 'branch_node') {
      await openBranchNodeDrawer(node.raw);
    }
  }

  // -------------------------------------------------------------------------
  // 画布平移、滚轮定点缩放、双击居中对齐
  // -------------------------------------------------------------------------
  function applyViewportTransform() {
    viewportEl.setAttribute(
      'transform',
      `translate(${state.view.x.toFixed(1)}, ${state.view.y.toFixed(1)}) scale(${state.view.scale.toFixed(4)})`
    );
    $('#zoom-pct').textContent = `${Math.round(state.view.scale * 100)}%`;
  }

  function fitToScreen() {
    if (simNodes.size === 0) {
      state.view = { x: window.innerWidth / 2, y: 120, scale: 1 };
      applyViewportTransform();
      return;
    }
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const n of simNodes.values()) {
      n.pinned = false;
      minX = Math.min(minX, n.tx - n.width / 2);
      maxX = Math.max(maxX, n.tx + n.width / 2);
      minY = Math.min(minY, n.ty - n.height / 2);
      maxY = Math.max(maxY, n.ty + n.height / 2);
    }
    const padX = 70;
    const padTop = 78;
    const padBottom = 52;
    const w = Math.max(200, maxX - minX);
    const h = Math.max(160, maxY - minY);
    const availW = Math.max(300, window.innerWidth - padX * 2);
    const availH = Math.max(240, window.innerHeight - padTop - padBottom);
    const scale = Math.max(0.38, Math.min(1.12, Math.min(availW / w, availH / h)));

    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    state.view.scale = scale;
    state.view.x = window.innerWidth / 2 - cx * scale;
    state.view.y = padTop + availH / 2 - cy * scale;
    applyViewportTransform();

    if (state.mode === 'universe') computeUniverseLayout();
  }

  function initStageControls() {
    const stage = $('#canvas-stage');

    stage.addEventListener('mousedown', (ev) => {
      if (ev.button !== 0) return;
      const sx = ev.clientX;
      const sy = ev.clientY;
      const vx0 = state.view.x;
      const vy0 = state.view.y;
      let panned = false;

      const onMove = (mev) => {
        const dx = mev.clientX - sx;
        const dy = mev.clientY - sy;
        if (!panned && Math.hypot(dx, dy) > 4) {
          panned = true;
          stage.classList.add('is-panning');
        }
        if (panned) {
          state.view.x = vx0 + dx;
          state.view.y = vy0 + dy;
          applyViewportTransform();
        }
      };

      const onUp = () => {
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        stage.classList.remove('is-panning');
        if (!panned) {
          // 点空白收起抽屉与浮层
          closeDrawer();
          hidePopovers();
          state.selectedNodeId = null;
          refreshHighlightClasses();
        }
      };

      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    });

    stage.addEventListener('wheel', (ev) => {
      ev.preventDefault();
      const factor = ev.deltaY < 0 ? 1.1 : 0.91;
      const nextScale = Math.max(0.28, Math.min(2.4, state.view.scale * factor));
      const rect = stage.getBoundingClientRect();
      const mx = ev.clientX - rect.left;
      const my = ev.clientY - rect.top;

      const wx = (mx - state.view.x) / state.view.scale;
      const wy = (my - state.view.y) / state.view.scale;

      state.view.scale = nextScale;
      state.view.x = mx - wx * nextScale;
      state.view.y = my - wy * nextScale;
      applyViewportTransform();
    }, { passive: false });

    stage.addEventListener('dblclick', (ev) => {
      if (ev.target === stage || ev.target === svgElRoot) {
        fitToScreen();
      }
    });

    $('#btn-fit-view').addEventListener('click', fitToScreen);
  }

  // -------------------------------------------------------------------------
  // 右侧滑出极简抽屉 (Slide-over Drawer, 400px)
  // -------------------------------------------------------------------------
  const drawerEl = $('#side-drawer');
  const drawerBody = $('#drawer-body');

  function openDrawerShell({ kicker, title, showFeedBtn = false, feedGenreId = null }) {
    $('#drawer-kicker').innerHTML = kicker || '';
    $('#drawer-title').textContent = title || '';
    const feedBtn = $('#drawer-feed-here');
    if (showFeedBtn && feedGenreId) {
      feedBtn.classList.remove('hidden');
      state.feedTargetGenreId = feedGenreId;
    } else {
      feedBtn.classList.add('hidden');
    }
    drawerEl.classList.add('open');
    drawerEl.setAttribute('aria-hidden', 'false');
  }

  function closeDrawer() {
    drawerEl.classList.remove('open');
    drawerEl.setAttribute('aria-hidden', 'true');
  }

  function hidePopovers() {
    $('#upload-tag-popover').classList.add('hidden');
    $('#new-project-popover').classList.add('hidden');
    $('#jinghua-panel').classList.add('hidden');
    $('#btn-jinghua-lock').classList.remove('active');
  }

  // 原位展开原书段落核对（不叠弹窗套娃）
  async function toggleInlineSpan(btn, workId, chapterNum, lineStart, lineEnd) {
    const card = btn.closest('.glass-card');
    const existing = card.querySelector('.inline-span-box');
    if (existing) {
      existing.remove();
      return;
    }
    btn.textContent = '读取原文中...';
    try {
      const start = Math.max(1, (lineStart || 1) - 2);
      const end = Math.max(start + 4, (lineEnd || lineStart || 1) + 3);
      const data = await api(`/api/works/${workId}/span?chapter_num=${chapterNum || 1}&line_start=${start}&line_end=${end}`);
      const span = data.span;
      const box = document.createElement('div');
      box.className = 'inline-span-box';
      box.innerHTML = (span.lines || []).map((ln) => {
        const focus = ln.line_num >= (lineStart || 0) && ln.line_num <= (lineEnd || 0);
        return `<div class="inline-span-line ${focus ? 'is-focus' : ''}"><span class="inline-span-ln">L${ln.line_num}</span><span>${escHtml(ln.text)}</span></div>`;
      }).join('');
      card.appendChild(box);
      btn.textContent = `第${chapterNum}章 L${lineStart || 1} ▴`;
    } catch (err) {
      btn.textContent = `原文回读失败`;
      toast(err.message, 'err');
    }
  }

  function resolveSnippetWorkTags(sn, workTagsOverride = null) {
    const base = [];
    const seen = new Set();
    const pushId = (id) => {
      if (id && !seen.has(id)) {
        seen.add(id);
        base.push(id);
      }
    };
    if (Array.isArray(workTagsOverride) && workTagsOverride.length) {
      workTagsOverride.forEach(pushId);
    } else if (sn.work_id) {
      const workObj = (state.universe.works || []).find((w) => w.id === sn.work_id);
      if (workObj) getWorkTagIds(workObj).forEach(pushId);
    }
    if (sn.genre_id) pushId(sn.genre_id);
    return base;
  }

  function renderSnippetCardHtml(sn, isPending, workTagsOverride = null) {
    const hasRef = sn.work_id && sn.chapter_num != null;
    const refLabel = hasRef ? `第${sn.chapter_num}章 L${sn.line_start || 1}` : '手工摘录';
    const currentGid = sn.genre_id || 'mengwa';
    const workTags = resolveSnippetWorkTags(sn, workTagsOverride);
    const canSwitch = isPending && workTags.length > 1;
    const tagName = getTagName(currentGid);
    const tagColor = getTagColor(currentGid);

    return `
      <div class="glass-card ${isPending ? 'is-pending-card' : ''}" data-snippet-id="${escHtml(sn.id)}" data-orig-genre-id="${escHtml(currentGid)}">
        <div class="card-top-row">
          <span class="badge-pill">${escHtml(sn.scene_tag || (isPending ? '待试菜' : '好梗'))}</span>
          <button
            class="snippet-tag-pill ${canSwitch ? 'is-switchable' : ''}"
            type="button"
            data-genre-id="${escHtml(currentGid)}"
            data-work-tags="${escHtml(workTags.join(','))}"
            title="${canSwitch ? '点击在本书多个标签间一键切换归属，防止串味' : '当前归属标签'}"
          >
            <span class="tag-dot" style="background:${tagColor}"></span>
            <span class="snippet-tag-name">${escHtml(tagName)}</span>
            ${canSwitch ? '<span class="snippet-tag-cycle">⇄</span>' : ''}
          </button>
        </div>
        <div class="quote-text">${escHtml(sn.raw_text)}</div>
        ${sn.why_funny_or_effective ? `<div class="muted-text">${escHtml(sn.why_funny_or_effective)}</div>` : ''}
        <div class="card-meta-row">
          ${hasRef
            ? `<button class="span-link-btn" type="button" data-work-id="${escHtml(sn.work_id)}" data-ch="${sn.chapter_num}" data-ls="${sn.line_start || 1}" data-le="${sn.line_end || sn.line_start || 1}">${refLabel} · 核对原文</button>`
            : `<span class="muted-text">${refLabel}</span>`}
        </div>
        ${isPending ? `
          <div class="taste-btn-row">
            <button class="btn-taste-yes" type="button" data-action="approve">✓ 留</button>
            <button class="btn-taste-no" type="button" data-action="reject">✗ 淘汰</button>
          </div>` : ''}
      </div>
    `;
  }

  function bindDrawerSnippetEvents(container, onReviewed) {
    if (container.dataset.snippetBound === '1') return;
    container.dataset.snippetBound = '1';

    container.addEventListener('click', async (ev) => {
      const switchBtn = ev.target.closest('.snippet-tag-pill.is-switchable');
      if (switchBtn) {
        const tags = (switchBtn.dataset.workTags || '').split(',').filter(Boolean);
        if (tags.length > 1) {
          const cur = switchBtn.dataset.genreId;
          const curIdx = tags.indexOf(cur);
          const nextGid = tags[(curIdx + 1) % tags.length];
          switchBtn.dataset.genreId = nextGid;
          const nameEl = switchBtn.querySelector('.snippet-tag-name');
          const dotEl = switchBtn.querySelector('.tag-dot');
          if (nameEl) nameEl.textContent = getTagName(nextGid);
          if (dotEl) dotEl.style.background = getTagColor(nextGid);
          switchBtn.classList.remove('is-pulsing');
          void switchBtn.offsetWidth;
          switchBtn.classList.add('is-pulsing');
        }
        return;
      }

      const spanBtn = ev.target.closest('.span-link-btn');
      if (spanBtn) {
        await toggleInlineSpan(
          spanBtn,
          spanBtn.dataset.workId,
          Number(spanBtn.dataset.ch),
          Number(spanBtn.dataset.ls),
          Number(spanBtn.dataset.le)
        );
        return;
      }

      const tasteBtn = ev.target.closest('.btn-taste-yes, .btn-taste-no');
      if (tasteBtn) {
        const card = tasteBtn.closest('.glass-card');
        const sid = card.dataset.snippetId;
        const origGid = card.dataset.origGenreId;
        const chosenGid = card.querySelector('.snippet-tag-pill')?.dataset.genreId || origGid;
        const status = tasteBtn.dataset.action === 'approve' ? 'approved' : 'rejected';
        tasteBtn.disabled = true;
        try {
          await api(`/api/snippets/${sid}/review`, {
            method: 'POST',
            body: {
              status,
              rating: status === 'approved' ? 5 : 1,
              genre_id: chosenGid || undefined,
            },
          });
          card.classList.add('is-leaving');
          setTimeout(() => card.remove(), 240);
          if (status === 'approved') {
            const switched = chosenGid && chosenGid !== origGid;
            toast(switched ? `已归入【${getTagName(chosenGid)}】好梗库` : `已收入【${getTagName(chosenGid)}】好梗库`, 'ok');
          } else {
            toast('已淘汰该片段', 'ok');
          }
          await loadUniverse(false);
          if (onReviewed) onReviewed();
        } catch (err) {
          tasteBtn.disabled = false;
          toast(err.message, 'err');
        }
      }
    });
  }

  async function openRootDrawer() {
    openDrawerShell({
      kicker: '<span class="badge-pill">编剧总库</span><span class="badge-pill">三频星图总览</span>',
      title: '编剧灵感星图 · 总库',
    });
    drawerBody.innerHTML = '<div class="muted-text">正在读取总库待试菜与好梗...</div>';
    try {
      const [pendingRes, approvedRes] = await Promise.all([
        api('/api/snippets?status=pending&limit=20'),
        api('/api/snippets?status=approved&limit=12'),
      ]);
      const pending = pendingRes.items || [];
      const approved = approvedRes.items || [];
      const r = state.universe.root || {};

      drawerBody.innerHTML = `
        <div class="drawer-row-actions">
          <span class="badge-pill">作品 ${r.total_works ?? state.universe.works.length} 本</span>
          <span class="badge-pill badge-clean">好梗 ${r.approved_count ?? r.total_approved ?? approved.length} 条</span>
          <span class="badge-pill badge-pending">待试菜 ${pending.length} 条</span>
        </div>
        ${pending.length ? `
          <div class="drawer-section">
            <div class="section-label-row"><span>待试菜新梗（置顶直审）</span><span>${pending.length}</span></div>
            <div id="root-pending-list" class="drawer-section">${pending.map((s) => renderSnippetCardHtml(s, true)).join('')}</div>
          </div>
        ` : '<div class="empty-hint-box">暂无待试菜片段。把电脑上的 .txt / .docx 小说拖到任意标签胶囊上即可自动喂书提取。</div>'}
        <div class="drawer-section">
          <div class="section-label-row"><span>最新留用原话好梗</span><span>${approved.length}</span></div>
          <div id="root-approved-list" class="drawer-section">
            ${approved.length ? approved.map((s) => renderSnippetCardHtml(s, false)).join('') : '<div class="muted-text">尚未收入好梗</div>'}
          </div>
        </div>
      `;
      bindDrawerSnippetEvents(drawerBody);
    } catch (err) {
      drawerBody.innerHTML = `<div class="empty-hint-box">${escHtml(err.message)}</div>`;
    }
  }

  async function openTagDrawer(tag) {
    const channelBadge = `<span class="badge-pill">${escHtml(tag.channel || '通用')}</span>`;
    const platBadge = tag.platforms ? `<span class="badge-pill">${escHtml(tag.platforms)}</span>` : '';
    openDrawerShell({
      kicker: `${channelBadge}${platBadge}`,
      title: tag.name,
      showFeedBtn: true,
      feedGenreId: tag.id,
    });

    drawerBody.innerHTML = '<div class="muted-text">加载标签素材中...</div>';
    try {
      const [pendingRes, approvedRes, charsRes, hlRes] = await Promise.all([
        api(`/api/snippets?genre_id=${encodeURIComponent(tag.id)}&status=pending&limit=30`),
        api(`/api/snippets?genre_id=${encodeURIComponent(tag.id)}&status=approved&limit=40`),
        api(`/api/characters?genre_id=${encodeURIComponent(tag.id)}`),
        api(`/api/highlights?genre_id=${encodeURIComponent(tag.id)}`),
      ]);

      const pending = pendingRes.items || [];
      const approved = approvedRes.items || [];
      const chars = charsRes.items || [];
      const highlights = hlRes.items || [];
      const tagWorks = (state.universe.works || []).filter(
        (w) => w.genre_id === tag.id || getWorkTagIds(w).includes(tag.id)
      );

      drawerBody.innerHTML = `
        ${tag.description ? `<div class="drawer-desc">${escHtml(tag.description)}${tag.channel_hint ? ` · 对白风味：${escHtml(tag.channel_hint)}` : ''}</div>` : ''}

        ${pending.length ? `
          <div class="drawer-section">
            <div class="section-label-row"><span>🔥 待试菜新梗</span><span class="badge-pill badge-pending">${pending.length} 待定</span></div>
            <div class="drawer-section" id="tag-pending-list">${pending.map((s) => renderSnippetCardHtml(s, true)).join('')}</div>
          </div>
        ` : ''}

        <div class="drawer-section">
          <div class="section-label-row">
            <span>原话好梗</span>
            <span>${approved.length} 条</span>
          </div>
          <div class="drawer-section" id="tag-approved-list">
            ${approved.length
              ? approved.map((s) => renderSnippetCardHtml(s, false)).join('')
              : `<div class="empty-hint-box">这个抽屉还是空的。<br/>把小说文件直接拖到【${escHtml(tag.name)}】胶囊上即可自动投喂！</div>`}
          </div>
        </div>

        ${chars.length ? `
          <div class="drawer-section">
            <div class="section-label-row"><span>人物特点卡</span><span>${chars.length}</span></div>
            ${chars.map((c) => `
              <div class="glass-card">
                <div class="card-meta-row">
                  <strong>${escHtml(c.name)}</strong>
                  ${c.role_type ? `<span class="badge-pill">${escHtml(c.role_type)}</span>` : ''}
                </div>
                <div class="drawer-desc">${escHtml(c.traits)}</div>
                ${c.catchphrases ? `<div class="muted-text">口头禅：${escHtml(c.catchphrases)}</div>` : ''}
              </div>
            `).join('')}
          </div>
        ` : ''}

        ${highlights.length ? `
          <div class="drawer-section">
            <div class="section-label-row"><span>名场面钩子</span><span>${highlights.length}</span></div>
            ${highlights.map((h) => `
              <div class="glass-card">
                <div class="quote-text">${escHtml(h.plain_summary)}</div>
                <div class="card-meta-row">
                  ${h.hook_type ? `<span class="badge-pill">${escHtml(h.hook_type)}</span>` : ''}
                  ${h.chapter_range ? `<span class="muted-text">${escHtml(h.chapter_range)}</span>` : ''}
                </div>
              </div>
            `).join('')}
          </div>
        ` : ''}

        ${tagWorks.length ? `
          <div class="drawer-section">
            <div class="section-label-row"><span>归属书籍（防盗隔离状态）</span><span>${tagWorks.length} 本</span></div>
            ${tagWorks.map((w) => {
              const isPrimaryInTag = w.genre_id === tag.id;
              return `
              <div class="glass-card work-jump-card" data-work-id="${escHtml(w.id)}" style="cursor:pointer;">
                <div class="card-meta-row">
                  <strong>《${escHtml(w.title)}》</strong>
                  <div style="display:inline-flex;gap:5px;align-items:center;">
                    <span class="badge-pill">${isPrimaryInTag ? '主标签' : '副标签'}</span>
                    <span class="badge-pill badge-clean">干净 ${w.clean_chapters ?? 0} 章</span>
                  </div>
                </div>
                ${(w.corrupted_chapters ?? 0) > 0
                  ? `<div class="card-meta-row"><span class="badge-pill badge-corrupt">已隔离防盗乱码 ${w.corrupted_chapters} 章</span><span class="muted-text">点击查看</span></div>`
                  : `<div class="muted-text">全本干净无防盗乱码 · 点击查看章节与摘要</div>`}
              </div>`;
            }).join('')}
          </div>
        ` : ''}
      `;

      bindDrawerSnippetEvents(drawerBody);
      $$('.work-jump-card', drawerBody).forEach((el) => {
        el.addEventListener('click', () => openWorkDrawer(el.dataset.workId));
      });
    } catch (err) {
      drawerBody.innerHTML = `<div class="empty-hint-box">${escHtml(err.message)}</div>`;
    }
  }

  async function saveWorkTagsAndRefresh(workId, primaryTagId, extraTagIds, keepPickerOpen = false) {
    try {
      await api(`/api/works/${encodeURIComponent(workId)}/tags`, {
        method: 'PUT',
        body: {
          primary_tag_id: primaryTagId,
          extra_tag_ids: extraTagIds,
        },
      });
      state.expandedTagIds.add(primaryTagId);
      const pTag = getTagById(primaryTagId);
      if (pTag) state.expandedChannels.add(pTag.channel || '通用');
      for (const eid of extraTagIds) {
        const eTag = getTagById(eid);
        if (eTag) state.expandedChannels.add(eTag.channel || '通用');
      }
      state.selectedNodeId = `work:${workId}`;
      await loadUniverse(false);
      await openWorkDrawer(workId, keepPickerOpen);
      toast('书籍标签已更新，星图连线已同步', 'ok');
    } catch (err) {
      toast(`标签更新失败：${err.message}`, 'err');
    }
  }

  async function openWorkDrawer(workId, pickerOpen = false) {
    openDrawerShell({
      kicker: '<span class="badge-pill">作品档案</span>',
      title: '读取作品中...',
    });
    try {
      const [detailRes, pendingRes, approvedRes, charsRes] = await Promise.all([
        api(`/api/works/${workId}`),
        api(`/api/snippets?work_id=${encodeURIComponent(workId)}&status=pending&limit=20`),
        api(`/api/snippets?work_id=${encodeURIComponent(workId)}&status=approved&limit=30`),
        api(`/api/characters?work_id=${encodeURIComponent(workId)}`),
      ]);
      const w = detailRes.work;
      const pending = pendingRes.items || [];
      const approved = approvedRes.items || [];
      const chars = charsRes.items || [];
      const chapters = w.chapters || [];
      const cleanCount = w.clean_chapters ?? chapters.filter((c) => !c.is_corrupted).length;
      const corruptCount = w.corrupted_chapters ?? chapters.filter((c) => c.is_corrupted).length;

      const workTagIds = getWorkTagIds(w);
      if (workTagIds.length === 0 && w.genre_id) workTagIds.push(w.genre_id);
      const primaryTagId = workTagIds[0] || w.genre_id || 'mengwa';
      const extraTagIds = workTagIds.slice(1);

      $('#drawer-title').textContent = `《${w.title}》`;
      $('#drawer-kicker').innerHTML = `
        <span class="badge-pill badge-clean">干净 ${cleanCount} 章</span>
        ${corruptCount > 0 ? `<span class="badge-pill badge-corrupt">已隔离防盗乱码 ${corruptCount} 章</span>` : ''}
      `;

      const renderPickerPills = (filterText = '') => {
        const q = filterText.trim().toLowerCase();
        const allTags = (state.universe.tags || []).filter(
          (t) => !q || (t.name || '').toLowerCase().includes(q) || (t.channel || '').toLowerCase().includes(q)
        );
        return allTags.map((t) => {
          const isPrim = t.id === primaryTagId;
          const isSec = extraTagIds.includes(t.id);
          const cls = isPrim ? 'is-assigned-primary' : (isSec ? 'is-assigned-secondary' : '');
          const mark = isPrim ? '<span class="tag-role-badge">主</span>' : (isSec ? '<span>✓</span>' : '');
          return `
            <button class="work-tag-pick-pill ${cls}" type="button" data-pick-tag-id="${escHtml(t.id)}">
              <span style="color:${CHANNEL_COLORS[t.channel] || '#7dd3fc'}">●</span>
              <span>${escHtml(t.name)}</span>
              ${mark}
            </button>
          `;
        }).join('');
      };

      drawerBody.innerHTML = `
        <div class="glass-card work-tags-card">
          <div class="section-label-row">
            <span>归属标签（${workTagIds.length}）</span>
            <span class="muted-text">画布实线=主 · 虚线=副</span>
          </div>
          <div class="work-tags-bar">
            <span class="work-tag-chip is-primary">
              <span class="tag-role-badge">主</span>
              <span>${escHtml(getTagName(primaryTagId))}</span>
            </span>
            ${extraTagIds.map((tid) => `
              <span class="work-tag-chip is-secondary">
                <span>${escHtml(getTagName(tid))}</span>
                <button class="work-tag-remove" type="button" data-remove-tag-id="${escHtml(tid)}" title="移除副标签">×</button>
              </span>
            `).join('')}
            <button id="btn-work-add-tag" class="btn-add-tag-mini" type="button">${pickerOpen ? '收起标签' : '+ 标签'}</button>
          </div>
          <div id="work-tag-picker" class="work-tag-picker ${pickerOpen ? '' : 'hidden'}">
            <input id="work-tag-filter" class="dark-input" type="text" placeholder="过滤标签，点击增删副标签（最多5个副标签）..." autocomplete="off" />
            <div id="work-tag-picker-grid" class="work-tag-picker-grid">${renderPickerPills('')}</div>
          </div>
        </div>

        ${w.summary_one_page ? `
          <div class="glass-card">
            <div class="section-label-row"><span>一页纸故事精简卡</span></div>
            <div class="drawer-desc">${escHtml(w.summary_one_page)}</div>
          </div>
        ` : ''}

        ${pending.length ? `
          <div class="drawer-section">
            <div class="section-label-row"><span>本书待试菜新梗</span><span class="badge-pill badge-pending">${pending.length}</span></div>
            ${pending.map((s) => renderSnippetCardHtml(s, true, workTagIds)).join('')}
          </div>
        ` : ''}

        <div class="drawer-section">
          <div class="section-label-row"><span>本书原话好梗</span><span>${approved.length}</span></div>
          ${approved.length
            ? approved.map((s) => renderSnippetCardHtml(s, false, workTagIds)).join('')
            : '<div class="muted-text">本书尚无已留用好梗，可在上方试菜确认</div>'}
        </div>

        ${chars.length ? `
          <div class="drawer-section">
            <div class="section-label-row"><span>本书人物特点</span><span>${chars.length}</span></div>
            ${chars.map((c) => `
              <div class="glass-card">
                <strong>${escHtml(c.name)}</strong>
                <div class="drawer-desc">${escHtml(c.traits)}</div>
              </div>
            `).join('')}
          </div>
        ` : ''}

        <div class="drawer-section">
          <div class="section-label-row"><span>章节清洗清单</span><span>共 ${chapters.length} 章</span></div>
          ${chapters.slice(0, 60).map((ch) => `
            <div class="glass-card">
              <div class="card-meta-row">
                <span>第 ${ch.chapter_num} 章 · ${escHtml(ch.chapter_title || '')}</span>
                ${ch.is_corrupted
                  ? `<span class="badge-pill badge-corrupt">防盗隔离</span>`
                  : `<button class="span-link-btn" type="button" data-work-id="${escHtml(w.id)}" data-ch="${ch.chapter_num}" data-ls="1" data-le="18">看前18行</button>`}
              </div>
              ${ch.is_corrupted && ch.corruption_reason ? `<div class="muted-text">${escHtml(ch.corruption_reason)}</div>` : ''}
            </div>
          `).join('')}
        </div>
      `;

      bindDrawerSnippetEvents(drawerBody);

      const addTagBtn = $('#btn-work-add-tag', drawerBody);
      const pickerBox = $('#work-tag-picker', drawerBody);
      const filterEl = $('#work-tag-filter', drawerBody);
      const gridEl = $('#work-tag-picker-grid', drawerBody);

      addTagBtn?.addEventListener('click', () => {
        const willOpen = pickerBox.classList.contains('hidden');
        pickerBox.classList.toggle('hidden', !willOpen);
        addTagBtn.textContent = willOpen ? '收起标签' : '+ 标签';
        if (willOpen) filterEl?.focus();
      });

      filterEl?.addEventListener('input', (ev) => {
        gridEl.innerHTML = renderPickerPills(ev.target.value);
      });

      $$('.work-tag-remove', drawerBody).forEach((btn) => {
        btn.addEventListener('click', async () => {
          const rmId = btn.dataset.removeTagId;
          const nextExtras = extraTagIds.filter((id) => id !== rmId);
          await saveWorkTagsAndRefresh(workId, primaryTagId, nextExtras, false);
        });
      });

      gridEl?.addEventListener('click', async (ev) => {
        const pill = ev.target.closest('.work-tag-pick-pill');
        if (!pill) return;
        const tid = pill.dataset.pickTagId;
        if (!tid || tid === primaryTagId) return;
        let nextExtras;
        if (extraTagIds.includes(tid)) {
          nextExtras = extraTagIds.filter((id) => id !== tid);
        } else {
          if (extraTagIds.length >= 5) {
            toast('单本书最多挂载 1 个主标签 + 5 个副标签', 'err');
            return;
          }
          nextExtras = [...extraTagIds, tid];
        }
        await saveWorkTagsAndRefresh(workId, primaryTagId, nextExtras, true);
      });
    } catch (err) {
      drawerBody.innerHTML = `<div class="empty-hint-box">${escHtml(err.message)}</div>`;
    }
  }

  // -------------------------------------------------------------------------
  // 互动支线节点抽屉（改台词 + 精话锁实时标红 + 一键 + 分支）
  // -------------------------------------------------------------------------
  async function openBranchNodeDrawer(node) {
    const graph = state.branchGraph;
    const outgoing = (graph?.choices || []).filter((c) => c.from_node_id === node.id);
    const typeLabel = NODE_TYPE_LABELS[node.node_type] || node.node_type;

    openDrawerShell({
      kicker: `<span class="badge-pill">${escHtml(node.node_code || 'S')}</span><span class="badge-pill">${escHtml(typeLabel)}</span>`,
      title: node.title,
    });

    drawerBody.innerHTML = `
      <div class="drawer-section">
        <div class="section-label-row"><span>场次标题 & 类型</span></div>
        <input id="bn-title" class="dark-input" type="text" value="${escHtml(node.title)}" />
        <div class="drawer-row-actions">
          <select id="bn-type" class="project-select" style="flex:1;">
            ${Object.entries(NODE_TYPE_LABELS).map(([k, v]) =>
              `<option value="${k}" ${node.node_type === k ? 'selected' : ''}>${v}</option>`
            ).join('')}
          </select>
          <input id="bn-speaker" class="dark-input" style="flex:1;" type="text" placeholder="出场人物" value="${escHtml(node.speaker_summary || '')}" />
        </div>
      </div>

      <div class="drawer-section">
        <div class="section-label-row"><span>台词与动作剧本</span></div>
        <textarea id="bn-script" class="dark-textarea" rows="7" placeholder="写下这一幕的对话与简练动作...">${escHtml(node.script_text || '')}</textarea>
        <div class="drawer-row-actions">
          <button id="bn-save" class="pill-btn-primary" type="button">保存场次</button>
          <button id="bn-jinghua" class="mini-pill-btn" type="button">🔒 精话锁标红</button>
          <button id="bn-delete" class="pill-btn-danger" type="button">删节点</button>
        </div>
        <div id="bn-jinghua-preview" class="jinghua-result-box hidden"></div>
      </div>

      <div class="drawer-section">
        <div class="section-label-row"><span>玩家分支选项 (${outgoing.length})</span></div>
        <div id="bn-choices-list" class="drawer-section">
          ${outgoing.length ? outgoing.map((ch) => {
            const target = (graph.nodes || []).find((n) => n.id === ch.to_node_id);
            return `
              <div class="branch-item-card">
                <div style="min-width:0;">
                  <div style="font-size:12px;color:#e2e8f0;">${escHtml(ch.choice_label)}</div>
                  <div class="muted-text">→ ${escHtml(target ? `${target.node_code} ${target.title}` : ch.to_node_id)}</div>
                </div>
                <button class="rule-chip-del bn-del-choice" type="button" data-choice-id="${escHtml(ch.id)}" title="删除连线">×</button>
              </div>
            `;
          }).join('') : '<div class="muted-text">当前为断头节点，点下方直接长出下一幕分支</div>'}
        </div>

        <div class="glass-card">
          <div class="section-label-row"><span>+ 分支（直接长出下一幕）</span></div>
          <input id="bn-new-choice-label" class="dark-input" type="text" placeholder="玩家选项（如：A. 嘴硬反呛 [好感-5]）" />
          <input id="bn-new-next-title" class="dark-input" type="text" placeholder="下一幕标题（如：反呛激怒反派）" />
          <div class="drawer-row-actions">
            <select id="bn-new-next-type" class="project-select" style="flex:1;">
              <option value="scene">普通剧情</option>
              <option value="climax">高潮反转</option>
              <option value="good_ending">好结局</option>
              <option value="bad_ending">坏结局</option>
              <option value="hidden_ending">隐藏结局</option>
            </select>
            <button id="bn-add-branch" class="pill-btn-primary" type="button">+ 分支</button>
          </div>
        </div>
      </div>
    `;

    $('#bn-save').addEventListener('click', async () => {
      try {
        await api(`/api/nodes/${node.id}`, {
          method: 'PUT',
          body: {
            project_id: node.project_id,
            title: $('#bn-title').value.trim() || node.title,
            node_code: node.node_code,
            episode_num: node.episode_num || 1,
            node_type: $('#bn-type').value,
            speaker_summary: $('#bn-speaker').value.trim(),
            script_text: $('#bn-script').value,
            camera_or_acting_cue: node.camera_or_acting_cue || '',
            pos_x: node.pos_x,
            pos_y: node.pos_y,
          },
        });
        toast('场次剧本已保存', 'ok');
        await loadBranchGraph(state.currentProjectId, false);
      } catch (err) {
        toast(err.message, 'err');
      }
    });

    $('#bn-jinghua').addEventListener('click', async () => {
      const txt = $('#bn-script').value.trim();
      if (!txt) return toast('请先输入台词或正文再检测', 'err');
      try {
        const res = await api('/api/jinghua/check', { method: 'POST', body: { text: txt } });
        const box = $('#bn-jinghua-preview');
        box.classList.remove('hidden');
        box.innerHTML = renderHighlightedJinghua(txt, res.result);
      } catch (err) {
        toast(err.message, 'err');
      }
    });

    $('#bn-delete').addEventListener('click', async () => {
      try {
        await api(`/api/nodes/${node.id}`, { method: 'DELETE' });
        closeDrawer();
        toast('已删除场次节点', 'ok');
        await loadBranchGraph(state.currentProjectId, false);
      } catch (err) {
        toast(err.message, 'err');
      }
    });

    $$('.bn-del-choice', drawerBody).forEach((btn) => {
      btn.addEventListener('click', async () => {
        try {
          await api(`/api/choices/${btn.dataset.choiceId}`, { method: 'DELETE' });
          toast('已断开分支连线', 'ok');
          await loadBranchGraph(state.currentProjectId, false);
          const refreshed = state.branchGraph?.nodes?.find((x) => x.id === node.id);
          if (refreshed) openBranchNodeDrawer(refreshed);
        } catch (err) {
          toast(err.message, 'err');
        }
      });
    });

    $('#bn-add-branch').addEventListener('click', async () => {
      const choiceLabel = $('#bn-new-choice-label').value.trim();
      const nextTitle = $('#bn-new-next-title').value.trim();
      if (!choiceLabel || !nextTitle) {
        return toast('请填写选项文字与下一幕标题', 'err');
      }
      try {
        const count = (state.branchGraph?.nodes?.length || 1) + 1;
        const offsetX = (outgoing.length - 0.5) * 165;
        const createdNodeRes = await api(`/api/projects/${state.currentProjectId}/nodes`, {
          method: 'POST',
          body: {
            title: nextTitle,
            node_code: `S${count}`,
            node_type: $('#bn-new-next-type').value,
            pos_x: (node.pos_x || 120) + offsetX,
            pos_y: (node.pos_y || 120) + 125,
          },
        });
        const newNode = createdNodeRes.item;
        await api(`/api/projects/${state.currentProjectId}/choices`, {
          method: 'POST',
          body: {
            from_node_id: node.id,
            to_node_id: newNode.id,
            choice_label: choiceLabel,
            sort_order: outgoing.length,
          },
        });
        toast('已长出新分支节点', 'ok');
        await loadBranchGraph(state.currentProjectId, false);
        const refreshed = state.branchGraph?.nodes?.find((x) => x.id === node.id);
        if (refreshed) openBranchNodeDrawer(refreshed);
      } catch (err) {
        toast(err.message, 'err');
      }
    });
  }

  // -------------------------------------------------------------------------
  // 精话锁标红渲染 & 浮层管理
  // -------------------------------------------------------------------------
  function renderHighlightedJinghua(text, result) {
    const hits = result?.hits || [];
    const patterns = Array.from(new Set(hits.map((h) => h.matched_text).filter(Boolean)));
    let html = escHtml(text);
    for (const pat of patterns) {
      const safe = escHtml(pat).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      html = html.replace(new RegExp(safe, 'g'), `<span class="greasy-mark">${escHtml(pat)}</span>`);
    }
    html = html.replace(/\n/g, '<br/>');

    if (hits.length === 0) {
      return `<div>${html}</div><div class="rewrite-tips" style="color:#6ee7b7;">✓ 通过精话锁体检（${result.score ?? 100}分）：引号外旁白平实克制，未命中油腻词。</div>`;
    }

    const tips = hits.slice(0, 5).map((h) =>
      `<div>• <span class="greasy-mark">${escHtml(h.matched_text)}</span> → ${escHtml(h.good_replacement_example || h.reason || '改为平实具体动作')}</div>`
    ).join('');
    return `<div>${html}</div><div class="rewrite-tips">${tips}</div>`;
  }

  async function loadJinghuaRules() {
    try {
      const res = await api('/api/jinghua/rules?include_disabled=true');
      state.jinghuaRules = res.items || [];
      const enabledCount = state.jinghuaRules.filter((r) => r.enabled).length;
      $('#lock-rule-count').textContent = String(enabledCount);
      $('#jinghua-rules-meta').textContent = `${enabledCount}/${state.jinghuaRules.length} 启用`;

      const cloud = $('#jinghua-rules-cloud');
      cloud.innerHTML = state.jinghuaRules.map((r) => `
        <span class="rule-chip ${r.enabled ? '' : 'disabled'}" data-rule-id="${escHtml(r.id)}" data-enabled="${r.enabled ? '1' : '0'}" title="${escHtml(r.reason || '点击切换启停')}">
          <span>${escHtml(r.bad_pattern)}</span>
          <button class="rule-chip-del" type="button" data-del-id="${escHtml(r.id)}" title="移除禁词">×</button>
        </span>
      `).join('');
    } catch (err) {
      toast(`精话锁加载失败：${err.message}`, 'err');
    }
  }

  function initJinghuaPanel() {
    const panel = $('#jinghua-panel');
    const toggleBtn = $('#btn-jinghua-lock');

    toggleBtn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const willOpen = panel.classList.contains('hidden');
      hidePopovers();
      if (willOpen) {
        panel.classList.remove('hidden');
        toggleBtn.classList.add('active');
        loadJinghuaRules();
      }
    });

    $('#jinghua-close').addEventListener('click', () => {
      panel.classList.add('hidden');
      toggleBtn.classList.remove('active');
    });

    $('#jinghua-run-btn').addEventListener('click', async () => {
      const text = $('#jinghua-input').value.trim();
      if (!text) return toast('请先粘贴需要检查的段落', 'err');
      try {
        const res = await api('/api/jinghua/check', { method: 'POST', body: { text } });
        const r = res.result;
        const badge = $('#jinghua-score-badge');
        badge.classList.remove('hidden', 'pass', 'fail');
        badge.classList.add(r.passed ? 'pass' : 'fail');
        badge.textContent = r.passed ? `✓ ${r.score}分 · 干净` : `⚠ ${r.score}分 · 命中 ${r.hits.length} 处`;

        const box = $('#jinghua-result-box');
        box.classList.remove('hidden');
        box.innerHTML = renderHighlightedJinghua(text, r);
      } catch (err) {
        toast(err.message, 'err');
      }
    });

    $('#jinghua-rules-cloud').addEventListener('click', async (ev) => {
      const delBtn = ev.target.closest('.rule-chip-del');
      if (delBtn) {
        ev.stopPropagation();
        const rid = delBtn.dataset.delId;
        try {
          await api(`/api/jinghua/rules/${rid}`, { method: 'DELETE' });
          await loadJinghuaRules();
        } catch (err) {
          toast(err.message, 'err');
        }
        return;
      }

      const chip = ev.target.closest('.rule-chip');
      if (chip) {
        const rid = chip.dataset.ruleId;
        const nextEnabled = chip.dataset.enabled !== '1';
        try {
          await api(`/api/jinghua/rules/${rid}`, { method: 'PATCH', body: { enabled: nextEnabled } });
          await loadJinghuaRules();
        } catch (err) {
          toast(err.message, 'err');
        }
      }
    });

    $('#jinghua-new-pattern').addEventListener('keydown', async (ev) => {
      if (ev.key !== 'Enter') return;
      const val = ev.target.value.trim();
      if (!val) return;
      try {
        await api('/api/jinghua/rules', { method: 'POST', body: { bad_pattern: val } });
        ev.target.value = '';
        toast(`已拉黑禁词：${val}`, 'ok');
        await loadJinghuaRules();
      } catch (err) {
        toast(err.message, 'err');
      }
    });
  }

  // -------------------------------------------------------------------------
  // 拖拽文件直接喂书 + 「+ 喂小说」按钮选择上传
  // -------------------------------------------------------------------------
  async function uploadNovelFile(file, genreId, extraGenreIds = [], autoDetectTags = true) {
    if (!file) return;
    const ext = (file.name.split('.').pop() || '').toLowerCase();
    if (!['txt', 'docx'].includes(ext)) {
      toast('仅支持 .txt 或 .docx 小说文件', 'err');
      return;
    }

    const tagObj = getTagById(genreId);
    toast(`正在解析喂入【${tagObj ? tagObj.name : genreId}】：${file.name}...`);

    try {
      const form = new FormData();
      form.append('file', file);
      form.append('genre_id', genreId);
      if (extraGenreIds.length > 0) {
        form.append('extra_genre_ids', extraGenreIds.join(','));
      }
      form.append('auto_detect_tags', autoDetectTags ? 'true' : 'false');

      const res = await api('/api/upload-novel', { method: 'POST', body: form });
      const info = res.result || res;

      const finalTagIds = Array.isArray(info.tag_ids) && info.tag_ids.length
        ? info.tag_ids
        : [genreId, ...extraGenreIds];

      if (tagObj) {
        state.expandedChannels.add(tagObj.channel || '通用');
        state.expandedTagIds.add(tagObj.id);
      }
      for (const tid of finalTagIds) {
        const t = getTagById(tid);
        if (t) state.expandedChannels.add(t.channel || '通用');
      }

      await loadUniverse(false);
      const corruptNote = info.corrupted_chapters > 0 ? `，已隔离防盗 ${info.corrupted_chapters} 章` : '';
      const tagNamesNote = finalTagIds.length > 1
        ? ` · 多签[${finalTagIds.map((tid) => getTagName(tid)).join(' / ')}]`
        : '';
      toast(`入库成功：《${info.title}》（干净 ${info.clean_chapters} 章${corruptNote}，待试菜 ${info.pending_snippets_count ?? 0} 条${tagNamesNote}）`, 'ok');

      if (info.work_id) {
        state.selectedNodeId = `work:${info.work_id}`;
        refreshHighlightClasses();
        await openWorkDrawer(info.work_id);
      } else if (tagObj) {
        state.selectedNodeId = `tag:${tagObj.id}`;
        refreshHighlightClasses();
        await openTagDrawer(tagObj);
      }
    } catch (err) {
      toast(`喂书失败：${err.message}`, 'err');
    }
  }

  function renderUploadSelectedChips() {
    const { primaryTagId, extraTagIds } = state.uploadDraft;
    const chipsEl = $('#upload-selected-chips');
    const confirmBtn = $('#upload-confirm-btn');

    if (!primaryTagId) {
      chipsEl.innerHTML = '<span class="muted-text">点击下方胶囊：第 1 个为主标签，可顺手点选 0~4 个副标签</span>';
      confirmBtn.disabled = true;
      return;
    }

    const parts = [];
    parts.push(`
      <span class="upload-sel-chip is-primary">
        <span class="pill-role-badge">主</span>
        <span>${escHtml(getTagName(primaryTagId))}</span>
        <button class="upload-sel-remove" type="button" data-rm-role="primary" data-rm-id="${escHtml(primaryTagId)}" title="重选主标签">×</button>
      </span>
    `);
    for (const eid of extraTagIds) {
      parts.push(`
        <span class="upload-sel-chip is-secondary">
          <span class="pill-role-badge">副</span>
          <span>${escHtml(getTagName(eid))}</span>
          <button class="upload-sel-remove" type="button" data-rm-role="extra" data-rm-id="${escHtml(eid)}" title="移除副标签">×</button>
        </span>
      `);
    }
    if (extraTagIds.length < 4) {
      parts.push(`<span class="muted-text">可再选 ${4 - extraTagIds.length} 个副标签</span>`);
    }
    chipsEl.innerHTML = parts.join('');
    confirmBtn.disabled = false;
  }

  function renderUploadTagPopover(filterText = '') {
    const q = filterText.trim().toLowerCase();
    const { primaryTagId, extraTagIds } = state.uploadDraft;
    const list = (state.universe.tags || []).filter((t) =>
      !q || (t.name || '').toLowerCase().includes(q) || (t.channel || '').toLowerCase().includes(q)
    );
    $('#upload-tag-list').innerHTML = list.map((t) => {
      const isPrim = t.id === primaryTagId;
      const isSec = extraTagIds.includes(t.id);
      const cls = isPrim ? 'is-primary' : (isSec ? 'is-secondary' : '');
      const roleBadge = isPrim
        ? '<span class="pill-role-badge">主</span>'
        : (isSec ? '<span class="pill-role-badge">副</span>' : '');
      return `
        <button class="upload-tag-pill ${cls}" type="button" data-gid="${escHtml(t.id)}">
          <span style="color:${CHANNEL_COLORS[t.channel] || '#7dd3fc'}">●</span>
          <span>${escHtml(t.channel)} · ${escHtml(t.name)}</span>
          ${roleBadge}
        </button>
      `;
    }).join('');
    renderUploadSelectedChips();
  }

  function openUploadTagPicker(file, defaultPrimaryTagId = null) {
    state.pendingUploadFile = file;
    state.uploadDraft = {
      primaryTagId: defaultPrimaryTagId || null,
      extraTagIds: [],
      autoDetect: true,
    };
    const autoChk = $('#upload-auto-detect');
    if (autoChk) autoChk.checked = true;
    $('#upload-file-name').textContent = `待导入文件：${file.name}`;
    $('#upload-tag-filter').value = '';
    renderUploadTagPopover('');
    $('#upload-tag-popover').classList.remove('hidden');
    $('#upload-tag-filter').focus();
  }

  function initUploadAndDragDrop() {
    const fileInput = $('#novel-file-input');

    $('#btn-feed-novel').addEventListener('click', () => {
      const selNode = state.selectedNodeId ? simNodes.get(state.selectedNodeId) : null;
      state.feedTargetGenreId = selNode?.genreId || null;
      fileInput.value = '';
      fileInput.click();
    });

    $('#drawer-feed-here').addEventListener('click', () => {
      fileInput.value = '';
      fileInput.click();
    });

    fileInput.addEventListener('change', () => {
      const file = fileInput.files && fileInput.files[0];
      if (!file) return;
      const targetGid = state.feedTargetGenreId || null;
      state.feedTargetGenreId = null;
      openUploadTagPicker(file, targetGid);
    });

    $('#upload-popover-close').addEventListener('click', () => {
      state.pendingUploadFile = null;
      $('#upload-tag-popover').classList.add('hidden');
    });

    $('#upload-tag-filter').addEventListener('input', (ev) => {
      renderUploadTagPopover(ev.target.value);
    });

    $('#upload-auto-detect').addEventListener('change', (ev) => {
      state.uploadDraft.autoDetect = Boolean(ev.target.checked);
    });

    $('#upload-selected-chips').addEventListener('click', (ev) => {
      const rmBtn = ev.target.closest('.upload-sel-remove');
      if (!rmBtn) return;
      const role = rmBtn.dataset.rmRole;
      const rmId = rmBtn.dataset.rmId;
      if (role === 'primary') {
        if (state.uploadDraft.extraTagIds.length > 0) {
          state.uploadDraft.primaryTagId = state.uploadDraft.extraTagIds.shift();
        } else {
          state.uploadDraft.primaryTagId = null;
        }
      } else {
        state.uploadDraft.extraTagIds = state.uploadDraft.extraTagIds.filter((id) => id !== rmId);
      }
      renderUploadTagPopover($('#upload-tag-filter').value);
    });

    $('#upload-tag-list').addEventListener('click', (ev) => {
      const btn = ev.target.closest('.upload-tag-pill');
      if (!btn || !state.pendingUploadFile) return;
      const gid = btn.dataset.gid;
      const draft = state.uploadDraft;
      if (!draft.primaryTagId) {
        draft.primaryTagId = gid;
      } else if (draft.primaryTagId === gid) {
        if (draft.extraTagIds.length > 0) {
          draft.primaryTagId = draft.extraTagIds.shift();
        } else {
          draft.primaryTagId = null;
        }
      } else if (draft.extraTagIds.includes(gid)) {
        draft.extraTagIds = draft.extraTagIds.filter((id) => id !== gid);
      } else {
        if (draft.extraTagIds.length >= 4) {
          toast('顺手点选最多支持 4 个副标签', 'err');
          return;
        }
        draft.extraTagIds.push(gid);
      }
      renderUploadTagPopover($('#upload-tag-filter').value);
    });

    $('#upload-confirm-btn').addEventListener('click', () => {
      const file = state.pendingUploadFile;
      const { primaryTagId, extraTagIds, autoDetect } = state.uploadDraft;
      if (!file || !primaryTagId) return;
      state.pendingUploadFile = null;
      $('#upload-tag-popover').classList.add('hidden');
      uploadNovelFile(file, primaryTagId, extraTagIds, autoDetect);
    });

    // 全屏拖拽文件直接投喂到对应标签胶囊（自动选中主标签并允许顺手选副标签）
    let activeDropTagId = null;
    const hintBanner = $('#drop-hint-banner');

    function clearDropHover() {
      if (activeDropTagId) {
        const g = nodeDomMap.get(activeDropTagId);
        if (g) {
          g.classList.remove('is-drop-hover');
          const tip = g.querySelector('.node-drop-tip');
          if (tip) tip.classList.add('hidden');
        }
        activeDropTagId = null;
      }
    }

    function findTagUnderCursor(clientX, clientY) {
      if (state.mode !== 'universe') return null;
      const rect = svgElRoot.getBoundingClientRect();
      const wx = (clientX - rect.left - state.view.x) / state.view.scale;
      const wy = (clientY - rect.top - state.view.y) / state.view.scale;
      for (const node of simNodes.values()) {
        if (node.kind !== 'tag') continue;
        if (Math.abs(wx - node.x) <= node.width / 2 + 12 && Math.abs(wy - node.y) <= node.height / 2 + 10) {
          return node;
        }
      }
      return null;
    }

    window.addEventListener('dragover', (ev) => {
      if (!ev.dataTransfer || !Array.from(ev.dataTransfer.types).includes('Files')) return;
      ev.preventDefault();
      hintBanner.classList.remove('hidden');

      const hitTag = findTagUnderCursor(ev.clientX, ev.clientY);
      const nextId = hitTag ? hitTag.id : null;
      if (nextId !== activeDropTagId) {
        clearDropHover();
        if (nextId) {
          activeDropTagId = nextId;
          const g = nodeDomMap.get(nextId);
          if (g) {
            g.classList.add('is-drop-hover');
            const tip = g.querySelector('.node-drop-tip');
            if (tip) tip.classList.remove('hidden');
          }
        }
      }
    });

    window.addEventListener('dragleave', (ev) => {
      if (ev.clientX <= 0 || ev.clientY <= 0 || ev.clientX >= window.innerWidth || ev.clientY >= window.innerHeight) {
        hintBanner.classList.add('hidden');
        clearDropHover();
      }
    });

    window.addEventListener('drop', (ev) => {
      if (!ev.dataTransfer || !ev.dataTransfer.files || ev.dataTransfer.files.length === 0) return;
      ev.preventDefault();
      hintBanner.classList.add('hidden');
      const hitTag = findTagUnderCursor(ev.clientX, ev.clientY);
      clearDropHover();

      const file = ev.dataTransfer.files[0];
      openUploadTagPicker(file, hitTag?.genreId || null);
    });
  }

  // -------------------------------------------------------------------------
  // 数据加载 & 模式切换
  // -------------------------------------------------------------------------
  async function loadUniverse(autoFit = false) {
    try {
      const data = await api('/api/graph/universe');
      state.universe = {
        root: data.root || {},
        channels: data.channels || [],
        tags: data.tags || [],
        works: data.works || [],
        work_tag_links: data.work_tag_links || [],
      };
      const lockCount = data.root?.enabled_jinghua_rules ?? data.root?.total_jinghua_rules;
      if (lockCount !== undefined) {
        $('#lock-rule-count').textContent = String(lockCount);
      }
      if (state.mode === 'universe') {
        computeUniverseLayout();
        if (autoFit || !didInitialFit) {
          didInitialFit = true;
          fitToScreen();
        }
      }
    } catch (err) {
      toast(`加载知识星图失败：${err.message}`, 'err');
    }
  }

  async function loadProjects() {
    const res = await api('/api/projects');
    state.projects = res.items || [];
    const sel = $('#branch-project-select');
    if (state.projects.length === 0) {
      sel.innerHTML = '<option value="">暂无剧本，点击右侧新建</option>';
      state.currentProjectId = null;
      state.branchGraph = null;
      if (state.mode === 'branching') computeBranchingLayout();
      return;
    }
    if (!state.currentProjectId || !state.projects.some((p) => p.id === state.currentProjectId)) {
      state.currentProjectId = state.projects[0].id;
    }
    sel.innerHTML = state.projects.map((p) =>
      `<option value="${escHtml(p.id)}" ${p.id === state.currentProjectId ? 'selected' : ''}>${escHtml(p.title)} (${p.node_count ?? 0}幕)</option>`
    ).join('');
    await loadBranchGraph(state.currentProjectId, true);
  }

  async function loadBranchGraph(projectId, autoFit = false) {
    if (!projectId) return;
    try {
      const graph = await api(`/api/projects/${projectId}/graph`);
      state.branchGraph = graph;
      const diag = graph.diagnostics || {};
      const hp = $('#branch-health-pill');
      const deadCount = (diag.dead_end_node_ids || []).length;
      if (diag.is_healthy) {
        hp.classList.remove('is-warn');
        hp.textContent = `✓ 健康 · ${diag.total_nodes}幕`;
      } else {
        hp.classList.add('is-warn');
        hp.textContent = deadCount > 0 ? `⚠ ${deadCount} 断头待补` : `⚠ 需补结局/起始幕`;
      }

      if (state.mode === 'branching') {
        computeBranchingLayout();
        if (autoFit) fitToScreen();
      }
    } catch (err) {
      toast(`加载互动支线失败：${err.message}`, 'err');
    }
  }

  function switchMode(mode) {
    state.mode = mode;
    state.selectedNodeId = null;
    state.hoveredNodeId = null;
    closeDrawer();
    hidePopovers();

    $('#mode-universe').classList.toggle('active', mode === 'universe');
    $('#mode-branching').classList.toggle('active', mode === 'branching');
    $('#controls-universe').classList.toggle('hidden', mode !== 'universe');
    $('#controls-branching').classList.toggle('hidden', mode !== 'branching');

    if (mode === 'universe') {
      computeUniverseLayout();
      fitToScreen();
    } else {
      loadProjects();
    }
  }

  function initTopbarControls() {
    $('#mode-universe').addEventListener('click', () => switchMode('universe'));
    $('#mode-branching').addEventListener('click', () => switchMode('branching'));

    const searchInput = $('#search-input');
    const clearBtn = $('#search-clear');
    let searchTimer = null;
    searchInput.addEventListener('input', () => {
      state.searchQuery = searchInput.value;
      clearBtn.classList.toggle('hidden', !state.searchQuery);
      if (state.mode === 'universe') {
        computeUniverseLayout();
      }
      if (searchTimer) clearTimeout(searchTimer);
      const q = state.searchQuery.trim();
      if (!q) {
        state.searchHitGenreIds.clear();
        state.searchHitWorkIds.clear();
        if (state.mode === 'universe') computeUniverseLayout();
        return;
      }
      searchTimer = setTimeout(async () => {
        try {
          const [appRes, pendRes] = await Promise.all([
            api(`/api/snippets?status=approved&query=${encodeURIComponent(q)}&limit=30`),
            api(`/api/snippets?status=pending&query=${encodeURIComponent(q)}&limit=20`),
          ]);
          const hits = [...(appRes.items || []), ...(pendRes.items || [])];
          state.searchHitGenreIds = new Set(hits.map((h) => h.genre_id).filter(Boolean));
          state.searchHitWorkIds = new Set(hits.map((h) => h.work_id).filter(Boolean));
          if (state.mode === 'universe') computeUniverseLayout();
        } catch { /* ignore transient search errors */ }
      }, 220);
    });

    clearBtn.addEventListener('click', () => {
      searchInput.value = '';
      state.searchQuery = '';
      state.searchHitGenreIds.clear();
      state.searchHitWorkIds.clear();
      clearBtn.classList.add('hidden');
      if (state.mode === 'universe') computeUniverseLayout();
    });

    $('#filter-populated-btn').addEventListener('click', () => {
      state.onlyPopulated = !state.onlyPopulated;
      $('#filter-populated-btn').classList.toggle('active', state.onlyPopulated);
      $('#filter-populated-label').textContent = state.onlyPopulated ? '仅看有货' : '全部标签';
      if (state.mode === 'universe') computeUniverseLayout();
    });

    $('#branch-project-select').addEventListener('change', (ev) => {
      state.currentProjectId = ev.target.value;
      loadBranchGraph(state.currentProjectId, true);
    });

    $('#btn-new-project').addEventListener('click', (ev) => {
      ev.stopPropagation();
      hidePopovers();
      $('#new-project-popover').classList.remove('hidden');
      $('#new-proj-title').focus();
    });

    $('#new-proj-close').addEventListener('click', () => $('#new-project-popover').classList.add('hidden'));

    $('#new-proj-submit').addEventListener('click', async () => {
      const title = $('#new-proj-title').value.trim();
      const synopsis = $('#new-proj-synopsis').value.trim();
      if (!title) return toast('请填写剧本名称', 'err');
      try {
        const res = await api('/api/projects', {
          method: 'POST',
          body: { title, synopsis, project_mode: 'branching' },
        });
        const proj = res.item;
        await api(`/api/projects/${proj.id}/nodes`, {
          method: 'POST',
          body: {
            title: '第一幕：开场冲突',
            node_code: 'S1',
            node_type: 'start',
            pos_x: 120,
            pos_y: 80,
          },
        });
        $('#new-proj-title').value = '';
        $('#new-proj-synopsis').value = '';
        $('#new-project-popover').classList.add('hidden');
        state.currentProjectId = proj.id;
        toast(`已创建剧本《${proj.title}》`, 'ok');
        await loadProjects();
      } catch (err) {
        toast(err.message, 'err');
      }
    });

    $('#btn-export-project').addEventListener('click', async () => {
      if (!state.currentProjectId) return toast('请先选择或新建一个互动剧本', 'err');
      try {
        const res = await api(`/api/projects/${state.currentProjectId}/export`, { method: 'POST' });
        await navigator.clipboard?.writeText(res.markdown).catch(() => {});
        toast('已导出 Markdown 到 exports/ 并复制到剪贴板', 'ok');
      } catch (err) {
        toast(err.message, 'err');
      }
    });

    $('#drawer-close').addEventListener('click', () => {
      closeDrawer();
      state.selectedNodeId = null;
      refreshHighlightClasses();
    });

    window.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') {
        closeDrawer();
        hidePopovers();
        state.selectedNodeId = null;
        refreshHighlightClasses();
      }
    });
  }

  // -------------------------------------------------------------------------
  // 启动入口
  // -------------------------------------------------------------------------
  async function boot() {
    initStageControls();
    initTopbarControls();
    initUploadAndDragDrop();
    initJinghuaPanel();
    await Promise.all([loadUniverse(true), loadJinghuaRules()]);
  }

  boot();
})();
