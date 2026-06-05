const API = window.location.origin;
const canvas = document.getElementById('office');
const ctx = canvas.getContext('2d');
const hint = document.getElementById('hint');
const simBadge = document.getElementById('simBadge');
const statusEl = document.getElementById('status');
const llmStatusEl = document.getElementById('llmStatus');
const eventsEl = document.getElementById('events');
const memoriesEl = document.getElementById('memories');
const tracesEl = document.getElementById('traces');
const tasksEl = document.getElementById('tasks');
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
  profiles = npcData.npcs;
  zones = mapData.zones;
  await refreshLLMStatus();
  await refreshStatus();
  await refreshTasks();
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
    llmStatusEl.innerHTML = `
      <div class="info-card service-card">
        <div class="row between"><b>${escapeHtml(modeText)}</b><span class="dot ${online ? 'ok' : 'muted'}"></span></div>
        <small>${escapeHtml(data.provider)} · ${escapeHtml(data.model)}</small><br>
        <small>行为模式：${escapeHtml(mode)} · 并发 ${concurrency} · 超时 ${timeout}s</small><br>
        <small>最近批次：${batch.requested_agents || 0} 人 / 调用 ${batch.llm_scheduled || 0} / 完成 ${batch.llm_completed || 0} / 兜底 ${batch.fallback_count || 0} · ${batch.latency_ms || 0}ms</small><br>
        <small>真实请求 ${data.real_requests || 0} · 降级 ${data.degraded_requests || 0} · 最近 ${data.last_latency_ms || 0}ms</small>
        ${firstError ? `<br><small class="warn">诊断：${escapeHtml(firstError).slice(0, 180)}</small>` : ''}
      </div>
    `;
  } catch (err) {
    llmStatusEl.innerHTML = `<div class="info-card">模型服务状态读取失败：${escapeHtml(err.message)}</div>`;
  }
}

async function refreshStatus() {
  const data = await getJSON('/npcs/status');
  setStates(data.npcs || []);
  renderStatus();
}

function setStates(newStates) {
  states = newStates;
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
    return `
      <div class="info-card trace">
        <div class="row between"><b>${escapeHtml(t.npc_name)}</b><span>${escapeHtml(phaseName(t.phase))}</span></div>
        <small>${escapeHtml(zoneDisplay(t.target_zone))} · ${escapeHtml(t.decision_source || '-')} · ${pct}%</small><br>
        <span>${escapeHtml(t.intention || t.goal || '')}</span><br>
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
  ctx.fillStyle = '#f4f6f8';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  ctx.strokeStyle = 'rgba(15,23,42,.055)';
  ctx.lineWidth = 1;
  for (let x = 0; x < canvas.width; x += 40) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); ctx.stroke(); }
  for (let y = 0; y < canvas.height; y += 40) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke(); }

  for (const z of zones) drawZone(z);
  drawOfficeDecorations();
}

function drawZone(z) {
  const [x, y, w, h] = z.rect;
  const palette = {
    tech: '#dfe9f7', product: '#fff2cc', algorithm: '#eadff7', security: '#ffe2df',
    meeting: '#dff4ef', workspace: '#e6f2ee', ops: '#e2f3e8', demo: '#f7e4ef',
    social: '#fff0d6', hr: '#e0f3f8', public: '#e9edf2'
  };
  ctx.fillStyle = palette[z.kind] || '#eef2f7';
  roundRect(ctx, x, y, w, h, 14, true, false);
  ctx.strokeStyle = 'rgba(15,23,42,.12)'; ctx.lineWidth = 1.4; roundRect(ctx, x, y, w, h, 14, false, true);
  ctx.fillStyle = '#334155'; ctx.font = '600 13px system-ui, sans-serif'; ctx.textAlign = 'left'; ctx.fillText(z.name, x + 12, y + 24);
}

function drawOfficeDecorations() {
  ctx.fillStyle = 'rgba(71,85,105,.12)';
  [[382,300],[452,300],[522,300],[592,300],[385,360],[455,360],[525,360],[595,360]].forEach(([x,y]) => roundRect(ctx, x, y, 48, 24, 6, true, false));
  ctx.fillStyle = 'rgba(59,130,246,.12)'; roundRect(ctx, 115, 310, 155, 54, 16, true, false);
  ctx.fillStyle = 'rgba(34,197,94,.12)'; [775,825,875,925].forEach(x => roundRect(ctx, x, 306, 30, 80, 6, true, false));
  ctx.fillStyle = 'rgba(120,53,15,.16)'; roundRect(ctx, 545, 492, 110, 28, 8, true, false);
}

function drawNpc(p, state) {
  const visual = visualStates.get(p.npc_id);
  const pos = visual || state?.position || p.position;
  const x = pos.x, y = pos.y;
  const phase = state?.action_phase || 'thinking';
  const target = state?.target_position;

  if (target && phase === 'walking') {
    const pulse = 3 + Math.sin(performance.now() / 190) * 1.5;
    ctx.beginPath(); ctx.arc(target.x, target.y, 7 + pulse, 0, Math.PI * 2); ctx.fillStyle = p.color + '24'; ctx.fill();
    ctx.beginPath(); ctx.arc(target.x, target.y, 3.5, 0, Math.PI * 2); ctx.fillStyle = p.color + 'aa'; ctx.fill();
  }

  drawTrail(p, visual, phase);
  drawNpcShadow(x, y, phase);

  ctx.beginPath(); ctx.arc(x, y, 21, 0, Math.PI * 2); ctx.fillStyle = p.color; ctx.fill();
  ctx.strokeStyle = state?.is_busy ? '#d97706' : phaseColor(phase); ctx.lineWidth = 3; ctx.stroke();

  drawProgressRing(x, y, 27, state?.action_progress || 0, phase);

  ctx.fillStyle = '#111827'; ctx.font = '600 13px system-ui, sans-serif'; ctx.textAlign = 'center'; ctx.fillText(p.name, x, y - 33);
  ctx.fillStyle = '#475569'; ctx.font = '12px system-ui, sans-serif';
  const action = (state?.intention || state?.current_action || p.role).slice(0, 18);
  ctx.fillText(action, x, y + 43);
  ctx.fillStyle = '#ffffff'; ctx.font = '700 11px system-ui, sans-serif'; ctx.fillText((p.department || p.role).slice(0, 4), x, y + 4);

  if (phase === 'walking') drawWalkingLegs(x, y, p.color);
  if (phase === 'acting') drawWorkSpark(x, y, p.color);
}

function drawTrail(p, visual, phase) {
  if (!visual || phase !== 'walking') return;
  const now = performance.now();
  for (const point of (visual.trail || [])) {
    const age = Math.max(0, Math.min(1, (now - point.t) / 900));
    const r = 2.6 + (1 - age) * 2.4;
    ctx.beginPath(); ctx.arc(point.x, point.y + 18, r, 0, Math.PI * 2); ctx.fillStyle = p.color + Math.round((1 - age) * 52).toString(16).padStart(2, '0'); ctx.fill();
  }
}

function drawNpcShadow(x, y, phase) {
  ctx.beginPath();
  ctx.ellipse(x, y + 24, phase === 'walking' ? 22 : 18, 6, 0, 0, Math.PI * 2);
  ctx.fillStyle = 'rgba(15,23,42,.18)';
  ctx.fill();
}

function drawProgressRing(x, y, r, progress, phase) {
  if (phase !== 'acting' && phase !== 'walking') return;
  ctx.beginPath(); ctx.arc(x, y, r, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.max(0.02, progress));
  ctx.strokeStyle = phase === 'acting' ? '#d97706' : '#2563eb';
  ctx.lineWidth = 3.5;
  ctx.stroke();
}

function drawWalkingLegs(x, y, color) {
  const t = performance.now() / 115;
  ctx.strokeStyle = color + 'dd'; ctx.lineWidth = 3; ctx.lineCap = 'round';
  ctx.beginPath(); ctx.moveTo(x - 6, y + 17); ctx.lineTo(x - 10 + Math.sin(t) * 4, y + 27); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(x + 6, y + 17); ctx.lineTo(x + 10 + Math.sin(t + Math.PI) * 4, y + 27); ctx.stroke();
}

function drawWorkSpark(x, y, color) {
  const t = performance.now() / 260;
  for (let i = 0; i < 3; i++) {
    const a = t + i * Math.PI * 0.7;
    ctx.beginPath(); ctx.arc(x + Math.cos(a) * 30, y + Math.sin(a) * 23, 2.4, 0, Math.PI * 2); ctx.fillStyle = color + 'bb'; ctx.fill();
  }
}

function drawPlayer() {
  ctx.beginPath(); ctx.arc(player.x, player.y, player.r, 0, Math.PI * 2); ctx.fillStyle = '#f59e0b'; ctx.fill();
  ctx.strokeStyle = '#92400e'; ctx.lineWidth = 2.5; ctx.stroke();
  ctx.fillStyle = '#fff'; ctx.font = '700 12px system-ui, sans-serif'; ctx.textAlign = 'center'; ctx.fillText('我', player.x, player.y + 4);
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
    autoBtn.textContent = '开始上班'; simBadge.textContent = '运行：暂停'; simBadge.classList.remove('on');
    return;
  }
  autoBtn.textContent = '暂停运行'; simBadge.textContent = '运行：上班中'; simBadge.classList.add('on');
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
      await Promise.all([refreshStatus(), refreshEvents(), refreshLLMStatus()]);
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
