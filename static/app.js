// Morph — shared shell: icons, routing, theme, toasts, helpers.
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtSize = b => b < 1024 ? b + ' B' : b < 1048576 ? (b/1024).toFixed(1) + ' KB'
  : b < 1073741824 ? (b/1048576).toFixed(1) + ' MB' : (b/1073741824).toFixed(2) + ' GB';
const sleep = ms => new Promise(r => setTimeout(r, ms));

const ICONS = {
  convert:'<path d="M4 7h13l-3-3M20 17H7l3 3"/>',
  pen:'<path d="M14.5 4.5l5 5L9 20H4v-5z"/><path d="M12.5 6.5l5 5"/>',
  moon:'<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
  sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  plus:'<path d="M12 5v14M5 12h14"/>', minus:'<path d="M5 12h14"/>', x:'<path d="M6 6l12 12M18 6L6 18"/>',
  arrow:'<path d="M7 17L17 7M9 7h8v8"/>', bolt:'<path d="M13 3L5 14h6l-1 7 8-11h-6z"/>',
  down:'<path d="M12 4v12M6 11l6 6 6-6M5 20h14"/>', chev:'<path d="M6 9l6 6 6-6"/>',
  sliders:'<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
  file:'<path d="M14 3H6v18h12V7z"/><path d="M14 3v4h4"/>', page:'<rect x="5" y="3" width="14" height="18" rx="1.5"/>',
  undo:'<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>', redo:'<path d="M15 14l5-5-5-5"/><path d="M20 9H10a6 6 0 0 0 0 12h3"/>',
  cursor:'<path d="M5 3l14 7-6 2-2 6z"/>',
  textedit:'<path d="M4 6V4h12v2M10 4v14M7 18h6"/><path d="M17 13v8M15 13h4M15 21h4"/>',
  addtext:'<path d="M5 7V5h11v2M10.5 5v13M8 18h5"/><path d="M19 13v6M16 16h6"/>',
  highlight:'<path d="M9 11l6-6 4 4-6 6"/><path d="M9 11l-3 3v3h3l3-3"/><path d="M4 21h16" stroke-width="2.6" opacity=".45"/>',
  draw:'<path d="M3 17c3-6 5-6 7-2s4 4 6-1 3-6 5-3"/>',
  whiteout:'<rect x="4" y="7" width="16" height="10" rx="1.5"/><path d="M8 12h8" opacity=".4"/>',
  redact:'<rect x="4" y="7" width="16" height="10" rx="1.5" fill="currentColor"/>',
  shape:'<rect x="4" y="5" width="16" height="14" rx="1"/>',
  image:'<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>',
  sign:'<path d="M3 17c2-4 4-9 6-9 3 0-2 9 1 9 2 0 3-4 5-4 1.5 0 1 3 3 3h2"/><path d="M3 21h18" opacity=".4"/>',
  rotate:'<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
  trash:'<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  scan:'<path d="M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4"/><path d="M8 10h8M8 14h5"/>',
  bold:'<path d="M7 5h6a3.5 3.5 0 0 1 0 7H7zM7 12h7a3.5 3.5 0 0 1 0 7H7z"/>',
  italic:'<path d="M10 5h8M6 19h8M14 5l-4 14"/>',
  aleft:'<path d="M4 6h16M4 10h10M4 14h16M4 18h10"/>', acenter:'<path d="M4 6h16M7 10h10M4 14h16M7 18h10"/>',
  aright:'<path d="M4 6h16M10 10h10M4 14h16M10 18h10"/>', drop:'<path d="M12 3s6 7 6 11a6 6 0 0 1-12 0c0-4 6-11 6-11z"/>',
  check:'<path d="M5 12l5 5 9-10"/>', back:'<path d="M15 5l-7 7 7 7"/>', search:'<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>', edit2:'<path d="M4 20h4L19 9l-4-4L4 16z"/>',
};
function icons(root = document) {
  $$('svg[data-i]', root).forEach(s => {
    if (s.dataset.done === s.dataset.i) return;
    s.setAttribute('viewBox', '0 0 24 24'); s.classList.add('i');
    s.innerHTML = ICONS[s.dataset.i] || ''; s.dataset.done = s.dataset.i;
  });
}
const ic = (n, extra = '') => `<svg class="i" viewBox="0 0 24 24" ${extra}>${ICONS[n] || ''}</svg>`;

function toast(msg, err = false, ms = 3200) {
  // the same message already on screen is not stacked again (one failure can hit many requests at once)
  if ([...$('#toasts').children].some(x => x.dataset.msg === msg && !x.classList.contains('out'))) return;
  const t = document.createElement('div');
  t.dataset.msg = msg;
  t.className = 'toast' + (err ? ' err' : '');
  t.innerHTML = (err ? '' : ic('check')) + esc(msg);
  $('#toasts').appendChild(t);
  setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 300); }, ms);
}

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.headers.get('content-type')?.includes('json') ? r.json() : r;
}

function downloadJob(job) {
  const a = document.createElement('a');
  a.href = '/api/download/' + job; a.download = '';
  document.body.appendChild(a); a.click(); a.remove();
}

// ---- theme
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  $('#theme svg').dataset.i = t === 'dark' ? 'sun' : 'moon'; icons($('#theme'));
  localStorage.morphTheme = t;
}
setTheme(localStorage.morphTheme || 'dark');
$('#theme').onclick = () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');

// ---- routing
const VIEWS = {};
function go(v) {
  if (!VIEWS[v]) v = 'convert';
  $$('.view').forEach(x => x.classList.toggle('on', x.id === 'v-' + v));
  $$('#nav button').forEach(b => b.classList.toggle('on', b.dataset.v === v));
  const on = $('#nav button.on'), pill = $('#nav .pill');
  if (on) { pill.style.left = on.offsetLeft + 'px'; pill.style.width = on.offsetWidth + 'px'; }
  if (location.hash !== '#' + v) history.replaceState(null, '', '#' + v);
  window.currentView = v;
  VIEWS[v].show?.();
}
$$('#nav button').forEach(b => b.onclick = () => go(b.dataset.v));
addEventListener('resize', () => go(window.currentView));

// ---- engine status
fetch('/api/health').then(r => r.json()).then(({engines, mode, limits = {}}) => {
  window.morphMode = mode;
  $('#status').classList.add('ok');
  $('#status span').textContent = mode === 'web' ? 'Private workspace' : 'On-device';
  if (mode !== 'web' && /Windows/.test(navigator.userAgent)) $('#wherenote b').textContent = 'on this PC';
  if (mode === 'web') {   // offer the desktop app, the visitor's own system first
    const ua = navigator.userAgent, os = /Windows/.test(ua) ? 'win' : /Mac/.test(ua) ? 'mac' : '';
    const mine = $(`#getapp [data-os="${os}"]`);
    if (mine) { mine.classList.add('mine'); mine.parentNode.prepend(mine); }
    window.morphGetApp = true; $('#getapp').hidden = !!document.querySelector('#list .item');
  }
  if (mode === 'web') {
    const h = limits.ttl_hours || 2, d = limits.library_days || 14;
    $('#wherenote').innerHTML = `Your files are <b>private to you</b> and deleted automatically.`;
    $('#stat3').innerHTML = `<b>${limits.upload_mb || 100} MB</b> per file`;
    $('#privacy').textContent = `Only you can see your files · converted files deleted after ${h}h · Recent kept ${d} days`;
    $('#status').title = 'Files are processed on this server, visible only to your browser, and deleted automatically.';
  }
  const names = {libreoffice:'LibreOffice',pandoc:'Pandoc',ffmpeg:'FFmpeg',ghostscript:'Ghostscript',
    tesseract:'Tesseract OCR',rsvg:'librsvg'};
  $('#eng').innerHTML = Object.entries(engines).map(([k,v]) =>
    `<span class="${v?'':'off'}">${names[k]||k}</span>`).join('') + '<span>PyMuPDF</span>';
  $('#stat2 b').textContent = Object.values(engines).filter(Boolean).length + 1;   // + PyMuPDF
}).catch(() => { $('#status span').textContent = 'Engine offline'; });

// native shell hooks (Morph.app menu items call these)
window.morphOpen = () => (window.currentView === 'edit' ? VIEWS.edit.open() : $('#file').click());
window.morphKey = k => document.dispatchEvent(new CustomEvent('morphkey', {detail: k}));

addEventListener('DOMContentLoaded', () => { icons(); go((location.hash || '#convert').slice(1)); });
document.fonts?.ready.then(() => go(window.currentView));
addEventListener('hashchange', () => go(location.hash.slice(1)));
