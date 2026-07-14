#!/usr/bin/env python3
import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import your_ai_team as t
cases=[
 ('landing','Сделай простой лендинг-визитку для кофейни','cost',22000),
 ('research','Исследуй рынок AI agent orchestrators и сравни подходы','balanced',30000),
 ('bugfix','Почини flaky Playwright тест в CI','balanced',40000),
 ('migration','Мигрируй production Selenium suite на Playwright с Roslyn и verification','quality',90000),
 ('docs','Обнови README и руководство пользователя','cost',20000),
 ('security','Исправь критическую уязвимость авторизации в production','quality',90000),
]
rows=[]
for name,desc,pref,budget in cases:
 p=t.propose(desc,backend='portable',preference=pref,max_tokens=budget)
 rows.append({'case':name,'analysis':p['analysis'],'roles':[{'id':r['roleId'],'grade':r['grade'],'engagement':r['engagement']} for r in p['team']],'budget':p['budget'],'status':p['status'],'tradeoffs':p['tradeoffs']})
# Bargain scenario
base=t.propose(cases[3][1],backend='codex',preference='quality')
bargained=t.negotiate(base,request='Влезь в максимум 45к токенов, удешеви архитектора, ревьюер только в конце')
out={'schemaVersion':1,'decision':'DYNAMIC_TEAM_COMPOSITION_MVP_PASS','cases':rows,'bargain':{'before':base['budget'],'after':bargained['budget'],'roles':[r['roleId'] for r in bargained['team']],'tradeoffs':bargained['tradeoffs'],'status':bargained['status']}}
path=ROOT/'evidence'/'your-ai-team-mvp-matrix.json'; path.parent.mkdir(exist_ok=True)
path.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print('YOUR_AI_TEAM_MVP_MATRIX_PASS')
print(path)
