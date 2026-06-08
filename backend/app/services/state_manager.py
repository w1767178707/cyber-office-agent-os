from __future__ import annotations
from datetime import datetime
from math import hypot
from threading import Lock
import re
from ..models import NPCProfile, Position
from .office_map import nearest_zone, zone_center, zone_name

def _clamp_int(value: int | float, low: int=0, high: int=100) -> int:
    return max(low, min(high, int(round(value))))

class StateManager:

    def __init__(self, profiles: list[NPCProfile]):
        self._states: dict[str, dict] = {}
        self._locks: dict[str, Lock] = {}
        self._init(profiles)

    def _init(self, profiles: list[NPCProfile]) -> None:
        for idx, p in enumerate(profiles):
            target = zone_center(p.home_zone)
            self._states[p.npc_id] = {'npc_id': p.npc_id, 'name': p.name, 'role': p.role, 'department': p.department, 'position': p.position.model_dump(), 'target_position': target.model_dump(), 'target_zone': p.home_zone, 'location': zone_name(p.home_zone), 'is_busy': False, 'current_action': '打开工作台，读取今日上下文', 'current_task': '等待自主决策', 'mood': 'calm', 'energy': 92 + idx % 6, 'plan_summary': '初始化办公室状态，准备生成第一个自主行动片段。', 'selected_tool': 'boot_sequence', 'last_interaction': None, 'action_phase': 'thinking', 'intention': '建立今日工作节奏', 'episode_id': '', 'action_started_tick': 0, 'action_duration_ticks': 1, 'action_remaining_ticks': 0, 'action_progress': 0.0, 'speed': 0.0, 'velocity': {'x': 0.0, 'y': 0.0}, 'autonomy': 0.76, 'stress': 20 + idx * 7 % 25, 'social_need': 35 + idx * 9 % 40, 'focus': 60 + idx * 8 % 35, 'reasoning_factors': ['系统启动', '等待任务池与环境事件'], 'action_kind': 'boot', 'recent_zones': [p.home_zone], 'zone_visit_counts': {p.home_zone: 1}, 'completed_episodes': 0, 'coffee_streak': 0, 'llm_decision_count': 0, 'last_decision_source': 'boot', 'player_influence': {}, 'memory_summary_count': 0, 'last_memory_summary': ''}
            self._locks[p.npc_id] = Lock()

    def list_states(self) -> list[dict]:
        return [dict(v) for v in self._states.values()]

    def get_state(self, npc_id: str) -> dict | None:
        state = self._states.get(npc_id)
        return dict(state) if state else None

    def acquire(self, npc_id: str) -> bool:
        lock = self._locks.get(npc_id)
        if not lock:
            return False
        acquired = lock.acquire(blocking=False)
        if acquired:
            self.set_busy(npc_id, True)
        return acquired

    def release(self, npc_id: str) -> None:
        lock = self._locks.get(npc_id)
        if lock and lock.locked():
            self.set_busy(npc_id, False)
            lock.release()

    def set_busy(self, npc_id: str, busy: bool) -> None:
        if npc_id in self._states:
            self._states[npc_id]['is_busy'] = busy
            self._states[npc_id]['last_interaction'] = datetime.now().isoformat(timespec='seconds')
            if busy:
                self._states[npc_id]['action_phase'] = 'talking'
                self._states[npc_id]['current_action'] = '正在与玩家对话，暂停自主移动'
                self._states[npc_id]['selected_tool'] = 'dialogue_agent'
                self._states[npc_id]['velocity'] = {'x': 0.0, 'y': 0.0}
                self._states[npc_id]['speed'] = 0.0
            elif self._states[npc_id].get('action_phase') == 'talking' or self._states[npc_id]['current_action'] == '正在与玩家对话，暂停自主移动':
                self._states[npc_id]['action_phase'] = 'thinking'
                influence = self._states[npc_id].get('player_influence') or {}
                if influence.get('active'):
                    self._states[npc_id]['current_action'] = f"受到{influence.get('player_name', '玩家')}影响，准备重新规划：{str(influence.get('directive', ''))[:48]}"
                    self._states[npc_id]['selected_tool'] = 'player_influence_router'
                    self._states[npc_id]['reasoning_factors'] = ['玩家对话产生短期行为影响', influence.get('reason', '等待 LLM 重新决策')]
                else:
                    self._states[npc_id]['current_action'] = '结束对话，回到办公室调度循环'

    def apply_player_influence(self, npc_id: str, *, player_name: str, player_message: str, npc_reply: str='', ttl_ticks: int=8, interrupt_current_episode: bool=True) -> dict:
        state = self._states.get(npc_id)
        if not state:
            return {}
        message = (player_message or '').strip()
        target_hint = self._infer_target_zone_from_text(message)
        actionable_words = ['请', '帮', '去', '先', '优先', '马上', '检查', '评审', '整理', '分析', '实现', '修改', '优化', '修复', '推进', '讨论', '准备', '设计', '复盘']
        encourage_words = ['不错', '很好', '感谢', '牛', '厉害', '赞', '可以', '继续']
        risk_words = ['风险', '阻塞', '问题', 'bug', '漏洞', '安全', '卡顿', '不对', '失败', '投诉', '紧急']
        is_actionable = bool(target_hint) or any((w in message for w in actionable_words))
        is_encourage = any((w in message for w in encourage_words))
        is_risk = any((w.lower() in message.lower() for w in risk_words))
        influence_score = 0.55 + (0.18 if is_actionable else 0) + (0.12 if is_risk else 0) + (0.06 if is_encourage else 0)
        influence_score = max(0.45, min(0.98, influence_score))
        reason_bits = []
        if is_actionable:
            reason_bits.append('玩家提出可执行方向')
        if target_hint:
            reason_bits.append(f'地点倾向={zone_name(target_hint)}')
        if is_risk:
            reason_bits.append('玩家强调风险/问题')
        if is_encourage:
            reason_bits.append('玩家给予正向反馈')
        reason = '；'.join(reason_bits) or '玩家对话成为下一轮决策上下文'
        if is_encourage:
            state['energy'] = _clamp_int(int(state.get('energy', 80)) + 4)
            state['focus'] = _clamp_int(int(state.get('focus', 70)) + 6)
            state['stress'] = _clamp_int(int(state.get('stress', 20)) - 5)
        if is_risk:
            state['stress'] = _clamp_int(int(state.get('stress', 20)) + 8)
            state['focus'] = _clamp_int(int(state.get('focus', 70)) + 5)
        state['social_need'] = _clamp_int(int(state.get('social_need', 40)) - 8)
        influence = {'active': True, 'player_name': player_name, 'message': message[:500], 'npc_reply': (npc_reply or '')[:500], 'directive': self._extract_directive(message), 'target_zone_hint': target_hint or '', 'influence_score': round(influence_score, 2), 'remaining_ticks': max(1, int(ttl_ticks)), 'created_at': datetime.now().isoformat(timespec='seconds'), 'reason': reason, 'interrupt_requested': bool(is_actionable and interrupt_current_episode)}
        state['player_influence'] = influence
        state['last_interaction'] = datetime.now().isoformat(timespec='seconds')
        if is_actionable and interrupt_current_episode:
            state['episode_id'] = ''
            state['action_phase'] = 'thinking'
            state['action_remaining_ticks'] = 0
            state['action_progress'] = 0.0
            state['current_action'] = f"收到{player_name}的新指令，准备让 DeepSeek 重新规划：{influence['directive'][:50]}"
            state['selected_tool'] = 'player_influence_router'
            state['reasoning_factors'] = [reason, '当前 episode 被玩家对话打断']
            state['velocity'] = {'x': 0.0, 'y': 0.0}
            state['speed'] = 0.0
        return dict(influence)

    def decay_player_influences(self) -> None:
        for state in self._states.values():
            influence = state.get('player_influence') or {}
            if not influence.get('active'):
                continue
            left = int(influence.get('remaining_ticks', 0)) - 1
            influence['remaining_ticks'] = max(0, left)
            if left <= 0:
                influence['active'] = False
            state['player_influence'] = influence

    def note_memory_summary(self, npc_id: str, summary: str) -> None:
        state = self._states.get(npc_id)
        if not state:
            return
        state['memory_summary_count'] = int(state.get('memory_summary_count', 0)) + 1
        state['last_memory_summary'] = str(summary or '')[:220]

    @staticmethod
    def _extract_directive(message: str) -> str:
        msg = re.sub('\\s+', ' ', (message or '').strip())
        if not msg:
            return '玩家进行了交流'
        for marker in ['请', '帮我', '帮', '先', '优先', '马上']:
            idx = msg.find(marker)
            if idx >= 0:
                return msg[idx:idx + 160]
        return msg[:160]

    @staticmethod
    def _infer_target_zone_from_text(text: str) -> str:
        text = (text or '').lower()
        mapping = [(('安全', '权限', '漏洞', '注入', '合规', '评审'), 'security_room'), (('后端', '监控', '接口', '稳定', 'deepseek', 'llmops', '并发', '超时'), 'server_corner'), (('算法', 'prompt', '记忆', 'rag', '上下文', '评估'), 'algo_pod'), (('架构', '白板', '服务', '技术方案', '中台'), 'architecture_board'), (('产品', '看板', '用户', '增长', '指标', '需求'), 'product_board'), (('demo', '演示', '可视化', '前端', '交互', '界面'), 'demo_zone'), (('会议', '同步', '讨论', '站会'), 'meeting_room'), (('面试', '招聘', '简历', '投递', '候选人'), 'interview_room'), (('咖啡', '休息', '恢复'), 'coffee_bar'), (('研发', '协作', '工位'), 'open_workspace')]
        for keys, zone in mapping:
            if any((k in text for k in keys)):
                return zone
        return ''

    def set_action(self, npc_id: str, action: str, mood: str | None=None) -> None:
        if npc_id in self._states:
            self._states[npc_id]['current_action'] = action
            if mood:
                self._states[npc_id]['mood'] = mood
            self._states[npc_id]['last_interaction'] = datetime.now().isoformat(timespec='seconds')

    def start_episode(self, npc_id: str, *, episode_id: str, intention: str, action: str, mood: str, target_zone: str, target_position: Position, current_task: str, plan_summary: str, selected_tool: str, duration_ticks: int, started_tick: int, reasoning_factors: list[str] | None=None, autonomy: float | None=None, action_kind: str='work', decision_source: str='local_generative') -> None:
        if npc_id not in self._states:
            return
        state = self._states[npc_id]
        state['episode_id'] = episode_id
        state['intention'] = intention
        state['current_action'] = f'前往{zone_name(target_zone)}：{action}'
        state['mood'] = mood
        state['target_zone'] = target_zone
        state['target_position'] = target_position.model_dump()
        state['current_task'] = current_task
        state['plan_summary'] = plan_summary
        state['selected_tool'] = selected_tool
        state['action_started_tick'] = started_tick
        state['action_duration_ticks'] = max(1, int(duration_ticks))
        state['action_remaining_ticks'] = max(1, int(duration_ticks))
        state['action_progress'] = 0.0
        state['action_phase'] = 'walking'
        state['reasoning_factors'] = reasoning_factors or []
        state['action_kind'] = action_kind or 'work'
        state['last_decision_source'] = decision_source
        if decision_source.startswith('deepseek'):
            state['llm_decision_count'] = int(state.get('llm_decision_count', 0)) + 1
        if autonomy is not None:
            state['autonomy'] = max(0.0, min(1.0, float(autonomy)))
        state['last_interaction'] = datetime.now().isoformat(timespec='seconds')

    def set_agent_plan(self, npc_id: str, *, action: str, mood: str, target_zone: str, target_position: Position, current_task: str, plan_summary: str, selected_tool: str) -> None:
        self.start_episode(npc_id, episode_id=f'legacy-{datetime.now().timestamp()}', intention=action, action=action, mood=mood, target_zone=target_zone, target_position=target_position, current_task=current_task, plan_summary=plan_summary, selected_tool=selected_tool, duration_ticks=2, started_tick=0, reasoning_factors=['兼容旧版 set_agent_plan'], action_kind='work', decision_source='legacy')

    def move_towards_target(self, npc_id: str, step: float=14.0) -> bool:
        state = self._states.get(npc_id)
        if not state or state.get('is_busy'):
            return False
        pos = state.get('position') or {'x': 0, 'y': 0}
        target = state.get('target_position') or pos
        dx, dy = (target['x'] - pos['x'], target['y'] - pos['y'])
        dist = hypot(dx, dy)
        if dist <= max(1.0, step):
            new_pos = {'x': target['x'], 'y': target['y']}
            reached = True
        else:
            ratio = step / dist
            new_pos = {'x': pos['x'] + dx * ratio, 'y': pos['y'] + dy * ratio}
            reached = False
        state['position'] = new_pos
        state['velocity'] = {'x': new_pos['x'] - pos['x'], 'y': new_pos['y'] - pos['y']}
        state['speed'] = round(hypot(state['velocity']['x'], state['velocity']['y']), 2)
        state['location'] = zone_name(nearest_zone(new_pos['x'], new_pos['y']))
        state['walk_ticks'] = int(state.get('walk_ticks', 0)) + 1
        if not reached and state['walk_ticks'] % 5 == 0:
            state['energy'] = _clamp_int(int(state.get('energy', 90)) - 1, 12, 100)
        if reached:
            state['walk_ticks'] = 0
            state['social_need'] = _clamp_int(int(state.get('social_need', 40)) + 1)
        return reached

    def begin_acting(self, npc_id: str) -> None:
        state = self._states.get(npc_id)
        if not state:
            return
        state['action_phase'] = 'acting'
        action = state.get('current_action', '执行行动')
        if '：' in action:
            action = action.split('：', 1)[1]
        state['current_action'] = f'正在{action}'
        state['velocity'] = {'x': 0.0, 'y': 0.0}
        state['speed'] = 0.0
        state['last_interaction'] = datetime.now().isoformat(timespec='seconds')

    def advance_action_progress(self, npc_id: str) -> bool:
        state = self._states.get(npc_id)
        if not state or state.get('is_busy'):
            return False
        duration = max(1, int(state.get('action_duration_ticks') or 1))
        remaining = max(0, int(state.get('action_remaining_ticks') or 0))
        if remaining > 0:
            remaining -= 1
        state['action_remaining_ticks'] = remaining
        state['action_progress'] = round(max(0.0, min(1.0, 1.0 - remaining / duration)), 3)
        kind = str(state.get('action_kind') or 'work')
        energy = int(state.get('energy', 90))
        stress = int(state.get('stress', 20))
        focus = int(state.get('focus', 70))
        social = int(state.get('social_need', 40))
        if kind == 'recover':
            energy += 7
            stress -= 4
            social -= 3
            focus += 1
        elif kind == 'sync':
            energy -= 1
            stress -= 2
            social -= 7
            focus += 1
        elif kind == 'derisk':
            energy -= 2
            stress -= 1 if remaining < duration * 0.5 else -1
            focus += 2
        elif kind == 'review':
            energy -= 1
            stress += 0 if remaining < duration * 0.5 else 1
            focus += 2
        elif kind == 'explore':
            energy -= 1
            stress -= 1
            social -= 1
            focus += 0
        elif kind == 'light_work':
            energy += 2
            stress -= 1
            focus += 1
        else:
            if remaining % 2 == 0:
                energy -= 1
            stress += 1 if remaining > duration * 0.65 else -1
            focus += 2
        state['energy'] = _clamp_int(energy, 12, 100)
        state['focus'] = _clamp_int(focus, 0, 100)
        state['stress'] = _clamp_int(stress, 0, 100)
        state['social_need'] = _clamp_int(social, 0, 100)
        state['last_interaction'] = datetime.now().isoformat(timespec='seconds')
        return remaining <= 0

    def finish_episode(self, npc_id: str, summary: str='') -> None:
        state = self._states.get(npc_id)
        if not state:
            return
        zone = state.get('target_zone') or 'open_workspace'
        recent = list(state.get('recent_zones') or [])
        recent.append(zone)
        state['recent_zones'] = recent[-8:]
        counts = dict(state.get('zone_visit_counts') or {})
        counts[zone] = int(counts.get(zone, 0)) + 1
        state['zone_visit_counts'] = counts
        state['completed_episodes'] = int(state.get('completed_episodes', 0)) + 1
        state['coffee_streak'] = int(state.get('coffee_streak', 0)) + 1 if zone == 'coffee_bar' else 0
        state['action_phase'] = 'cooldown'
        state['current_action'] = summary or '完成当前行动，准备重新观察环境'
        state['action_remaining_ticks'] = 0
        state['action_progress'] = 1.0
        state['velocity'] = {'x': 0.0, 'y': 0.0}
        state['speed'] = 0.0
        if state.get('action_kind') != 'recover':
            state['stress'] = _clamp_int(int(state.get('stress', 20)) - 2, 0, 100)
        state['social_need'] = _clamp_int(int(state.get('social_need', 40)) - 1, 0, 100)
        state['last_interaction'] = datetime.now().isoformat(timespec='seconds')

    def clear_episode_for_next_decision(self, npc_id: str) -> None:
        state = self._states.get(npc_id)
        if not state:
            return
        state['action_phase'] = 'thinking'
        state['episode_id'] = ''
        state['action_duration_ticks'] = 1
        state['action_remaining_ticks'] = 0
        state['action_progress'] = 0.0
        state['selected_tool'] = 'intent_synthesizer'
        state['action_kind'] = 'thinking'

    def add_profile(self, profile: NPCProfile) -> bool:
        if profile.npc_id in self._states:
            return False
        self._init([profile])
        return True

    def remove_agent(self, npc_id: str) -> bool:
        existed = npc_id in self._states
        self._states.pop(npc_id, None)
        self._locks.pop(npc_id, None)
        return existed

    def reset_all(self, profiles: list[NPCProfile]) -> None:
        self._states = {}
        self._locks = {}
        self._init(profiles)

    def count(self) -> int:
        return len(self._states)
