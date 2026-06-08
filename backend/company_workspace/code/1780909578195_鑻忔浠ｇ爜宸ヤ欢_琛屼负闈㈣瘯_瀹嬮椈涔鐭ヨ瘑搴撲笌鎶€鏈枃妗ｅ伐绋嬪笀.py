from statistics import mean

def evaluate_company_signal(metrics):
    values = [float(v) for v in metrics.values() if isinstance(v, (int, float))]
    base = mean(values) if values else 0.0
    risk_penalty = float(metrics.get("risk", 0)) * 0.4
    velocity_bonus = float(metrics.get("velocity", 0)) * 0.2
    return round(base + velocity_bonus - risk_penalty, 3)

sample = {"quality": 82, "velocity": 12, "risk": 8}
print({"purpose": "行为面试：宋闻书 / 知识库与技术文档工程师", "score": evaluate_company_signal(sample)})
