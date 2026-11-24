/* interview-atlas 前端：原生 JS，无构建工具，所有原文都作为文本渲染（转义后插入）。 */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[c]));
const SPEAKER = { interviewee: '受访者', interviewer: '访谈者', unknown: '未标注' };

const S = {
  stats: {}, stages: [], docs: [], people: [], codes: [], exports: [],
  transcribe: {}, paths: {},
  pendingFiles: [], currentDocId: null, codingSegs: [], selected: new Set(),
  editingPerson: null, searchCodes: new Set(),
};

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  let j;
  try { j = await res.json(); } catch (e) { throw new Error('服务器返回不是 JSON：' + res.status); }
  if (!res.ok || !j.ok) throw new Error(j.error || ('HTTP ' + res.status));
  return j.data;
}
const get = (p) => api(p);
const post = (p, d) => api(p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(d || {}) });
const patch = (p, d) => api(p, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(d || {}) });
const del = (p, d) => api(p, { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(d || {}) });
const upload = (p, fd) => api(p, { method: 'POST', body: fd });

let toastTimer;
function toast(msg, isErr = false) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast show' + (isErr ? ' err' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = 'toast'; }, 3200);
}
function guard(fn) {
  return async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message, true); } };
}
function result(sel, msg, good = true) {
  const el = $(sel);
  el.textContent = msg;
  el.className = 'result-box ' + (good ? 'ok' : 'err');
}
function fmtSize(n) { return n > 1024 * 1024 ? (n / 1024 / 1024).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB'; }
function opt(value, label, selected) {
  return `<option value="${esc(value)}" ${String(selected) === String(value) ? 'selected' : ''}>${esc(label)}</option>`;
}
function stageOptions(withAll = false, current = '') {
  return (withAll ? opt('', '全部阶段', current) : '') +
    S.stages.map(x => opt(x, x, current)).join('');
}

/* ---------------- 数据刷新 ---------------- */

async function refreshCore() {
  const [state, docs, people, codes] = await Promise.all([
    get('/api/state'), get('/api/documents'), get('/api/interviewees'), get('/api/codes')
  ]);
  S.stats = state.stats; S.stages = state.stages;
  S.transcribe = state.transcribe; S.paths = state.paths;
  S.docs = docs; S.people = people; S.codes = codes;
  renderStrip(); renderStageSelects(); renderPersonSelects();
  renderCodingDocSelect(); renderSearchCodes(); renderTranscribeStatus(); renderPaths();
  renderOverview();
}

function renderStrip() {
  const s = S.stats;
  $('#stat-strip').innerHTML = [
    ['受访者', s.interviewees], ['材料', s.documents], ['段落', s.segments],
    ['码号', s.codes], ['已编码段落', s.coded_segments]
  ].map(([k, v]) => `<span>${k} <b>${v ?? 0}</b></span>`).join('');
}

function renderStageSelects() {
  ['#import-stage', '#folder-stage', '#p-stage'].forEach(sel => {
    const el = $(sel); if (!el) return;
    const cur = el.value;
    el.innerHTML = opt('', '（不指定）') + S.stages.map(x => opt(x, x, cur)).join('');
  });
  const ss = $('#search-stage');
  if (ss) { const cur = ss.value; ss.innerHTML = stageOptions(true, cur); }
}

function renderPersonSelects() {
  const el = $('#search-person'); if (!el) return;
  const cur = el.value;
  el.innerHTML = opt('', '全部受访者', cur) +
    S.people.map(p => opt(p.id, `${p.code} ${p.alias || p.role || ''}`.trim(), cur)).join('');
}

function renderCodingDocSelect() {
  const el = $('#coding-doc'); if (!el) return;
  const cur = el.value || (S.currentDocId ? String(S.currentDocId) : '');
  el.innerHTML = S.docs.length
    ? S.docs.map(d => opt(d.id, `${d.code ? '[' + d.code + '] ' : ''}${d.title}`, cur)).join('')
    : '<option value="">（尚未导入材料）</option>';
}

function renderSearchCodes() {
  const el = $('#search-codes'); if (!el) return;
  el.innerHTML = S.codes.length
    ? S.codes.map(c => `<span class="chip ${S.searchCodes.has(c.id) ? 'on' : ''}" data-code="${c.id}"
        style="${S.searchCodes.has(c.id) ? 'background:' + esc(c.color) : ''}">
        <i class="dot" style="background:${esc(c.color)}"></i>${esc(c.name)} · ${c.usage}</span>`).join('')
    : '<span class="muted small">还没有码号，请先在「编码」页添加。</span>';
}

/* ---------------- 概览 ---------------- */

function renderOverview() {
  const s = S.stats;
  const cards = [
    ['受访者', s.interviewees], ['材料', s.documents], ['段落', s.segments],
    ['码号', s.codes], ['已编码段落', s.coded_segments], ['未编码段落', s.uncoded_segments]
  ];
  $('#overview-stats').innerHTML = cards.map(([k, v], i) =>
    `<div class="stat-card ${i === 5 && v > 0 ? 'warn' : ''}"><div class="num">${v ?? 0}</div><div class="label">${k}</div></div>`
  ).join('');
  $('#overview-docs').innerHTML = S.docs.length
    ? S.docs.slice(-6).reverse().map(d =>
      `<div><span>${esc(d.code || '未归类')} · ${esc(d.title)}</span><span>${esc(d.interview_date || '日期未注明')} · ${d.segments} 段</span></div>`).join('')
    : '<div class="empty">还没有材料。去「材料」页导入第一份访谈稿吧。</div>';
}

/* ---------------- 材料 ---------------- */

function renderDocs() {
  const el = $('#docs-table');
  if (!S.docs.length) { el.innerHTML = '<div class="empty">暂无材料。</div>'; return; }
  el.innerHTML = `<table><thead><tr>
      <th>编号</th><th>材料标题</th><th>受访者</th><th>日期</th><th>阶段</th><th>轮次</th>
      <th class="num">段落</th><th class="num">编码</th><th>操作</th></tr></thead><tbody>` +
    S.docs.map(d => `<tr>
      <td class="seg-label">${esc(d.code || '—')}</td>
      <td>${esc(d.title)}</td>
      <td>${esc([d.code, d.alias || d.role].filter(Boolean).join(' / ') || '未归类')}</td>
      <td>${esc(d.interview_date || '—')}</td><td>${esc(d.stage || '未分期')}</td>
      <td class="num">${d.wave}</td><td class="num">${d.segments}</td><td class="num">${d.codings}</td>
      <td><div class="cell-actions">
        <button class="btn ghost small" data-act="view" data-id="${d.id}">查看段落</button>
        <button class="btn ghost small" data-act="assign" data-id="${d.id}">归属</button>
        <button class="btn ghost small" data-act="del" data-id="${d.id}">删除</button>
      </div></td></tr>`).join('') + '</tbody></table>';
}

async function loadDocDetail(id) {
  const d = await get('/api/documents/' + id);
  S.currentDocId = id;
  $('#doc-detail-card').classList.remove('hidden');
  $('#doc-detail-title').textContent = `段落浏览 · ${d.document.title}（${d.segments.length} 段）`;
  $('#doc-segments').innerHTML = d.segments.length ? d.segments.map(s => `
    <div class="segment" data-id="${s.id}">
      <div class="seg-head">
        <span class="seg-label">${esc(s.label)}</span>
        <span class="seg-speaker">${SPEAKER[s.speaker] || esc(s.speaker)}</span>
        ${s.timecode ? `<span class="seg-time">${esc(s.timecode)}</span>` : ''}
        <select class="mini-select seg-sp">
          ${Object.entries(SPEAKER).map(([k, v]) => opt(k, v, s.speaker)).join('')}
        </select>
        <button class="btn ghost small" data-act="save-seg">保存</button>
        <button class="btn ghost small" data-act="del-seg">删除</button>
      </div>
      <div class="seg-text" title="双击可编辑">${esc(s.text)}</div>
      <div class="seg-meta">已有码号：${s.codes.length
        ? s.codes.map(c => `<span class="chip static"><i class="dot" style="background:${esc(c.color)}"></i>${esc(c.name)}</span>`).join(' ')
        : '无'}</div>
    </div>`).join('') : '<div class="empty">这份材料没有段落。</div>';
  $('#doc-detail-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ---------------- 受访者 / 脱敏 ---------------- */

function renderPeople() {
  const el = $('#people-table');
  if (!S.people.length) { el.innerHTML = '<div class="empty">暂无受访者。导入带编号的材料会自动创建，也可在上方手动新增。</div>'; return; }
  el.innerHTML = `<table><thead><tr>
      <th>编号</th><th>化名</th><th>身份/岗位</th><th>单位</th><th>阶段</th><th>日期</th>
      <th class="num">轮次</th><th class="num">材料</th><th class="num">段落</th><th>操作</th>
    </tr></thead><tbody>` + S.people.map(p => `<tr>
      <td class="seg-label">${esc(p.code)}</td><td>${esc(p.alias || '—')}</td><td>${esc(p.role || '—')}</td>
      <td>${esc(p.org || '—')}</td><td>${esc(p.stage || '—')}</td><td>${esc(p.interview_date || '—')}</td>
      <td class="num">${p.wave}</td><td class="num">${p.documents}</td><td class="num">${p.segments}</td>
      <td><div class="cell-actions">
        <button class="btn ghost small" data-act="edit-person" data-id="${p.id}">编辑</button>
        <button class="btn ghost small" data-act="del-person" data-id="${p.id}">删除</button>
      </div></td></tr>`).join('') + '</tbody></table>';
}

function resetPersonForm() {
  S.editingPerson = null;
  ['#p-code', '#p-alias', '#p-role', '#p-org', '#p-date', '#p-note'].forEach(s => { $(s).value = ''; });
  $('#p-wave').value = '1'; $('#p-stage').value = '';
  $('#person-form-title').textContent = '新增受访者';
  $('#person-edit-hint').textContent = '';
}

async function renderAliases() {
  const items = await get('/api/aliases');
  $('#alias-table').innerHTML = items.length
    ? `<table><thead><tr><th>原文写法</th><th>替换为</th><th>操作</th></tr></thead><tbody>` +
      items.map(a => `<tr><td>${esc(a.real_name)}</td><td>${esc(a.replacement)}</td>
        <td><button class="btn ghost small" data-act="del-alias" data-id="${a.id}">删除</button></td></tr>`).join('') +
      '</tbody></table>'
    : '<div class="empty">还没有登记脱敏映射。</div>';
}

/* ---------------- 编码 ---------------- */

async function loadCodingSegments() {
  const id = $('#coding-doc').value;
  if (!id) { $('#coding-segments').innerHTML = '<div class="empty">请先导入材料。</div>'; return; }
  const d = await get('/api/documents/' + id);
  const q = $('#coding-q').value.trim().toLowerCase();
  const onlyUncoded = $('#coding-filter').value === 'uncoded';
  S.codingSegs = d.segments.filter(s =>
    (!onlyUncoded || s.codes.length === 0) && (!q || s.text.toLowerCase().includes(q)));
  S.selected.clear();
  renderCodingSegments();
}

function renderCodingSegments() {
  const el = $('#coding-segments');
  el.innerHTML = S.codingSegs.length ? S.codingSegs.map(s => `
    <div class="segment ${S.selected.has(s.id) ? 'selected' : ''}" data-id="${s.id}">
      <div class="seg-head">
        <input type="checkbox" class="seg-check" ${S.selected.has(s.id) ? 'checked' : ''}>
        <span class="seg-label">${esc(s.label)}</span>
        <span class="seg-speaker">${SPEAKER[s.speaker] || esc(s.speaker)}</span>
        ${s.timecode ? `<span class="seg-time">${esc(s.timecode)}</span>` : ''}
      </div>
      <div class="seg-text">${esc(s.text)}</div>
      <div class="seg-meta">${s.codes.length
        ? s.codes.map(c => `<span class="chip static"><i class="dot" style="background:${esc(c.color)}"></i>${esc(c.name)}</span>`).join(' ')
        : '<span class="muted">未编码</span>'}</div>
    </div>`).join('') : '<div class="empty">没有符合条件的段落。</div>';
  renderCodingCurrent();
}

function renderCodingCurrent() {
  const ids = [...S.selected];
  $('#coding-selection').textContent = `已选 ${ids.length} 段`;
  const el = $('#coding-current');
  if (!ids.length) { el.className = 'muted'; el.textContent = '尚未选择段落。'; return; }
  const first = S.codingSegs.find(s => s.id === ids[0]);
  el.className = '';
  el.innerHTML = `<p class="small">已选 <b>${ids.length}</b> 段；点右侧码号即可批量打码。</p>
    <div class="seg-label">${esc(first ? first.label : '')}</div>
    <div class="small" style="margin-top:6px">${esc(first ? first.text.slice(0, 160) : '')}${(first && first.text.length > 160) ? '…' : ''}</div>`;
}

function renderCodes() {
  const el = $('#code-list');
  el.innerHTML = S.codes.length ? S.codes.map(c => `
    <div class="code-item" data-code="${c.id}">
      <i class="swatch" style="background:${esc(c.color)}"></i>
      <span class="cname">${esc(c.name)}<span class="cdef">${esc(c.definition || '未写定义')}</span></span>
      <span class="cuse">${c.usage} 段</span>
      <span class="cdel" data-act="del-code" data-id="${c.id}" title="删除码号">✕</span>
    </div>`).join('') : '<div class="empty">码本为空。添加一个码号开始编码。</div>';
}

async function applyCode(codeId) {
  const ids = [...S.selected];
  if (!ids.length) { toast('先在左侧选中段落', true); return; }
  const segs = S.codingSegs.filter(s => ids.includes(s.id));
  const allHave = segs.every(s => s.codes.some(c => c.id === codeId));
  for (const s of segs) {
    const has = s.codes.some(c => c.id === codeId);
    if (allHave) await del('/api/codings', { segment_id: s.id, code_id: codeId });
    else if (!has) await post('/api/codings', { segment_id: s.id, code_id: codeId });
  }
  toast(allHave ? `已取消 ${segs.length} 段的该码号` : `已给 ${segs.length} 段打码`);
  await refreshCore();
  await loadCodingSegments();
}

/* ---------------- 检索 / 矩阵 ---------------- */

function renderSearchResults(rows) {
  const el = $('#search-list');
  if (!rows.length) { el.innerHTML = '<div class="empty">没有命中的段落。</div>'; return; }
  el.innerHTML = rows.map(s => `
    <div class="segment">
      <div class="seg-head">
        <span class="seg-label">${esc(s.label)}</span>
        <span class="seg-speaker">${SPEAKER[s.speaker] || esc(s.speaker)}</span>
        <span class="seg-meta">${esc([s.code, s.alias, s.document_title, s.interview_date, s.stage]
          .filter(Boolean).join(' · '))}</span>
        ${s.timecode ? `<span class="seg-time">${esc(s.timecode)}</span>` : ''}
      </div>
      <div class="seg-text">${esc(s.text)}</div>
      <div class="seg-meta">${s.codes.length
        ? s.codes.map(c => `<span class="chip static"><i class="dot" style="background:${esc(c.color)}"></i>${esc(c.name)}</span>`).join(' ')
        : '<span class="muted">未编码</span>'}</div>
    </div>`).join('');
}

async function loadMatrix() {
  const rows = $('#matrix-rows').value;
  const m = await get('/api/matrix?rows=' + encodeURIComponent(rows));
  const el = $('#matrix-table');
  if (!m.rows.length) { el.innerHTML = '<div class="empty">还没有编码记录，矩阵为空。</div>'; return; }
  el.innerHTML = `<table><thead><tr><th>${esc(m.row_label)}</th>` +
    m.codes.map(c => `<th class="num">${esc(c.name)}</th>`).join('') +
    '<th class="num">合计</th></tr></thead><tbody>' +
    m.rows.map(r => `<tr><td><b>${esc(r)}</b></td>` +
      m.codes.map(c => `<td class="num">${m.cells[r][c.id] || '·'}</td>`).join('') +
      `<td class="num"><b>${m.totals[r]}</b></td></tr>`).join('') + '</tbody></table>';
}

async function loadFrequency() {
  const group = $('#freq-group').value;
  const rows = await get('/api/frequency?group=' + encodeURIComponent(group));
  const el = $('#freq-table');
  if (!rows.length) { el.innerHTML = '<div class="empty">还没有编码记录。</div>'; return; }
  const max = Math.max(...rows.map(r => r.n), 1);
  el.innerHTML = `<table><thead><tr><th>分组</th><th>码号</th><th class="num">段落数</th><th>分布</th></tr></thead><tbody>` +
    rows.map(r => `<tr><td>${esc(r.grp)}</td><td>${esc(r.code)}</td><td class="num">${r.n}</td>
      <td><div style="background:var(--primary);height:8px;border-radius:4px;width:${Math.max(2, Math.round(r.n / max * 100))}%"></div></td></tr>`).join('') +
    '</tbody></table>';
}

/* ---------------- 导出 ---------------- */

async function refreshExports() {
  S.exports = await get('/api/exports');
  const el = $('#export-list');
  el.innerHTML = S.exports.length
    ? `<table><thead><tr><th>文件名</th><th>大小</th><th>生成时间</th><th>操作</th></tr></thead><tbody>` +
      S.exports.map(f => `<tr><td>${esc(f.name)}</td><td>${fmtSize(f.size)}</td><td>${esc(f.mtime)}</td>
        <td><a class="btn small" href="/api/download/${encodeURIComponent(f.name)}" download>下载</a></td></tr>`).join('') +
      '</tbody></table>'
    : '<div class="empty">还没有导出文件。</div>';
}

/* ---------------- 设置 ---------------- */

function renderTranscribeStatus() {
  const t = S.transcribe, el = $('#transcribe-status');
  if (!el) return;
  el.className = 'status-box ' + (t.available ? 'on' : 'off');
  el.innerHTML = t.available
    ? `<b>已就绪</b> · ${esc(t.engine)}<br><span class="small">${esc(t.note)}</span>`
    : `<b>未安装</b> · 可选功能，未安装不影响其他功能。<br>
       在项目目录执行 <code>${esc(t.hint || '')}</code> 后可启用。<br>
       <span class="small">替代方案：用外部转写工具导出 .srt/.vtt/.json 后直接在「材料」页导入。</span>`;
  $('#btn-transcribe').disabled = !t.available;
}

function renderPaths() {
  const p = S.paths, el = $('#settings-paths');
  if (!el || !p.data) return;
  el.innerHTML = `<div><span>数据目录</span><code>${esc(p.data)}</code></div>
    <div><span>导出目录</span><code>${esc(p.export)}</code></div>
    <div><span>示例材料</span><code>${esc(p.examples)}</code></div>
    <div><span>版本</span><code>0.1.0</code></div>`;
}

/* ---------------- Tab 切换 ---------------- */

function switchTab(name) {
  $$('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  $$('.panel').forEach(p => p.classList.toggle('active', p.id === 'panel-' + name));
  location.hash = name;
  const tasks = {
    materials: () => { renderDocs(); },
    people: () => { renderPeople(); renderAliases(); },
    coding: () => { renderCodes(); if (S.currentDocId) $('#coding-doc').value = String(S.currentDocId); loadCodingSegments(); },
    analysis: () => { renderSearchCodes(); loadMatrix(); loadFrequency(); },
    export: () => { refreshExports(); },
    settings: () => { $('#settings-stages').value = S.stages.join('\n'); renderTranscribeStatus(); renderPaths(); },
  };
  if (tasks[name]) guard(tasks[name])();
}

/* ---------------- 事件 ---------------- */

function bind() {
  $$('.nav-item').forEach(b => b.addEventListener('click', () => switchTab(b.dataset.tab)));

  // 材料：拖拽与选择
  const dz = $('#dropzone');
  ['dragenter', 'dragover'].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add('dragover'); }));
  ['dragleave', 'drop'].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove('dragover'); }));
  dz.addEventListener('drop', e => {
    S.pendingFiles = [...e.dataTransfer.files];
    dz.querySelector('b').textContent = `已选择 ${S.pendingFiles.length} 个文件`;
  });
  $('#file-input').addEventListener('change', e => {
    S.pendingFiles = [...e.target.files];
    if (S.pendingFiles.length) dz.querySelector('b').textContent = `已选择 ${S.pendingFiles.length} 个文件`;
  });
  $('#btn-import').addEventListener('click', guard(async () => {
    if (!S.pendingFiles.length) { toast('先选择或拖入文件', true); return; }
    const fd = new FormData();
    S.pendingFiles.forEach(f => fd.append('files', f));
    fd.append('stage', $('#import-stage').value);
    fd.append('code', $('#import-code').value.trim());
    const r = await upload('/api/import', fd);
    let msg = `成功导入 ${r.imported.length} 份，共 ${r.imported.reduce((n, x) => n + x.segments, 0)} 段。`;
    const warns = r.imported.flatMap(x => (x.warnings || []).map(w => `${x.title}：${w}`));
    if (warns.length) msg += '\n提示：' + warns.join('；');
    if (r.errors.length) msg += '\n失败：' + r.errors.map(e => `${e.file}（${e.error}）`).join('；');
    result('#import-result', msg, r.errors.length === 0);
    S.pendingFiles = []; $('#file-input').value = '';
    dz.querySelector('b').textContent = '把访谈稿拖到这里';
    await refreshCore(); renderDocs();
    toast('导入完成');
  }));
  $('#btn-folder').addEventListener('click', guard(async () => {
    const path = $('#folder-path').value.trim();
    if (!path) { toast('填写文件夹路径', true); return; }
    const r = await post('/api/import/folder', { path, stage: $('#folder-stage').value });
    let msg = `扫描 ${r.total} 份，成功 ${r.imported.length} 份。`;
    if (r.errors.length) msg += '\n失败：' + r.errors.map(e => `${e.file}（${e.error}）`).join('；');
    result('#import-result', msg, r.errors.length === 0);
    await refreshCore(); renderDocs();
  }));
  $('#btn-refresh-docs').addEventListener('click', guard(async () => { await refreshCore(); renderDocs(); toast('已刷新'); }));

  $('#docs-table').addEventListener('click', guard(async e => {
    const btn = e.target.closest('button[data-act]'); if (!btn) return;
    const id = Number(btn.dataset.id), d = S.docs.find(x => x.id === id);
    if (btn.dataset.act === 'view') await loadDocDetail(id);
    if (btn.dataset.act === 'del') {
      if (!confirm(`删除材料「${d.title}」？该材料的段落与编码记录会一起删除（原始副本仍保留在 data/sources/）。`)) return;
      await del('/api/documents/' + id);
      $('#doc-detail-card').classList.add('hidden'); S.currentDocId = null;
      await refreshCore(); renderDocs(); toast('已删除');
    }
    if (btn.dataset.act === 'assign') {
      const code = prompt('归入哪个受访者编号？（如 P01；留空＝取消归类）', d.code || '');
      if (code === null) return;
      let iid = null;
      if (code.trim()) {
        const p = S.people.find(x => x.code === code.trim());
        if (p) iid = p.id;
        else { const r = await post('/api/interviewees', { code: code.trim() }); iid = r.id; }
      }
      await post(`/api/documents/${id}/assign`, { interviewee_id: iid });
      await refreshCore(); renderDocs(); toast('归属已更新，段落编号已重编');
    }
  }));

  $('#btn-close-detail').addEventListener('click', () => $('#doc-detail-card').classList.add('hidden'));
  $('#btn-bulk-speaker').addEventListener('click', guard(async () => {
    if (!S.currentDocId) return;
    const r = await post(`/api/documents/${S.currentDocId}/speaker`, { speaker: $('#bulk-speaker').value });
    await loadDocDetail(S.currentDocId); toast(`已更新 ${r.updated} 段`);
  }));
  $('#doc-segments').addEventListener('dblclick', e => {
    const t = e.target.closest('.seg-text');
    if (t) { t.contentEditable = 'true'; t.focus(); }
  });
  $('#doc-segments').addEventListener('click', guard(async e => {
    const btn = e.target.closest('button[data-act]'); if (!btn) return;
    const box = btn.closest('.segment'), id = Number(box.dataset.id);
    if (btn.dataset.act === 'save-seg') {
      const text = box.querySelector('.seg-text').textContent;
      const speaker = box.querySelector('.seg-sp').value;
      await patch('/api/segments/' + id, { text, speaker });
      await loadDocDetail(S.currentDocId); toast('段落已保存');
    }
    if (btn.dataset.act === 'del-seg') {
      if (!confirm('删除这个段落？它的编码记录也会一起删除。')) return;
      await del('/api/segments/' + id);
      await loadDocDetail(S.currentDocId); await refreshCore(); toast('已删除');
    }
  }));

  // 受访者
  $('#btn-person-save').addEventListener('click', guard(async () => {
    const d = {
      code: $('#p-code').value.trim(), alias: $('#p-alias').value.trim(),
      role: $('#p-role').value.trim(), org: $('#p-org').value.trim(),
      stage: $('#p-stage').value, interview_date: $('#p-date').value.trim(),
      wave: Number($('#p-wave').value || 1), note: $('#p-note').value.trim(),
    };
    if (S.editingPerson) await patch('/api/interviewees/' + S.editingPerson, d);
    else await post('/api/interviewees', d);
    resetPersonForm(); await refreshCore(); renderPeople(); toast('已保存');
  }));
  $('#btn-person-reset').addEventListener('click', resetPersonForm);
  $('#people-table').addEventListener('click', guard(async e => {
    const btn = e.target.closest('button[data-act]'); if (!btn) return;
    const id = Number(btn.dataset.id), p = S.people.find(x => x.id === id);
    if (btn.dataset.act === 'edit-person') {
      S.editingPerson = id;
      $('#p-code').value = p.code; $('#p-alias').value = p.alias || '';
      $('#p-role').value = p.role || ''; $('#p-org').value = p.org || '';
      $('#p-stage').value = p.stage || ''; $('#p-date').value = p.interview_date || '';
      $('#p-wave').value = p.wave; $('#p-note').value = p.note || '';
      $('#person-form-title').textContent = '编辑受访者 · ' + p.code;
      $('#person-edit-hint').textContent = '改编号会同步刷新段落引用号。';
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
    if (btn.dataset.act === 'del-person') {
      if (!confirm(`删除受访者 ${p.code}？其材料会变成「未归类」（材料本身不删）。`)) return;
      await del('/api/interviewees/' + id);
      await refreshCore(); renderPeople(); toast('已删除');
    }
  }));

  // 脱敏
  $('#btn-alias-add').addEventListener('click', guard(async () => {
    await post('/api/aliases', { real_name: $('#alias-real').value, replacement: $('#alias-repl').value });
    $('#alias-real').value = ''; $('#alias-repl').value = '';
    await renderAliases(); toast('映射已登记');
  }));
  $('#btn-alias-apply').addEventListener('click', guard(async () => {
    if (!confirm('将把映射应用到全部原文与材料标题，且不可撤销。建议先导出整包备份。确定继续？')) return;
    const r = await post('/api/aliases/apply', {});
    result('#alias-result', `替换 ${r.replacements} 处，涉及 ${r.segments_changed} 个段落、${r.documents_changed} 个标题。`);
    await refreshCore(); toast('脱敏完成');
  }));
  $('#alias-table').addEventListener('click', guard(async e => {
    const b = e.target.closest('button[data-act="del-alias"]'); if (!b) return;
    await del('/api/aliases/' + b.dataset.id); await renderAliases();
  }));

  // 编码
  $('#btn-coding-load').addEventListener('click', guard(loadCodingSegments));
  $('#coding-q').addEventListener('keydown', e => { if (e.key === 'Enter') guard(loadCodingSegments)(); });
  $('#coding-filter').addEventListener('change', guard(loadCodingSegments));
  $('#btn-select-all').addEventListener('click', () => {
    S.codingSegs.forEach(s => S.selected.add(s.id)); renderCodingSegments();
  });
  $('#btn-select-none').addEventListener('click', () => { S.selected.clear(); renderCodingSegments(); });
  $('#coding-segments').addEventListener('click', e => {
    const box = e.target.closest('.segment'); if (!box) return;
    const id = Number(box.dataset.id);
    if (S.selected.has(id)) S.selected.delete(id); else S.selected.add(id);
    renderCodingSegments();
  });
  $('#btn-code-add').addEventListener('click', guard(async () => {
    const name = $('#code-name').value.trim(); if (!name) { toast('填写码号名称', true); return; }
    await post('/api/codes', { name, definition: $('#code-def').value.trim() });
    $('#code-name').value = ''; $('#code-def').value = '';
    await refreshCore(); renderCodes(); toast('码号已添加');
  }));
  $('#code-list').addEventListener('click', guard(async e => {
    const delBtn = e.target.closest('[data-act="del-code"]');
    if (delBtn) {
      e.stopPropagation();
      if (!confirm('删除这个码号？所有段落上该码号的编码记录也会删除。')) return;
      await del('/api/codes/' + delBtn.dataset.id);
      await refreshCore(); renderCodes(); await loadCodingSegments(); return;
    }
    const item = e.target.closest('.code-item'); if (!item) return;
    await applyCode(Number(item.dataset.code));
  }));

  // 检索
  $('#search-codes').addEventListener('click', e => {
    const chip = e.target.closest('.chip[data-code]'); if (!chip) return;
    const id = Number(chip.dataset.code);
    if (S.searchCodes.has(id)) S.searchCodes.delete(id); else S.searchCodes.add(id);
    renderSearchCodes();
  });
  $('#btn-search').addEventListener('click', guard(async () => {
    const p = new URLSearchParams({
      q: $('#search-q').value.trim(), stage: $('#search-stage').value,
      speaker: $('#search-speaker').value, codes: [...S.searchCodes].join(','),
    });
    const pid = $('#search-person').value;
    if (pid) p.set('interviewee_id', pid);
    const rows = await get('/api/search?' + p.toString());
    result('#search-result', `命中 ${rows.length} 段（最多返回 500 段）。`);
    renderSearchResults(rows);
  }));
  $('#search-q').addEventListener('keydown', e => { if (e.key === 'Enter') $('#btn-search').click(); });
  $('#matrix-rows').addEventListener('change', guard(loadMatrix));
  $('#freq-group').addEventListener('change', guard(loadFrequency));

  // 导出
  $$('[data-export]').forEach(b => b.addEventListener('click', guard(async () => {
    const p = new URLSearchParams({
      kind: b.dataset.export, fmt: b.dataset.fmt, rows: $('#matrix-rows').value,
    });
    const r = await get('/api/export?' + p.toString());
    result('#export-result', `已生成：${r.name}（${fmtSize(r.size)}）`);
    await refreshExports(); toast('导出完成');
  })));
  $('#btn-refresh-exports').addEventListener('click', guard(refreshExports));

  // 设置
  $('#btn-save-stages').addEventListener('click', guard(async () => {
    const r = await post('/api/settings', { stages: $('#settings-stages').value });
    S.stages = r.stages; renderStageSelects();
    result('#settings-result', '阶段已保存，共 ' + S.stages.length + ' 个。');
    toast('已保存');
  }));
  $('#btn-transcribe').addEventListener('click', guard(async () => {
    const f = $('#media-input').files[0];
    if (!f) { toast('先选择音视频文件', true); return; }
    const fd = new FormData();
    fd.append('file', f); fd.append('model_size', $('#model-size').value);
    fd.append('language', 'zh');
    toast('转写中，首次运行需下载模型，请耐心等待…');
    const r = await upload('/api/transcribe', fd);
    result('#settings-result', `转写完成：${r.segments} 段，已导入材料「${r.title}」。请到「材料」页标注说话人并核对错字。`);
    await refreshCore(); renderDocs(); toast('转写完成');
  }));
}

/* ---------------- 启动 ---------------- */

(async function boot() {
  bind();
  try {
    await refreshCore();
    renderDocs(); renderPeople(); renderCodes(); renderAliases();
    await refreshExports();
    const tab = (location.hash || '').replace('#', '');
    switchTab(['overview', 'materials', 'people', 'coding', 'analysis', 'export', 'settings'].includes(tab) ? tab : 'overview');
  } catch (e) {
    toast('初始化失败：' + e.message, true);
  }
})();
