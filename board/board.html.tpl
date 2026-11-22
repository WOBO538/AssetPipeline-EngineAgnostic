<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TA 作品集 · 进度看板</title>
<style>
  :root{
    --bg:#14161a; --panel:#1c1f26; --panel2:#101216; --line:#262b34; --line2:#2f3644;
    --txt:#d7dbe6; --dim:#8b93a7; --dim2:#5b6478; --blue:#2f6fed; --blue2:#3d7bf5;
    --green:#3fb950; --amber:#d29922; --red:#f85149; --violet:#a371f7;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);
       font-family:"Microsoft YaHei UI","Segoe UI",system-ui,sans-serif;font-size:13px}
  header{padding:18px 22px 10px}
  h1{margin:0 0 6px;font-size:19px;letter-spacing:.5px}
  .meta{color:var(--dim);font-size:12px}
  .meta b{color:var(--txt)}
  .headline{margin-top:8px;padding:9px 12px;border-left:3px solid var(--blue);
            background:#151b29;color:#a9c2ff;border-radius:0 8px 8px 0;font-size:12.5px}
  nav{display:flex;gap:8px;padding:12px 22px 0}
  nav button{background:var(--panel);color:var(--dim);border:1px solid var(--line);border-radius:8px;
             padding:7px 16px;cursor:pointer;font-size:13px}
  nav button:hover{background:#232833;color:var(--txt)}
  nav button.active{background:var(--blue);border-color:var(--blue);color:#fff;font-weight:600}
  main{padding:14px 22px 40px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:14px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
  .card h2{margin:0 0 12px;font-size:13px;color:var(--dim);letter-spacing:1px;font-weight:600}
  .row{display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px dashed #222833}
  .row:last-child{border-bottom:0}
  .row .name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .tag{font-size:11px;padding:1px 7px;border-radius:999px;border:1px solid var(--line2);color:var(--dim)}
  .tag.done{color:var(--green);border-color:#1d3a24;background:#12210f}
  .tag.doing{color:var(--amber);border-color:#3d3218;background:#241e0e}
  .tag.todo{color:var(--dim)}
  .tag.user{color:var(--violet);border-color:#2f2340;background:#1d152a}
  .bar{height:6px;background:var(--panel2);border-radius:4px;overflow:hidden;margin:4px 0 8px}
  .bar>i{display:block;height:100%;background:var(--blue);border-radius:4px}
  .proj{padding:10px 0;border-bottom:1px dashed #222833}
  .proj:last-child{border-bottom:0}
  .proj .top{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
  .proj .pname{font-weight:600}
  .proj .sub{color:var(--dim2);font-size:11.5px;margin-top:2px}
  .pct{color:var(--blue2);font-family:Consolas,monospace}
  .next{color:var(--dim);font-size:12px;margin-top:6px}
  .next b{color:var(--amber);font-weight:600}
  table{width:100%;border-collapse:collapse;font-size:12.5px}
  th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #222833;vertical-align:top}
  th{color:var(--dim);font-weight:600;font-size:11.5px;letter-spacing:.5px}
  .mono{font-family:Consolas,"Cascadia Mono",monospace}
  .days{font-size:11.5px;color:var(--dim2)}
  .days.hot{color:var(--red)}
  .dots{display:flex;gap:5px}
  .dot{width:14px;height:14px;border-radius:4px;border:1px solid var(--line2);background:var(--panel2)}
  .dot.done{background:#1d5c2b;border-color:#2ea043}
  .mats{display:grid;grid-template-columns:1fr;gap:7px}
  .mats .m{display:flex;gap:8px;align-items:center;min-width:0}
  .mats .m span:first-of-type{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12.5px}
  .mats .m .tag{flex:none;white-space:nowrap;font-family:Consolas,monospace}
  .warn{margin-top:10px;padding:8px 10px;border-radius:8px;background:#2a1d13;border:1px solid #5c3d1f;color:#e8b478;font-size:12px}
  .ok{margin-top:10px;padding:8px 10px;border-radius:8px;background:#12210f;border:1px solid #1d3a24;color:#7ee787;font-size:12px}
  /* gantt */
  .gantt{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--panel)}
  .gwrap{min-width:1180px;padding:14px 16px}
  .grow{display:flex;align-items:center;height:30px}
  .glabel{width:300px;flex:none;color:var(--dim);font-size:12px;padding-right:10px;
          overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .gtrack{position:relative;flex:1;height:18px;background:var(--panel2);border-radius:4px}
  .gbar{position:absolute;top:3px;height:12px;border-radius:6px;min-width:6px}
  .gbar.material{background:#8a6420}
  .gbar.contest{background:#1f4f8a}
  .gbar.jam{background:#68338a}
  .gpoint{position:absolute;top:4px;width:10px;height:10px;border-radius:50%;
          background:var(--blue2);box-shadow:0 0 0 2px #0d1017}
  .gpoint.done{background:var(--green)}
  .gtoday{position:absolute;top:0;bottom:0;width:2px;background:var(--red);opacity:.85}
  .gaxis{display:flex;margin:8px 0 6px 300px;color:var(--dim2);font-size:11px}
  .gaxis span{flex:none;width:calc(7px * 7);border-left:1px solid #222833;padding-left:3px}
  .glegend{display:flex;gap:16px;margin:4px 0 0 300px;color:var(--dim2);font-size:11.5px}
  .glegend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px}
  /* kanban */
  .kanban{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
  .col{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px}
  .col h3{margin:0 0 10px;font-size:12.5px;color:var(--dim);letter-spacing:.6px}
  .kcard{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:9px 10px;margin-bottom:8px}
  .kcard .t{font-size:12.5px}
  .kcard .b{margin-top:5px;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
  .chip{font-size:11px;color:var(--dim);border:1px solid var(--line2);border-radius:999px;padding:1px 7px}
  .empty{color:var(--dim2);font-size:12px;padding:6px 0}
</style>
</head>
<body>
<header>
  <h1>TA 作品集 · 进度看板</h1>
  <div class="meta">更新 <b>__UPDATED__</b> ｜ 今天 <b>__TODAY__</b> ｜ 数据源 <span class="mono">board/status.json</span></div>
  <div class="headline">__HEADLINE__</div>
</header>
<nav>
  <button data-tab="overview" class="active">总览</button>
  <button data-tab="gantt">甘特</button>
  <button data-tab="kanban">看板</button>
</nav>
<main>
  <section id="tab-overview">
    <div class="grid">
      <div class="card"><h2>硬闸（不过就重排）</h2><div id="gates"></div></div>
      <div class="card"><h2>项目队列</h2><div id="projects"></div></div>
      <div class="card"><h2>投递材料六件</h2><div id="materials"></div><div id="learning"></div></div>
      <div class="card"><h2>评审日历</h2><div id="reviews"></div></div>
    </div>
  </section>
  <section id="tab-gantt" hidden>
    <div class="gantt"><div class="gwrap">
      <div class="gaxis" id="gaxis"></div>
      <div id="grows"></div>
      <div class="glegend">
        <span><i style="background:#8a6420"></i>材料期</span>
        <span><i style="background:#1f4f8a"></i>赛事</span>
        <span><i style="background:#68338a"></i>Game Jam</span>
        <span><i style="background:#3d7bf5;border-radius:50%"></i>节点（蓝=未到，绿=已完成）</span>
        <span><i style="background:#f85149;width:2px;height:12px"></i>今天</span>
      </div>
    </div></div>
  </section>
  <section id="tab-kanban" hidden>
    <div class="kanban" id="kcols"></div>
  </section>
</main>
<script>
const STATUS = __STATUS_JSON__;

const $ = (id) => document.getElementById(id);
const pad = (n) => String(n).padStart(2, '0');
const D = (s) => new Date(s + 'T00:00:00');
/* 节点日期允许写 11-22 这种省略年份的短格式：11/12 月算 2026，01 及以后算 2027 */
const DN = (s) => {
  if (!s) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return D(s);
  const [m, d] = String(s).split('-').map(Number);
  if (!m || !d) return null;
  return new Date(m >= 11 ? 2026 : 2027, m - 1, d);
};
const today = D(STATUS.today);
const DAY = 86400000;
const daysBetween = (a, b) => Math.round((b - a) / DAY);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const STATUS_TXT = { done: '已完成', doing: '进行中', todo: '待办', blocked: '阻塞' };

/* ---------- 总览 ---------- */
function renderGates() {
  const el = $('gates');
  el.innerHTML = STATUS.gates.map(g => {
    const left = daysBetween(today, D(g.date));
    const cls = g.done ? 'done' : 'todo';
    const leftTxt = g.done ? '已过' : (left <= 0 ? '已到期' : left + ' 天');
    return `<div class="row"><span class="tag ${cls}">${g.done ? '✓' : '未过'}</span>
      <span class="name">${esc(g.name)}</span>
      <span class="days ${!g.done && left <= 7 ? 'hot' : ''}">${g.date}　${leftTxt}</span></div>`;
  }).join('');
}

function renderProjects() {
  $('projects').innerHTML = STATUS.projects.map(p => {
    const items = p.items || [];
    const next = items.find(i => i.status !== 'done');
    return `<div class="proj">
      <div class="top"><span class="pname">${esc(p.id)} · ${esc(p.name)}</span>
        <span class="pct">${p.progress}%</span></div>
      <div class="sub">${esc(p.subtitle)}　·　${esc(p.phase)}　·　<span class="tag ${p.status === '进行中' ? 'doing' : 'todo'}">${esc(p.status)}</span></div>
      <div class="bar"><i style="width:${Math.max(2, p.progress)}%"></i></div>
      <div class="next">下一件事：<b>${next ? esc(next.name) : '全部完成'}</b>${next && next.owner === 'user' ? ' <span class="tag user">需你手操</span>' : ''}</div>
    </div>`;
  }).join('');
}

function renderMaterials() {
  $('materials').innerHTML = '<div class="mats">' + STATUS.materials.map(m => {
    const done = m.status === 'done';
    return `<div class="m"><span class="dot ${done ? 'done' : ''}"></span>
      <span>${esc(m.name)}</span><span class="tag ${done ? 'done' : 'todo'}">${m.due}</span></div>`;
  }).join('') + '</div>';
}

function renderLearning() {
  const days = STATUS.learning.days || [];
  const bad = days.filter(d => d.learningMin > 0 && d.projectMin === 0).length;
  const box = bad > 0
    ? `<div class="warn">⚠ 有 ${bad} 天只学习没推进项目 —— 违反「${esc(STATUS.learning.rule)}」</div>`
    : `<div class="ok">✓ 项目优先规则：最近记录里没有「只学习、零项目」的日子</div>`;
  $('learning').innerHTML = box + `<div class="days" style="margin-top:8px">${esc(STATUS.learning.rule)}</div>`;
}

function renderReviews() {
  $('reviews').innerHTML = STATUS.reviews.map(r => {
    const left = daysBetween(today, D(r.date));
    return `<div class="row"><span class="tag ${r.done ? 'done' : 'todo'}">${r.done ? '✓' : '待办'}</span>
      <span class="name">${esc(r.name)}</span>
      <span class="days">${r.date}　${r.done ? '' : (left >= 0 ? left + ' 天' : '已过')}</span></div>`;
  }).join('');
}

/* ---------- 甘特 ---------- */
const G_START = D('2026-11-16'), G_PXD = 7;
function gx(dateStr) { const d = DN(dateStr); return d ? Math.round((d - G_START) / DAY) * G_PXD : 0; }
function renderGantt() {
  const rows = [];
  STATUS.projects.forEach(p => (p.items || []).forEach(it => {
    if (it.date) rows.push({ label: `${p.id} · ${it.name}`, date: it.date, point: it.date, done: it.status === 'done' });
  }));
  STATUS.events.forEach(e => rows.push({ label: e.name, start: e.start, end: e.end, type: e.type }));
  STATUS.reviews.forEach(r => rows.push({ label: '评审 · ' + r.name, point: r.date, done: r.done }));
  STATUS.gates.forEach(g => rows.push({ label: '硬闸 · ' + g.name, point: g.date, done: g.done }));

  const end = D('2027-08-31');
  const totalDays = daysBetween(G_START, end);
  const axis = [];
  for (let i = 0; i < totalDays; i += 7) {
    const d = new Date(G_START.getTime() + i * DAY);
    axis.push(`<span>${d.getMonth() + 1}/${d.getDate()}</span>`);
  }
  $('gaxis').innerHTML = axis.join('');

  $('grows').innerHTML = rows.map(r => {
    const todayX = gx(STATUS.today);
    let inner = '';
    if (r.point) {
      const x = gx(r.point);
      inner = `<span class="gpoint ${r.done ? 'done' : ''}" style="left:${x}px" title="${r.point}"></span>
               <span class="gtoday" style="left:${todayX}px"></span>`;
    } else {
      const x1 = gx(r.start), x2 = gx(r.end);
      inner = `<span class="gbar ${r.type}" style="left:${x1}px;width:${Math.max(8, x2 - x1)}px" title="${r.start} → ${r.end}"></span>
               <span class="gtoday" style="left:${todayX}px"></span>`;
    }
    return `<div class="grow"><span class="glabel" title="${esc(r.label)}">${esc(r.label)}${r.date ? ' <span class="mono" style="color:#5b6478">' + esc(r.date) + '</span>' : ''}</span>
      <span class="gtrack">${inner}</span></div>`;
  }).join('');
}

/* ---------- 看板 ---------- */
function renderKanban() {
  const cols = { todo: [], doing: [], done: [] };
  STATUS.projects.forEach(p => (p.items || []).forEach(it => {
    const k = it.status === 'done' ? 'done' : (it.status === 'doing' ? 'doing' : 'todo');
    cols[k].push({ p, it });
  }));
  const titles = { todo: '待办 TODO', doing: '进行中 DOING', done: '已完成 DONE' };
  $('kcols').innerHTML = ['todo', 'doing', 'done'].map(k => {
    const cards = cols[k].length ? cols[k].map(({ p, it }) => `
      <div class="kcard"><div class="t">${esc(it.name)}</div>
        <div class="b"><span class="chip">${esc(p.id)}</span>
          ${it.date ? `<span class="chip">${esc(it.date)}</span>` : ''}
          ${it.owner === 'user' ? '<span class="tag user">你手操</span>' : ''}</div></div>`).join('')
      : '<div class="empty">（空）</div>';
    return `<div class="col"><h3>${titles[k]} · ${cols[k].length}</h3>${cards}</div>`;
  }).join('');
}

document.querySelectorAll('nav button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    ['overview', 'gantt', 'kanban'].forEach(t => { $('tab-' + t).hidden = (t !== btn.dataset.tab); });
  });
});

renderGates(); renderProjects(); renderMaterials(); renderLearning(); renderReviews();
renderGantt(); renderKanban();
</script>
</body>
</html>
