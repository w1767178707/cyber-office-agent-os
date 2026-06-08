from __future__ import annotations
from ..models import OfficeZone, Position
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 620
ZONES: dict[str, dict] = {'entrance': {'name': '前台 / 门禁', 'kind': 'public', 'rect': [35, 40, 120, 100], 'description': '访客进入办公室后的第一站。'}, 'architecture_board': {'name': '架构白板区', 'kind': 'tech', 'rect': [180, 50, 210, 150], 'description': 'CTO 拆解 Agent 架构和模块边界。'}, 'product_board': {'name': '产品看板区', 'kind': 'product', 'rect': [430, 50, 210, 150], 'description': '产品经理维护 PRD、用户故事和优先级。'}, 'algo_pod': {'name': '算法工位', 'kind': 'algorithm', 'rect': [670, 50, 190, 150], 'description': '算法工程师做 Prompt、Memory 与 Eval 实验。'}, 'security_room': {'name': '安全评审室', 'kind': 'security', 'rect': [895, 50, 150, 175], 'description': '进行提示注入、权限和审计评审。'}, 'meeting_room': {'name': '玻璃会议室', 'kind': 'meeting', 'rect': [60, 255, 265, 165], 'description': '站会、需求评审和技术方案对齐。'}, 'open_workspace': {'name': '开放研发区', 'kind': 'workspace', 'rect': [355, 255, 360, 165], 'description': '研发同学协作编码和联调。'}, 'server_corner': {'name': '后端监控角', 'kind': 'ops', 'rect': [745, 270, 300, 145], 'description': '观察 LLM 调用链、日志、成本和健康状态。'}, 'demo_zone': {'name': '路演 Demo 区', 'kind': 'demo', 'rect': [160, 455, 315, 120], 'description': '候选人和团队展示完整项目。'}, 'coffee_bar': {'name': '咖啡吧', 'kind': 'social', 'rect': [505, 455, 190, 120], 'description': '非正式沟通和灵感碰撞。'}, 'interview_room': {'name': '面试间', 'kind': 'hr', 'rect': [730, 455, 315, 120], 'description': '模拟项目面试、复盘和简历打磨。'}}

def list_zones() -> list[OfficeZone]:
    return [OfficeZone(zone_id=k, name=v['name'], kind=v['kind'], rect=v['rect'], description=v['description']) for k, v in ZONES.items()]

def zone_center(zone_id: str) -> Position:
    z = ZONES.get(zone_id) or ZONES['open_workspace']
    x, y, w, h = z['rect']
    return Position(x=x + w / 2, y=y + h / 2)

def zone_name(zone_id: str) -> str:
    return (ZONES.get(zone_id) or ZONES['open_workspace'])['name']

def nearest_zone(x: float, y: float) -> str:
    best_id, best_d = ('open_workspace', float('inf'))
    for zid, z in ZONES.items():
        c = zone_center(zid)
        d = (c.x - x) ** 2 + (c.y - y) ** 2
        if d < best_d:
            best_id, best_d = (zid, d)
    return best_id
