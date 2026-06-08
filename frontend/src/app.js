const API = window.location.origin;
const canvas = document.getElementById('office');
const ctx = canvas.getContext('2d');
const hint = document.getElementById('hint');
const simBadge = document.getElementById('simBadge');
const statusEl = document.getElementById('status');
const llmStatusEl = document.getElementById('llmStatus');
const companyStatusEl = document.getElementById('companyStatus');
const eventsEl = document.getElementById('events');
const memoriesEl = document.getElementById('memories');
const tracesEl = document.getElementById('traces');
const tasksEl = document.getElementById('tasks');
const resourcesEl = document.getElementById('resources');
const bossPanelEl = document.getElementById('bossPanel');
const bossTaskBtn = document.getElementById('bossTaskBtn');
const missionReportBtn = document.getElementById('missionReportBtn');
const bossDialog = document.getElementById('bossDialog');
const bossTaskTitle = document.getElementById('bossTaskTitle');
const bossTaskDesc = document.getElementById('bossTaskDesc');
const bossTaskOutcome = document.getElementById('bossTaskOutcome');
const submitBossTaskBtn = document.getElementById('submitBossTaskBtn');
const playerNameEl = document.getElementById('playerName');
const chatDialog = document.getElementById('chatDialog');
const chatNpcName = document.getElementById('chatNpcName');
const chatNpcRole = document.getElementById('chatNpcRole');
const chatLog = document.getElementById('chatLog');
const chatInput = document.getElementById('chatInput');
const sendBtn = document.getElementById('sendBtn');
const tickBtn = document.getElementById('tickBtn');
const llmTickBtn = document.getElementById('llmTickBtn');
const autoBtn = document.getElementById('autoBtn');
const standupBtn = document.getElementById('standupBtn');
const companyBtn = document.getElementById('companyBtn');
const resetBtn = document.getElementById('resetBtn');

const AUTO_TICK_MS = 760;
const SIDE_PANEL_EVERY_TICKS = 2;
const LLM_STATUS_EVERY_TICKS = 6;

let profiles = [];
let states = [];
let visualStates = new Map();
let zones = [];
let currentNpc = null;
let agentTraces = [];
let tasks = [];
let autoTimer = null;
let tickInFlight = false;
let lastTick = 0;
let lastFrameTs = performance.now();
let lastLlmStatusTick = -99;
let lastAutoStartedAt = 0;
const player = { x: 96, y: 92, r: 15, speed: 3.6 };
const keys = new Set();

async function getJSON(path, options) {
  const resp = await fetch(API + path, options);
  if (!resp.ok) throw new Error(await resp.text());
  return await resp.json();
}

async function boot() {
  const [npcData, mapData] = await Promise.all([getJSON('/npcs'), getJSON('/office/map')]);
  setProfiles(npcData.npcs);
  zones = mapData.zones;
  await refreshLLMStatus();
  await refreshBossMissions();
  await refreshCompanyStatus();
  await refreshStatus();
  await refreshTasks();
  await refreshResources();
  await refreshEvents();
  requestAnimationFrame(loop);
}

async function refreshLLMStatus() {
  try {
    const [data, batch] = await Promise.all([
      getJSON('/office/llm-stats'),
      getJSON('/office/parallel-decision-stats').catch(() => ({}))
    ]);
    const online = data.active_mode === 'real_llm';
    const modeText = online ? 'DeepSeek：在线' : 'DeepSeek：演示模式';
    const mode = data.decision_mode || data.office_decision_mode || 'llm_parallel';
    const concurrency = data.parallel_concurrency ?? data.office_llm_parallel_concurrency ?? 8;
    const timeout = data.decision_timeout_seconds ?? data.office_llm_decision_timeout_seconds ?? 25;
    const firstError = (batch.errors && batch.errors[0] && batch.errors[0].error) || data.last_error || '';
    const lc = data.langchain_available ? 'LangChain 可用' : 'LangChain 可选依赖未安装';
    const lg = data.langgraph_available ? 'LangGraph 可用' : 'LangGraph 可选';
    const toolCount = (data.last_tool_traces || []).length;
    llmStatusEl.innerHTML = `
      <div class="info-card service-card">
        <div class="row between"><b>${escapeHtml(modeText)}</b><span class="dot ${online ? 'ok' : 'muted'}"></span></div>
        <small>${escapeHtml(data.provider)} · ${escapeHtml(data.model)} · ${escapeHtml(lc)} · ${escapeHtml(lg)}</small><br>
        <small>行为模式：${escapeHtml(mode)} · 并发 ${concurrency} · 超时 ${timeout}s</small><br>
        <small>最近批次：${batch.requested_agents || 0} 人 / 调用 ${batch.llm_scheduled || 0} / 完成 ${batch.llm_completed || 0} / 兜底 ${batch.fallback_count || 0} · ${batch.latency_ms || 0}ms</small><br>
        <small>真实请求 ${data.real_requests || 0} · 降级 ${data.degraded_requests || 0} · 最近 ${data.last_latency_ms || 0}ms · 工具轨迹 ${toolCount}</small>
        ${firstError ? `<br><small class="warn">诊断：${escapeHtml(firstError).slice(0, 180)}</small>` : ''}
      </div>
    `;
  } catch (err) {
    llmStatusEl.innerHTML = `<div class="info-card">模型服务状态读取失败：${escapeHtml(err.message)}</div>`;
  }
}

async function refreshBossMissions() {
  if (!bossPanelEl) return;
  try {
    const data = await getJSON('/boss/missions?limit=5');
    const missions = data.missions || [];
    const active = missions.find(m => m.status === 'active') || missions[0];
    if (!active) {
      bossPanelEl.innerHTML = `<div class="info-card boss-card"><b>等待老板发布主任务</b><br><small>发布后，Agent 会自行分配任务并按需调用工具。</small></div>`;
      return;
    }
    const tasks = active.tasks || [];
    const done = tasks.filter(t => t.status === 'done').length;
    bossPanelEl.innerHTML = `
      <div class="info-card boss-card">
        <div class="row between"><b>#${active.id} ${escapeHtml(active.title)}</b><span>${escapeHtml(active.status)}</span></div>
        <small>任务 ${done}/${tasks.length} 完成 · ${active.ready_for_report ? '可生成报告' : '执行中'}</small><br>
        ${active.report_text ? `<small>报告已生成：资源 #${active.report_resource_id || '-'}</small>` : '<small>完成后会自动形成报告，也可手动生成阶段报告。</small>'}
      </div>
    `;
  } catch (err) {
    bossPanelEl.innerHTML = `<div class="info-card">老板任务读取失败：${escapeHtml(err.message)}</div>`;
  }
}

async function refreshCompanyStatus() {
  if (!companyStatusEl) return;
  try {
    const data = await getJSON('/company/status');
    const latestTx = (data.recent_transactions || [])[0];
    const candidate = (data.hiring_pipeline || [])[0];
    companyStatusEl.innerHTML = `
      <div class="info-card company-card">
        <div class="row between"><b>公司规模 ${data.agent_count}/${data.max_agents}</b><span>任务 ${data.active_task_count}</span></div>
        <small>阻塞 ${data.blocked_task_count} · 面试中 ${data.open_candidate_count} · 动态入职 ${data.hired_dynamic_count}</small><br>
        ${candidate ? `<small>候选：${escapeHtml(candidate.name)} / ${escapeHtml(candidate.role)} · ${escapeHtml(candidate.status)}</small><br>` : ''}
        ${latestTx ? `<small>最近事务 #${latestTx.id} ${escapeHtml(latestTx.kind)} · ${escapeHtml(latestTx.status)}</small>` : '<small>暂无招聘/扩张事务</small>'}
      </div>
    `;
  } catch (err) {
    companyStatusEl.innerHTML = `<div class="info-card">公司状态读取失败：${escapeHtml(err.message)}</div>`;
  }
}

async function refreshStatus() {
  const [data, npcData] = await Promise.all([
    getJSON('/npcs/status'),
    getJSON('/npcs').catch(() => ({ npcs: [] }))
  ]);
  setProfiles(npcData.npcs || []);
  setStates(data.npcs || []);
  renderStatus();
}

function setProfiles(newProfiles) {
  const byId = new Map(profiles.map(p => [p.npc_id, p]));
  for (const p of (newProfiles || [])) {
    if (!p || !p.npc_id) continue;
    byId.set(p.npc_id, { ...byId.get(p.npc_id), ...p });
  }
  profiles = Array.from(byId.values());
}

function syncProfilesFromStates(newStates) {
  const byId = new Map(profiles.map(p => [p.npc_id, p]));
  let changed = false;
  for (const s of (newStates || [])) {
    if (!s || !s.npc_id || byId.has(s.npc_id)) continue;
    byId.set(s.npc_id, {
      npc_id: s.npc_id,
      name: s.name || '新同事',
      role: s.role || '动态 Agent',
      title: s.role || '动态 Agent',
      department: s.department || '新团队',
      position: s.position || { x: 930, y: 500 },
      home_zone: s.target_zone || 'open_workspace',
      color: colorFromId(s.npc_id),
      goals: [], skills: [], responsibilities: [],
      speaking_style: '作为动态加入的新同事，结合当前任务和公共记忆回答。'
    });
    changed = true;
  }
  if (changed) profiles = Array.from(byId.values());
}

function setStates(newStates) {
  states = newStates;
  syncProfilesFromStates(states);
  const now = performance.now();
  for (const s of states) {
    const pos = s.position || { x: 0, y: 0 };
    const prev = visualStates.get(s.npc_id);
    if (!prev) {
      visualStates.set(s.npc_id, {
        x: pos.x, y: pos.y,
        serverX: pos.x, serverY: pos.y,
        lastServerX: pos.x, lastServerY: pos.y,
        updatedAt: now,
        trail: [{ x: pos.x, y: pos.y, t: now }],
      });
    } else {
      prev.lastServerX = prev.serverX;
      prev.lastServerY = prev.serverY;
      prev.serverX = pos.x;
      prev.serverY = pos.y;
      prev.updatedAt = now;
    }
  }
}

function renderStatus() {
  statusEl.innerHTML = states.map(s => {
    const pct = Math.round((s.action_progress || 0) * 100);
    const phase = phaseName(s.action_phase);
    return `
      <div class="info-card status-card">
        <div class="row between"><b>${escapeHtml(s.name)}</b><span>${escapeHtml(s.department || s.role)}</span></div>
        <small>${escapeHtml(s.location || '-')} · ${escapeHtml(phase)} · 能量 ${s.energy ?? 0} · 专注 ${s.focus ?? 0}</small>
        <div class="mini-progress"><i style="width:${pct}%"></i></div>
        <div class="action-title">${escapeHtml(s.intention || s.current_action || '')}</div>
        <small>剩余 ${s.action_remaining_ticks ?? 0}/${s.action_duration_ticks ?? 0} tick · 压力 ${s.stress ?? 0} · 社交 ${s.social_need ?? 0}</small><br>
        <small>工具：${escapeHtml(s.selected_tool || '-')} · 来源：${escapeHtml(s.last_decision_source || '-')}</small>
        ${s.player_influence && s.player_influence.active ? `<div class="influence-pill">受${escapeHtml(s.player_influence.player_name || '玩家')}影响：${escapeHtml(s.player_influence.directive || s.player_influence.message || '').slice(0, 70)} · ${s.player_influence.remaining_ticks || 0}tick</div>` : ''}
        ${s.memory_summary_count ? `<small>记忆摘要 ${s.memory_summary_count} 次：${escapeHtml(s.last_memory_summary || '').slice(0, 80)}</small>` : ''}
      </div>
    `;
  }).join('');
}

async function refreshEvents() {
  const data = await getJSON('/events?limit=10');
  renderEvents(data.events || []);
}

function renderEvents(events) {
  eventsEl.innerHTML = (events || []).map(e => `<div class="info-card event-card">${escapeHtml(e.content)}<br><small>${escapeHtml(e.event_type)} · ${escapeHtml(e.created_at)}</small></div>`).join('') || '<p>暂无事件</p>';
}

async function refreshResources() {
  if (!resourcesEl) return;
  try {
    const [resources, dyn] = await Promise.all([
      getJSON('/company/resources?limit=6'),
      getJSON('/company/dynamic-tools?limit=4').catch(() => ({ tools: [] }))
    ]);
    const rs = resources.resources || [];
    const tools = dyn.tools || [];
    resourcesEl.innerHTML = `
      <div class="info-card resource-card">
        <b>共享产物 ${rs.length}</b><br>
        ${rs.slice(0, 4).map(r => `<small>${escapeHtml(r.resource_type)} · #${r.id} ${escapeHtml(r.title).slice(0, 52)}</small><br>`).join('') || '<small>等待 Agent 生成文档/代码/运行结果</small><br>'}
        <small>动态工具：${tools.map(t => escapeHtml(t.name)).slice(0, 3).join(' / ') || '暂无'}</small>
      </div>
    `;
  } catch (err) {
    resourcesEl.innerHTML = `<div class="info-card">共享资源读取失败：${escapeHtml(err.message)}</div>`;
  }
}

async function refreshTasks() {
  const data = await getJSON('/office/tasks?limit=16');
  tasks = data.tasks || [];
  renderTasks();
}

function renderTasks() {
  tasksEl.innerHTML = tasks.map(t => `
    <div class="info-card task ${escapeHtml(t.status)}">
      <b>#${t.id} ${escapeHtml(t.title)}</b><br>
      <small>${statusName(t.status)} · P${t.priority} · Owner: ${escapeHtml(ownerName(t.owner_npc_id))}</small><br>
      <small>${(t.tags || []).map(escapeHtml).join(' / ')}</small>
    </div>
  `).join('') || '<p>暂无任务</p>';
}

function renderTraces() {
  tracesEl.innerHTML = agentTraces.slice(0, 9).map(t => {
    const pct = Math.round((t.action_progress || 0) * 100);
    const factors = (t.reasoning_factors || []).slice(0, 2).map(escapeHtml).join('；');
    const toolTrace = (t.tool_trace || [])[0] || null;
    const toolLine = toolTrace ? `<div class="tool-chip ${toolTrace.ok ? 'ok' : 'blocked'}">Tool: ${escapeHtml(toolTrace.tool_name || t.selected_tool || '-')} · ${escapeHtml(toolTrace.ok ? 'ok' : (toolTrace.blocked ? 'blocked' : 'failed'))} · ${escapeHtml(toolTrace.risk_level || 'low')}</div>` : `<div class="tool-chip">Tool: ${escapeHtml(t.selected_tool || '-')}</div>`;
    const obs = (t.observations || []).slice(0, 1).map(escapeHtml).join('');
    const thinking = t.thinking_trace || {};
    const thinkLine = thinking.private_judgement ? `<small>思考：${escapeHtml(thinking.private_judgement).slice(0, 120)}</small><br>` : '';
    const collabLine = t.collaboration_judgement ? `<small>合作判断：${escapeHtml(t.collaboration_judgement).slice(0, 120)}</small><br>` : '';
    const rollbackLine = t.rollback_plan ? `<small class="rollback-line">回滚：${escapeHtml(t.rollback_plan).slice(0, 120)}</small><br>` : '';
    return `
      <div class="info-card trace">
        <div class="row between"><b>${escapeHtml(t.npc_name)}</b><span>${escapeHtml(phaseName(t.phase))}</span></div>
        <small>${escapeHtml(zoneDisplay(t.target_zone))} · ${escapeHtml(t.decision_source || '-')} · ${pct}%</small><br>
        ${toolLine}
        <span>${escapeHtml(t.intention || t.goal || '')}</span><br>
        ${obs ? `<small>观察：${obs}</small><br>` : ''}
        ${thinkLine}${collabLine}${rollbackLine}
        <small>${escapeHtml(t.thought || '')}</small><br>
        ${factors ? `<small>依据：${factors}</small>` : ''}
      </div>
    `;
  }).join('') || '<p>开始运行后显示成员动态。</p>';
}

function updateVisualStates(dt) {
  const now = performance.now();
  const alpha = 1 - Math.pow(0.003, Math.min(dt, 80) / 240);
  for (const s of states) {
    const v = visualStates.get(s.npc_id);
    if (!v) continue;
    const phase = s.action_phase || 'thinking';
    const serverDx = v.serverX - v.x;
    const serverDy = v.serverY - v.y;
    const serverDist = Math.hypot(serverDx, serverDy);

    if (serverDist < 0.35) {
      v.x = v.serverX;
      v.y = v.serverY;
    } else {
      v.x += serverDx * alpha;
      v.y += serverDy * alpha;
    }



    const target = s.target_position;
    if (phase === 'walking' && target && (autoTimer || tickInFlight)) {
      const tx = target.x - v.x;
      const ty = target.y - v.y;
      const d = Math.hypot(tx, ty);
      if (d > 2) {
        const pxPerTick = Math.max(8, Math.min(24, Number(s.speed || 16)));
        const step = Math.min(d, pxPerTick * (dt / AUTO_TICK_MS) * 0.95);
        v.x += (tx / d) * step;
        v.y += (ty / d) * step;
      }
    }

    const moved = !v.trail || v.trail.length === 0 || Math.hypot(v.trail[v.trail.length - 1].x - v.x, v.trail[v.trail.length - 1].y - v.y) > 8;
    if (phase === 'walking' && moved) {
      v.trail = v.trail || [];
      v.trail.push({ x: v.x, y: v.y, t: now });
    }
    v.trail = (v.trail || []).filter(p => now - p.t < 900).slice(-12);
  }
}

function drawOffice() {
  const bg = ctx.createLinearGradient(0, 0, canvas.width, canvas.height);
  bg.addColorStop(0, '#0c172a');
  bg.addColorStop(0.48, '#101b31');
  bg.addColorStop(1, '#07111f');
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  drawCanvasAura();
  drawFloorGrid();
  drawBoardroomGlow();
  for (const z of zones) drawZone(z);
  drawOfficeDecorations();
  drawMiniLegend();
}

function drawCanvasAura() {
  const a = ctx.createRadialGradient(180, 40, 0, 180, 40, 420);
  a.addColorStop(0, 'rgba(6,182,212,.20)');
  a.addColorStop(1, 'rgba(6,182,212,0)');
  ctx.fillStyle = a;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const b = ctx.createRadialGradient(900, 140, 0, 900, 140, 480);
  b.addColorStop(0, 'rgba(139,92,246,.20)');
  b.addColorStop(1, 'rgba(139,92,246,0)');
  ctx.fillStyle = b;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
}

function drawFloorGrid() {
  ctx.save();
  ctx.strokeStyle = 'rgba(148,163,184,.075)';
  ctx.lineWidth = 1;
  for (let x = -80; x < canvas.width + 80; x += 42) {
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x + 140, canvas.height); ctx.stroke();
  }
  for (let y = 0; y < canvas.height; y += 42) {
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
  }
  ctx.restore();
}

function drawBoardroomGlow() {
  const g = ctx.createRadialGradient(canvas.width * .5, canvas.height * .52, 0, canvas.width * .5, canvas.height * .52, 360);
  g.addColorStop(0, 'rgba(245,158,11,.10)');
  g.addColorStop(1, 'rgba(245,158,11,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
}

function zoneTheme(kind) {
  const themes = {
    tech: ['#0ea5e9', '#1d4ed8', '⌘'], product: ['#f59e0b', '#d97706', '◆'], algorithm: ['#a855f7', '#7c3aed', '∑'], security: ['#fb7185', '#be123c', '盾'],
    meeting: ['#14b8a6', '#0f766e', '会'], workspace: ['#22c55e', '#15803d', '办'], ops: ['#38bdf8', '#0369a1', '云'], demo: ['#f472b6', '#db2777', '演'],
    social: ['#fbbf24', '#b45309', '咖'], hr: ['#2dd4bf', '#0f766e', '人'], public: ['#94a3b8', '#475569', '库']
  };
  return themes[kind] || ['#94a3b8', '#475569', '室'];
}

function drawZone(z) {
  const [x, y, w, h] = z.rect;
  const [c1, c2, icon] = zoneTheme(z.kind);
  ctx.save();

  ctx.shadowColor = c1 + '24';
  ctx.shadowBlur = 22;
  ctx.shadowOffsetY = 10;
  const fill = ctx.createLinearGradient(x, y, x + w, y + h);
  fill.addColorStop(0, hexToRgba(c1, .18));
  fill.addColorStop(.58, 'rgba(15,23,42,.46)');
  fill.addColorStop(1, hexToRgba(c2, .18));
  ctx.fillStyle = fill;
  roundRect(ctx, x, y, w, h, 18, true, false);

  ctx.shadowBlur = 0;
  ctx.strokeStyle = hexToRgba(c1, .38);
  ctx.lineWidth = 1.3;
  roundRect(ctx, x, y, w, h, 18, false, true);

  ctx.fillStyle = 'rgba(255,255,255,.055)';
  roundRect(ctx, x + 10, y + 10, 38, 30, 12, true, false);
  ctx.fillStyle = '#f8fafc';
  ctx.font = icon.length > 1 ? '800 14px system-ui, sans-serif' : '900 18px system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(icon, x + 29, y + 31);

  ctx.textAlign = 'left';
  ctx.fillStyle = '#f8fafc';
  ctx.font = '800 14px system-ui, sans-serif';
  ctx.fillText(z.name, x + 58, y + 27);
  ctx.fillStyle = 'rgba(226,232,240,.58)';
  ctx.font = '11px system-ui, sans-serif';
  ctx.fillText(zoneSubtitle(z.kind), x + 58, y + 43);
  ctx.restore();
}

function zoneSubtitle(kind) {
  return ({ tech: 'Architecture', product: 'Product & Metrics', algorithm: 'RAG / Reasoning', security: 'Risk Control', meeting: 'Decision Room', workspace: 'Team Floor', ops: 'Runtime Ops', demo: 'Experience Lab', social: 'Coffee Break', hr: 'Hiring Desk', public: 'Knowledge Hub' })[kind] || 'Company Zone';
}

function drawOfficeDecorations() {
  ctx.save();

  const table = ctx.createLinearGradient(380, 300, 640, 410);
  table.addColorStop(0, 'rgba(148,163,184,.22)');
  table.addColorStop(1, 'rgba(30,41,59,.46)');
  ctx.fillStyle = table;
  roundRect(ctx, 370, 290, 250, 92, 24, true, false);
  ctx.strokeStyle = 'rgba(226,232,240,.13)'; ctx.lineWidth = 1.5; roundRect(ctx, 370, 290, 250, 92, 24, false, true);


  const seats = [[398,274],[456,274],[514,274],[572,274],[398,390],[456,390],[514,390],[572,390]];
  for (const [x,y] of seats) {
    ctx.fillStyle = 'rgba(59,130,246,.18)';
    roundRect(ctx, x, y, 38, 20, 8, true, false);
    ctx.strokeStyle = 'rgba(147,197,253,.17)'; roundRect(ctx, x, y, 38, 20, 8, false, true);
  }


  ctx.fillStyle = 'rgba(245,158,11,.16)'; roundRect(ctx, 112, 304, 162, 62, 20, true, false);
  ctx.fillStyle = 'rgba(255,255,255,.10)'; roundRect(ctx, 124, 316, 60, 38, 14, true, false); roundRect(ctx, 194, 316, 60, 38, 14, true, false);


  for (const x of [772,822,872,922]) {
    const rack = ctx.createLinearGradient(x, 300, x + 32, 390);
    rack.addColorStop(0, 'rgba(6,182,212,.20)'); rack.addColorStop(1, 'rgba(15,23,42,.42)');
    ctx.fillStyle = rack; roundRect(ctx, x, 300, 32, 92, 7, true, false);
    ctx.fillStyle = 'rgba(103,232,249,.7)';
    for (let i = 0; i < 4; i++) roundRect(ctx, x + 8, 314 + i * 16, 16, 3, 3, true, false);
  }


  drawPlant(74, 525); drawPlant(996, 88); drawPlant(705, 535);
  ctx.fillStyle = 'rgba(2,6,23,.34)'; roundRect(ctx, 534, 484, 130, 38, 13, true, false);
  ctx.fillStyle = '#fde68a'; ctx.font = '800 12px system-ui, sans-serif'; ctx.textAlign = 'center'; ctx.fillText('Shared Assets', 599, 508);
  ctx.restore();
}

function drawPlant(x, y) {
  ctx.fillStyle = 'rgba(120,53,15,.55)'; roundRect(ctx, x - 9, y + 8, 18, 18, 5, true, false);
  ctx.fillStyle = 'rgba(34,197,94,.58)';
  for (let i = 0; i < 5; i++) {
    const a = -Math.PI / 2 + (i - 2) * .42;
    ctx.beginPath(); ctx.ellipse(x + Math.cos(a) * 9, y + Math.sin(a) * 8, 5, 13, a, 0, Math.PI * 2); ctx.fill();
  }
}

function drawMiniLegend() {
  const x = 22, y = 22;
  ctx.save();
  ctx.fillStyle = 'rgba(2,6,23,.42)'; roundRect(ctx, x, y, 176, 38, 16, true, false);
  ctx.strokeStyle = 'rgba(148,163,184,.16)'; roundRect(ctx, x, y, 176, 38, 16, false, true);
  ctx.fillStyle = '#e2e8f0'; ctx.font = '800 12px system-ui, sans-serif'; ctx.textAlign = 'left'; ctx.fillText('公司总部实时沙盘', x + 15, y + 23);
  ctx.fillStyle = '#67e8f9'; ctx.beginPath(); ctx.arc(x + 145, y + 19, 4, 0, Math.PI * 2); ctx.fill();
  ctx.restore();
}

function drawNpc(p, state) {
  const visual = visualStates.get(p.npc_id);
  const pos = visual || state?.position || p.position;
  const x = pos.x, y = pos.y;
  const phase = state?.action_phase || 'thinking';
  const target = state?.target_position;
  const color = normalizeHexColor(p.color || colorFromId(p.npc_id));

  if (target && phase === 'walking') {
    const pulse = 3 + Math.sin(performance.now() / 190) * 1.5;
    ctx.beginPath(); ctx.arc(target.x, target.y, 8 + pulse, 0, Math.PI * 2); ctx.fillStyle = hexToRgba(color, .18); ctx.fill();
    ctx.beginPath(); ctx.arc(target.x, target.y, 3.6, 0, Math.PI * 2); ctx.fillStyle = hexToRgba(color, .75); ctx.fill();
  }

  drawTrail({ ...p, color }, visual, phase);
  drawNpcShadow(x, y, phase);

  ctx.save();
  const halo = ctx.createRadialGradient(x, y, 0, x, y, 42);
  halo.addColorStop(0, hexToRgba(color, .34));
  halo.addColorStop(1, hexToRgba(color, 0));
  ctx.fillStyle = halo; ctx.beginPath(); ctx.arc(x, y, 42, 0, Math.PI * 2); ctx.fill();

  const body = ctx.createLinearGradient(x - 22, y - 22, x + 22, y + 24);
  body.addColorStop(0, lightenHex(color, .16));
  body.addColorStop(1, color);
  ctx.beginPath(); ctx.arc(x, y, 22, 0, Math.PI * 2); ctx.fillStyle = body; ctx.fill();
  ctx.strokeStyle = state?.is_busy ? '#fbbf24' : phaseColor(phase); ctx.lineWidth = 3.2; ctx.stroke();

  drawProgressRing(x, y, 29, state?.action_progress || 0, phase);

  const initials = initialsOf(p.name || p.role || 'A');
  ctx.fillStyle = '#fff'; ctx.font = '900 12px system-ui, sans-serif'; ctx.textAlign = 'center'; ctx.fillText(initials, x, y + 4);

  const nameW = Math.max(56, Math.min(104, ctx.measureText(p.name || '').width + 18));
  ctx.fillStyle = 'rgba(2,6,23,.60)'; roundRect(ctx, x - nameW / 2, y - 48, nameW, 23, 11, true, false);
  ctx.fillStyle = '#f8fafc'; ctx.font = '800 12px system-ui, sans-serif'; ctx.fillText(p.name, x, y - 32);

  ctx.fillStyle = 'rgba(15,23,42,.58)';
  const action = (state?.intention || state?.current_action || p.role).slice(0, 18);
  const actionW = Math.max(86, Math.min(150, ctx.measureText(action).width + 18));
  roundRect(ctx, x - actionW / 2, y + 35, actionW, 23, 11, true, false);
  ctx.fillStyle = 'rgba(226,232,240,.92)'; ctx.font = '11px system-ui, sans-serif'; ctx.fillText(action, x, y + 51);
  ctx.restore();

  if (phase === 'walking') drawWalkingLegs(x, y, color);
  if (phase === 'acting') drawWorkSpark(x, y, color);
  if (phase === 'thinking') drawThinkingDots(x, y, color);
}

function drawTrail(p, visual, phase) {
  if (!visual || phase !== 'walking') return;
  const now = performance.now();
  for (const point of (visual.trail || [])) {
    const age = Math.max(0, Math.min(1, (now - point.t) / 900));
    const r = 2.6 + (1 - age) * 2.6;
    ctx.beginPath(); ctx.arc(point.x, point.y + 18, r, 0, Math.PI * 2); ctx.fillStyle = hexToRgba(p.color, (1 - age) * .32); ctx.fill();
  }
}

function drawNpcShadow(x, y, phase) {
  ctx.beginPath();
  ctx.ellipse(x, y + 25, phase === 'walking' ? 24 : 19, 7, 0, 0, Math.PI * 2);
  ctx.fillStyle = 'rgba(0,0,0,.30)';
  ctx.fill();
}

function drawProgressRing(x, y, r, progress, phase) {
  if (phase !== 'acting' && phase !== 'walking') return;
  ctx.beginPath(); ctx.arc(x, y, r, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.max(0.02, progress));
  ctx.strokeStyle = phase === 'acting' ? '#f59e0b' : '#67e8f9';
  ctx.lineWidth = 3.6;
  ctx.lineCap = 'round';
  ctx.stroke();
}

function drawWalkingLegs(x, y, color) {
  const t = performance.now() / 115;
  ctx.strokeStyle = hexToRgba(color, .86); ctx.lineWidth = 3; ctx.lineCap = 'round';
  ctx.beginPath(); ctx.moveTo(x - 6, y + 17); ctx.lineTo(x - 10 + Math.sin(t) * 4, y + 28); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(x + 6, y + 17); ctx.lineTo(x + 10 + Math.sin(t + Math.PI) * 4, y + 28); ctx.stroke();
}

function drawWorkSpark(x, y, color) {
  const t = performance.now() / 260;
  for (let i = 0; i < 4; i++) {
    const a = t + i * Math.PI * 0.5;
    ctx.beginPath(); ctx.arc(x + Math.cos(a) * 32, y + Math.sin(a) * 24, 2.4, 0, Math.PI * 2); ctx.fillStyle = hexToRgba(color, .76); ctx.fill();
  }
}

function drawThinkingDots(x, y, color) {
  const t = performance.now() / 260;
  for (let i = 0; i < 3; i++) {
    const yy = y - 30 - Math.sin(t + i) * 3;
    ctx.beginPath(); ctx.arc(x + 24 + i * 7, yy, 2.2, 0, Math.PI * 2); ctx.fillStyle = hexToRgba(color, .72 - i * .12); ctx.fill();
  }
}

function drawPlayer() {
  ctx.save();
  const halo = ctx.createRadialGradient(player.x, player.y, 0, player.x, player.y, 45);
  halo.addColorStop(0, 'rgba(245,158,11,.35)');
  halo.addColorStop(1, 'rgba(245,158,11,0)');
  ctx.fillStyle = halo; ctx.beginPath(); ctx.arc(player.x, player.y, 45, 0, Math.PI * 2); ctx.fill();
  const g = ctx.createLinearGradient(player.x - 18, player.y - 18, player.x + 18, player.y + 18);
  g.addColorStop(0, '#fbbf24'); g.addColorStop(1, '#f97316');
  ctx.beginPath(); ctx.arc(player.x, player.y, player.r + 2, 0, Math.PI * 2); ctx.fillStyle = g; ctx.fill();
  ctx.strokeStyle = '#fef3c7'; ctx.lineWidth = 2.5; ctx.stroke();
  ctx.fillStyle = '#111827'; ctx.font = '900 12px system-ui, sans-serif'; ctx.textAlign = 'center'; ctx.fillText('CEO', player.x, player.y + 4);
  ctx.fillStyle = 'rgba(2,6,23,.62)'; roundRect(ctx, player.x - 24, player.y - 45, 48, 22, 11, true, false);
  ctx.fillStyle = '#fde68a'; ctx.font = '900 12px system-ui, sans-serif'; ctx.fillText('老板', player.x, player.y - 30);
  ctx.restore();
}

function normalizeHexColor(color) {
  const s = String(color || '').trim();
  if (/^#[0-9a-fA-F]{6}$/.test(s)) return s;
  if (/^#[0-9a-fA-F]{3}$/.test(s)) return '#' + s.slice(1).split('').map(ch => ch + ch).join('');
  return '#38bdf8';
}

function hexToRgba(hex, alpha) {
  const h = normalizeHexColor(hex).slice(1);
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${Math.max(0, Math.min(1, alpha))})`;
}

function lightenHex(hex, ratio) {
  const h = normalizeHexColor(hex).slice(1);
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
  const nr = Math.round(r + (255 - r) * ratio), ng = Math.round(g + (255 - g) * ratio), nb = Math.round(b + (255 - b) * ratio);
  return '#' + [nr, ng, nb].map(v => v.toString(16).padStart(2, '0')).join('');
}

function initialsOf(name) {
  const s = String(name || 'A').trim();
  if (/^[A-Za-z]/.test(s)) return s.slice(0, 2).toUpperCase();
  return s.slice(-2);
}

function npcPositionForInteraction(p) {
  const visual = visualStates.get(p.npc_id);
  if (visual) return visual;
  const s = states.find(x => x.npc_id === p.npc_id);
  return s?.position || p.position;
}

function nearestNpc() {
  let best = null, bestD = Infinity;
  for (const p of profiles) {
    const pos = npcPositionForInteraction(p);
    const dx = pos.x - player.x, dy = pos.y - player.y;
    const d = Math.hypot(dx, dy);
    if (d < bestD) { best = p; bestD = d; }
  }
  return bestD < 82 ? best : null;
}

function updatePlayer() {
  if (keys.has('ArrowLeft') || keys.has('a')) player.x -= player.speed;
  if (keys.has('ArrowRight') || keys.has('d')) player.x += player.speed;
  if (keys.has('ArrowUp') || keys.has('w')) player.y -= player.speed;
  if (keys.has('ArrowDown') || keys.has('s')) player.y += player.speed;
  player.x = Math.max(20, Math.min(canvas.width - 20, player.x));
  player.y = Math.max(20, Math.min(canvas.height - 20, player.y));
}

function loop(ts) {
  const dt = ts - lastFrameTs;
  lastFrameTs = ts;
  updatePlayer();
  updateVisualStates(dt);
  drawOffice();
  for (const p of profiles) drawNpc(p, states.find(s => s.npc_id === p.npc_id));
  drawPlayer();
  const near = nearestNpc();
  const busy = tickInFlight ? ' · 同步中' : '';
  hint.textContent = near ? `靠近 ${near.name}｜${near.role}｜按 E 对话${busy}` : `WASD 移动；点击“开始上班”后成员会由 DeepSeek 并行生成行动。当前第 ${lastTick} 分钟${busy}`;
  requestAnimationFrame(loop);
}

function openChat(npc) {
  currentNpc = npc;
  const s = states.find(x => x.npc_id === npc.npc_id) || {};
  chatNpcName.textContent = npc.name;
  chatNpcRole.textContent = `${npc.title}｜${npc.department}｜当前位置：${s.location || '-'}｜状态：${phaseName(s.action_phase)}`;
  chatLog.innerHTML = `<div class="msg npc">你好，我是${escapeHtml(npc.name)}。我现在正在处理「${escapeHtml(s.intention || s.current_task || '办公室协作')}」，进度 ${Math.round((s.action_progress || 0) * 100)}%。</div>`;
  chatDialog.showModal();
  setTimeout(() => chatInput.focus(), 50);
}

async function sendMessage() {
  if (!currentNpc) return;
  const text = chatInput.value.trim();
  if (!text) return;
  chatInput.value = '';
  chatLog.insertAdjacentHTML('beforeend', `<div class="msg user">${escapeHtml(text)}</div>`);
  chatLog.insertAdjacentHTML('beforeend', `<div class="msg npc" id="loading">正在回复...</div>`);
  chatLog.scrollTop = chatLog.scrollHeight;
  try {
    const data = await getJSON('/dialogue', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ player_name: playerNameEl.value || '候选人', npc_id: currentNpc.npc_id, player_message: text })
    });
    document.getElementById('loading').remove();
    const infl = data.behavior_influence || {};
    const inflText = infl.active ? `<br><small class="warn">行为影响：${escapeHtml(infl.directive || infl.message || '')}｜目标倾向：${escapeHtml(zoneDisplay(infl.target_zone_hint) || '无')}｜强度 ${infl.influence_score}</small>` : '';
    chatLog.insertAdjacentHTML('beforeend', `<div class="msg npc">${escapeHtml(data.npc_reply)}<br><small>关系：${data.affinity_level} ${data.affinity_score}/100（${data.score_delta >= 0 ? '+' : ''}${data.score_delta}）｜${data.latency_ms}ms</small>${inflText}</div>`);
    memoriesEl.innerHTML = data.retrieved_memories.map(m => `<div class="info-card">${escapeHtml(m.content).slice(0, 180)}<br><small>${escapeHtml(m.kind || 'memory')} · 重要性 ${m.importance} · ${m.created_at}</small></div>`).join('') || '<p>暂无命中记忆</p>';
    await refreshStatus(); await refreshEvents(); await refreshLLMStatus();
  } catch (err) {
    const loading = document.getElementById('loading');
    if (loading) loading.textContent = '请求失败：' + err.message;
  }
  chatLog.scrollTop = chatLog.scrollHeight;
}

async function runTick(useLlm = false) {
  if (tickInFlight) return;
  tickInFlight = true;
  tickBtn.disabled = true;
  llmTickBtn.disabled = true;
  try {
    const qs = useLlm ? '?use_llm_planner=true' : '';
    const data = await getJSON('/simulate/tick' + qs, { method: 'POST' });
    lastTick = data.tick;
    setProfiles(data.npc_profiles || []);
    setStates(data.npc_states || []);
    tasks = data.tasks || tasks;
    agentTraces = data.agent_traces || [];
    renderTraces();
    if (lastTick % SIDE_PANEL_EVERY_TICKS === 0 || useLlm) {
      renderStatus();
      renderTasks();
      renderEvents(data.events || []);
    }
    if (useLlm || lastTick - lastLlmStatusTick >= LLM_STATUS_EVERY_TICKS) {
      lastLlmStatusTick = lastTick;
      refreshLLMStatus().catch(() => {});
      refreshBossMissions().catch(() => {});
      refreshCompanyStatus().catch(() => {});
    }
  } finally {
    tickInFlight = false;
    tickBtn.disabled = false;
    llmTickBtn.disabled = false;
  }
}

function toggleAuto() {
  if (autoTimer) {
    clearInterval(autoTimer); autoTimer = null;
    autoBtn.textContent = '开始经营'; simBadge.textContent = '暂停'; simBadge.classList.remove('on');
    return;
  }
  autoBtn.textContent = '暂停经营'; simBadge.textContent = '经营中'; simBadge.classList.add('on');
  lastAutoStartedAt = performance.now();
  runTick(false).catch(showError);
  autoTimer = setInterval(() => runTick(false).catch(showError), AUTO_TICK_MS);
}

async function showStandup() {
  const data = await getJSON('/office/standup');
  eventsEl.insertAdjacentHTML('afterbegin', `<div class="info-card standup"><b>站会纪要</b><br>${escapeHtml(data.summary)}</div>`);
}

function showError(err) { hint.textContent = '运行失败：' + err.message; }
function ownerName(id) { return profiles.find(p => p.npc_id === id)?.name || id || '待分配'; }
function colorFromId(id) {
  const palette = ['#38BDF8', '#34D399', '#F97316', '#A78BFA', '#FB7185', '#22C55E', '#60A5FA', '#F59E0B'];
  let h = 0; for (const ch of String(id || 'agent')) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return palette[h % palette.length];
}
function zoneDisplay(id) { return zones.find(z => z.zone_id === id)?.name || id || '-'; }
function phaseName(p) { return ({ thinking: '观察中', walking: '移动中', acting: '处理中', cooldown: '收尾', talking: '对话中' })[p] || p || '-'; }
function statusName(s) { return ({ todo: '待处理', doing: '处理中', review: '待验收', blocked: '阻塞', done: '完成' })[s] || s; }
function phaseColor(p) { return ({ thinking: '#8b5cf6', walking: '#2563eb', acting: '#d97706', cooldown: '#059669', talking: '#ea580c' })[p] || 'rgba(15,23,42,.6)'; }
function escapeHtml(s) { return String(s ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function roundRect(ctx, x, y, w, h, r, fill, stroke) {
  if (w < 2 * r) r = w / 2; if (h < 2 * r) r = h / 2;
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  if (fill) ctx.fill(); if (stroke) ctx.stroke();
}

window.addEventListener('keydown', e => {
  keys.add(e.key.length === 1 ? e.key.toLowerCase() : e.key);
  if (e.key.toLowerCase() === 'e' && !chatDialog.open) {
    const near = nearestNpc(); if (near) openChat(near);
  }
});
window.addEventListener('keyup', e => keys.delete(e.key.length === 1 ? e.key.toLowerCase() : e.key));
sendBtn.addEventListener('click', sendMessage);
chatInput.addEventListener('keydown', e => { if (e.key === 'Enter') sendMessage(); });
tickBtn.addEventListener('click', () => runTick(false).catch(showError));
llmTickBtn.addEventListener('click', () => runTick(true).catch(showError));
autoBtn.addEventListener('click', toggleAuto);

if (companyBtn) {
  companyBtn.addEventListener('click', async () => {
    try {
      const data = await getJSON('/company/trigger-cycle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: '玩家触发公司智能循环：招聘/扩张/回滚事务演示' })
      });
      await Promise.all([refreshBossMissions(), refreshCompanyStatus(), refreshStatus(), refreshTasks(), refreshEvents()]);
      hint.textContent = '公司智能循环已触发：团队会判断招聘、行为面试和扩张事务。';
    } catch (err) { showError(err); }
  });
}

if (bossTaskBtn && bossDialog) {
  bossTaskBtn.addEventListener('click', () => {
    bossTaskTitle.value = '';
    bossTaskDesc.value = '';
    bossTaskOutcome.value = '';
    bossDialog.showModal();
    setTimeout(() => bossTaskTitle.focus(), 50);
  });
}

if (submitBossTaskBtn) {
  submitBossTaskBtn.addEventListener('click', async () => {
    const title = bossTaskTitle.value.trim();
    if (!title) { hint.textContent = '请先填写主任务标题。'; return; }
    try {
      const data = await getJSON('/boss/tasks', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          boss_name: playerNameEl.value || '老板',
          title,
          description: bossTaskDesc.value.trim(),
          desired_outcome: bossTaskOutcome.value.trim(),
          priority: 5,
          auto_dispatch: true,
        })
      });
      bossDialog.close();
      hint.textContent = `老板主任务已发布：Agent 团队拆分出 ${data.subtasks.length} 个子任务。`;
      await Promise.all([refreshBossMissions(), refreshTasks(), refreshEvents(), refreshCompanyStatus()]);
    } catch (err) { showError(err); }
  });
}

if (missionReportBtn) {
  missionReportBtn.addEventListener('click', async () => {
    try {
      const data = await getJSON('/boss/missions?status=active&limit=1');
      const mission = (data.missions || [])[0];
      if (!mission) { hint.textContent = '当前没有 active 老板主任务。'; return; }
      const report = await getJSON(`/boss/missions/${mission.id}/report`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force: true, note: '老板手动生成阶段性/完整任务报告。' })
      });
      hint.textContent = report.ok ? `任务报告已生成：${mission.title}` : '报告暂未生成：仍有任务未完成。';
      await Promise.all([refreshBossMissions(), refreshResources(), refreshTasks(), refreshEvents()]);
    } catch (err) { showError(err); }
  });
}

standupBtn.addEventListener('click', () => showStandup().catch(showError));

if (resetBtn) {
  resetBtn.addEventListener('click', async () => {
    if (tickInFlight) return;
    tickInFlight = true;
    hint.textContent = '正在重置办公室运行态...';
    try {
      await getJSON('/office/reset-runtime', { method: 'POST' });
      agentTraces = [];
      visualStates.clear();
      await Promise.all([refreshStatus(), refreshEvents(), refreshLLMStatus(), refreshBossMissions(), refreshCompanyStatus(), refreshTasks()]);
      renderTraces();
      hint.textContent = '运行态已重置。下一轮将重新并行请求 DeepSeek。';
    } catch (err) {
      hint.textContent = '重置失败：' + err.message;
    } finally {
      tickInFlight = false;
    }
  });
}

boot().catch(err => { hint.textContent = '启动失败：' + err.message; });
