// Morph — Convert view.
(() => {
const list = $('#list'), drop = $('#drop'), input = $('#file');
const items = [];
const KIND = {image:'Image',vector:'Vector',pdf:'PDF',document:'Document',slides:'Slides',spreadsheet:'Sheet',
  table:'Table',data:'Data',text:'Text',audio:'Audio',video:'Video',unknown:'File'};
const EDITABLE = ['pdf','image','vector','document','slides','spreadsheet','table','text'];
const ORDER = ['Document','Slides','Sheet','Data','Text','Video','Audio','Image','AI','Tools'];
// the file's own family is listed first, then ORDER
const HOME = {pdf:'Document',document:'Document',slides:'Slides',spreadsheet:'Sheet',table:'Sheet',data:'Data',
  text:'Document',image:'Image',vector:'Image',audio:'Audio',video:'Video'};
// the few targets most people want, shown up front (first 4 that exist for the file)
const PICKS = {
  pdf:['docx','pdf_png','pdf_small','pdf_ocr','txt'], document:['pdf','txt','md','pdf_png'],
  slides:['pdf','pdf_png','pdf_jpg'], spreadsheet:['pdf','csv','xlsx','json'], table:['xlsx','json','pdf','csv'],
  data:['csv','xlsx','md'], text:['pdf','docx','html','md'], image:['jpg','png','pdf','webp','ocr_txt'],
  vector:['png','pdf','jpg'], audio:['mp3','wav','m4a','flac','ogg'], video:['mp4','mov','mp3','vgif','thumb']};
const NICE = {docx:'Word', xlsx:'Excel'};          // friendlier names for the formats people ask for by app
const GNAME = {AI:'OCR'};                          // group headings as shown

VIEWS.convert = {show() {}};
const TOUCH = matchMedia('(hover: none)').matches;   // phones/tablets: "tap", not "drop or click"
sync();

// drag & drop anywhere while Convert is visible
let depth = 0;
addEventListener('dragenter', e => { if (currentView !== 'convert') return; e.preventDefault(); depth++; drop.classList.add('over'); });
addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; drop.classList.remove('over'); } });
addEventListener('dragover', e => e.preventDefault());
addEventListener('drop', e => {
  e.preventDefault(); depth = 0; drop.classList.remove('over');
  if (currentView === 'convert' && e.dataTransfer.files.length) add(e.dataTransfer.files);
});
input.onchange = () => { add(input.files); input.value = ''; };
addEventListener('paste', e => { const f = e.clipboardData?.files; if (currentView === 'convert' && f?.length) add(f); });
$$('#features .feat').forEach(f => f.onclick = () => f.dataset.go === 'edit' ? go('edit') : input.click());

window.add = add;   // used by the native app when files are dropped on the Dock icon
function add(files) {
  [...files].forEach(f => {
    const it = {file: f, id: null, target: null, targets: [], state: 'uploading', p: 0};
    items.push(it); render(it); upload(it);
  });
  sync();
}

function sync() {
  const n = items.length;
  $('#listwrap').hidden = !n;
  $('#features').hidden = n > 0;
  drop.classList.toggle('compact', n > 0);
  $('#count').textContent = `${String(n).padStart(2,'0')} ${n === 1 ? 'file' : 'files'}`;
  $('.drop-t').textContent = n ? 'Add more files' : TOUCH ? 'Tap to choose files' : 'Drop files, or click to choose';
  // "convert all to" — formats every ready file can produce
  const ready = items.filter(i => i.targets.length && i.state !== 'uploading');
  let common = null;
  ready.forEach(i => { const ex = new Set(i.targets.map(t => t.label));
    common = common ? common.filter(x => ex.has(x)) : [...ex]; });
  const box = $('#allto');
  box.innerHTML = ready.length > 1 && common?.length
    ? `<span class="grp">All to</span>` + common.slice(0, 6).map(l => `<button class="chip" data-l="${esc(l)}">${esc(l)}</button>`).join('') : '';
  $$('.chip', box).forEach(c => c.onclick = () => {
    items.forEach(i => { const t = i.targets.find(t => t.label === c.dataset.l);
      if (t && i.state !== 'running') { i.target = t.id; i.state = 'ready'; paint(i); } });
    items.filter(i => i.state === 'ready').forEach(convert);
  });
  const done = items.filter(i => i.state === 'done');
  $('#zipall').hidden = done.length < 2;
}

function upload(it) {
  const fd = new FormData(); fd.append('file', it.file);
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/upload');
  xhr.upload.onprogress = e => e.lengthComputable && progress(it, e.loaded / e.total, 'Uploading to local engine');
  xhr.onload = () => {
    if (xhr.status !== 200) { it.state = 'error'; it.error = 'Upload failed'; paint(it); return sync(); }
    const r = JSON.parse(xhr.responseText);
    const home = HOME[r.kind], rank = g => g === home ? -1 : ORDER.indexOf(g);
    const targets = r.targets.map((t, i) => ({...t, label: NICE[t.id] || t.label, i}))
      .sort((a, b) => (rank(a.group) - rank(b.group)) || a.i - b.i);
    const ids = new Set(targets.map(t => t.id));
    let picks = (PICKS[r.kind] || []).filter(id => ids.has(id)).slice(0, 4);
    if (targets.length <= 5) picks = targets.map(t => t.id);          // few options: just show them all
    else if (!picks.length) picks = targets.slice(0, 4).map(t => t.id);
    Object.assign(it, {id: r.id, kind: r.kind, targets, picks, more: false, state: 'ready', target: picks[0] || null});
    paint(it); sync(); loadThumb(it);
  };
  xhr.onerror = () => { it.state = 'error'; it.error = 'Engine offline — is Morph running?'; paint(it); };
  xhr.send(fd);
}

function loadThumb(it) {
  if (!['pdf','image','video','vector'].includes(it.kind)) return;
  const img = new Image();
  img.onload = () => { it.thumb = img.src; const t = $('.thumb', it.el); if (t) t.innerHTML = `<img src="${img.src}">`; };
  img.src = '/api/thumb/' + it.id;
}

function render(it) {
  const el = document.createElement('div');
  el.className = 'item'; it.el = el; list.appendChild(el); paint(it);
}

function dots(p) {
  const n = 40, indet = p == null, f = indet ? 0 : Math.round(p * n);
  return `<div class="dots ${indet?'indet':''}">${Array.from({length:n},(_,i) =>
    `<i class="${i<f?'f':''}" style="${indet?`animation-delay:${i*35}ms`:''}"></i>`).join('')}</div>`;
}

function paint(it) {
  const ext = (it.file.name.split('.').pop() || '').slice(0, 5).toUpperCase();
  const k = it.kind ? KIND[it.kind] : '…';
  let chips = '';
  const chip = t => `<button class="chip ${t.group==='AI'||t.group==='Tools'?'ai':''} ${t.id===it.target?'on':''}" data-t="${t.id}">${esc(t.label)}</button>`;
  if (it.targets.length && it.state !== 'uploading') {
    const byId = Object.fromEntries(it.targets.map(t => [t.id, t]));
    const shown = it.picks.map(id => byId[id]);
    // a format chosen from the full list stays visible up front when the list is closed
    if (!it.more && it.target && !it.picks.includes(it.target)) shown.push(byId[it.target]);
    const extra = it.targets.length - it.picks.length;
    chips = `<div class="picks"><span class="to">To</span>${shown.map(chip).join('')}` +
      (extra > 0 ? `<button class="more ${it.more?'open':''}" data-more aria-expanded="${it.more}">${it.more ? 'Fewer' : `+${extra} more`}${ic('chev')}</button>` : '') +
      `</div>`;
    if (it.more && extra > 0) {
      const groups = [];
      for (const t of it.targets) {
        const g = groups.at(-1);
        if (g && g.name === t.group) g.list.push(t); else groups.push({name: t.group, list: [t]});
      }
      chips += `<div class="allfmts">${groups.map(g =>
        `<div class="frow"><span class="grp">${esc(GNAME[g.name] || g.name)}</span><div class="row">${g.list.map(chip).join('')}</div></div>`).join('')}</div>`;
    }
  } else if (it.state === 'ready') chips = `<div class="none">No conversions available for this file type yet.</div>`;

  const canEdit = EDITABLE.includes(it.kind) && it.id;
  const editBtn = canEdit ? `<button class="btn sm" data-edit>${ic('pen')}Edit</button>` : '';
  let action = '', status = '';
  if (it.state === 'uploading') status = `<div class="msg">Uploading</div>${dots(it.p)}`;
  else if (it.state === 'ready' && it.targets.length) {
    const t = it.targets.find(x => x.id === it.target);
    action = `<button class="btn red" data-go>Convert ${ic('arrow')}</button>${editBtn}`;
    status = `<div class="msg">${fmtSize(it.file.size)} → ${t ? t.label : ''}</div>`;
  } else if (it.state === 'running') {
    action = `<button class="btn" disabled>Working</button>`;
    status = `<div class="msg">${esc(it.msg || 'Starting')}</div>${dots(it.p)}`;
  } else if (it.state === 'done') {
    const pdfOut = /\.pdf$/i.test(it.outName || '');
    action = `<button class="btn inv" data-dl>${ic('down')}Download</button>` +
      (pdfOut ? `<button class="btn sm" data-editout>${ic('pen')}Edit result</button>` : editBtn);
    status = `<div class="msg">${esc(it.outName)} · ${fmtSize(it.outSize)} · ${(it.ms/1000).toFixed(1)}s</div>${dots(1)}`;
  } else if (it.state === 'error') {
    action = (it.targets.length ? `<button class="btn red" data-go>Retry</button>` : '') + editBtn;
    status = `<div class="msg err">${esc(it.error || 'Something went wrong')}</div>`;
  }
  it.el.innerHTML = `
    <div class="thumb">${it.thumb ? `<img src="${it.thumb}">` : `<span class="ext">${esc(ext)}</span>`}<span class="kind">${k}</span></div>
    <div class="body">
      <div class="head"><div class="meta"><div class="name" title="${esc(it.file.name)}">${esc(it.file.name)}</div>
        <div class="mono">${k} · ${fmtSize(it.file.size)}</div></div>
        <button class="x" title="Remove" data-x>${ic('x')}</button></div>
      ${chips ? `<div class="fmts">${chips}</div>` : ''}
      ${(action || status) ? `<div class="foot">${action}<div class="status2">${status}</div></div>` : ''}
    </div>`;
  $$('.chip', it.el).forEach(c => c.onclick = () => {
    if (it.state === 'running') return;
    it.target = c.dataset.t; it.state = 'ready'; paint(it); sync();
  });
  $('[data-more]', it.el) && ($('[data-more]', it.el).onclick = () => { it.more = !it.more; paint(it); });
  $('[data-go]', it.el) && ($('[data-go]', it.el).onclick = () => convert(it));
  $('[data-dl]', it.el) && ($('[data-dl]', it.el).onclick = () => downloadJob(it.job));
  $('[data-edit]', it.el) && ($('[data-edit]', it.el).onclick = () => { go('edit'); VIEWS.edit.openFrom({upload: it.id}, it.file.name); });
  $('[data-editout]', it.el) && ($('[data-editout]', it.el).onclick = () => { go('edit'); VIEWS.edit.openFrom({job: it.job}, it.outName); });
  $('[data-x]', it.el).onclick = () => { items.splice(items.indexOf(it), 1); it.el.remove(); it.dead = true; sync(); };
}

function progress(it, p, msg) {
  it.p = p; it.msg = msg;
  const st = $('.status2', it.el);
  if (st && (it.state === 'running' || it.state === 'uploading'))
    st.innerHTML = `<div class="msg">${esc(msg)}${p != null ? ` · ${Math.round(p*100)}%` : ''}</div>${dots(p)}`;
}

async function convert(it) {
  if (!it.id || !it.target || it.state === 'running') return;
  it.state = 'running'; it.p = null; it.msg = 'Starting'; paint(it);
  const t0 = performance.now();
  try {
    const {job} = await api('/api/convert', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id: it.id, target: it.target})});
    it.job = job;
    while (!it.dead) {
      await sleep(400);
      const s = await api('/api/jobs/' + job);
      if (s.status === 'running') progress(it, s.progress, s.message);
      else if (s.status === 'done') {
        Object.assign(it, {state:'done', outName: s.filename, outSize: s.size, ms: performance.now() - t0});
        paint(it); break;
      } else { it.state = 'error'; it.error = s.error; paint(it); break; }
    }
  } catch (e) { it.state = 'error'; it.error = e.message; paint(it); }
  sync();
}

$('#all').onclick = () => items.filter(i => i.state === 'ready').forEach(convert);
$('#clear').onclick = () => { items.forEach(i => i.dead = true); items.length = 0; list.innerHTML = ''; sync(); };
$('#zipall').onclick = async () => {
  try {
    const {job} = await api('/api/zip', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({jobs: items.filter(i => i.state === 'done').map(i => i.job)})});
    downloadJob(job);
  } catch (e) { toast(e.message, true); }
};
})();
