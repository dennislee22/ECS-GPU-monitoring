import subprocess
import json
import os
import datetime
from flask import Flask, Response, render_template_string, abort

SSH_USERNAME = "cloud-user"
SSH_KEY_FILE = "/home/cdsw/ssh-ares"
REMOTE_NODES = [
    "node1.xyz.com",
    "node2.xyz.com",
    "node3.xyz.com",
]
LOG_FILE = "gpu-monitor.log"

app = Flask(__name__)

REMOTE_METRICS_SCRIPT = r"""
import json, subprocess, os

def cpu_metrics():
    result = {}
    try:
        import psutil
        result['cpu_total']    = psutil.cpu_percent(interval=0.5)
        result['cpu_per_core'] = psutil.cpu_percent(interval=0, percpu=True)
        freq = psutil.cpu_freq()
        result['freq_mhz']   = int(freq.current) if freq else None
        result['cpu_count']  = psutil.cpu_count(logical=True)
        mem = psutil.virtual_memory()
        result['mem_pct']      = round(mem.percent, 1)
        result['mem_used_gb']  = round(mem.used  / 1024**3, 1)
        result['mem_total_gb'] = round(mem.total / 1024**3, 1)
        la = os.getloadavg()
        result['load_avg'] = [round(la[0],2), round(la[1],2), round(la[2],2)]
    except Exception as e:
        result['cpu_error'] = str(e)
    return result

def gpu_metrics():
    gpus = []
    try:
        out = subprocess.check_output([
            'nvidia-smi',
            '--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw',
            '--format=csv,noheader,nounits'
        ], timeout=5).decode()
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(',')]
            if len(parts) < 7:
                continue
            idx, name, util, mem_used, mem_total, temp, power = parts
            mu, mt = int(mem_used), int(mem_total)
            gpus.append({
                'id':           int(idx),
                'name':         name,
                'util_pct':     int(util),
                'mem_pct':      round(mu / mt * 100) if mt else 0,
                'mem_used_gb':  round(mu  / 1024, 1),
                'mem_total_gb': round(mt  / 1024, 1),
                'temp_c':       int(temp),
                'power_w':      round(float(power)) if power not in ('N/A','[N/A]') else None,
            })
    except Exception:
        pass
    return gpus

data = cpu_metrics()
data['gpus']    = gpu_metrics()
data['num_gpu'] = len(data['gpus'])
print(json.dumps(data))
"""

def log_error(hostname: str, message: str, stderr_output: str = ""):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{timestamp}] [{hostname}] {message}\n")
        if stderr_output:
            f.write(f"STDERR:\n{stderr_output.strip()}\n")
            f.write("-" * 40 + "\n")

def fetch_metrics(hostname: str) -> dict:
    cmd = [
        'ssh',
        '-i', SSH_KEY_FILE,
        '-o', 'StrictHostKeyChecking=no',
        '-o', 'BatchMode=yes',
        '-o', 'ConnectTimeout=8',
        f'{SSH_USERNAME}@{hostname}',
        'python3 -'
    ]
    try:
        result = subprocess.run(cmd, input=REMOTE_METRICS_SCRIPT, timeout=15, capture_output=True, text=True)
        if result.returncode != 0:
            log_error(hostname, f"SSH error (exit {result.returncode})", result.stderr)
            return {'error': f'SSH error (exit {result.returncode})'}
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        log_error(hostname, "SSH timeout")
        return {'error': 'SSH timeout'}
    except json.JSONDecodeError:
        log_error(hostname, f"Bad JSON from remote. STDOUT was:\n{result.stdout.strip()}", result.stderr)
        return {'error': 'Bad JSON from remote'}
    except Exception as e:
        log_error(hostname, f"Unexpected error: {str(e)}")
        return {'error': str(e)}

@app.route('/metrics/<hostname>')
def metrics(hostname):
    if hostname not in REMOTE_NODES:
        abort(404)
    data = fetch_metrics(hostname)
    return Response(json.dumps(data), mimetype='application/json')

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, nodes=REMOTE_NODES)

HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <title>Node Monitor Dashboard</title>
  <style>
:root{
  --bg:#030712;--headerBg:#070d1a;--border:#0f1f35;
  --accent:#38bdf8;--accent2:#7dd3fc;
  --msgBg:#070d1a;--inputBg:#0a0f1e;
  --statusBg:#040b14;--dimText:#94a3b8; 
  --bodyText:#e2e8f0;
  --panelBg:#020a14;--panelBorder:#0f1f35;
  --panelLabel:#7dd3fc;--panelDim:#64748b;
  --textMain:#e2e8f0;
  --shimmer:linear-gradient(90deg,#38bdf8 0%,#818cf8 25%,#c084fc 50%,#38bdf8 75%,#00C1DE 100%);
  --font-mono: 'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  --font-sans: 'IBM Plex Sans', system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden}
body{font-family:var(--font-sans);background:var(--bg);color:var(--textMain);}
::-webkit-scrollbar{width:4px}
::-webkit-scrollbar-track{background:var(--bg)}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:4px}
button{cursor:pointer;font-family:inherit}
@keyframes shimmer{0%{background-position:-500px 0}100%{background-position:500px 0}}
@keyframes rdotPulse{0%,100%{opacity:1}50%{opacity:.3}}
.shiny{
  background:var(--shimmer);background-size:500px 100%;
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
  background-clip:text;animation:shimmer 4s linear infinite;
}
#hdr{
  padding:10px 20px;border-bottom:1px solid var(--border);
  background:var(--headerBg);display:flex;align-items:center;gap:12px;flex-shrink:0;
}
#title-block{display:flex;flex-direction:column;gap:2px}
#ttl{font-size:19px;font-weight:700;letter-spacing:-.02em;line-height:1;display:flex;align-items:center;gap:8px;}
#subtitle{font-size:11px;color:var(--dimText);font-family:var(--font-mono)}
#hdr-right{margin-left:auto;display:flex;align-items:center;gap:12px}
.contact-btn{font-family:var(--font-mono);font-size:11px;color:var(--accent);cursor:pointer;display:inline-flex;align-items:center;gap:4px;transition:color 0.2s;}
.contact-btn:hover{color:var(--accent2);text-decoration:underline;}
.hdr-divider{width:1px;height:14px;background:var(--border);margin:0 4px;}
#rdot{width:9px;height:9px;border-radius:50%;background:#ef4444;box-shadow:0 0 8px #ef444488;flex-shrink:0}
#rdot.live{background:#22c55e;box-shadow:0 0 8px #22c55e88;animation:rdotPulse 1.6s ease-in-out infinite}
#rtxt{font-size:11px;color:var(--dimText);font-family:var(--font-mono)}
#body{flex:1;display:flex;overflow:hidden}
#left-panel{
  width:380px;flex-shrink:0;display:flex;flex-direction:column;
  background:var(--panelBg);border-right:1px solid var(--panelBorder);
  font-family:var(--font-mono);
}
#left-panel-hdr{
  flex-shrink:0;padding:9px 12px;
  background:linear-gradient(135deg,var(--accent)22,var(--accent2)11);
  border-bottom:2px solid var(--accent)66;
  display:flex;align-items:center;gap:8px;
}
.pdh-title{font-size:10px;font-weight:700;letter-spacing:.06em;color:var(--accent2);text-transform:uppercase;line-height:1.2}
.pdh-sub{font-size:9px;color:var(--dimText);margin-top:1px}
.cat-badge-container {
  padding: 12px 10px 4px;
}
.cat-badge {
  display: inline-block;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 700;
  color: var(--bg);
  background: var(--accent);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.cat-badge.gpu-only {
  background: var(--accent2);
}
.node-list{display:flex;flex-direction:column;gap:4px;padding:8px 10px;overflow-y:auto;flex:1}
.node-btn{
  display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:6px;
  font-size:11px;font-family:var(--font-mono);font-weight:600;
  border:1px solid var(--panelBorder);background:var(--headerBg);
  color:var(--bodyText);text-align:left;width:100%;transition:all .15s;
}
.node-btn:hover{border-color:var(--accent);color:var(--accent2);background:var(--inputBg)}
.node-btn.active{border-color:var(--accent);color:var(--accent2);background:var(--inputBg);box-shadow:0 0 0 1px var(--accent)22 inset}
.node-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0;background:var(--panelDim);transition:background .2s}
.node-btn.active .node-dot{background:#22c55e;box-shadow:0 0 6px #22c55e88}
.node-hostname{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#dashboard{flex:1;display:flex;flex-direction:column;overflow:hidden;background:var(--bg)}
#dash-hdr{
  flex-shrink:0;padding:9px 16px;
  background:linear-gradient(135deg,var(--accent)22,var(--accent2)11);
  border-bottom:2px solid var(--accent)66;
  display:flex;align-items:center;gap:10px;font-family:var(--font-mono);
}
#dash-body{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:14px}
.panel{
  background:var(--panelBg);border:1px solid var(--panelBorder);
  border-radius:8px;display:flex;flex-direction:column;
  font-family:var(--font-mono);overflow:hidden;
}
.ph{
  padding:8px 12px 6px;border-bottom:1px solid var(--panelBorder);
  display:flex;justify-content:space-between;align-items:center;flex-shrink:0;
}
.ph-title{color:var(--panelLabel);font-weight:700;font-size:12px;letter-spacing:.04em}
.psec{padding:8px 12px 6px;flex-shrink:0}
.spark-wrap{
  background:var(--headerBg);border:1px solid var(--panelBorder);
  border-radius:4px;margin-bottom:6px;overflow:hidden;position:relative;
}
.spark-lbl{position:absolute;top:3px;right:5px;font-size:10px;color:var(--dimText)}
#cpu-dotbar{display:flex;gap:1px;align-items:center;overflow:hidden;flex:1}
.util-row{display:flex;align-items:center;gap:6px;margin-bottom:3px}
.util-lbl{font-size:11px;color:var(--bodyText);min-width:24px;font-weight:600;} 
.util-pct{font-size:12px;font-weight:700;min-width:38px;text-align:right}
.ubar-wrap{flex:1;position:relative;height:8px;border-radius:2px;overflow:hidden;background:var(--panelBorder)33}
.ubar-fill{position:absolute;top:0;left:0;bottom:0;border-radius:2px;transition:width .9s ease}
.ubar-dots{
  position:absolute;inset:0;
  background-image:repeating-linear-gradient(90deg,transparent,transparent 4px,var(--panelBg)66 4px,var(--panelBg)66 5px);
}
#cores-grid{padding:0 12px 8px;display:flex;gap:8px}
.core-col{flex:1;display:flex;flex-direction:column;gap:2px}
.core-row{display:flex;align-items:center;gap:3px}
.core-lbl{color:var(--dimText);min-width:20px;font-size:10px}
.core-track{flex:1;position:relative;height:7px}
.core-bg{position:absolute;inset:0;background:var(--panelBorder)33;border-radius:2px}
.core-fill{position:absolute;top:0;left:0;bottom:0;border-radius:2px;transition:width .8s ease}
.core-dots{
  position:absolute;inset:0;border-radius:2px;
  background-image:repeating-linear-gradient(90deg,transparent,transparent 4px,var(--panelBg)66 4px,var(--panelBg)66 5px);
}
.core-pct{min-width:28px;text-align:right;font-size:10px;color:var(--dimText)}
.load-sec{
  padding:5px 12px 7px;border-top:1px solid var(--panelBorder);
  display:flex;justify-content:space-between;align-items:center;flex-shrink:0;
}
.load-title{color:var(--panelLabel);font-weight:700;font-size:11px}
#load-avg{color:var(--dimText);font-size:11px;letter-spacing:.06em}
.gpu-cards-wrap{
  padding:10px 12px;display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px;
}
.gpu-card{
  background:var(--headerBg);border:1px solid var(--panelBorder);
  border-radius:8px;padding:10px 12px;display:flex;flex-direction:column;gap:7px;
}
.gpu-chdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:2px}
.gpu-idx{color:var(--panelLabel);font-weight:700;font-size:12px}
.gpu-name{color:var(--dimText);font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.g-bar-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:3px}
.g-bar-lbl{color:var(--bodyText);font-size:11px;font-weight:600;} 
.g-bar-val{font-size:12px;font-weight:700}
.g-track{height:9px;background:var(--panelBorder)33;border-radius:3px;position:relative;overflow:hidden}
.g-fill{position:absolute;top:0;left:0;bottom:0;border-radius:3px;transition:width 1s ease}
.g-stripe{
  position:absolute;inset:0;
  background-image:repeating-linear-gradient(90deg,transparent,transparent 4px,var(--panelBg)55 4px,var(--panelBg)55 5px);
}
.vram-track{height:7px;background:var(--panelBorder)33;border-radius:3px;position:relative;overflow:hidden}
.vram-fill{position:absolute;top:0;left:0;bottom:0;border-radius:3px;transition:width 1s ease}
.gpu-meta{display:flex;gap:10px;font-size:11px}
#metric-grid{display:flex;gap:14px}
#metric-grid>.panel{flex:1;min-width:0}
#dash-empty{
  flex:1;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:14px;padding:60px 20px;
  font-family:var(--font-mono);text-align:center;
}
#dash-empty svg{opacity:.18}
#dash-empty p{font-size:12px;color:var(--dimText);line-height:1.65}
#dash-error{
  padding:10px 16px;font-size:12px;font-family:var(--font-mono);
  color:#f87171;background:#1c0a0a;border:1px solid #7f1d1d;
  border-radius:6px;display:none;
}
  </style>
</head>
<body>
<div id="app">

  <div id="hdr">
    <div id="title-block">
      <div id="ttl" class="shiny">
        <svg width="24" height="24" viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg" fill="none" stroke="currentColor" stroke-width="14" stroke-linecap="round" stroke-linejoin="round">
          <rect x="40" y="40" width="120" height="120" rx="15"/>
          <text x="100" y="123" text-anchor="middle" font-size="46" font-family="Arial, sans-serif" font-weight="900" fill="currentColor" stroke="none">GPU</text>
          <g>
            <line x1="75" y1="20" x2="75" y2="40"/>
            <line x1="125" y1="20" x2="125" y2="40"/>
          </g>
          <g>
            <line x1="75" y1="160" x2="75" y2="180"/>
            <line x1="125" y1="160" x2="125" y2="180"/>
          </g>
          <g>
            <line x1="20" y1="75" x2="40" y2="75"/>
            <line x1="20" y1="125" x2="40" y2="125"/>
          </g>
          <g>
            <line x1="160" y1="75" x2="180" y2="75"/>
            <line x1="160" y1="125" x2="180" y2="125"/>
          </g>
        </svg>
        GPU + 
        <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="4" y="4" width="16" height="16" rx="2" ry="2"></rect><rect x="9" y="9" width="6" height="6"></rect>
          <line x1="9" y1="1" x2="9" y2="4"></line><line x1="15" y1="1" x2="15" y2="4"></line>
          <line x1="9" y1="20" x2="9" y2="23"></line><line x1="15" y1="20" x2="15" y2="23"></line>
          <line x1="20" y1="9" x2="23" y2="9"></line><line x1="20" y1="14" x2="23" y2="14"></line>
          <line x1="1" y1="9" x2="4" y2="9"></line><line x1="1" y1="14" x2="4" y2="14"></line>
        </svg>
        CPU Monitoring
      </div>
      <div id="subtitle">Live ECS Node Resource Monitoring</div>
    </div>
    
    <div id="hdr-right">
      <div class="contact-btn" onclick="copyEmail(this)">
        <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path><polyline points="22,6 12,13 2,6"></polyline></svg>
        <span>dennislee@cloudera.com</span>
      </div>
      <div class="hdr-divider"></div>
      <div id="rdot"></div>
      <span id="rtxt">idle</span>
    </div>
  </div>

  <div id="body">
    <div id="left-panel">
      <div id="left-panel-hdr">
        <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>
        <div><div class="pdh-title">Nodes</div><div class="pdh-sub">{{ nodes|length }} configured</div></div>
      </div>
      
      <div class="cat-badge-container">
        <span class="cat-badge">GPU + CPU + RAM Monitoring</span>
      </div>
      <div class="node-list">
        {% for node in nodes %}
        <button class="node-btn" data-hostname="{{ node }}" data-view="std" onclick="selectNode(this)"><div class="node-dot"></div><span class="node-hostname" title="{{ node }}">{{ node }}</span></button>
        {% endfor %}
      </div>
      
      <div class="cat-badge-container">
        <span class="cat-badge gpu-only">8 x GPU per Node Monitoring</span>
      </div>
      <div class="node-list">
        {% for node in nodes %}
        <button class="node-btn" data-hostname="{{ node }}" data-view="gpu" onclick="selectNode(this)"><div class="node-dot"></div><span class="node-hostname" title="{{ node }}">{{ node }}</span></button>
        {% endfor %}
      </div>
    </div>

    <div id="dashboard">
      <div id="dash-hdr">
        <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="12" width="4" height="9" rx="1"/><rect x="10" y="7" width="4" height="14" rx="1"/><rect x="17" y="3" width="4" height="18" rx="1"/></svg>
        <div><div class="pdh-title" id="dash-node-title" style="font-size:18px;line-height:18px;text-transform:none">Node Dashboard</div></div>
      </div>
      <div id="dash-body">
        <div id="dash-error"></div>
        <div id="dash-empty"><svg xmlns="http://www.w3.org/2000/svg" width="52" height="52" viewBox="0 0 24 24" fill="none" stroke="var(--accent2)" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="12" width="4" height="9" rx="1"/><rect x="10" y="7" width="4" height="14" rx="1"/><rect x="17" y="3" width="4" height="18" rx="1"/></svg><p>Select a node from the left panel</p></div>
        <div class="panel" id="gpu-panel" style="display:none"><div class="ph"><span class="ph-title">GPU MONITOR</span><span class="ph-sub" id="gpu-count-lbl"></span></div><div class="gpu-cards-wrap" id="gpu-cards"></div><div id="gpu-none-msg" style="display:none;padding:12px;font-size:11px;color:var(--dimText);text-align:center">No NVIDIA GPU detected</div></div>
        <div id="metric-grid" style="display:none">
          <div class="panel"><div class="ph"><span class="ph-title">CPU MONITOR</span><span class="ph-sub" id="cpu-freq-lbl"></span></div><div class="psec"><div class="spark-wrap"><svg id="sparkline" width="100%" height="28"></svg><span class="spark-lbl">60s</span></div><div class="util-row"><span class="util-lbl">CPU</span><div id="cpu-dotbar"></div><span id="cpu-total-pct" class="util-pct"></span></div></div><div id="cores-grid"></div><div class="load-sec"><span class="load-title">LOAD AVG</span><span id="load-avg"></span></div></div>
          <div class="panel"><div class="ph"><span class="ph-title">RAM MONITOR</span><span class="ph-sub" id="mem-gb"></span></div><div class="psec"><div class="spark-wrap"><svg id="ram-sparkline" width="100%" height="28"></svg><span class="spark-lbl">60s</span></div><div class="util-row"><span class="util-lbl">RAM</span><div class="ubar-wrap"><div id="mem-fill" class="ubar-fill"></div><div class="ubar-dots"></div></div><span id="mem-pct" class="util-pct"></span></div></div></div>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
(function(){
'use strict';
window.copyEmail = function(btn) {
  navigator.clipboard.writeText('dennislee@cloudera.com');
  const span = btn.querySelector('span');
  const original = span.textContent;
  span.textContent = 'Copied!';
  setTimeout(() => span.textContent = original, 2000);
};

let pollTimer = null, activeNode = null, currentView = 'std';
const sparkPts = [], ramPts = [];

function cpuCol(p){ return p<40?'hsl('+(220-p*0.5)+',80%,52%)':p<70?'hsl('+(200-(p-40)*3)+',85%,48%)':'hsl('+(110-(p-70)*3)+',80%,40%)'; }
function gpuCol(p){ return p<50?'hsl('+(190-p*0.6)+',85%,45%)':p<80?'hsl('+(160-(p-50)*4)+',80%,42%)':'hsl('+(40-(p-80)*2)+',85%,42%)'; }
function tempCol(c){ return c<60?'#3b82f6':c<75?'#8b5cf6':'#dc2626'; }
function esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function renderSpark(svg, pts, pct){
  const w = svg.clientWidth || 300, h = 28;
  if(pts.length < 2) return;
  const col = cpuCol(pct), mapped = pts.map((v,i)=>((i/(pts.length-1))*w)+','+(h-(v/100)*h)).join(' ');
  svg.innerHTML = '<defs><linearGradient id="sg'+svg.id+'" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="'+col+'" stop-opacity="0.35"/><stop offset="100%" stop-color="'+col+'" stop-opacity="0.03"/></linearGradient></defs><polyline points="'+mapped+'" fill="none" stroke="'+col+'" stroke-width="1.5"/><polygon points="0,'+h+' '+mapped+' '+w+','+h+'" fill="url(#sg'+svg.id+')"/>';
}

function renderDots(el, pct){
  const N=32, filled=Math.round((pct/100)*N); let h='';
  for(let i=0;i<N;i++){
    const a=i<filled, col=a?cpuCol((i/N)*100):'transparent';
    h+='<div style="width:4px;height:8px;border-radius:1px;flex-shrink:0;background:'+col+';border:1px solid '+(a?col:'var(--panelBorder)22')+'"></div>';
  }
  el.innerHTML=h;
}

function renderCores(el, cores){
  if(!cores) return;
  const cols=cores.length>12?3:cores.length>6?2:1, perCol=Math.ceil(cores.length/cols); let h='';
  for(let ci=0;ci<cols;ci++){
    h+='<div class="core-col">';
    cores.slice(ci*perCol,(ci+1)*perCol).forEach((pct,i)=>{
      const idx=ci*perCol+i, col=cpuCol(pct);
      h+='<div class="core-row"><span class="core-lbl">C'+(idx+1)+'</span><div class="core-track"><div class="core-bg"></div><div class="core-fill" style="width:'+pct+'%;background:'+col+'"></div><div class="core-dots"></div></div><span class="core-pct">'+pct+'%</span></div>';
    });
    h+='</div>';
  }
  el.innerHTML=h;
}

function renderGpus(el, gpus){
  while (el.children.length < gpus.length) {
    const c = document.createElement('div'); c.className = 'gpu-card';
    c.innerHTML = '<div class="gpu-chdr"><span class="gpu-idx"></span><span class="gpu-name"></span></div><div><div class="g-bar-row"><span class="g-bar-lbl">Utilisation</span><span class="g-bar-val"></span></div><div class="g-track"><div class="g-fill"></div><div class="g-stripe"></div></div></div><div><div class="g-bar-row"><span class="g-bar-lbl">VRAM</span><span class="g-bar-val" style="font-weight:400;font-size:11px"></span></div><div class="vram-track"><div class="vram-fill"></div></div></div><div class="gpu-meta"><span class="gpu-temp"></span><span class="gpu-power" style="color:var(--dimText)"></span></div>';
    el.appendChild(c);
  }
  while (el.children.length > gpus.length) el.removeChild(el.lastChild);
  gpus.forEach((gpu, i) => {
    const card = el.children[i], uc = gpuCol(gpu.util_pct), mc = gpuCol(gpu.mem_pct), tc = tempCol(gpu.temp_c);
    card.querySelector('.gpu-idx').textContent = 'GPU ' + gpu.id; card.querySelector('.gpu-name').textContent = esc(gpu.name.replace(/NVIDIA\s*|GeForce\s*/gi,''));
    const uv = card.querySelectorAll('.g-bar-val')[0]; uv.textContent = gpu.util_pct + '%'; uv.style.color = uc;
    const uf = card.querySelector('.g-fill'); uf.style.width = gpu.util_pct + '%'; uf.style.background = uc; uf.style.boxShadow = '0 0 5px ' + uc + '88';
    const vv = card.querySelectorAll('.g-bar-val')[1]; vv.textContent = gpu.mem_used_gb + ' / ' + gpu.mem_total_gb + ' GB'; vv.style.color = mc;
    card.querySelector('.vram-fill').style.width = gpu.mem_pct + '%'; card.querySelector('.vram-fill').style.background = mc;
    const te = card.querySelector('.gpu-temp'); te.textContent = gpu.temp_c + '°C'; te.style.color = tc; te.style.fontWeight = '700';
    card.querySelector('.gpu-power').textContent = gpu.power_w ? '⚡ ' + gpu.power_w + ' W' : '';
  });
}

function applyMetrics(d){
  document.getElementById('rdot').className='live'; document.getElementById('rtxt').textContent='live \u00b7 '+activeNode;
  document.getElementById('dash-empty').style.display='none'; document.getElementById('dash-error').style.display='none';
  document.getElementById('gpu-panel').style.display='flex'; 
  
  if (currentView === 'gpu') {
    document.getElementById('metric-grid').style.display='none';
  } else {
    document.getElementById('metric-grid').style.display='flex';
  }
  
  const gpus=d.gpus||[]; document.getElementById('gpu-count-lbl').textContent= gpus.length + ' device' + (gpus.length!==1?'s':'');
  document.getElementById('gpu-none-msg').style.display=gpus.length?'none':'block'; renderGpus(document.getElementById('gpu-cards'), gpus);
  const cpu=d.cpu_total||0; sparkPts.push(cpu); if(sparkPts.length>60) sparkPts.shift(); renderSpark(document.getElementById('sparkline'), sparkPts, cpu);
  renderDots(document.getElementById('cpu-dotbar'), cpu); const cp=document.getElementById('cpu-total-pct'); cp.textContent=cpu+'%'; cp.style.color=cpuCol(cpu);
  document.getElementById('cpu-freq-lbl').textContent= (d.freq_mhz?(d.freq_mhz/1000).toFixed(1)+' GHz':'--')+' \u00b7 '+(d.cpu_count||'--')+'C';
  renderCores(document.getElementById('cores-grid'), d.cpu_per_core);
  document.getElementById('load-avg').textContent=(d.load_avg||['--','--','--']).join('  ');
  const mp=d.mem_pct||0; ramPts.push(mp); if(ramPts.length>60) ramPts.shift(); renderSpark(document.getElementById('ram-sparkline'), ramPts, mp);
  document.getElementById('mem-fill').style.width=mp+'%'; document.getElementById('mem-fill').style.background=cpuCol(mp);
  const rp=document.getElementById('mem-pct'); rp.textContent=mp+'%'; rp.style.color=cpuCol(mp);
  document.getElementById('mem-gb').textContent=(d.mem_used_gb||0)+' / '+(d.mem_total_gb||0)+' GB';
}

async function poll(){
  if(!activeNode) return;
  try{
    const r=await fetch('/metrics/'+encodeURIComponent(activeNode)); const d=await r.json();
    if(d.error) throw new Error(d.error); applyMetrics(d);
  } catch(err){
    document.getElementById('rdot').className=''; document.getElementById('rtxt').textContent='error';
    const e=document.getElementById('dash-error'); e.style.display='block'; e.textContent='⚠ '+err.message;
  }
}

window.selectNode = function(btn){
  document.querySelectorAll('.node-btn').forEach(b=>b.classList.remove('active')); btn.classList.add('active');
  activeNode=btn.dataset.hostname; 
  currentView=btn.dataset.view;
  sparkPts.length=0; ramPts.length=0;
  document.getElementById('dash-node-title').textContent=activeNode; document.getElementById('rdot').className=''; document.getElementById('rtxt').textContent='connecting...';
  if(pollTimer) clearInterval(pollTimer); poll(); pollTimer=setInterval(poll, 1500);
};
})();
</script>
</body>
</html>
"""

if __name__ == '__main__':
    port = int(os.environ.get("CDSW_READONLY_PORT", 8080))
    app.run(host='127.0.0.1', port=port, debug=True, threaded=True, use_reloader=False)
