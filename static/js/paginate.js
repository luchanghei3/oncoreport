/* =====================================================================
 * PDF 物理分页引擎
 * ---------------------------------------------------------------------
 * 目标：任何业务容器都不允许作为「一个 DOM 元素」跨 PDF 物理分页。
 *
 * 规则实现：
 *  1. 报告内容被重新装入显式的物理页容器 .pdf-page（高度 = 内容区高度）。
 *  2. 当某容器在当前页放不下时：
 *       ① 把本页放得下的内容切出来，放进一个【全新、独立、完整闭合】的
 *          同类容器（cloneNode(false) 保留 class/style，因此 border、
 *          padding、background、margin 与原容器一致），留在当页并结束本页；
 *       ② 剩余内容新建另一个同类容器，完整闭合后放到下一页继续渲染。
 *  3. 同一个 DOM 容器绝不会被浏览器拆分到两个物理页。
 *  4. 不使用 break-inside:avoid 整块禁止拆分（report.css 中 .pdf-page 下已
 *     把这些规则重置为 auto），允许内容分页，只是必须拆成多个闭合容器。
 *  5. 原子元素（img/svg/canvas 等）无法切分时，单独占据一整页。
 *
 * 版面质量保障（避免「一页只有一个标题」）：
 *  A. 阻塞子节点也会被切分：若下一个子节点放不下、但本页仍有可观余量，
 *     则递归切分该子节点，把它的首段并入本页，尽量填满当页。
 *     （只在子节点边界切分会导致余量被大块内容白白浪费。）
 *  B. 标题 keep-with-next：标题不会单独留在本页末尾成为孤行标题。
 *  C. 表格按行切分并克隆表头，用 colgroup 固定列宽保证片段间列对齐。
 *
 * 数据安全保障（绝不允许内容丢失）：
 *  D. 所有切分都在【克隆体】上进行，切分失败绝不破坏原始 DOM。
 *  E. 每次切分后校验内容指纹，head + tail 必须与原内容完全一致，
 *     否则放弃切分（返回 null），由调用方整块换页。
 *
 * 由 render_oncoseeing_v5_0_1.py 在 Chromium 中注入执行（就地改造 DOM）。
 * ===================================================================== */
(function () {
  'use strict';

  var MM = 96 / 25.4;

  // 可按需微调的常量
  var SAFETY = 3 * MM;           // 浮点/舍入安全余量，保证片段严格小于页高
  var SPLIT_EPS = 2;             // 切分预算再留 2px，抵消测量噪声，避免量出的片段高度刚好卡在可用空间之外
  var MIN_FILL = 8 * MM;         // 当页余量小于该值就不再填充，整块移至下页
  var MIN_SPLIT = 6 * MM;        // 余量小于该值不再尝试切分
  var MIN_BLOCK_SPLIT = 20 * MM; // 阻塞子节点至少要有这么多余量才值得下钻切分
  var MAX_DEPTH = 20;            // 最大递归切分深度

  // 大章节（01/02/03…）是否强制另起一页。
  //   true  = 保持原有版式：每个编号章节从新页开始，章节末尾可能留白；
  //   false = 全报告连续流式排布，最省纸，但短章节会从上页中部开始。
  // 可在注入脚本前设置 window.PAGINATE_CHAPTER_BREAK 覆盖。
  var CHAPTER_BREAK = (typeof window.PAGINATE_CHAPTER_BREAK === 'boolean')
    ? window.PAGINATE_CHAPTER_BREAK
    : true;

  /* ------------------------- 尺寸读取与测量 ------------------------- */

  function rootVarPx(name, fallbackMm) {
    var raw = getComputedStyle(document.documentElement).getPropertyValue(name);
    if (!raw) return fallbackMm * MM;
    raw = String(raw).trim();
    var m = raw.match(/^([\d.]+)\s*(mm|cm|in|pt|px)$/);
    if (!m) return fallbackMm * MM;
    var v = parseFloat(m[1]);
    switch (m[2]) {
      case 'mm': return v * MM;
      case 'cm': return v * 10 * MM;
      case 'in': return v * 96;
      case 'pt': return v * 96 / 72;
      default: return v;
    }
  }

  var PAGE_H = rootVarPx('--page-content-height', 252) - SAFETY;
  var PAGE_W = rootVarPx('--page-content-width', 186);

  // 离屏测量台：宽度固定为打印内容区宽度，保证测量结果与打印布局一致
  var host = document.createElement('div');
  host.setAttribute('aria-hidden', 'true');
  host.style.cssText =
    'position:absolute;left:-100000px;top:0;width:' + PAGE_W + 'px;' +
    'visibility:hidden;pointer-events:none;';
  document.body.appendChild(host);

  // 建立 BFC 的元素会把外边距包含在自身高度内，不存在外边距穿透
  function isBFC(cs) {
    if (cs.display === 'flow-root') return true;
    if (cs.overflow !== 'visible') return true;
    return /^(grid|flex|inline-grid|inline-flex)$/.test(cs.display);
  }

  // 无 padding/border 的非 BFC 元素，其首/末子块的外边距会「穿透」出来参与折叠，
  // getBoundingClientRect() 量不到这段间距，必须显式计入，否则页内实际占位会超出测量值。
  function effMarginBottom(el) {
    var cs = getComputedStyle(el);
    var own = parseFloat(cs.marginBottom) || 0;
    if (isBFC(cs)) return own;
    if ((parseFloat(cs.paddingBottom) || 0) > 0) return own;
    if ((parseFloat(cs.borderBottomWidth) || 0) > 0) return own;
    var last = el.lastElementChild;
    if (!last) return own;
    var lcs = getComputedStyle(last);
    if (lcs.position !== 'static' || !isBlockDisplay(last)) return own;
    return Math.max(own, effMarginBottom(last));
  }

  function effMarginTop(el) {
    var cs = getComputedStyle(el);
    var own = parseFloat(cs.marginTop) || 0;
    if (isBFC(cs)) return own;
    if ((parseFloat(cs.paddingTop) || 0) > 0) return own;
    if ((parseFloat(cs.borderTopWidth) || 0) > 0) return own;
    var first = el.firstElementChild;
    if (!first) return own;
    var fcs = getComputedStyle(first);
    if (fcs.position !== 'static' || !isBlockDisplay(first)) return own;
    return Math.max(own, effMarginTop(first));
  }

  // 含上下外边距的外框高度（含穿透外边距，宁可保守也不能溢出）
  function outerHeight(el) {
    host.appendChild(el);
    var h = el.getBoundingClientRect().height + effMarginTop(el) + effMarginBottom(el);
    host.removeChild(el);
    return h;
  }

  // 就地测量（不移动节点）：必须在宿主/页面内调用
  function inlineHeight(el) {
    return el.getBoundingClientRect().height + effMarginTop(el) + effMarginBottom(el);
  }

  function isBlockDisplay(el) {
    return !/^(inline|ruby)/.test(getComputedStyle(el).display || '');
  }

  function isAtomic(el) {
    if (!el || el.nodeType !== 1) return true;
    if (el.getAttribute && el.getAttribute('data-nosplit') === '1') return true;
    return /^(IMG|SVG|CANVAS|BR|HR|SCRIPT|STYLE|NOSCRIPT|IFRAME|VIDEO|AUDIO|INPUT|SELECT|TEXTAREA|COLGROUP|COL)$/.test(el.tagName);
  }

  // 标题类节点：不允许单独留在本页末尾（孤行标题）
  function isHeadingNode(el) {
    if (!el || el.nodeType !== 1) return false;
    if (/^H[1-6]$/.test(el.tagName)) return true;
    var cl = el.classList;
    return !!(cl && (cl.contains('subsection-title') || cl.contains('finding-title') ||
      cl.contains('supplement-title') || cl.contains('card-title') ||
      cl.contains('section-title') || cl.contains('prevention-title')));
  }

  // 浅拷贝：保留标签、class、style、属性 → 样式与原容器完全一致
  function shell(el) {
    return el.cloneNode(false);
  }

  /* ------------------------- 内容指纹（数据无损校验） ------------------------- */

  // thead 会在每个表格片段中被克隆一次，比对时必须剔除，否则会误判为内容重复
  function normText(root) {
    if (!root) return '';
    var c = root.cloneNode(true);
    var heads = c.querySelectorAll ? c.querySelectorAll('thead') : [];
    for (var i = 0; i < heads.length; i++) {
      if (heads[i].parentNode) heads[i].parentNode.removeChild(heads[i]);
    }
    return (c.textContent || '').replace(/\s+/g, '');
  }

  // 内容签名必须是「可拼接」的：verifySplit 会把 head 与 tail 的签名直接相加后
  // 与原内容比对，因此这里不能携带任何前缀（早期版本加了 rows=N| 前缀，导致
  // 拼接结果永远不匹配，表格按行切分被全部误判为失败）。
  function contentSig(el) {
    if (!el) return '';
    return normText(el);
  }

  // 行数单独校验：纯文本比对可能因内容完全相同的行而漏判整行丢失。
  function rowCount(el) {
    if (!el) return 0;
    if (el.tagName === 'TABLE') {
      var tb = el.tBodies && el.tBodies[0];
      return tb ? tb.rows.length : 0;
    }
    return el.querySelectorAll ? el.querySelectorAll('tbody tr').length : 0;
  }

  function sortedChars(s) { return s.split('').sort().join(''); }

  // 切分前后内容必须完全一致，否则宁可不切。
  // 先严格按序比对；若不一致，允许「标题 keep-with-next 把标题顺延到下一段」
  // 造成的顺序调整——此时退化为按字符多重集比对，仍能捕获内容丢失或重复。
  function verifySplit(el, head, tail) {
    if (rowCount(el) !== rowCount(head) + (tail ? rowCount(tail) : 0)) return false;
    var before = contentSig(el);
    if (!before) return true; // 无文本内容（纯结构），跳过校验
    var after = contentSig(head) + (tail ? contentSig(tail) : '');
    if (after === before) return true;
    return sortedChars(after) === sortedChars(before);
  }

  /* ------------------------- 表格按行切分 ------------------------- */

  // 列宽一律用【百分比】：百分比能随容器宽度自适应；绝对 px 是在 186mm 的
  // 测量台上量出来的，而表格实际位于带内边距的卡片中会偏窄，套用绝对 px 会溢出。
  function columnPercents(table) {
    var raw = table.getAttribute && table.getAttribute('data-colwidths');
    if (raw) {
      var parts = raw.split(',').map(parseFloat).filter(function (v) { return !isNaN(v); });
      if (parts.length) return parts;
    }
    var t = table.cloneNode(true);     // 用克隆测量，避免把原表格摘出父节点
    host.appendChild(t);
    var ref = t.tHead && t.tHead.rows[0]
      ? t.tHead.rows[0]
      : (t.tBodies[0] && t.tBodies[0].rows[0]);
    var pcts = [];
    if (ref) {
      var total = t.getBoundingClientRect().width;
      for (var i = 0; i < ref.cells.length; i++) {
        var w = ref.cells[i].getBoundingClientRect().width;
        pcts.push(total ? (w / total * 100) : (100 / ref.cells.length));
      }
    }
    host.removeChild(t);
    return pcts;
  }

  // 固定列宽，保证拆分后各片段列对齐
  function fixColumnWidths(table, pcts) {
    if (!pcts || !pcts.length) return;
    table.style.tableLayout = 'fixed';
    table.style.width = '100%';
    var old = table.querySelector('colgroup');
    if (old) table.removeChild(old);
    var cg = document.createElement('colgroup');
    pcts.forEach(function (p) {
      var c = document.createElement('col');
      c.style.width = p + '%';
      cg.appendChild(c);
    });
    table.insertBefore(cg, table.firstChild);
  }

  // 分页前先记录所有表格的自然列宽：此刻它们仍在真实文档流中，宽度是正确的。
  // 以百分比写入 data-colwidths，cloneNode 会复制属性，拆分片段可直接复用。
  function recordColumnWidths() {
    var tables = document.querySelectorAll('table');
    for (var i = 0; i < tables.length; i++) {
      var t = tables[i];
      if (t.getAttribute('data-colwidths')) continue;
      var pcts = columnPercents(t);
      if (pcts.length) {
        t.setAttribute('data-colwidths', pcts.map(function (p) { return p.toFixed(2); }).join(','));
      }
    }
  }

  function splitTable(table, maxH) {
    if (maxH < MIN_SPLIT) return null;
    var tbody = table.tBodies && table.tBodies[0];
    if (!tbody) return null;
    var rows = Array.prototype.slice.call(tbody.rows);
    if (rows.length < 2) return null;

    var pcts = columnPercents(table);

    var head = shell(table);
    if (table.tHead) head.appendChild(table.tHead.cloneNode(true));
    var hb = document.createElement('tbody');
    head.appendChild(hb);
    fixColumnWidths(head, pcts); // 先定列宽再逐行量高，避免量完再定宽导致变高

    var i = 0;
    for (; i < rows.length; i++) {
      hb.appendChild(rows[i].cloneNode(true));
      if (outerHeight(head) > maxH) { hb.removeChild(hb.lastChild); break; }
    }
    if (i === 0) return null;                  // 单行就超页，无法按行切分
    if (i >= rows.length) return { head: head, tail: null }; // 未真正拆分

    // 确实发生了跨页拆分：打上片段标记，供 CSS 做「续表」样式
    head.classList.add('table-frag', 'table-frag-head');

    var tail = shell(table);
    tail.classList.add('table-frag', 'table-frag-tail');
    if (table.tHead) tail.appendChild(table.tHead.cloneNode(true)); // 续页重复表头
    var tb = document.createElement('tbody');
    tail.appendChild(tb);
    for (var j = i; j < rows.length; j++) tb.appendChild(rows[j].cloneNode(true));
    fixColumnWidths(tail, pcts);
    head.style.marginBottom = '0';
    tail.style.marginTop = '0';
    return { head: head, tail: tail };
  }

  /* ------------------------- 按文本切分 ------------------------- */

  function textNodes(root) {
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
    var out = [];
    var n;
    while ((n = walker.nextNode())) out.push(n);
    return out;
  }

  function locate(nodes, offset) {
    var acc = 0;
    for (var i = 0; i < nodes.length; i++) {
      var len = nodes[i].nodeValue.length;
      if (acc + len >= offset) {
        return { node: nodes[i], off: Math.max(0, Math.min(len, offset - acc)) };
      }
      acc += len;
    }
    var last = nodes[nodes.length - 1];
    return { node: last, off: last ? last.nodeValue.length : 0 };
  }

  // 用 Range 删除文本，保留行内标记（如 <span class="latin">）
  function deleteRange(root, start, end) {
    if (start >= end) return;
    var nodes = textNodes(root);
    if (!nodes.length) return;
    var s = locate(nodes, start);
    var e = locate(nodes, end);
    var r = document.createRange();
    r.setStart(s.node, s.off);
    r.setEnd(e.node, e.off);
    r.deleteContents();
  }

  function isWordChar(c) {
    return /[A-Za-z0-9._%+-]/.test(c);
  }

  // 含表格 / 图片等结构化内容的元素绝不能按文本切分：Range 删除会横跨单元格
  // 边界，把行切碎、丢掉 thead，产生残行与多余表框。
  function hasStructuredContent(el) {
    return !!(el.querySelector && el.querySelector('table,img,svg,canvas'));
  }

  function splitByText(el, maxH) {
    if (maxH < MIN_SPLIT) return null;
    if (hasStructuredContent(el)) return null;
    var full = el.textContent || '';
    var len = full.length;
    if (len < 2) return null;

    function buildHead(cut) {
      var h = el.cloneNode(true);
      deleteRange(h, cut, h.textContent.length);
      return h;
    }

    if (outerHeight(el.cloneNode(true)) <= maxH) return null;

    // 二分查找能放进 maxH 的最大字符数
    var lo = 1, hi = len - 1, best = 0;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (outerHeight(buildHead(mid)) <= maxH) { best = mid; lo = mid + 1; }
      else { hi = mid - 1; }
    }
    if (best <= 0) return null;

    // 尽量不从英文单词/数字中间断开
    var cut = best;
    var back = 0;
    while (cut > 1 && back < 18 && isWordChar(full[cut]) && isWordChar(full[cut - 1])) {
      cut--; back++;
    }
    if (cut <= 0) cut = best;

    var head = buildHead(cut);
    var tail = el.cloneNode(true);
    deleteRange(tail, 0, cut);
    if (!head.textContent.trim() || !tail.textContent.trim()) return null;
    // 续段不再悬挂缩进（悬挂缩进只属于首行）
    tail.style.textIndent = '0';
    return { head: head, tail: tail };
  }

  /* ------------------------- 按子节点切分（含阻塞节点下钻） ------------------------- */

  function splitByChildren(el, maxH, depth) {
    if (maxH < MIN_SPLIT || depth > MAX_DEPTH) return null;
    if (isAtomic(el)) return null;

    // 在克隆体上操作：切分失败时原始 DOM 毫发无损，杜绝内容丢失
    var work = el.cloneNode(true);

    host.appendChild(work);
    var kids = Array.prototype.slice.call(work.children);
    if (!kids.length) { host.removeChild(work); return null; }

    // 子元素全是行内元素（如 <p> 内的 <span class="latin">）→ 交给按文本切分
    var blockKids = kids.filter(isBlockDisplay);
    if (!blockKids.length) { host.removeChild(work); return null; }

    var cs = getComputedStyle(work);
    var chrome =
      (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0) +
      (parseFloat(cs.borderTopWidth) || 0) + (parseFloat(cs.borderBottomWidth) || 0);

    // fit: [{n, h}]，只累加块级子节点高度
    var fit = [];
    var used = chrome;
    var i = 0;
    for (; i < kids.length; i++) {
      var k = kids[i];
      if (blockKids.indexOf(k) === -1) { fit.push({ n: k, h: 0 }); continue; }
      var kh = inlineHeight(k);
      if (used + kh <= maxH) { used += kh; fit.push({ n: k, h: kh }); }
      else break;
    }
    host.removeChild(work);

    // 全部放得下
    if (i >= kids.length) return { head: work, tail: null };

    // ---- 关键：先尝试切分阻塞的那个子节点，用它的首段填满本页余量 ----
    var blocker = kids[i];
    var rest = maxH - used;
    var sub = null;
    var blockerIsBlock = blockKids.indexOf(blocker) !== -1;
    if (blockerIsBlock && rest >= MIN_BLOCK_SPLIT && !isAtomic(blocker) && depth < MAX_DEPTH) {
      var s = splitNode(blocker, rest, depth + 1);
      if (s && s.head) {
        var sh = outerHeight(s.head);
        if (sh > 0 && sh <= rest) sub = s;
      }
    }

    // ---- 孤行标题处理 ----
    // 若后面还有内容(sub)顶上，标题就不算孤行，应留在本页与其内容同页；
    // 只有当标题之后本页再无内容时，才把标题顺延到下一段，避免标题孤悬页尾。
    var orphans = [];
    if (!sub) {
      while (fit.length && isHeadingNode(fit[fit.length - 1].n)) {
        var orph = fit.pop();
        used -= orph.h;
        orphans.unshift(orph.n);
      }
    }

    // 既没有可放内容、也无法下钻 → 不可切分，交由调用方换页重试
    if (!fit.length && !sub) return null;

    // ① 本页片段：全新独立容器，样式与原容器一致
    var head = shell(work);
    fit.forEach(function (o) { head.appendChild(o.n); });
    if (sub) head.appendChild(sub.head);

    // ② 下页片段：另一个全新独立同类容器
    var tail = shell(work);
    orphans.forEach(function (o) { tail.appendChild(o); });
    if (sub) {
      if (sub.tail) tail.appendChild(sub.tail);   // 阻塞节点剩余部分
    } else {
      tail.appendChild(blocker);                  // 未切分，整块顺延到下一段
    }
    for (var j = i + 1; j < kids.length; j++) tail.appendChild(kids[j]);

    // 不返回空壳容器，否则会被单独排到一页上形成空白页
    if (!head.children.length) return null;
    if (!tail.children.length) tail = null;

    // 拆分片段不应保留「整块」语义的外边距：首页片段直通页底，无需底部留白；
    // 续页片段从新页顶部开始，无需顶部留白。否则 outerHeight 会把这段余量算进
    // 片段高度，量出的值超出可用空间而被误判为「不可切分」。
    head.style.marginBottom = '0';
    if (tail) tail.style.marginTop = '0';

    // 数据无损校验：不一致就放弃切分（此时原始 el 未被改动）
    if (!verifySplit(el, head, tail)) return null;

    return { head: head, tail: tail };
  }

  /* ------------------------- 切分入口 ------------------------- */

  function splitNode(el, maxH, depth) {
    if (!el || el.nodeType !== 1) return null;
    if (isAtomic(el)) return null;
    if (depth > MAX_DEPTH) return null;

    var res;
    if (el.tagName === 'TABLE') {
      // 表格只按行切分；按行切不开就整块换页，避免切出残缺结构
      res = splitTable(el, maxH);
    } else {
      res = splitByChildren(el, maxH, depth || 0);
      // 只有纯文本块才允许按文本切分；含表格/图片时宁可整块换页，也不能切碎结构
      if ((!res || !res.head) && !hasStructuredContent(el)) res = splitByText(el, maxH);
    }

    if (!res || !res.head) return null;

    // 统一安全校验：任何切分都不得丢失内容
    if (!verifySplit(el, res.head, res.tail)) return null;

    return res;
  }

  /* ------------------------- 物理页装配 ------------------------- */

  function newPage() {
    var p = document.createElement('section');
    p.className = 'pdf-page';
    return { el: p, used: 0, empty: true };
  }

  function place(page, node, h) {
    page.el.appendChild(node);
    page.used += h;
    page.empty = false;
  }

  /* ------------------------- 真实渲染自校正 ------------------------- */

  // 页内内容的真实底部（含外边距折叠后的实际占位）
  function contentBottom(page) {
    var bottom = page.getBoundingClientRect().top;
    var kids = page.children;
    for (var i = 0; i < kids.length; i++) {
      var r = kids[i].getBoundingClientRect();
      if (r.bottom > bottom) bottom = r.bottom;
    }
    return bottom;
  }

  function nextPageOf(page) {
    var n = page.nextElementSibling;
    while (n && !n.classList.contains('pdf-page')) n = n.nextElementSibling;
    if (n) return n;
    var np = document.createElement('section');
    np.className = 'pdf-page';
    if (page.parentNode) page.parentNode.insertBefore(np, page.nextSibling);
    else document.body.appendChild(np);
    return np;
  }

  /* 离屏测量与真实页内渲染之间可能存在细微差异（外边距折叠、BFC 生效范围、
     子像素舍入等）。本阶段以真实渲染高度为准做收尾校正：
     任何溢出页底的容器，按真实余量再切一次，剩余部分顺延到下一页。 */
  function enforceBounds(maxPass) {
    for (var pass = 0; pass < (maxPass || 6); pass++) {
      var pages = Array.prototype.slice.call(document.body.querySelectorAll('.pdf-page'));
      var changed = false;
      for (var i = 0; i < pages.length; i++) {
        var p = pages[i];
        var guard = 0;
        while (p.children.length && guard++ < 200) {
          var pr = p.getBoundingClientRect();
          var over = contentBottom(p) - pr.bottom;
          if (over <= 0.5) break;
          changed = true;

          var last = p.children[p.children.length - 1];
          var allowed = pr.bottom - last.getBoundingClientRect().top;
          var moved = false;

          if (allowed >= MIN_SPLIT && !isAtomic(last)) {
            var sp = splitNode(last, allowed, 0);
            if (sp && sp.head) {
              p.replaceChild(sp.head, last);
              if (sp.head.getBoundingClientRect().bottom - pr.bottom <= 0.5) {
                if (sp.tail) {
                  var np1 = nextPageOf(p);
                  np1.insertBefore(sp.tail, np1.firstChild);
                }
                moved = true;
              } else {
                p.replaceChild(last, sp.head); // 切完仍溢出 → 回滚，整块顺延
              }
            }
          }

          if (!moved) {
            // 该节点无法再切分。若它是本页唯一内容，就地保留并标记溢出，
            // 绝不再向前推——否则会触发逐页连锁迁移，把内容全堆到最后一页。
            if (p.children.length === 1) {
              if (last.getBoundingClientRect().height > pr.height + 0.5) {
                p.classList.add('pdf-page-overflow');
              }
              break;
            }
            p.removeChild(last);
            var np2 = nextPageOf(p);
            np2.insertBefore(last, np2.firstChild);
          }
        }
      }
      if (!changed) break;
    }
  }

  /* 兜底清理：移除 0 单元格的残缺行，以及没有任何数据行的表格碎片。
     正常流程不会产生这类结构，此处仅作安全网，确保绝不出现空白表框。 */
  function pruneEmptyTables() {
    var tables = document.body.querySelectorAll('.pdf-page table');
    for (var i = 0; i < tables.length; i++) {
      var t = tables[i];
      var tb = t.tBodies && t.tBodies[0];
      if (!tb) continue;
      for (var r = tb.rows.length - 1; r >= 0; r--) {
        if (tb.rows[r].cells.length === 0) tb.deleteRow(r);
      }
      if (tb.rows.length === 0 && t.parentNode) t.parentNode.removeChild(t);
    }
  }

  function paginateReport() {
    var stats = { pages: 0, containers: 0, splits: 0, overflow: 0 };

    if (document.querySelector('.pdf-page')) return stats; // 已分页，幂等

    // 先在真实文档流中记录表格自然列宽，供拆分后固定列宽使用
    recordColumnWidths();

    var body = document.body;
    var units = Array.prototype.slice.call(body.children).filter(function (n) {
      return n !== host && (n.classList.contains('cover-page') || n.classList.contains('report-section'));
    });
    if (!units.length) {
      units = Array.prototype.slice.call(body.children).filter(function (n) {
        return n !== host && n.nodeType === 1 && n.tagName !== 'SCRIPT';
      });
    }

    var pages = [];
    var page = newPage();
    pages.push(page);

    function flush() {
      page = newPage();
      pages.push(page);
    }

    function remain() { return PAGE_H - page.used; }

    units.forEach(function (unit) {
      // 封面整页独占（内含绝对定位背景图，不参与切分）
      if (unit.classList.contains('cover-page')) {
        if (!page.empty) flush();
        var cover = document.createElement('section');
        cover.className = 'pdf-page pdf-page-cover';
        cover.appendChild(unit);
        page.el = cover;
        page.empty = false;
        flush();
        return;
      }

      // 大章节默认从新的一页开始，章节内部连续流式排布并按需切分
      if (CHAPTER_BREAK && !page.empty) flush();

      var queue = Array.prototype.slice.call(unit.children);
      unit.innerHTML = '';
      var guard = 0;

      while (queue.length) {
        if (guard++ > 50000) break; // 死循环保护
        var node = queue.shift();
        if (!node || node.nodeType !== 1) continue;

        var h = outerHeight(node);

        // ① 当页放得下 → 直接放入
        if (h <= remain()) { place(page, node, h); continue; }

        // ② 放不下当页但能放下一整页
        if (h <= PAGE_H) {
          if (!page.empty) {
            if (remain() >= MIN_FILL) {
              var sp = splitNode(node, remain() - SPLIT_EPS, 0);
              if (sp && sp.head) {
                var hh = outerHeight(sp.head);
                if (hh > 0 && hh <= remain()) {
                  place(page, sp.head, hh);
                  stats.splits++;
                  if (sp.tail) queue.unshift(sp.tail);
                  flush();
                  continue;
                }
              }
            }
            flush();          // 余量太小，整块移到新页
            queue.unshift(node);
            continue;
          }
          place(page, node, h);
          continue;
        }

        // ③ 比一整页还高：必须切分
        if (!page.empty && remain() < MIN_FILL) flush();

        var lim = remain();
        var sp2 = splitNode(node, Math.min(lim, PAGE_H) - SPLIT_EPS, 0);
        if (sp2 && sp2.head) {
          var h2 = outerHeight(sp2.head);
          if (h2 > 0 && h2 <= lim) {
            place(page, sp2.head, h2);
            stats.splits++;
            if (sp2.tail) queue.unshift(sp2.tail);
            flush();
            continue;
          }
        }

        // ④ 无法切分的原子元素：单独占一页
        if (!page.empty) flush();
        if (h > PAGE_H) {
          stats.overflow++;
          page.el.className = 'pdf-page pdf-page-overflow';
        }
        place(page, node, h);
        flush();
      }
    });

    // 组装输出：每个 .pdf-page 内部都是完整闭合的容器
    var frag = document.createDocumentFragment();
    pages.forEach(function (p) {
      if (p.empty) return;
      frag.appendChild(p.el);
    });

    body.innerHTML = '';
    body.appendChild(host);   // 保留测量台，供真实渲染校正阶段使用
    body.appendChild(frag);

    // 以真实渲染高度收尾校正，确保没有任何容器溢出页底
    enforceBounds(6);

    // 兜底清理：删除没有数据行的表格碎片，避免出现空白表框
    pruneEmptyTables();

    // 清理测量台
    if (host.parentNode) host.parentNode.removeChild(host);

    var finalPages = body.querySelectorAll('.pdf-page');
    stats.pages = finalPages.length;
    stats.containers = 0;
    for (var fi = 0; fi < finalPages.length; fi++) {
      stats.containers += finalPages[fi].children.length;
    }

    window.__paginateStats = stats;
    return stats;
  }

  window.paginateReport = paginateReport;
})();
