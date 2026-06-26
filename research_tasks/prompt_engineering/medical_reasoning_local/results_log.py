"""Live experiment log + dashboard for the medical_reasoning prompt-engineering run.

Experiment design enforced by run_iter.py:
  - DEV  (data.jsonl, n=20)      : evaluated EVERY iteration.
  - TEST (data_test.jsonl, n=100): evaluated every TEST_EVERY=3 dev iterations,
                                    on every new dev-best, and on the final run.

This module just persists results and (re)builds the dashboard files:
  results/leaderboard.jsonl   append-only run log (one JSON per line)
  results/leaderboard.json    snapshot array the dashboard fetches
  results/index.html          live (auto-refresh) dashboard: graph + table
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TASK_DIR = Path(__file__).resolve().parent
RESULTS_DIR = TASK_DIR / "results"
LEDGER = RESULTS_DIR / "leaderboard.jsonl"
SNAPSHOT = RESULTS_DIR / "leaderboard.json"
STATE = RESULTS_DIR / "state.json"
INDEX = RESULTS_DIR / "index.html"

TEST_EVERY = 3  # run test set every Nth dev iteration


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state() -> dict[str, Any]:
    if STATE.is_file():
        return json.loads(STATE.read_text())
    return {"iteration": 0, "best_dev_acc": -1.0, "best_dev_prompt": None}


def save_state(state: dict[str, Any]) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))


def read_records() -> list[dict[str, Any]]:
    if not LEDGER.is_file():
        return []
    out = []
    for line in LEDGER.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def append_run(*, iteration: int, prompt_name: str, split: str, result: dict[str, Any],
               trigger: str = "", is_dev_best: bool = False) -> dict[str, Any]:
    RESULTS_DIR.mkdir(exist_ok=True)
    rec = {
        "ts": _now(),
        "iteration": iteration,
        "prompt_name": prompt_name,
        "split": split,
        "n": result["n"],
        "n_correct": result["n_correct"],
        "n_errors": result.get("n_errors", 0),
        "accuracy": result["accuracy"],
        "by_task": result.get("accuracy_by_medical_task", {}),
        "model": result.get("model"),
        "trigger": trigger,
        "is_dev_best": is_dev_best,
    }
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    rebuild_dashboard()
    return rec


def rebuild_dashboard() -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    recs = read_records()
    SNAPSHOT.write_text(json.dumps(recs, indent=2, ensure_ascii=False), encoding="utf-8")
    if not INDEX.is_file():
        INDEX.write_text(_INDEX_HTML, encoding="utf-8")
    else:
        # keep template authoritative so edits to _INDEX_HTML propagate
        INDEX.write_text(_INDEX_HTML, encoding="utf-8")


_INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta http-equiv="refresh" content="5"/>
<title>MedXpertQA · Qwen3.5-27B · Prompt-Engineering Dashboard</title>
<style>
  :root{--dev:#2563eb;--test:#dc2626;--bg:#0f172a;--card:#1e293b;--fg:#e2e8f0;--muted:#94a3b8;--line:#334155;}
  body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:var(--bg);color:var(--fg);}
  .wrap{max-width:1080px;margin:0 auto;padding:24px;}
  h1{font-size:20px;margin:0 0 2px;}
  .sub{color:var(--muted);font-size:13px;margin-bottom:18px;}
  .cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px;}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;min-width:150px;}
  .card .k{color:var(--muted);font-size:12px;}
  .card .v{font-size:24px;font-weight:700;margin-top:4px;}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:20px;}
  .panel h2{font-size:14px;margin:0 0 10px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;}
  table{width:100%;border-collapse:collapse;font-size:13px;}
  th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);}
  th{color:var(--muted);font-weight:600;}
  tr:hover td{background:rgba(255,255,255,.03);}
  .dev{color:var(--dev);font-weight:700;}
  .test{color:var(--test);font-weight:700;}
  .best{background:rgba(37,99,235,.18);}
  .legend{display:flex;gap:18px;font-size:12px;color:var(--muted);margin-bottom:6px;}
  .dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:middle;}
  .pill{font-size:11px;padding:1px 7px;border-radius:999px;background:#334155;color:#cbd5e1;}
  .muted{color:var(--muted);}
</style>
</head>
<body>
<div class="wrap">
  <h1>MedXpertQA · Qwen3.5-27B · Prompt Engineering</h1>
  <div class="sub">Iterate on <b>dev (n=20)</b>; report <b>test (n=100)</b> every 3rd iter + new dev-best + final. Auto-refresh 5s · <span id="ts" class="muted"></span></div>

  <div class="cards" id="cards"></div>

  <div class="panel">
    <h2>Accuracy by iteration</h2>
    <div class="legend"><span><span class="dot" style="background:var(--dev)"></span>dev</span><span><span class="dot" style="background:var(--test)"></span>test</span></div>
    <div id="chart"></div>
  </div>

  <div class="panel">
    <h2>Runs</h2>
    <table id="tbl"><thead><tr>
      <th>iter</th><th>prompt</th><th>split</th><th>acc</th><th>correct</th><th>err</th><th>trigger</th><th>time (UTC)</th>
    </tr></thead><tbody></tbody></table>
  </div>
</div>

<script>
const W=1000,H=320,PAD=46;
function fmtPct(x){return (x*100).toFixed(1)+'%';}
function chart(recs){
  const dev=recs.filter(r=>r.split==='dev').sort((a,b)=>a.iteration-b.iteration);
  const test=recs.filter(r=>r.split==='test').sort((a,b)=>a.iteration-b.iteration);
  const its=recs.map(r=>r.iteration); const maxIt=Math.max(1,...its);
  const x=it=> PAD + (maxIt<=1?0:(it-1)/(maxIt-1))*(W-2*PAD);
  const y=a=> H-PAD - a*(H-2*PAD);
  let svg=`<svg viewBox="0 0 ${W} ${H}" width="100%" style="max-height:340px">`;
  // grid + y labels (0..1 every .25, plus .1 marks)
  for(let g=0; g<=1.0001; g+=0.25){
    svg+=`<line x1="${PAD}" y1="${y(g)}" x2="${W-PAD}" y2="${y(g)}" stroke="#334155" stroke-width="1"/>`;
    svg+=`<text x="${PAD-8}" y="${y(g)+4}" fill="#94a3b8" font-size="11" text-anchor="end">${(g*100).toFixed(0)}%</text>`;
  }
  // x labels (iterations) — thinned so 30 iters don't crowd
  const xs=[...new Set(its)].sort((a,b)=>a-b);
  const step=Math.max(1,Math.ceil(maxIt/12));
  xs.forEach(it=>{ if(it===1||it===maxIt||it%step===0) svg+=`<text x="${x(it)}" y="${H-PAD+18}" fill="#94a3b8" font-size="11" text-anchor="middle">${it}</text>`; });
  svg+=`<text x="${W/2}" y="${H-6}" fill="#94a3b8" font-size="11" text-anchor="middle">dev iteration</text>`;
  function series(data,color){
    if(!data.length) return '';
    let path=''; data.forEach((r,i)=>{ path+=(i?'L':'M')+x(r.iteration)+' '+y(r.accuracy)+' '; });
    let s=`<path d="${path}" fill="none" stroke="${color}" stroke-width="2.5"/>`;
    const showLabels=data.length<=12;
    data.forEach(r=>{ s+=`<circle cx="${x(r.iteration)}" cy="${y(r.accuracy)}" r="${data.length<=12?4:3}" fill="${color}"/>`;
      if(showLabels) s+=`<text x="${x(r.iteration)}" y="${y(r.accuracy)-9}" fill="${color}" font-size="10" text-anchor="middle">${fmtPct(r.accuracy)}</text>`;});
    return s;
  }
  svg+=series(dev,'#2563eb'); svg+=series(test,'#dc2626'); svg+='</svg>';
  if(!recs.length) svg='<div class="muted">No runs yet…</div>';
  document.getElementById('chart').innerHTML=svg;
}
function cards(recs){
  const dev=recs.filter(r=>r.split==='dev'); const test=recs.filter(r=>r.split==='test');
  const bestDev=dev.reduce((m,r)=>r.accuracy>m.accuracy?r:m, {accuracy:-1});
  const lastTest=test.length?test[test.length-1]:null;
  const iters=Math.max(0,...recs.map(r=>r.iteration));
  const c=[
    ['Dev iterations', iters||0],
    ['Best dev acc', bestDev.accuracy>=0?fmtPct(bestDev.accuracy)+' ('+bestDev.prompt_name+')':'—'],
    ['Latest test acc', lastTest?fmtPct(lastTest.accuracy)+' ('+lastTest.prompt_name+')':'—'],
    ['Test runs', test.length],
  ];
  document.getElementById('cards').innerHTML=c.map(([k,v])=>`<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
}
function table(recs){
  const bestDev=recs.filter(r=>r.split==='dev').reduce((m,r)=>r.accuracy>m.accuracy?r:m,{accuracy:-1,ts:''});
  const rows=[...recs].reverse().map(r=>{
    const cls=(r.split==='dev'&&r.ts===bestDev.ts)?'best':'';
    const sp=r.split==='dev'?'<span class="dev">dev</span>':'<span class="test">test</span>';
    return `<tr class="${cls}"><td>${r.iteration}</td><td>${r.prompt_name}</td><td>${sp}</td>
      <td><b>${fmtPct(r.accuracy)}</b></td><td>${r.n_correct}/${r.n}</td><td>${r.n_errors||0}</td>
      <td><span class="pill">${r.trigger||(r.split==='dev'?'iter':'')}</span></td><td class="muted">${(r.ts||'').replace('T',' ').replace('+00:00','')}</td></tr>`;
  }).join('');
  document.querySelector('#tbl tbody').innerHTML=rows||'<tr><td colspan="8" class="muted">No runs yet…</td></tr>';
}
async function load(){
  try{
    const recs=await (await fetch('leaderboard.json?_='+Date.now())).json();
    cards(recs); chart(recs); table(recs);
    document.getElementById('ts').textContent='updated '+new Date().toLocaleTimeString();
  }catch(e){ document.getElementById('chart').innerHTML='<div class="muted">waiting for data…</div>'; }
}
load();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    rebuild_dashboard()
    print(f"dashboard written to {INDEX}")
