"""Build a SELF-CONTAINED dashboard you can open directly (file://), no server.

Reads results/leaderboard.jsonl and writes results/dashboard.html with the data
baked in as a JS array, so double-clicking the file shows the graph + table.
It auto-refreshes every 5s (meta refresh re-reads the file); run this with
--watch to keep regenerating the file as the search appends new rows.

    python make_dashboard.py            # build once
    python make_dashboard.py --watch    # rebuild every few seconds
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parent
LEDGER = TASK_DIR / "results" / "leaderboard.jsonl"
STATUS = TASK_DIR / "results" / "status.json"
OUT = TASK_DIR / "results" / "dashboard.html"

HTML = r"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"/><meta http-equiv="refresh" content="5"/>
<title>MedXpertQA · Qwen3.5-27B · Prompt-Engineering Dashboard</title>
<style>
 :root{--dev:#2563eb;--test:#dc2626;--bg:#0f172a;--card:#1e293b;--fg:#e2e8f0;--muted:#94a3b8;--line:#334155;}
 body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:var(--bg);color:var(--fg);}
 .wrap{max-width:1080px;margin:0 auto;padding:24px;} h1{font-size:20px;margin:0 0 2px;}
 .sub{color:var(--muted);font-size:13px;margin-bottom:18px;}
 .cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px;}
 .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;min-width:150px;}
 .card .k{color:var(--muted);font-size:12px;} .card .v{font-size:24px;font-weight:700;margin-top:4px;}
 .panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:20px;}
 .panel h2{font-size:14px;margin:0 0 10px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;}
 table{width:100%;border-collapse:collapse;font-size:13px;} th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);}
 th{color:var(--muted);font-weight:600;} tr:hover td{background:rgba(255,255,255,.03);}
 .dev{color:var(--dev);font-weight:700;} .test{color:var(--test);font-weight:700;} .best{background:rgba(37,99,235,.18);}
 .legend{display:flex;gap:18px;font-size:12px;color:var(--muted);margin-bottom:6px;}
 .dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:middle;}
 .pill{font-size:11px;padding:1px 7px;border-radius:999px;background:#334155;color:#cbd5e1;} .muted{color:var(--muted);}
 .status{display:flex;align-items:center;gap:10px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:18px;font-size:13px;}
 .live{display:inline-block;width:10px;height:10px;border-radius:50%;background:#22c55e;animation:pulse 1.2s ease-in-out infinite;}
 .idle{display:inline-block;width:10px;height:10px;border-radius:50%;background:#64748b;}
 @keyframes pulse{0%,100%{opacity:1;transform:scale(1);}50%{opacity:.35;transform:scale(.7);}}
 .status b{color:var(--fg);}
</style></head><body><div class="wrap">
 <h1>MedXpertQA · Qwen3.5-27B · Prompt Engineering</h1>
 <div class="sub">Engineer the prompt on <b>dev (n=20)</b> (LLM refines against dev failures); <b>test (n=100)</b> scored at iter 0, 3, 6, … (selection by dev only). Auto-refresh 5s · <span id="ts" class="muted"></span></div>
 <div class="status" id="status"></div>
 <div class="cards" id="cards"></div>
 <div class="panel"><h2>Accuracy by iteration</h2>
  <div class="legend"><span><span class="dot" style="background:var(--dev)"></span>dev</span><span><span class="dot" style="background:var(--test)"></span>test</span></div>
  <div id="chart"></div></div>
 <div class="panel"><h2>Runs</h2><table id="tbl"><thead><tr>
  <th>iter</th><th>prompt</th><th>split</th><th>acc</th><th>correct</th><th>err</th><th>trigger</th><th>time (UTC)</th>
 </tr></thead><tbody></tbody></table></div></div>
<script>
const RECS=__DATA__;
const STATUS=__STATUS__;
const W=1000,H=320,PAD=46; const f=x=>(x*100).toFixed(1)+'%';
function status(st){const el=document.getElementById('status');
 if(!st){el.innerHTML='<span class="idle"></span><span class="muted">no run state yet</span>';return;}
 if(st.running){const p=st.prompt?(' · prompt <b>'+st.prompt+'</b>'):'';
  el.innerHTML=`<span class="live"></span><span><b>Running</b> · iter <b>${st.iteration}</b>/${st.total} · ${st.phase}${p}`+
   (st.best_dev!=null?` · best dev <b>${f(st.best_dev)}</b>`:'')+`</span>`;}
 else{el.innerHTML=`<span class="idle"></span><span><b>Idle</b> — ${st.phase||'done'} · best dev `+
   (st.best_dev!=null?`<b>${f(st.best_dev)}</b>`:'—')+`</span>`;}}
function chart(recs){
 const dev=recs.filter(r=>r.split==='dev').sort((a,b)=>a.iteration-b.iteration);
 const test=recs.filter(r=>r.split==='test').sort((a,b)=>a.iteration-b.iteration);
 const its=recs.map(r=>r.iteration), maxIt=Math.max(1,...its), minIt=Math.min(0,...its);
 const x=it=>PAD+(maxIt<=minIt?0:(it-minIt)/(maxIt-minIt))*(W-2*PAD), y=a=>H-PAD-a*(H-2*PAD);
 let s=`<svg viewBox="0 0 ${W} ${H}" width="100%" style="max-height:340px">`;
 for(let g=0;g<=1.0001;g+=0.25){s+=`<line x1="${PAD}" y1="${y(g)}" x2="${W-PAD}" y2="${y(g)}" stroke="#334155"/>`;
  s+=`<text x="${PAD-8}" y="${y(g)+4}" fill="#94a3b8" font-size="11" text-anchor="end">${(g*100).toFixed(0)}%</text>`;}
 const step=Math.max(1,Math.ceil((maxIt-minIt)/12)); [...new Set(its)].sort((a,b)=>a-b).forEach(it=>{
  if(it===minIt||it===maxIt||it%step===0) s+=`<text x="${x(it)}" y="${H-PAD+18}" fill="#94a3b8" font-size="11" text-anchor="middle">${it}</text>`;});
 s+=`<text x="${W/2}" y="${H-6}" fill="#94a3b8" font-size="11" text-anchor="middle">dev iteration</text>`;
 const ser=(d,c)=>{if(!d.length)return'';let p='';d.forEach((r,i)=>p+=(i?'L':'M')+x(r.iteration)+' '+y(r.accuracy)+' ');
  let o=`<path d="${p}" fill="none" stroke="${c}" stroke-width="2.5"/>`; const lab=d.length<=12;
  d.forEach(r=>{o+=`<circle cx="${x(r.iteration)}" cy="${y(r.accuracy)}" r="${lab?4:3}" fill="${c}"/>`;
   if(lab)o+=`<text x="${x(r.iteration)}" y="${y(r.accuracy)-9}" fill="${c}" font-size="10" text-anchor="middle">${f(r.accuracy)}</text>`;});return o;};
 s+=ser(dev,'#2563eb')+ser(test,'#dc2626')+'</svg>';
 document.getElementById('chart').innerHTML=recs.length?s:'<div class="muted">No runs yet…</div>';
}
function cards(recs){const dev=recs.filter(r=>r.split==='dev'),test=recs.filter(r=>r.split==='test');
 const bd=dev.reduce((m,r)=>r.accuracy>m.accuracy?r:m,{accuracy:-1}),lt=test.length?test[test.length-1]:null;
 const it=Math.max(0,...recs.map(r=>r.iteration));
 const c=[['Dev iterations',it||0],['Best dev acc',bd.accuracy>=0?f(bd.accuracy)+' ('+bd.prompt_name+')':'—'],
  ['Latest test acc',lt?f(lt.accuracy)+' ('+lt.prompt_name+')':'—'],['Test runs',test.length]];
 document.getElementById('cards').innerHTML=c.map(([k,v])=>`<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');}
function table(recs){const bd=recs.filter(r=>r.split==='dev').reduce((m,r)=>r.accuracy>m.accuracy?r:m,{accuracy:-1,ts:''});
 document.querySelector('#tbl tbody').innerHTML=([...recs].reverse().map(r=>{const cls=(r.split==='dev'&&r.ts===bd.ts)?'best':'';
  const sp=r.split==='dev'?'<span class="dev">dev</span>':'<span class="test">test</span>';
  return `<tr class="${cls}"><td>${r.iteration}</td><td>${r.prompt_name}</td><td>${sp}</td><td><b>${f(r.accuracy)}</b></td>
   <td>${r.n_correct}/${r.n}</td><td>${r.n_errors||0}</td><td><span class="pill">${r.trigger||''}</span></td>
   <td class="muted">${(r.ts||'').replace('T',' ').replace('+00:00','')}</td></tr>`;}).join(''))||'<tr><td colspan="8" class="muted">No runs yet…</td></tr>';}
status(STATUS);cards(RECS);chart(RECS);table(RECS);document.getElementById('ts').textContent='built '+new Date().toLocaleTimeString();
</script></body></html>"""


def build() -> int:
    recs = []
    if LEDGER.is_file():
        for line in LEDGER.read_text().splitlines():
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    status = None
    if STATUS.is_file():
        try:
            status = json.loads(STATUS.read_text())
        except Exception:
            status = None
    OUT.parent.mkdir(exist_ok=True)
    html = HTML.replace("__DATA__", json.dumps(recs)).replace("__STATUS__", json.dumps(status))
    OUT.write_text(html, encoding="utf-8")
    return len(recs)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=float, default=3.0)
    args = ap.parse_args()
    n = build()
    print(f"wrote {OUT} ({n} rows)")
    if args.watch:
        while True:
            time.sleep(args.interval)
            build()


if __name__ == "__main__":
    main()
