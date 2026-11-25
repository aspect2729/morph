// Morph — PDF editor. State lives here; the engine renders pages, reads text (PDF or OCR) and builds the final file.
(() => {
const ed = $('#ed'), pagesEl = $('#pages'), canvas = $('#canvas'), insp = $('#insp');
const TOOLS = [
  ['select', 'cursor', 'Select & move', 'V'],
  ['edit', 'textedit', 'Edit text', 'E'],
  ['text', 'addtext', 'Add text', 'T'],
  '|',
  ['highlight', 'highlight', 'Highlight', 'H'],
  ['draw', 'draw', 'Draw', 'D'],
  ['shape', 'shape', 'Rectangle', 'B'],
  '|',
  ['whiteout', 'whiteout', 'Whiteout', 'W'],
  ['redact', 'redact', 'Redact', 'R'],
  '|',
  ['image', 'image', 'Image', 'I'],
  ['sign', 'sign', 'Sign', 'S'],
];
const INFO = {
  select: ['Select & move', 'Click anything you added to move it, drag a corner to resize, ⌫ to delete.'],
  edit: ['Edit text', 'Click any text to change it — including text inside scans and photos, which is read automatically with OCR.'],
  text: ['Add text', 'Click anywhere on a page to start typing.'],
  highlight: ['Highlight', 'Drag over text, or click a line to highlight it.'],
  draw: ['Draw', 'Freehand pen. Each stroke can be moved or deleted later.'],
  shape: ['Rectangle', 'Drag to draw an outlined box.'],
  whiteout: ['Whiteout', 'Drag to paint over anything with a solid colour.'],
  redact: ['Redact', 'Permanently removes the text and image pixels underneath when you save.'],
  image: ['Image', 'Choose a picture to place on the page.'],
  sign: ['Sign', 'Draw or type your signature and place it.'],
};
const FAM = {sans: 'Arial, Helvetica, sans-serif', serif: "'Times New Roman', Times, serif", mono: "'Courier New', Courier, monospace"};
const SW = ['#111111', '#ffffff', '#ff3b30', '#1f5eff', '#0a8f4d', '#ff9500', '#7a3cff'];
const HL = ['#ffe033', '#7dff8a', '#ff8ad8', '#7cd4ff', '#ffb347'];
const WO = ['#ffffff', '#f7f3ea', '#000000', '#e9e9e9'];

const S = {
  sid: null, pages: [], zoom: 1, tool: 'edit', sel: null, active: null, current: 0,
  style: {family: 'sans', size: 14, color: '#111111', bold: false, italic: false, align: 'left'},
  hl: '#ffe033', ink: {color: '#1f5eff', width: 2.5}, white: '#ffffff', shape: {color: '#ff3b30', width: 2},
  undo: [], redo: [], last: null, dirty: false,
};
const nid = p => p + Math.random().toString(36).slice(2, 9);
const pageOf = el => S.pages.find(p => p.key === el?.closest('.pg')?.dataset.key);
const Z = () => S.zoom;

// ------------------------------------------------------------------ toolbar
$('#tools').innerHTML = TOOLS.map(t => t === '|' ? '<span class="div"></span>' :
  `<button class="tool" data-t="${t[0]}">${ic(t[1])}<span class="tip">${t[2]}<kbd>${t[3]}</kbd></span></button>`).join('');
$$('#tools .tool').forEach(b => b.onclick = () => setTool(b.dataset.t));

function setTool(t) {
  if (t === 'image') return pickImage();
  if (t === 'sign') return openSig();
  finishEditing(); S.tool = t; S.active = null;
  if (!['select', 'edit', 'text'].includes(t)) S.sel = null;
  $$('#tools .tool').forEach(b => b.classList.toggle('on', b.dataset.t === t));
  ed.className = 'ed' + (S.pages.length ? '' : ' empty') + ' tool-' + t;
  S.pages.forEach(renderOverlay);
  if (t === 'edit') visiblePages().forEach(ensureLines);
  renderInsp();
}

// ------------------------------------------------------------------ history
function snap() {
  return JSON.stringify(S.pages.map(p => ({key: p.key, src: p.src, index: p.index, w: p.w, h: p.h, rotate: p.rotate,
    blank: p.blank, scanned: p.scanned, hasText: p.hasText, edits: p.edits, objects: p.objects})));
}
function commit() {
  const now = snap();
  if (now === S.last) return;
  S.undo.push(S.last); if (S.undo.length > 100) S.undo.shift();
  S.redo = []; S.last = now; setDirty(true); updUndo();
}
function restore(json) {
  const old = Object.fromEntries(S.pages.map(p => [p.key, p]));
  S.pages = JSON.parse(json).map(a => Object.assign({lines: old[a.key]?.lines || null, ocr: old[a.key]?.ocr,
    lstate: old[a.key]?.lstate || 'none'}, a));
  S.last = json; S.sel = null; S.active = null; layout(); setDirty(true); updUndo();
  S.pages.forEach(p => Object.entries(p.edits).forEach(([id, e]) => { if (!e.preview?.img) requestPreview(p, id); }));
}
function undo() { if (!S.undo.length) return; finishEditing(); S.redo.push(S.last); restore(S.undo.pop()); }
function redo() { if (!S.redo.length) return; finishEditing(); S.undo.push(S.last); restore(S.redo.pop()); }
function updUndo() { $('#undo').disabled = !S.undo.length; $('#redo').disabled = !S.redo.length; }
function setDirty(d) { S.dirty = d; $('#dirty').classList.toggle('on', d); }
$('#undo').onclick = undo; $('#redo').onclick = redo;

// ------------------------------------------------------------------ busy indicator
let busyN = 0;
function busy(msg) {
  busyN++; $('#busymsg').textContent = msg; $('#busy').classList.add('on');
  if (!$('#busydots').children.length) $('#busydots').innerHTML = Array.from({length: 14}, (_, i) => `<i style="animation-delay:${i * 60}ms"></i>`).join('');
  return () => { if (--busyN <= 0) { busyN = 0; $('#busy').classList.remove('on'); } };
}

// ------------------------------------------------------------------ opening documents
// The editing session lives in the server's memory. A redeploy or an idle shutdown loses it, so the browser
// keeps a copy of every opened PDF and silently reopens the session (same pages, same edits) when that happens.
S.srcFiles = {};   // source id -> Blob (the opened document as PDF)
S.srcNames = {};
function keepSource(r) {
  S.srcNames[r.src] = r.name;
  const sid = S.sid;
  fetch(`/api/pdf/${sid}/source/${r.src}`).then(x => x.ok ? x.blob() : null)
    .then(b => { if (b) S.srcFiles[r.src] = b; }).catch(() => {});
}
const expired = e => /session expired|unknown page source/i.test(e?.message || '');
let recovering = null;
function recover() {
  if (recovering) return recovering;
  recovering = (async () => {
    const srcs = [...new Set(S.pages.filter(p => !p.blank).map(p => p.src))];
    const missing = srcs.filter(s => !S.srcFiles[s]);
    if (!srcs.length) { S.sid = null; return; }
    if (missing.length) throw new Error('The server restarted before this file was fully loaded. Please open it again.');
    const done = busy('Reconnecting'), map = {};
    let sid = null;
    try {
      for (const src of srcs) {
        const fd = new FormData();
        fd.append('file', new File([S.srcFiles[src]], (S.srcNames[src] || 'document').replace(/\.[^.]+$/, '') + '.pdf'));
        const r = await api(`/api/pdf/${sid || 'new'}/add`, {method: 'POST', body: fd});
        sid = r.sid; map[src] = r.src;
        S.srcFiles[r.src] = S.srcFiles[src]; S.srcNames[r.src] = S.srcNames[src];
      }
    } finally { done(); }
    S.sid = sid;
    const remap = j => j && Object.entries(map).reduce((t, [a, b]) => t.split(`"src":"${a}"`).join(`"src":"${b}"`), j);
    S.undo = S.undo.map(remap); S.redo = S.redo.map(remap); S.last = remap(S.last);
    S.pages.forEach(p => { if (map[p.src]) p.src = map[p.src]; p.imgW = 0; if (p.visible) loadImg(p); });
    thumbs();
    toast('Reconnected to the server — your edits are safe');
  })().finally(() => { recovering = null; });
  return recovering;
}
// a request bound to the editing session: if the session is gone, reopen it once and retry
async function edApi(path, opts) {
  try { return await api(path(), opts?.()); } catch (e) {
    if (!expired(e) || !S.pages.length) throw e;
    await recover();
    return api(path(), opts?.());
  }
}
function pagesFrom(r) {
  return r.pages.map((pg, i) => ({key: nid('p'), src: r.src, index: i, w: pg.w, h: pg.h, rotate: 0, blank: null,
    scanned: pg.scanned, hasText: pg.hasText, edits: {}, objects: [], lines: null, lstate: 'none'}));
}
function insertPages(r, at) {
  const first = !S.pages.length;
  const np = pagesFrom(r);
  S.pages.splice(at ?? S.pages.length, 0, ...np);
  if (first) {
    $('#docname').value = r.name.replace(/\.[^.]+$/, '');
    S.undo = []; S.redo = []; S.zoom = 0; layout(); S.last = snap(); setDirty(false); updUndo();
  } else { layout(); commit(); }
  return np;
}
async function addFiles(files, at) {
  files = [...files]; if (!files.length) return;
  const done = busy(files.length > 1 ? `Opening ${files.length} files` : `Opening ${files[0].name}`);
  const fresh = !S.pages.length;
  try {
    for (const f of files) {
      const fd = new FormData(); fd.append('file', f);
      const first = !S.pages.length;
      if (first) S.sid = null;
      const r = await edApi(() => `/api/pdf/${S.sid || 'new'}/add${first ? '?remember=1' : ''}`, () => ({method: 'POST', body: fd}));
      S.sid = r.sid; keepSource(r);
      if (first) S.lib = r.lib || null;
      const np = insertPages(r, at); if (at != null) at += np.length;
      const sc = np.filter(p => p.scanned || !p.hasText).length;
      toast(`${r.name} · ${np.length} page${np.length > 1 ? 's' : ''}${sc ? ` · ${sc} scanned, text will be read automatically` : ''}`);
    }
  } catch (e) { toast(e.message, true); } finally {
    done();
    if (fresh && S.pages.length) { S.undo = []; S.redo = []; S.last = snap(); setDirty(false); updUndo(); }
  }
}
VIEWS.edit = {
  show() { requestAnimationFrame(() => { if (S.pages.length && !S.zoom) fit(); }); if (!S.pages.length) loadRecent(); },
  open() { $('#edfile').click(); },
  async openFrom(ref, name) {
    if (S.pages.length && S.dirty && !confirm('Replace the document you are editing? Unsaved changes will be lost.')) return;
    const done = busy(`Opening ${name}`);
    try {
      S.sid = null; S.pages = [];
      const r = await api(`/api/pdf/new/add-from`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(ref)});
      S.sid = r.sid; keepSource(r);
      S.lib = r.lib || null;
      insertPages(r);
    } catch (e) { toast(e.message, true); } finally { done(); }
  },
};
// ------------------------------------------------------------------ home: recent files
let recent = [];
const ago = t => {
  const s = (Date.now() / 1000 - t) | 0;
  if (s < 60) return 'just now';
  if (s < 3600) return `${(s / 60) | 0} min ago`;
  if (s < 86400) return `${(s / 3600) | 0} h ago`;
  if (s < 86400 * 7) return `${(s / 86400) | 0} d ago`;
  return new Date(t * 1000).toLocaleDateString(undefined, {day: 'numeric', month: 'short'});
};
async function loadRecent() {
  try { recent = (await api('/api/library')).items; } catch { recent = []; }
  paintRecent();
}
function paintRecent() {
  const q = $('#ehsearch').value.trim().toLowerCase();
  const list = q ? recent.filter(i => i.name.toLowerCase().includes(q)) : recent;
  $('#ehnone').hidden = recent.length > 0;
  $('#ehsearchwrap').hidden = recent.length < 7;
  $('#ehgrid').innerHTML = list.map((i, n) => `
    <div class="eh-card" tabindex="0" data-id="${i.id}" style="animation-delay:${Math.min(n, 12) * 30}ms">
      <div class="pv"><img src="/api/library/${i.id}/thumb?v=${Math.round(i.updated)}" alt="" loading="lazy"></div>
      <button class="rm" title="Remove from Recent" data-forget>${ic('x')}</button>
      <div class="nm" title="${esc(i.name)}">${esc(i.name.replace(/\.pdf$/i, ''))}</div>
      <div class="mt">${i.edited ? '<i title="Edited in Morph"></i>' : ''}${i.pages} page${i.pages === 1 ? '' : 's'} · ${ago(Math.max(i.opened || 0, i.updated || 0))}</div>
    </div>`).join('') || (recent.length ? `<p class="mono" style="color:var(--dim)">No match</p>` : '');
  $$('#ehgrid .eh-card').forEach(c => {
    c.onclick = e => {
      if (e.target.closest('[data-forget]')) {
        e.stopPropagation();
        api('/api/library/' + c.dataset.id, {method: 'DELETE'}).then(() => { recent = recent.filter(i => i.id !== c.dataset.id); paintRecent(); });
        return;
      }
      const it = recent.find(i => i.id === c.dataset.id);
      VIEWS.edit.openFrom({library: it.id}, it.name);
    };
    c.onkeydown = e => { if (e.key === 'Enter') c.click(); };
  });
}
$('#ehsearch').oninput = paintRecent;
function goHome() {
  finishEditing();
  if (S.pages.length && S.dirty && !confirm("Close this document? Changes you haven't saved will be lost.")) return;
  S.sid = null; S.pages = []; S.lib = null; S.sel = null; S.active = null; S.undo = []; S.redo = []; S.zoom = 0;
  setDirty(false); updUndo(); layout(); loadRecent();
  $('#canvas').scrollTop = 0;
}
$('#edback').onclick = goHome;
loadRecent();

$('#edfile').onchange = e => { addFiles(e.target.files, S.pages.length ? S.current + 1 : null); e.target.value = ''; };
$('#edempty').onclick = e => { e.preventDefault(); $('#edfile').click(); };
$('#addpages').onclick = () => $('#edfile').click();
$('#addblank').onclick = () => {
  const ref = S.pages[S.current] || {w: 595, h: 842};
  S.pages.splice(S.current + 1, 0, {key: nid('p'), blank: {w: ref.w, h: ref.h}, w: ref.w, h: ref.h, rotate: 0,
    edits: {}, objects: [], lines: [], lstate: 'ready', hasText: false, scanned: false});
  layout(); commit(); scrollToPage(S.current + 1);
};
// drop files onto the editor
canvas.addEventListener('dragover', e => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); canvas.classList.add('over'); } });
canvas.addEventListener('dragleave', () => canvas.classList.remove('over'));
canvas.addEventListener('drop', e => {
  canvas.classList.remove('over');
  if (e.dataTransfer.files.length) { e.preventDefault(); e.stopPropagation(); addFiles(e.dataTransfer.files); }
});

// ------------------------------------------------------------------ layout & rendering
function dims(p) {
  const r = (p.rotate % 180) !== 0;
  return {W: (r ? p.h : p.w) * Z(), H: (r ? p.w : p.h) * Z()};
}
function layout() {
  ed.className = 'ed' + (S.pages.length ? '' : ' empty') + ' tool-' + S.tool;
  if (S.pages.length && !S.zoom) { S.zoom = 1; fit(true); }
  pagesEl.innerHTML = '';
  io.disconnect();
  S.pages.forEach((p, i) => {
    const el = document.createElement('div');
    el.className = 'pg'; el.dataset.key = p.key;
    el.innerHTML = `<div class="pg-in"><img class="loading" alt=""><div class="lay lines"></div><div class="lay objs"></div><div class="veil"><span><i></i>Reading text</span></div></div><div class="pg-n mono">${i + 1}</div>`;
    p.el = el; p.imgW = 0;
    pagesEl.appendChild(el); size(p); renderOverlay(p); io.observe(el);
  });
  S.current = Math.min(S.current, Math.max(0, S.pages.length - 1));
  thumbs(); renderInsp(); updPg();
}
function size(p) {
  const {W, H} = dims(p);
  p.el.style.width = W + 'px'; p.el.style.height = H + 'px';
  const inn = p.el.firstElementChild;
  inn.style.width = p.w * Z() + 'px'; inn.style.height = p.h * Z() + 'px';
  inn.style.transform = `translate(-50%,-50%) rotate(${p.rotate}deg)`;
}
function loadImg(p) {
  if (p.blank) { $('img', p.el).remove?.(); return; }
  const want = Math.min(3200, Math.ceil(p.w * Z() * (devicePixelRatio || 1) / 300) * 300);
  if (p.imgW >= want) return;
  p.imgW = want;
  const img = $('img', p.el), src = `/api/pdf/${S.sid}/render/${p.src}/${p.index}?w=${want}`;
  const pre = new Image();
  pre.onload = () => { img.src = src; img.classList.remove('loading'); };
  pre.onerror = () => {   // usually the session is gone (server restarted): reopen it, then load again
    p.imgW = 0;
    if (!S.pages.includes(p) || p._imgRetry) return;
    p._imgRetry = true;
    recover().then(() => loadImg(p)).catch(e => toast(e.message, true)).finally(() => { p._imgRetry = false; });
  };
  pre.src = src;
}
const io = new IntersectionObserver(es => es.forEach(en => {
  const p = pageOf(en.target); if (!p) return;
  p.visible = en.isIntersecting;
  if (en.isIntersecting) { loadImg(p); if (S.tool === 'edit' || S.tool === 'highlight') ensureLines(p); }
}), {root: canvas, rootMargin: '600px 0px'});
const visiblePages = () => S.pages.filter(p => p.visible);

// ---- text lines (PDF text or OCR)
async function ensureLines(p, forceOcr = false) {
  if (p.blank || p.lstate === 'loading' || (p.lstate === 'ready' && !forceOcr)) return;
  const ocr = forceOcr || p.scanned || !p.hasText;
  p.lstate = 'loading'; veil(p, ocr);
  try {
    let r = await edApi(() => `/api/pdf/${S.sid}/lines/${p.src}/${p.index}${ocr ? '?ocr=1' : ''}`);
    if (!ocr && !r.lines.length) { veil(p, true); r = await edApi(() => `/api/pdf/${S.sid}/lines/${p.src}/${p.index}?ocr=1`); }
    p.lines = r.lines; p.ocr = r.ocr; p.lstate = 'ready';
    if (forceOcr) {   // drop edits that pointed at the old lines
      for (const k of Object.keys(p.edits)) if (!p.lines.find(l => l.id === k && l.source === p.edits[k].line.source)) delete p.edits[k];
    }
  } catch (e) { p.lstate = 'error'; toast(e.message, true); }
  veil(p, false); renderOverlay(p); if (p === S.pages[S.current]) renderInsp();
}
function veil(p, on) { $('.veil', p.el)?.classList.toggle('on', !!on); }

function renderOverlay(p) {
  if (!p.el) return;
  const L = $('.lines', p.el), O = $('.objs', p.el), z = Z();
  L.innerHTML = '';
  (p.lines || []).forEach(l => {
    const e = p.edits[l.id];
    if (!e && S.tool !== 'edit') return;
    const d = document.createElement('div');
    d.className = 'tl' + (e ? ' edited' : '') + (S.active?.p === p && S.active.id === l.id ? ' active' : '');
    d.dataset.id = l.id;
    styleLine(d, l, e);
    if (e && e.preview && !(S.active?.p === p && S.active.id === l.id && document.activeElement?.dataset?.id === l.id)) {
      d.classList.add('cloned');
      if (e.preview.img) {
        const [x0, y0, x1, y1] = e.preview.rect, im = document.createElement('img');
        im.className = 'tlp'; im.dataset.id = l.id; im.src = e.preview.img;
        Object.assign(im.style, {left: x0 * z + 'px', top: y0 * z + 'px', width: (x1 - x0) * z + 'px', height: (y1 - y0) * z + 'px'});
        L.appendChild(im);
      } else {
        d.classList.add('erased');
      }
    }
    L.appendChild(d);
  });
  O.innerHTML = '';
  p.objects.forEach(o => O.appendChild(objEl(p, o)));
}
function styleLine(d, l, e) {
  const z = Z(), [x0, y0, x1, y1] = l.bbox, st = e || l, h = (y1 - y0) * z;
  // put the typed text on the line's real baseline: CSS centres the font's box (≈ ascent .905, descent .212 em)
  // inside the line box, so pick the line-height that lands the baseline on the PDF's origin
  const base = (l.origin?.[1] ?? y1) - y0, lh = 2 * (base - 0.905 * st.size) + 1.117 * st.size;
  // the document's own font first (if this computer has it), then the closest generic family
  const own = (l.font || '').replace(/^[A-Z]{6}\+/, '').split(/[-,]/)[0].replace(/(MT|PSMT|PS)$/, '').replace(/'/g, '');
  const famCss = (st.family === l.family && own ? `'${own}', ` : '') + (FAM[st.family] || FAM.sans);
  Object.assign(d.style, {left: x0 * z + 'px', top: y0 * z + 'px', minWidth: (x1 - x0) * z + 'px', height: h + 'px',
    lineHeight: (lh > 0 ? lh * z : h) + 'px', fontSize: st.size * z + 'px', fontFamily: famCss,
    fontWeight: st.bold ? 700 : 400, fontStyle: st.italic ? 'italic' : 'normal',
    color: e ? st.color : 'transparent', backgroundColor: e ? (l.bg || '#fff') : ''});
  if (document.activeElement !== d) d.textContent = e ? e.text : l.text;
}

function rectOf(o) {
  if (o.type === 'ink') {
    const xs = o.paths.flat().map(p => p[0]), ys = o.paths.flat().map(p => p[1]), pad = o.width;
    return [Math.min(...xs) - pad, Math.min(...ys) - pad, Math.max(...xs) + pad, Math.max(...ys) + pad];
  }
  return o.rect;
}
function objEl(p, o) {
  const z = Z(), [x0, y0, x1, y1] = rectOf(o), d = document.createElement('div');
  const sel = S.sel?.id === o.id;
  d.className = `obj o-${o.type}` + (sel ? ' sel' : ''); d.dataset.id = o.id;
  Object.assign(d.style, {left: x0 * z + 'px', top: y0 * z + 'px', width: (x1 - x0) * z + 'px'});
  if (o.type !== 'text') d.style.height = (y1 - y0) * z + 'px';
  if (o.type === 'text') {
    Object.assign(d.style, {fontSize: o.size * z + 'px', fontFamily: FAM[o.family], fontWeight: o.bold ? 700 : 400,
      fontStyle: o.italic ? 'italic' : 'normal', color: o.color, textAlign: o.align, minHeight: o.size * 1.25 * z + 'px'});
    d.textContent = o.text;
  } else if (o.type === 'highlight') d.style.background = o.color;
  else if (o.type === 'whiteout') d.style.background = o.color;
  else if (o.type === 'shape') d.style.border = `${o.width * z}px solid ${o.color}`;
  else if (o.type === 'image') d.innerHTML = `<img src="${o.data}" alt="">`;
  else if (o.type === 'ink') {
    const pts = o.paths.map(path => `<polyline points="${path.map(q => q.join(',')).join(' ')}" fill="none" stroke="${o.color}"
      stroke-width="${o.width}" stroke-linecap="round" stroke-linejoin="round"/>`).join('');
    d.innerHTML = `<svg viewBox="${x0} ${y0} ${x1 - x0} ${y1 - y0}" preserveAspectRatio="none">${pts}</svg>`;
  }
  if (sel) {
    if (o.type !== 'ink') d.insertAdjacentHTML('beforeend', '<i class="h nw"></i><i class="h ne"></i><i class="h sw"></i><i class="h se"></i>');
    d.insertAdjacentHTML('beforeend', `<button class="del" data-del>${ic('trash')}Delete</button>`);
  }
  return d;
}

// ------------------------------------------------------------------ coordinates
function toPage(ev, p) {
  const r = p.el.getBoundingClientRect(), z = Z();
  const dx = (ev.clientX - r.left) / z, dy = (ev.clientY - r.top) / z;
  switch (((p.rotate % 360) + 360) % 360) {
    case 90: return [dy, p.h - dx];
    case 180: return [p.w - dx, p.h - dy];
    case 270: return [p.w - dy, dx];
    default: return [dx, dy];
  }
}
const clampR = (p, r) => [Math.max(0, Math.min(r[0], p.w)), Math.max(0, Math.min(r[1], p.h)), Math.max(0, Math.min(r[2], p.w)), Math.max(0, Math.min(r[3], p.h))];
const norm = (a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];

// ------------------------------------------------------------------ pointer interaction
let drag = null;
pagesEl.addEventListener('pointerdown', ev => {
  if (ev.button !== 0) return;
  const p = pageOf(ev.target); if (!p) return;
  setCurrent(S.pages.indexOf(p));
  if (ev.target.isContentEditable) return;               // typing inside a text box / line
  const pt = toPage(ev, p);
  const objEl_ = ev.target.closest('.obj');
  if (ev.target.closest('[data-del]')) { ev.preventDefault(); removeSel(); return; }
  // resize handle
  if (ev.target.classList.contains('h') && S.sel) {
    ev.preventDefault();
    const o = p.objects.find(o => o.id === S.sel.id);
    drag = {mode: 'resize', p, o, corner: [...ev.target.classList].find(c => c.length === 2), start: pt, orig: [...o.rect],
      ratio: (o.rect[2] - o.rect[0]) / Math.max(1, o.rect[3] - o.rect[1])};
    return;
  }
  // existing object: select / move
  if (objEl_ && !['draw', 'highlight', 'whiteout', 'redact', 'shape'].includes(S.tool)) {
    ev.preventDefault(); finishEditing();
    const o = p.objects.find(o => o.id === objEl_.dataset.id);
    select(p, o);
    drag = {mode: 'move', p, o, start: pt, orig: JSON.parse(JSON.stringify(o)), moved: false};
    return;
  }
  if (ev.target.closest('.tl')) return;                  // handled by click → edit line
  finishEditing();
  if (S.sel || S.active) { S.sel = null; S.active = null; S.pages.forEach(renderOverlay); renderInsp(); }
  if (S.tool === 'text') {
    ev.preventDefault();
    const o = {id: nid('o'), type: 'text', rect: [pt[0], pt[1] - S.style.size * 0.7, Math.min(p.w - 8, pt[0] + 240), pt[1] + S.style.size],
      text: '', ...S.style};
    p.objects.push(o); select(p, o); editText(p, o);
    return;
  }
  if (['highlight', 'whiteout', 'redact', 'shape'].includes(S.tool)) {
    ev.preventDefault();
    const o = {id: nid('o'), type: S.tool, rect: [pt[0], pt[1], pt[0], pt[1]],
      color: S.tool === 'highlight' ? S.hl : S.tool === 'whiteout' ? S.white : S.shape.color, width: S.shape.width};
    p.objects.push(o);
    drag = {mode: 'rect', p, o, start: pt};
    return;
  }
  if (S.tool === 'draw') {
    ev.preventDefault();
    const o = {id: nid('o'), type: 'ink', paths: [[pt]], color: S.ink.color, width: S.ink.width};
    p.objects.push(o);
    drag = {mode: 'ink', p, o};
  }
});
addEventListener('pointermove', ev => {
  if (!drag) return;
  const {p, o} = drag, pt = toPage(ev, p);
  if (drag.mode === 'rect') {
    o.rect = clampR(p, norm(drag.start, pt)); rerenderObj(p, o);
  } else if (drag.mode === 'ink') {
    const path = o.paths[0], last = path[path.length - 1];
    if (Math.hypot(pt[0] - last[0], pt[1] - last[1]) > 0.8 / Z()) { path.push(pt.map(v => Math.round(v * 100) / 100)); rerenderObj(p, o); }
  } else if (drag.mode === 'move') {
    const dx = pt[0] - drag.start[0], dy = pt[1] - drag.start[1];
    if (Math.abs(dx) + Math.abs(dy) > 0.5) drag.moved = true;
    if (o.type === 'ink') o.paths = drag.orig.paths.map(path => path.map(([x, y]) => [x + dx, y + dy]));
    else o.rect = [drag.orig.rect[0] + dx, drag.orig.rect[1] + dy, drag.orig.rect[2] + dx, drag.orig.rect[3] + dy];
    rerenderObj(p, o);
  } else if (drag.mode === 'resize') {
    let [x0, y0, x1, y1] = drag.orig;
    const c = drag.corner;
    if (c.includes('w')) x0 = Math.min(pt[0], x1 - 4); else x1 = Math.max(pt[0], x0 + 4);
    if (c.includes('n')) y0 = Math.min(pt[1], y1 - 4); else y1 = Math.max(pt[1], y0 + 4);
    if (o.type === 'image' && !ev.shiftKey) {             // keep proportions
      const h = (x1 - x0) / drag.ratio;
      if (c.includes('n')) y0 = y1 - h; else y1 = y0 + h;
    }
    o.rect = [x0, y0, x1, y1]; rerenderObj(p, o);
  }
});
addEventListener('pointerup', ev => {
  if (!drag) return;
  const {p, o, mode, moved} = drag; drag = null;
  if (mode === 'rect') {
    const [x0, y0, x1, y1] = o.rect;
    if (x1 - x0 < 3 || y1 - y0 < 3) {
      // a click: for highlight, highlight the text line under the cursor
      const pt = toPage(ev, p), line = o.type === 'highlight' && (p.lines || []).find(l => pt[0] >= l.bbox[0] && pt[0] <= l.bbox[2] && pt[1] >= l.bbox[1] - 1 && pt[1] <= l.bbox[3] + 1);
      if (line) {
        const ed_ = $(`.tl[data-id="${line.id}"]`, p.el);   // edited line: use its new width
        const x1 = ed_ ? line.bbox[0] + ed_.offsetWidth / Z() : line.bbox[2];
        o.rect = [line.bbox[0] - 1, line.bbox[1], Math.max(x1, line.bbox[2]) + 1, line.bbox[3]];
      }
      else { p.objects.splice(p.objects.indexOf(o), 1); renderOverlay(p); return; }
      rerenderObj(p, o);
    }
    commit();
  } else if (mode === 'ink') {
    if (o.paths[0].length < 2) o.paths[0].push([o.paths[0][0][0] + 0.5, o.paths[0][0][1] + 0.5]);
    renderOverlay(p); commit();
  } else if (mode === 'move') { if (moved) commit(); }
  else commit();
});
function rerenderObj(p, o) {
  const old = $(`.obj[data-id="${o.id}"]`, p.el), n = objEl(p, o);
  old ? old.replaceWith(n) : $('.objs', p.el).appendChild(n);
}
pagesEl.addEventListener('dblclick', ev => {
  const p = pageOf(ev.target), el = ev.target.closest('.obj.o-text'); if (!p || !el) return;
  const o = p.objects.find(o => o.id === el.dataset.id); select(p, o); editText(p, o);
});
// edit-text tool: click on a line
pagesEl.addEventListener('click', ev => {
  if (S.tool !== 'edit') return;
  const d = ev.target.closest('.tl'), p = pageOf(ev.target);
  if (!d || !p || d.isContentEditable) return;
  const l = p.lines.find(l => l.id === d.dataset.id); if (l) activateLine(p, l, d, ev);
});

function select(p, o) {
  S.sel = o ? {p, id: o.id} : null; S.active = null;
  S.pages.forEach(renderOverlay); renderInsp();
}
function selObj() { return S.sel && S.sel.p.objects.find(o => o.id === S.sel.id); }
function removeSel() {
  const o = selObj(); if (!o) return;
  const p = S.sel.p; p.objects.splice(p.objects.indexOf(o), 1); S.sel = null;
  renderOverlay(p); renderInsp(); commit();
}

// ---- editing text boxes (added text)
function editText(p, o) {
  const el = $(`.obj[data-id="${o.id}"]`, p.el); if (!el) return;
  $$('.h,.del', el).forEach(x => x.remove());
  el.contentEditable = 'plaintext-only'; el.focus();
  const r = document.createRange(); r.selectNodeContents(el); r.collapse(false);
  getSelection().removeAllRanges(); getSelection().addRange(r);
  el.oninput = () => { o.text = el.innerText; };
  el.onblur = () => {
    el.contentEditable = 'false'; o.text = el.innerText.replace(/\n$/, '');
    if (!o.text.trim()) { p.objects.splice(p.objects.indexOf(o), 1); if (S.sel?.id === o.id) S.sel = null; }
    else { const hpt = el.offsetHeight / Z(); o.rect[3] = o.rect[1] + Math.max(hpt, o.size * 1.25); }
    renderOverlay(p); renderInsp(); commit();
  };
  el.onkeydown = e => {
    if (e.key === 'Escape') el.blur();
    else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'a') { e.preventDefault(); selectAll(el); }
  };
}

// ---- editing existing text lines
function activateLine(p, l, d, ev) {
  if (S.sel) { S.sel = null; S.pages.forEach(renderOverlay); d = $(`.tl[data-id="${l.id}"]`, p.el); }
  $$('.tl.active', pagesEl).forEach(x => x.classList.remove('active'));
  if (!p.edits[l.id]) p.edits[l.id] = {line: JSON.parse(JSON.stringify(l)), text: l.text, size: l.size, color: l.color,
    family: l.family, bold: l.bold, italic: l.italic};
  S.active = {p, id: l.id};
  d.classList.remove('cloned', 'erased'); $(`.tlp[data-id="${l.id}"]`, p.el)?.remove();
  d.classList.add('edited', 'active');
  styleLine(d, l, p.edits[l.id]);
  d.contentEditable = 'plaintext-only'; d.spellcheck = false; d.focus();
  const r = document.caretRangeFromPoint?.(ev.clientX, ev.clientY);
  if (r && d.contains(r.startContainer)) { getSelection().removeAllRanges(); getSelection().addRange(r); }
  d.oninput = () => { p.edits[l.id].text = d.textContent; };
  d.onkeydown = e => {
    if (e.key === 'Enter' || e.key === 'Escape') { e.preventDefault(); d.blur(); }
    else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'a') { e.preventDefault(); selectAll(d); }
  };
  d.onblur = () => { d.contentEditable = 'false'; settleLine(p, l.id); };
  renderInsp();
}
function settleLine(p, id) {
  const e = p.edits[id]; if (!e) return;
  const l = e.line;
  if (e.text === l.text && e.size === l.size && e.color === l.color && e.family === l.family && e.bold === l.bold && e.italic === l.italic)
    delete p.edits[id];
  else { e.preview = null; requestPreview(p, id); }   // show exactly what Save will write
  commit(); renderOverlay(p); thumbTag(p);
}
function selectAll(el) { const r = document.createRange(); r.selectNodeContents(el); getSelection().removeAllRanges(); getSelection().addRange(r); }
// scanned lines: ask the engine to rewrite the scan with the document's own letters and show that
async function requestPreview(p, id) {
  const e = p.edits[id]; if (!e) return;
  const ver = (e._v = (e._v || 0) + 1);
  try {
    const r = await edApi(() => `/api/pdf/${S.sid}/preview`, () => ({method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({src: p.src, index: p.index, edit: {line: e.line, text: e.text, size: e.size, color: e.color,
        family: e.family, bold: e.bold, italic: e.italic}})}));
    if (p.edits[id] !== e || e._v !== ver) return;
    e.preview = r.img ? {img: r.img, rect: r.rect} : {img: null};
    renderOverlay(p);
  } catch (err) { toast(err.message, true); }
}
function finishEditing() { const a = document.activeElement; if (a && a.isContentEditable) a.blur(); }

// ------------------------------------------------------------------ images & signatures
function pickImage() { $('#imgfile').click(); }
$('#imgfile').onchange = e => {
  const f = e.target.files[0]; e.target.value = ''; if (!f) return;
  const rd = new FileReader();
  rd.onload = () => placeImage(rd.result);
  rd.readAsDataURL(f);
};
function placeImage(data, maxFrac = 0.45, where = 'center') {
  const p = S.pages[S.current]; if (!p) return toast('Open a document first', true);
  const img = new Image();
  img.onload = () => {
    let w = Math.min(p.w * maxFrac, img.naturalWidth * 0.5), h = w * img.naturalHeight / img.naturalWidth;
    // re-encode very large photos so the editor stays fast
    if (img.naturalWidth > 2400) {
      const c = document.createElement('canvas'), k = 2400 / img.naturalWidth;
      c.width = 2400; c.height = img.naturalHeight * k; c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
      data = c.toDataURL(data.startsWith('data:image/png') ? 'image/png' : 'image/jpeg', 0.9);
    }
    const cy = where === 'low' ? p.h * 0.78 : p.h / 2;
    const o = {id: nid('o'), type: 'image', rect: [(p.w - w) / 2, cy - h / 2, (p.w + w) / 2, cy + h / 2], data};
    p.objects.push(o); setTool('select'); select(p, o); commit();
  };
  img.src = data;
}

// signature pad
const pad = $('#sigpad'), pctx = pad.getContext('2d');
let sigMode = 'draw', sigColor = localStorage.morphSigColor || '#111111', sigDrawn = false;
$('#sigcolors').innerHTML = ['#111111', '#1f3fbf', '#c0392b'].map(c => `<button style="background:${c}" data-c="${c}"></button>`).join('');
function openSig() {
  if (!S.pages.length) return toast('Open a document first', true);
  $('#sigmodal').classList.add('on'); clearSig(); paintSigColors();
}
function paintSigColors() { $$('#sigcolors button').forEach(b => b.classList.toggle('on', b.dataset.c === sigColor)); }
$$('#sigcolors button').forEach(b => b.onclick = () => { sigColor = b.dataset.c; localStorage.morphSigColor = sigColor; paintSigColors(); drawTyped(); });
function clearSig() { pctx.clearRect(0, 0, pad.width, pad.height); sigDrawn = false; if (sigMode === 'type') drawTyped(); }
$('#sigclear').onclick = clearSig;
$('#sigclose').onclick = () => $('#sigmodal').classList.remove('on');
$('#sigmodal').onclick = e => { if (e.target.id === 'sigmodal') $('#sigmodal').classList.remove('on'); };
$$('#sigmode button').forEach(b => b.onclick = () => {
  sigMode = b.dataset.m; $$('#sigmode button').forEach(x => x.classList.toggle('on', x === b));
  $('#sigtext').hidden = sigMode !== 'type'; clearSig();
  if (sigMode === 'type') { $('#sigtext').focus(); drawTyped(); }
});
$('#sigtext').oninput = drawTyped;
function drawTyped() {
  if (sigMode !== 'type') return;
  pctx.clearRect(0, 0, pad.width, pad.height);
  const t = $('#sigtext').value; if (!t) return;
  pctx.fillStyle = sigColor; pctx.textBaseline = 'middle'; pctx.textAlign = 'center';
  let fs = 170; pctx.font = `600 ${fs}px Caveat, cursive`;
  while (pctx.measureText(t).width > pad.width - 80 && fs > 30) { fs -= 6; pctx.font = `600 ${fs}px Caveat, cursive`; }
  pctx.fillText(t, pad.width / 2, pad.height / 2 + 8); sigDrawn = true;
}
let sigLast = null;
pad.addEventListener('pointerdown', e => { if (sigMode !== 'draw') return; pad.setPointerCapture(e.pointerId); sigLast = padPt(e); });
pad.addEventListener('pointermove', e => {
  if (!sigLast) return; const q = padPt(e);
  pctx.strokeStyle = sigColor; pctx.lineWidth = 5.5; pctx.lineCap = pctx.lineJoin = 'round';
  pctx.beginPath(); pctx.moveTo(...sigLast); pctx.lineTo(...q); pctx.stroke(); sigLast = q; sigDrawn = true;
});
pad.addEventListener('pointerup', () => sigLast = null);
function padPt(e) { const r = pad.getBoundingClientRect(); return [(e.clientX - r.left) * pad.width / r.width, (e.clientY - r.top) * pad.height / r.height]; }
$('#sigok').onclick = () => {
  if (!sigDrawn) return toast('Draw or type a signature first', true);
  // trim transparent edges
  const d = pctx.getImageData(0, 0, pad.width, pad.height).data;
  let x0 = pad.width, y0 = pad.height, x1 = 0, y1 = 0;
  for (let y = 0; y < pad.height; y++) for (let x = 0; x < pad.width; x++) if (d[(y * pad.width + x) * 4 + 3] > 10) {
    if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
  const c = document.createElement('canvas'), m = 8;
  c.width = x1 - x0 + 2 * m; c.height = y1 - y0 + 2 * m;
  c.getContext('2d').drawImage(pad, x0 - m, y0 - m, c.width, c.height, 0, 0, c.width, c.height);
  $('#sigmodal').classList.remove('on');
  placeImage(c.toDataURL('image/png'), 0.28, 'low');
};

// ------------------------------------------------------------------ thumbnails & pages
function thumbs() {
  const L = $('#thlist'); L.innerHTML = '';
  S.pages.forEach((p, i) => {
    const r = (p.rotate % 180) !== 0, w = 132, h = w * (r ? p.w / p.h : p.h / p.w);
    const t = document.createElement('div');
    t.className = 'th' + (i === S.current ? ' cur' : ''); t.draggable = true; t.dataset.key = p.key;
    const img = p.blank ? '' : `<img src="/api/pdf/${S.sid}/render/${p.src}/${p.index}?w=300" alt="" style="width:${r ? h : w}px;height:${r ? w : h}px;transform:rotate(${p.rotate}deg)">`;
    t.innerHTML = `<div class="tb"><div style="width:${w}px;height:${h}px;display:grid;place-items:center">${img}</div></div>
      <span class="num">${i + 1}</span>
      <div class="acts"><button data-a="rot" title="Rotate">${ic('rotate')}</button><button class="del" data-a="del" title="Delete page">${ic('trash')}</button></div>`;
    t.onclick = e => {
      const a = e.target.closest('[data-a]')?.dataset.a;
      if (a === 'rot') rotatePage(i); else if (a === 'del') deletePage(i); else scrollToPage(i);
    };
    t.ondragstart = e => { e.dataTransfer.setData('text/x-morph-page', p.key); e.dataTransfer.effectAllowed = 'move'; t.classList.add('drag'); };
    t.ondragend = () => t.classList.remove('drag');
    t.ondragover = e => {
      if (!e.dataTransfer.types.includes('text/x-morph-page')) return;
      e.preventDefault(); const after = e.offsetY > t.offsetHeight / 2;
      $$('.th', L).forEach(x => x.classList.remove('dropa', 'dropb')); t.classList.add(after ? 'dropa' : 'dropb');
    };
    t.ondragleave = () => t.classList.remove('dropa', 'dropb');
    t.ondrop = e => {
      const key = e.dataTransfer.getData('text/x-morph-page'); if (!key) return;
      e.preventDefault(); e.stopPropagation();
      const after = t.classList.contains('dropa'); t.classList.remove('dropa', 'dropb');
      const from = S.pages.findIndex(x => x.key === key); if (from < 0) return;
      const [moved] = S.pages.splice(from, 1);
      let to = S.pages.findIndex(x => x.key === p.key) + (after ? 1 : 0);
      S.pages.splice(to, 0, moved); S.current = to; layout(); commit(); scrollToPage(to, false);
    };
    L.appendChild(t); thumbTag(p, t);
  });
}
function thumbTag(p, t = $(`.th[data-key="${p.key}"]`)) {
  if (!t) return; $('.tag', t)?.remove();
  const n = Object.keys(p.edits).length + p.objects.length;
  if (n) t.insertAdjacentHTML('beforeend', `<span class="tag">${n} EDIT${n > 1 ? 'S' : ''}</span>`);
}
function rotatePage(i) { const p = S.pages[i]; p.rotate = (p.rotate + 90) % 360; size(p); thumbs(); commit(); }
function deletePage(i) {
  if (S.pages.length === 1 && !confirm('Delete the only page? This closes the document.')) return;
  S.pages.splice(i, 1); if (!S.pages.length) { S.sid = null; S.lib = null; S.undo = []; S.redo = []; setDirty(false); loadRecent(); }
  layout(); if (S.pages.length) commit(); updUndo();
}
function scrollToPage(i, smooth = true) {
  const p = S.pages[i]; if (!p) return;
  canvas.scrollTo({top: p.el.offsetTop - 24, behavior: smooth ? 'smooth' : 'auto'}); setCurrent(i);
}
function setCurrent(i) {
  if (i === S.current && $('.th.cur')) return;
  if (S.active && S.active.p !== S.pages[i] && !document.activeElement?.isContentEditable) { const ap = S.active.p; S.active = null; renderOverlay(ap); }
  S.current = i; $$('.th').forEach((t, j) => t.classList.toggle('cur', j === i)); updPg(); renderInsp();
}
function updPg() { $('#pgind').textContent = S.pages.length ? `${S.current + 1} / ${S.pages.length}` : '— / —'; }
canvas.addEventListener('scroll', () => {
  const mid = canvas.scrollTop + canvas.clientHeight * 0.4;
  let best = 0; S.pages.forEach((p, i) => { if (p.el.offsetTop <= mid) best = i; });
  if (best !== S.current) setCurrent(best);
}, {passive: true});

// ------------------------------------------------------------------ zoom
function setZoom(z, anchor) {
  z = Math.max(0.25, Math.min(4, z));
  if (Math.abs(z - S.zoom) < 0.001) return;
  const rel = (canvas.scrollTop + (anchor ?? canvas.clientHeight / 2)) / Math.max(1, pagesEl.scrollHeight);
  S.zoom = z; $('#zval').textContent = Math.round(z * 100) + '%';
  S.pages.forEach(p => { size(p); renderOverlay(p); if (p.visible) loadImg(p); });
  canvas.scrollTop = rel * pagesEl.scrollHeight - (anchor ?? canvas.clientHeight / 2);
}
function fit(silent) {
  const maxW = Math.max(...S.pages.map(p => (p.rotate % 180 ? p.h : p.w)), 100);
  const margin = PHONE.matches ? 24 : 100;           // phones: use almost the full width
  const z = Math.min(1.75, (canvas.clientWidth - margin) / maxW);
  if (silent) { S.zoom = Math.max(0.25, z); $('#zval').textContent = Math.round(S.zoom * 100) + '%'; } else setZoom(z);
}
$('#zin').onclick = () => setZoom(S.zoom * 1.2);
$('#zout').onclick = () => setZoom(S.zoom / 1.2);
$('#zval').onclick = () => fit();
// phones: the inspector is a bottom sheet, opened from the zoom bar
const PHONE = matchMedia('(max-width: 720px)');
function sheet(open) {
  insp.classList.toggle('open', open);
  $('#inspbtn').classList.toggle('on', open); $('#inspbtn').setAttribute('aria-expanded', open);
}
$('#inspbtn').onclick = () => sheet(!insp.classList.contains('open'));
// close it by tapping the page behind it, or its grab handle
canvas.addEventListener('pointerdown', () => { if (insp.classList.contains('open')) sheet(false); }, true);
insp.addEventListener('click', e => {
  if (PHONE.matches && e.target === insp && e.clientY - insp.getBoundingClientRect().top < 26) sheet(false);
});
PHONE.addEventListener('change', () => { sheet(false); if (S.pages.length) fit(); });
canvas.addEventListener('wheel', e => {
  if (!e.ctrlKey) return; e.preventDefault();
  setZoom(S.zoom * Math.exp(-e.deltaY * 0.01), e.clientY - canvas.getBoundingClientRect().top);
}, {passive: false});

// ------------------------------------------------------------------ inspector
function swatches(list, cur, attr) {
  return `<div class="sw">${list.map(c => `<button data-${attr}="${c}" class="${c.toLowerCase() === (cur || '').toLowerCase() ? 'on' : ''}" style="background:${c}"></button>`).join('')}
    <label title="Custom colour"><input type="color" data-${attr}-custom value="${cur || '#000000'}"></label></div>`;
}
function textStyleTarget() {
  if (S.active) return {kind: 'line', obj: S.active.p.edits[S.active.id] || null, line: S.active.p.lines?.find(l => l.id === S.active.id)};
  const o = selObj(); if (o?.type === 'text') return {kind: 'obj', obj: o};
  if (S.tool === 'text' || S.tool === 'edit') return {kind: 'default', obj: S.style};
  return null;
}
function renderInsp() {
  if (!S.pages.length) { insp.innerHTML = ''; return; }
  const p = S.pages[S.current], o = selObj();
  let ctxT, ctxP, ctxI;
  if (S.active) {
    const sl = S.active.p.lines?.find(l => l.id === S.active.id);
    ctxI = 'textedit'; ctxT = sl?.source === 'ocr' ? 'Editing scanned text' : 'Editing text';
    ctxP = sl?.source === 'ocr' ? 'Type, then Enter. New letters are copied from the scan itself and written into the image — keep the style unchanged for a perfect match.'
      : 'Type to replace. Enter to finish. Style changes apply to this line.';
  }
  else if (o) { ctxI = {text: 'addtext', ink: 'draw', image: 'image'}[o.type] || o.type; ctxT = {text: 'Text box', ink: 'Drawing', image: 'Image', highlight: 'Highlight', whiteout: 'Whiteout', redact: 'Redaction', shape: 'Rectangle'}[o.type]; ctxP = 'Drag to move · corners to resize · ⌫ deletes'; }
  else { [ctxT, ctxP] = INFO[S.tool]; ctxI = TOOLS.find(t => t[0] === S.tool)[1]; }
  let h = `<div class="sec"><div class="ctx"><div class="ico ${S.tool === 'redact' && !o ? 'red' : ''}">${ic(ctxI)}</div><div><b>${ctxT}</b><p>${ctxP}</p></div></div></div>`;

  const ts = textStyleTarget();
  if (ts && ts.obj) {
    const st = ts.obj;
    h += `<div class="sec"><h4>Font${ts.line?.font ? `<span title="${esc(ts.line.font)}">${esc(ts.line.font.slice(0, 18))}</span>` : ''}</h4>
      <div class="segs" data-g="family">${['sans', 'serif', 'mono'].map(f => `<button data-v="${f}" class="${st.family === f ? 'on' : ''}" style="font-family:${FAM[f]};font-size:13px;text-transform:none">${f[0].toUpperCase() + f.slice(1)}</button>`).join('')}</div>
      <div class="row"><div class="stepper"><button data-step="-1">${ic('minus')}</button><input id="fsize" value="${Math.round(st.size * 10) / 10}"><button data-step="1">${ic('plus')}</button></div>
        <button class="tog ${st.bold ? 'on' : ''}" data-tog="bold" title="Bold">${ic('bold')}</button>
        <button class="tog ${st.italic ? 'on' : ''}" data-tog="italic" title="Italic">${ic('italic')}</button></div>
      ${ts.kind !== 'line' ? `<div class="segs" data-g="align">${['left', 'center', 'right'].map(a => `<button data-v="${a}" class="${(st.align || 'left') === a ? 'on' : ''}">${ic('a' + a)}</button>`).join('')}</div>` : ''}
      <h4>Colour</h4>${swatches(SW, st.color, 'tc')}
      ${ts.kind === 'line' ? `<button class="btn sm" data-reset style="justify-content:center">${ic('undo')}Restore original</button>` : ''}</div>`;
  }
  const curType = o?.type || S.tool;
  if (curType === 'highlight') h += `<div class="sec"><h4>Highlight colour</h4>${swatches(HL, o?.color || S.hl, 'hc')}</div>`;
  if (curType === 'whiteout') h += `<div class="sec"><h4>Fill</h4>${swatches(WO, o?.color || S.white, 'wc')}</div>`;
  if (curType === 'draw' || curType === 'ink') {
    const c = o?.color || S.ink.color, w = o?.width || S.ink.width;
    h += `<div class="sec"><h4>Pen</h4>${swatches(SW, c, 'ic')}<h4>Thickness<span>${w.toFixed(1)}</span></h4><input type="range" min="0.5" max="12" step="0.5" value="${w}" id="inkw"></div>`;
  }
  if (curType === 'shape') {
    const c = o?.color || S.shape.color, w = o?.width || S.shape.width;
    h += `<div class="sec"><h4>Outline</h4>${swatches(SW, c, 'sc')}<h4>Thickness<span>${w.toFixed(1)}</span></h4><input type="range" min="0.5" max="10" step="0.5" value="${w}" id="shw"></div>`;
  }
  if (o) h += `<div class="sec"><div class="btns"><button class="btn" data-dup>${ic('plus')}Duplicate</button><button class="btn" data-rm>${ic('trash')}Delete</button></div></div>`;

  // page section
  if (p) {
    const tags = p.blank ? '<span>Blank</span>' : (p.ocr ? '<span class="red">Scanned · OCR</span>' : p.hasText ? '<span class="ok">Digital text</span>' : '<span class="red">Scanned</span>');
    const n = Object.keys(p.edits).length + p.objects.length;
    const linesInfo = p.lstate === 'loading' ? 'Reading…' : p.lstate === 'ready' ? `${(p.lines || []).length} lines` : p.lstate === 'error' ? 'Failed' : '—';
    h += `<div class="sec"><h4>Page ${S.current + 1} of ${S.pages.length}</h4><div class="tags">${tags}${p.rotate ? `<span>${p.rotate}°</span>` : ''}</div>
      <div class="kv"><span>Size</span><b>${Math.round(p.w)} × ${Math.round(p.h)} pt</b></div>
      <div class="kv"><span>Editable text</span><b>${linesInfo}</b></div>
      <div class="kv"><span>Edits on page</span><b>${n}</b></div>
      <div class="btns">${p.blank ? '' : `<button class="btn" data-ocr>${ic('scan')}${p.ocr ? 'Re-read text (OCR)' : 'Read text with OCR'}</button>`}
        <div class="row"><button class="btn" data-prot style="flex:1;justify-content:center">${ic('rotate')}Rotate</button><button class="btn" data-pdel style="flex:1;justify-content:center">${ic('trash')}Delete</button></div></div></div>`;
  }
  const total = S.pages.reduce((a, p) => a + Object.keys(p.edits).length + p.objects.length, 0);
  h += `<div class="sec"><h4>Document</h4><div class="kv"><span>Pages</span><b>${S.pages.length}</b></div><div class="kv"><span>Total edits</span><b>${total}</b></div>
    <div class="hint">Shortcuts: <kbd>E</kbd> edit text · <kbd>T</kbd> add text · <kbd>H</kbd> highlight · <kbd>D</kbd> draw · <kbd>⌘Z</kbd> undo · <kbd>⌘S</kbd> save</div></div>`;
  insp.innerHTML = h;
  wireInsp(p, o, ts);
}
function wireInsp(p, o, ts) {
  const apply = fn => {   // change style on whatever is being edited
    if (ts?.kind === 'line') {
      const e = S.active.p.edits[S.active.id]; fn(e);
      const d = $(`.tl[data-id="${S.active.id}"]`, S.active.p.el); if (d) styleLine(d, e.line, e);
      if (document.activeElement !== d) { e.preview = null; requestPreview(S.active.p, S.active.id); }
      commit(); thumbTag(S.active.p);
    } else if (ts?.kind === 'obj') { fn(ts.obj); rerenderObj(S.sel.p, ts.obj); commit(); }
    else if (ts) fn(S.style);
    renderInsp();
  };
  $$('[data-g=family] button', insp).forEach(b => b.onmousedown = e => { e.preventDefault(); apply(t => t.family = b.dataset.v); });
  $$('[data-g=align] button', insp).forEach(b => b.onmousedown = e => { e.preventDefault(); apply(t => t.align = b.dataset.v); });
  $$('[data-tog]', insp).forEach(b => b.onmousedown = e => { e.preventDefault(); apply(t => t[b.dataset.tog] = !t[b.dataset.tog]); });
  $$('[data-step]', insp).forEach(b => b.onmousedown = e => { e.preventDefault(); apply(t => t.size = Math.max(4, Math.round((t.size + +b.dataset.step) * 2) / 2)); });
  const fs = $('#fsize', insp); if (fs) fs.onchange = () => { const v = parseFloat(fs.value); if (v > 2 && v < 400) apply(t => t.size = v); };
  $$('[data-tc]', insp).forEach(b => b.onmousedown = e => { e.preventDefault(); apply(t => t.color = b.dataset.tc); });
  $('[data-tc-custom]', insp)?.addEventListener('change', e => apply(t => t.color = e.target.value));
  $('[data-reset]', insp)?.addEventListener('mousedown', e => {
    e.preventDefault(); const a = S.active; delete a.p.edits[a.id]; S.active = null; renderOverlay(a.p); commit(); thumbTag(a.p); renderInsp();
  });
  const objOr = (key, setDefault) => v => { if (o) { o[key] = v; rerenderObj(S.sel.p, o); commit(); } else setDefault(v); renderInsp(); };
  const hc = objOr('color', v => S.hl = v), wc = objOr('color', v => S.white = v), icc = objOr('color', v => S.ink.color = v), scc = objOr('color', v => S.shape.color = v);
  $$('[data-hc]', insp).forEach(b => b.onclick = () => hc(b.dataset.hc)); $('[data-hc-custom]', insp)?.addEventListener('change', e => hc(e.target.value));
  $$('[data-wc]', insp).forEach(b => b.onclick = () => wc(b.dataset.wc)); $('[data-wc-custom]', insp)?.addEventListener('change', e => wc(e.target.value));
  $$('[data-ic]', insp).forEach(b => b.onclick = () => icc(b.dataset.ic)); $('[data-ic-custom]', insp)?.addEventListener('change', e => icc(e.target.value));
  $$('[data-sc]', insp).forEach(b => b.onclick = () => scc(b.dataset.sc)); $('[data-sc-custom]', insp)?.addEventListener('change', e => scc(e.target.value));
  const iw = $('#inkw', insp); if (iw) iw.onchange = () => objOr('width', v => S.ink.width = v)(+iw.value);
  const sw = $('#shw', insp); if (sw) sw.onchange = () => objOr('width', v => S.shape.width = v)(+sw.value);
  $('[data-rm]', insp)?.addEventListener('click', removeSel);
  $('[data-dup]', insp)?.addEventListener('click', () => {
    const c = JSON.parse(JSON.stringify(o)); c.id = nid('o');
    if (c.type === 'ink') c.paths = c.paths.map(pa => pa.map(([x, y]) => [x + 12, y + 12])); else c.rect = c.rect.map(v => v + 12);
    S.sel.p.objects.push(c); select(S.sel.p, c); commit();
  });
  $('[data-ocr]', insp)?.addEventListener('click', () => ensureLines(p, true).then(() => { if (S.tool !== 'edit') setTool('edit'); }));
  $('[data-prot]', insp)?.addEventListener('click', () => rotatePage(S.current));
  $('[data-pdel]', insp)?.addEventListener('click', () => deletePage(S.current));
}

// ------------------------------------------------------------------ saving
function buildSpec() {
  return S.pages.map(p => {
    const edits = [
      ...Object.values(p.edits).map(e => ({type: 'text_edit', line: e.line, text: e.text, size: e.size, color: e.color,
        family: e.family, bold: e.bold, italic: e.italic})),
      ...p.objects.map(o => o.type === 'text' ? {type: 'text', rect: o.rect, text: o.text, size: o.size, color: o.color, family: o.family, bold: o.bold, italic: o.italic, align: o.align}
        : o.type === 'ink' ? {type: 'ink', paths: o.paths, color: o.color, width: o.width}
        : o.type === 'shape' ? {type: 'rect', rect: o.rect, color: o.color, width: o.width}
        : o.type === 'image' ? {type: 'image', rect: o.rect, data: o.data}
        : {type: o.type, rect: o.rect, color: o.color}),
    ];
    return p.blank ? {blank: p.blank, rotate: p.rotate, edits} : {src: p.src, index: p.index, rotate: p.rotate, edits};
  });
}
async function save(format = 'pdf') {
  if (!S.pages.length) return;
  finishEditing(); $('#savemenu').classList.remove('on');
  const label = {pdf: 'PDF', docx: 'Word document', png: 'page images', txt: 'text'}[format];
  const done = busy(`Building ${label}`);
  try {
    const r = await edApi(() => `/api/pdf/${S.sid}/save`, () => ({method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pages: buildSpec(), name: $('#docname').value || 'edited', format, lib: S.lib})}));
    downloadJob(r.job);
    if (format === 'pdf') setDirty(false);
    toast(`Saved ${r.filename} · ${fmtSize(r.size)}`);
  } catch (e) { toast(e.message, true); } finally { done(); }
}
$('#save').onclick = () => save('pdf');
$('#savemore').onclick = e => { e.stopPropagation(); $('#savemenu').classList.toggle('on'); };
$$('#savemenu button').forEach(b => b.onclick = () => save(b.dataset.f));
addEventListener('click', e => { if (!e.target.closest('.split')) $('#savemenu').classList.remove('on'); });

// ------------------------------------------------------------------ keyboard
const KEYS = Object.fromEntries(TOOLS.filter(t => t !== '|').map(t => [t[3].toLowerCase(), t[0]]));
function handleKey(k, e) {
  const typing = document.activeElement?.isContentEditable || ['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName);
  if (k === 'undo') { if (typing) document.execCommand('undo'); else undo(); return true; }
  if (k === 'redo') { if (typing) document.execCommand('redo'); else redo(); return true; }
  if (k === 'save') { save('pdf'); return true; }
  if (typing) return false;
  if ((k === 'Backspace' || k === 'Delete') && S.sel) { removeSel(); return true; }
  if (k === 'Escape') { S.sel = null; S.active = null; S.pages.forEach(renderOverlay); renderInsp(); return true; }
  if (k.startsWith('Arrow') && S.sel) {
    const o = selObj(), d = e?.shiftKey ? 10 : 1, dx = k === 'ArrowLeft' ? -d : k === 'ArrowRight' ? d : 0, dy = k === 'ArrowUp' ? -d : k === 'ArrowDown' ? d : 0;
    if (o.type === 'ink') o.paths = o.paths.map(pa => pa.map(([x, y]) => [x + dx, y + dy])); else o.rect = o.rect.map((v, i) => v + (i % 2 ? dy : dx));
    rerenderObj(S.sel.p, o); commit(); return true;
  }
  if (KEYS[k.toLowerCase()] && !e?.metaKey && !e?.ctrlKey) { setTool(KEYS[k.toLowerCase()]); return true; }
  return false;
}
addEventListener('keydown', e => {
  if (currentView !== 'edit' || $('#sigmodal').classList.contains('on')) return;
  const m = e.metaKey || e.ctrlKey;
  let k = e.key;
  if (m && k.toLowerCase() === 'z') k = e.shiftKey ? 'redo' : 'undo';
  else if (m && k.toLowerCase() === 's') k = 'save';
  else if (m && (k === '=' || k === '+')) { e.preventDefault(); return setZoom(S.zoom * 1.2); }
  else if (m && k === '-') { e.preventDefault(); return setZoom(S.zoom / 1.2); }
  else if (m && k === '0') { e.preventDefault(); return fit(); }
  else if (m) return;
  if (handleKey(k, e)) e.preventDefault();
});
document.addEventListener('morphkey', e => { if (currentView === 'edit') handleKey(e.detail); else document.execCommand(e.detail); });
addEventListener('resize', () => { if (S.pages.length && currentView === 'edit') renderInsp(); });

setTool('edit'); updUndo(); layout();
})();
