# app.py — Render 部署就绪（支持 / 与 /api/* 双路径，SSE 稳定，健康检查）
from flask import Flask, request, render_template_string, jsonify, Response
from treys import Card, Evaluator
from flask_cors import CORS
import os, re, json, random, time, uuid, threading
from datetime import datetime, timedelta

# ================== 基础与 CORS ==================
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": os.getenv("CORS_ORIGINS", "*")}}, supports_credentials=False)

# ========= 可调参数（与你一致） =========
DEFAULT_VILLAINS = 3
TRIALS_FLOP, TRIALS_TURN, TRIALS_RIVER = 8000, 12000, 16000
EARLYSTOP_EPS = 0.012
TIME_BUDGET_S = 0.25

SIM_WEIGHT_PER_STREET = 0.85
LLM_WEIGHT_PER_STREET = 0.15

# ========= LLM =========
DEFAULT_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
try:
    DEFAULT_TEMP = float(os.getenv("LLM_TEMPERATURE", "0.2"))
except:
    DEFAULT_TEMP = 0.2

# ========= 牌面解析 =========
SUIT_MAP = {'s':'s','♠':'s','黑桃':'s','h':'h','♥':'h','红桃':'h',
            'd':'d','♦':'d','方片':'d','方块':'d','c':'c','♣':'c','梅花':'c'}
RANK_MAP = {'A':'A','a':'A','K':'K','k':'K','Q':'Q','q':'Q','J':'J','j':'J',
            'T':'T','t':'T','10':'T','１０':'T','9':'9','９':'9','8':'8','８':'8',
            '7':'7','７':'7','6':'6','６':'6','5':'5','５':'5','4':'4','４':'4',
            '3':'3','３':'3','2':'2','２':'2'}
SEP_REGEX = re.compile(r'[,\u3001/\s]+')

def normalize_token(tok: str) -> str:
    t = tok.strip()
    if not t: raise ValueError("空牌面")
    m = re.match(r'^(A|K|Q|J|10|T|[2-9]|１０|９|８|７|６|５|４|３|２)\s*(s|h|d|c|♠|♥|♦|♣|黑桃|红桃|方片|方块|梅花)$', t, re.IGNORECASE)
    if m: r,s = m.group(1), m.group(2); return RANK_MAP[r]+SUIT_MAP[s]
    m2 = re.match(r'^(黑桃|红桃|方片|方块|梅花|♠|♥|♦|♣|s|h|d|c)\s*(A|K|Q|J|10|T|[2-9]|１０|９|８|７|６|５|４|３|２)$', t, re.IGNORECASE)
    if m2: s,r = m2.group(1), m2.group(2); return RANK_MAP[r]+SUIT_MAP[s]
    t2 = t
    for sym,letter in [('♠','s'),('♥','h'),('♦','d'),('♣','c')]: t2 = t2.replace(sym,letter)
    t2 = t2.replace(' ','')
    m3 = re.match(r'^(A|K|Q|J|T|10|[2-9]|１０|９|８|７|６|５|４|３|２)(s|h|d|c)$', t2, re.IGNORECASE)
    if m3: r,s = m3.group(1), m3.group(2); return RANK_MAP[r]+SUIT_MAP[s]
    raise ValueError(f"无法识别的牌面：{tok}")

def parse_cards(line: str, min_n: int, max_n: int):
    tokens = [x for x in SEP_REGEX.split(line.strip()) if x] if line else []
    if not (min_n <= len(tokens) <= max_n): raise ValueError(f"牌数量应在 {min_n}–{max_n} 张，当前 {len(tokens)} 张。")
    std = [normalize_token(t) for t in tokens]
    if len(set(std)) != len(std): raise ValueError(f"存在重复牌：{std}")
    return [Card.new(s) for s in std], std

# ========= 快速蒙特卡洛 =========
_EVAL = Evaluator()
_FULL_DECK = [Card.new(r+s) for r in "23456789TJQKA" for s in "shdc"]

def equity_mc_fast(hero, board, villains=1, trials=10000, seed=None, eps=EARLYSTOP_EPS, t_budget_s=TIME_BUDGET_S, progress_cb=None):
    rng = random.Random(seed)
    known = set(hero) | set(board)
    avail = [c for c in _FULL_DECK if c not in known]
    need_public = 5 - len(board)
    need_opp = villains * 2
    need_total = need_public + need_opp

    wins_equiv = 0.0
    n = 0
    start = time.monotonic()
    BATCH = 400

    while n < trials:
        if time.monotonic() - start > t_budget_s:
            break
        this = min(BATCH, trials - n)
        for _ in range(this):
            draw = rng.sample(avail, need_total)
            opp = draw[:need_opp]; pub = draw[need_opp:]
            vill = [(opp[i], opp[i+1]) for i in range(0, need_opp, 2)]
            b = tuple(board) + tuple(pub)
            my = _EVAL.evaluate(tuple(hero), b)
            best = 7463; ties = 0
            for v1, v2 in vill:
                s = _EVAL.evaluate((v1, v2), b)
                if s < best: best = s; ties = 1
                elif s == best: ties += 1
            if my < best: wins_equiv += 1.0
            elif my == best: wins_equiv += 1.0/(ties+1)
        n += this

        p = wins_equiv / n
        se = (p * (1 - p) / max(n, 1)) ** 0.5
        half_width = 1.96 * se

        if progress_cb:
            approx_pct = min(100, int(n * 100 / trials))
            progress_cb(approx_pct)

        if half_width < eps:
            break

    return wins_equiv / max(n, 1)

# ========= 牌型中文名 =========
CLASS_ZH = {"High Card":"高牌","Pair":"一对","Two Pair":"两对","Three of a Kind":"三条",
            "Straight":"顺子","Flush":"同花","Full House":"葫芦","Four of a Kind":"四条",
            "Straight Flush":"同花顺"}
def hand_class_zh(hero, board):
    score = _EVAL.evaluate(hero, board)
    rc = _EVAL.get_rank_class(score)
    name_en = _EVAL.class_to_string(rc)
    return CLASS_ZH.get(name_en, name_en), score

# ========= 棋面/赔率/SPR =========
def board_features(board_std):
    feats = {"flush_draw":False,"two_tone":False,"mono":False,"paired":False,"straight_draw":False}
    if not board_std: return feats
    suits = [b[1] for b in board_std]
    cnt = {s: suits.count(s) for s in "shdc"}
    if max(cnt.values())>=3: feats["mono"] = True
    elif max(cnt.values())==2: feats["two_tone"] = True
    ranks = [b[0] for b in board_std]
    feats["paired"] = any(ranks.count(r)>=2 for r in set(ranks))
    order = "A23456789TJQKA"
    idxs = sorted(set(order.index(r) for r in ranks if r in order))
    if len(idxs)>=3 and any(idxs[i+2]-idxs[i]<=3 for i in range(len(idxs)-2)): feats["straight_draw"]=True
    if len(idxs)==2 and idxs[1]-idxs[0]==1: feats["straight_draw"]=True
    return feats

def pot_odds(call_amt, pot_amt):
    if not call_amt or not pot_amt: return None
    return call_amt / (pot_amt + call_amt)

def spr(stack_bb, pot_bb):
    if not pot_bb or pot_bb<=0: return None
    if stack_bb is None: return None
    return stack_bb / pot_bb

def rule_advice_struct(equity, hand_name, opponents, feats, facing_bet=False, call_bb=0.0, pot_bb=0.0, spr_val=None):
    tight = min(0.10 + opponents*0.03, 0.25)
    strong = 0.65 - tight
    medium = 0.45 - tight
    if feats.get("mono"):        strong += 0.03; medium += 0.02
    if feats.get("paired"):      strong -= 0.02
    if feats.get("straight_draw"): medium += 0.01
    size = "check"
    if equity >= strong: size = "66%" if not feats.get("mono") else "50%"
    elif equity >= medium: size = "33%"
    def _opp_desc(eq):
        return "你更常领先" if eq>0.6 else ("势均力敌" if 0.40<=eq<=0.60 else "对手更常领先")
    line = ""
    need = pot_odds(call_bb, pot_bb) if (facing_bet and call_bb and pot_bb) else None
    if need is not None:
        if equity >= need + 0.05: line = "可跟注"
        elif equity >= need - 0.02 and equity >= medium: line = "边缘跟注/看下一张"
        else: line = "建议弃牌"
    action = ("以价值下注为主" if equity>=strong else
              ("控制底池/小注或过牌" if equity>=medium else
               ("以过牌/弃牌为主")))
    if facing_bet and line: action = line
    tips=[]
    if spr_val and spr_val<=3 and equity>=medium: tips.append("SPR低：可推进价值或施压")
    if spr_val and spr_val>=6 and equity<medium: tips.append("SPR高：边缘牌优先控池")
    if feats.get("two_tone"): tips.append("两色面：留意同花听牌")
    if feats.get("mono"): tips.append("同花面：无同花更谨慎")
    if feats.get("paired"): tips.append("配对面：提防葫芦与三条")
    if feats.get("straight_draw"): tips.append("顺子听牌丰富：中等尺寸更好")
    return {
        "summary": f"{action}；建议下注：{size}",
        "line": ("面对下注："+line) if (facing_bet and line) else ("建议线：下注" if size!="check" else "建议线：过牌"),
        "sizing": size,
        "opponent_compare": _opp_desc(equity),
        "tips": tips[:4]
    }

# ========= 文本清理 =========
_CJK_SEP_RE = re.compile(r'([\u4e00-\u9fff])[\u200b\s,，;；、·]+(?=[\u4e00-\u9fff])')
_ZW_RE = re.compile(r'[\u200b\u200c\u200d\uFEFF]+')
def clean_model_text(s: str) -> str:
    if not s: return s
    s = _ZW_RE.sub('', s)
    while True:
        ns = _CJK_SEP_RE.sub(r'\1', s)
        if ns == s: break
        s = ns
    s = re.sub(r'[ \t]{2,}', ' ', s)
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s.strip()

# ========= 轻量护栏 =========
ALLOWED_SIZES = {"check","33%","50%","66%","100%","overbet"}
ALLOWED_ACTIONS = {"bet","check","raise","call","fold","need_more_info"}

def business_rules_check(ctx: dict, adv: dict) -> str|None:
    equity = ctx.get("equity", None)
    pot_ratio = ctx.get("pot_odds", None)
    villains = ctx.get("villains", 1)
    feats = ctx.get("features", {}) or {}
    spr_val = ctx.get("spr", None)
    facing = ctx.get("facing_bet", False)
    action = adv.get("action"); sizing = adv.get("sizing")
    if action not in ALLOWED_ACTIONS or sizing not in ALLOWED_SIZES:
        return "行动/尺寸不在白名单"
    if facing and (pot_ratio is not None) and (equity is not None):
        if equity + 0.02 < pot_ratio and action in {"call","raise"}:
            return "违反赔率理性（胜率低于所需阈值却建议跟/加）"
        if equity > pot_ratio + 0.08 and action == "fold":
            return "违反赔率理性（明显有边际却建议弃）"
    if villains >= 3 and equity is not None and equity < 0.45 and action in {"bet","raise"} and sizing in {"66%","100%","overbet"}:
        return "多人+低胜率不宜大尺寸进攻"
    if feats.get("mono") and action in {"bet","raise"} and sizing in {"100%","overbet"} and equity is not None and equity < 0.55:
        return "同花面+胜率不高不宜大尺寸"
    if spr_val is not None and spr_val >= 6 and equity is not None and equity < 0.55 and action in {"bet","raise"} and sizing in {"66%","100%","overbet"}:
        return "SPR高+边缘牌不宜扩池"
    for tip in adv.get("tips", []):
        if any(ch.isdigit() for ch in tip): return "tips 不应包含数字"
    return None

def fallback_text(ctx):
    rb = rule_advice_struct(ctx["equity"], ctx["hand_class"], ctx["villains"], ctx["features"],
                            ctx.get("facing_bet",False), ctx.get("call_bb") or 0, ctx.get("pot_bb") or 0, ctx.get("spr"))
    lines = [rb["summary"], rb["line"], "对手相对强弱："+rb["opponent_compare"]]
    if rb["tips"]: lines.append("要点："+"；".join(rb["tips"]))
    return "\n".join(lines)

def try_llm_guarded(ctx: dict) -> dict:
    api = os.environ.get("OPENAI_API_KEY")
    if not api:
        return {"text": fallback_text(ctx), "source": "rule", "reason": "未配置 OPENAI_API_KEY"}
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api)
        system = (
            "你是德州扑克教练。严格遵循："
            "1) 只基于我提供的 JSON 字段给建议；不得自行计算或编造任何数值；"
            "2) 仅以 JSON 格式输出，键：action,sizing,summary,opponent_compare,tips；"
            "3) action∈{bet,check,raise,call,fold,need_more_info}；sizing∈{check,33%,50%,66%,100%,overbet}；"
            "4) 禁止在中文字符之间插入任何分隔符（如分号、顿号、逗号、点号），正常书写中文；"
            "5) 信息不足则 action=need_more_info，并在 tips 说明。"
        )
        user = "当前牌局 JSON：\n" + json.dumps(ctx, ensure_ascii=False, indent=2)
        last_err = None
        for _ in range(2):
            messages=[{"role":"system","content":system}]
            if last_err: messages.append({"role":"system","content":"上次失败原因："+last_err})
            messages.append({"role":"user","content":user})
            resp = client.chat.completions.create(
                model=DEFAULT_MODEL,
                messages=messages,
                temperature=DEFAULT_TEMP,
                response_format={"type":"json_object"},
            )
            raw = resp.choices[0].message.content
            try:
                data = json.loads(raw)
            except Exception as e:
                last_err = f"JSON解析失败：{e}"
                continue
            for k in ["action","sizing","summary","opponent_compare","tips"]:
                if k not in data:
                    last_err = f"缺少字段 {k}"; data=None; break
            if not data: continue
            rule_err = business_rules_check(ctx, data)
            if rule_err:
                last_err = rule_err
                continue
            tips = "；".join(data.get("tips") or [])
            body = [
                f"建议：{data['summary']}",
                f"行动：{data['action']} / 尺寸：{data['sizing']}",
                f"对手相对强弱：{data['opponent_compare']}",
                f"要点：{tips}" if tips else ""
            ]
            text = "\n".join([b for b in body if b])
            return {"text": clean_model_text(text), "source": "llm"}
        return {"text": fallback_text(ctx), "source": "rule", "reason": last_err or "LLM返回不合规"}
    except Exception as e:
        return {"text": fallback_text(ctx), "source": "rule", "reason": f"调用异常：{e}"}

# ========= 顶部 LLM 状态 =========
def llm_runtime_config():
    key_ok = bool(os.getenv("OPENAI_API_KEY"))
    return {
        "enabled": key_ok,
        "model": DEFAULT_MODEL,
        "temperature": DEFAULT_TEMP,
        "reason": None if key_ok else "未配置 OPENAI_API_KEY",
        "guardrails": ["JSON限定", "白名单动作/尺寸", "赔率/SPR校验", "中文分隔清理", "早停+时间预算"]
    }

# ========= 进度/任务（SSE） =========
PROGRESS = {}   # task_id -> state
LOCK = threading.Lock()
TASK_TTL = int(os.getenv("TASK_TTL_SECONDS", "900"))  # 任务结果在内存中保留秒数（默认15分钟）

def set_progress(task_id, **fields):
    with LOCK:
        state = PROGRESS.setdefault(task_id, {"pct":0,"stage":"Queued","eta":None,"done":False,"cancel":False,"results":[],"detail":{}, "ts": time.time()})
        state.update({k:v for k,v in fields.items() if v is not None})
        state["ts"] = time.time()

def get_progress(task_id):
    with LOCK:
        return PROGRESS.get(task_id)

def cleanup_old_tasks():
    now = time.time()
    with LOCK:
        dead = [k for k,v in PROGRESS.items() if now - v.get("ts", now) > TASK_TTL]
        for k in dead:
            PROGRESS.pop(k, None)

# ========= 模板（保留你原来的 UI，便于单体调试） =========
TPL = """<!doctype html>
<html lang="zh"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>德扑逐街助手</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
:root{--bg1:#0f172a;--bg2:#111827;--txt:#e5e7eb;--muted:#94a3b8;--glass:rgba(255,255,255,.06);--border:rgba(255,255,255,.12);--grad1:#60a5fa;--grad2:#a78bfa}
body{background:linear-gradient(120deg,var(--bg1),var(--bg2));color:var(--txt)}
.card{background:var(--glass);border:1px solid var(--border);border-radius:16px;color:var(--txt)}
.advice-box{white-space:pre-wrap;background:#0b1020;color:#e5e7eb;border:1px solid #1f2937;border-radius:10px;padding:12px;min-height:170px;overflow:auto}
.badge-src{font-weight:600;letter-spacing:.02em}
.statusbar{background:rgba(16,24,39,.6);border:1px solid var(--border);border-radius:14px;padding:10px 14px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.pill{padding:4px 10px;border-radius:999px;border:1px solid var(--border);font-weight:600}
.pill.on{background:rgba(34,197,94,.15)}.pill.off{background:rgba(239,68,68,.15)}.muted{color:var(--muted)}
.progress-wrap{height:14px;border-radius:999px;background:rgba(255,255,255,.08);overflow:hidden}
.progress-bar-x{height:100%;width:0%;background:linear-gradient(90deg,var(--grad1),var(--grad2));box-shadow:0 0 16px rgba(96,165,250,.6) inset;transition:width .15s ease}
</style>
</head>
<body>
<div class="container py-4">
  <h3 class="mb-3">德州扑克逐街助手（胜率 + 中文建议）</h3>
  <div class="statusbar mb-3">
    {% if llm.enabled %}<span class="pill on">LLM：ON</span>{% else %}<span class="pill off">LLM：OFF</span>{% endif %}
    <span class="pill">模型：{{llm.model}}</span>
    <span class="pill">temperature：{{"%.2f"%llm.temperature}}</span>
    {% if not llm.enabled and llm.reason %}<span class="muted">原因：{{llm.reason}}</span>{% endif %}
    <span class="muted">护栏：{{ ", ".join(llm.guardrails) }}</span>
  </div>

  <form id="mainForm" class="row g-3">
    <div class="col-12 col-md-6"><label class="form-label">手牌（两张）</label>
      <input name="hero" class="form-control" placeholder="如：A♠ K♠ / 黑桃A 梅花K" value="{{form.hero or ''}}" required></div>
    <div class="col-12 col-md-6"><label class="form-label">位置（可选）</label>
      <input name="pos" class="form-control" placeholder="UTG/MP/CO/BTN/SB/BB" value="{{form.pos or ''}}"></div>
    <div class="col-12 col-md-6"><label class="form-label">Flop（三张，可空）</label>
      <input name="flop" class="form-control" placeholder="A♥ 7♦ 2♣ / 红桃A 方片7 梅花2" value="{{form.flop or ''}}"></div>
    <div class="col-12 col-md-3"><label class="form-label">Turn（单张，可空）</label>
      <input name="turn" class="form-control" placeholder="9♣ / 梅花9" value="{{form.turn or ''}}"></div>
    <div class="col-12 col-md-3"><label class="form-label">River（单张，可空）</label>
      <input name="river" class="form-control" placeholder="K♦ / 方片K" value="{{form.river or ''}}"></div>
    <div class="col-6 col-md-3"><label class="form-label">对手人数</label>
      <input name="villains" type="number" min="1" class="form-control" value="{{form.villains or 1}}"></div>
    <div class="col-6 col-md-3"><label class="form-label">有效筹码（bb，可空）</label>
      <input name="stack_bb" type="number" step="0.1" class="form-control" value="{{form.stack_bb or ''}}"></div>
    <div class="col-6 col-md-3"><label class="form-label">当前底池（bb，可空）</label>
      <input name="pot_bb" type="number" step="0.1" class="form-control" value="{{form.pot_bb or ''}}"></div>
    <div class="col-12"><strong>若“面对下注”，填写下面两项（当街）</strong></div>
    <div class="col-6 col-md-3"><label class="form-label">Flop 跟注额（bb）</label>
      <input name="call_flop" type="number" step="0.1" class="form-control" value="{{form.call_flop or ''}}"></div>
    <div class="col-6 col-md-3"><label class="form-label">Turn 跟注额（bb）</label>
      <input name="call_turn" type="number" step="0.1" class="form-control" value="{{form.call_turn or ''}}"></div>
    <div class="col-6 col-md-3"><label class="form-label">River 跟注额（bb）</label>
      <input name="call_river" type="number" step="0.1" class="form-control" value="{{form.call_river or ''}}"></div>
    <div class="col-12 d-flex gap-2">
      <button id="startBtn" class="btn btn-primary" type="submit">开始计算（带进度条）</button>
      <button id="cancelBtn" class="btn btn-outline-light" type="button" disabled>取消</button>
    </div>
  </form>

  <div id="progressPanel" class="card mt-3" style="display:none;"><div class="card-body">
    <div class="d-flex justify-content-between align-items-center mb-2"><div><b id="stageText">准备中…</b></div><div class="text-secondary"><span id="etaText"></span></div></div>
    <div class="progress-wrap"><div class="progress-bar-x" id="bar"></div></div>
    <div class="mt-2 text-secondary"><span id="pctText">0%</span><span class="ms-3" id="streetText"></span></div>
  </div></div>

  <div id="errBox" class="alert alert-danger mt-3" style="display:none;"></div>
  <div id="resultsHook"></div>

  {% if error %}<div class="alert alert-danger mt-3">{{error}}</div>{% endif %}
</div>

<script>
let es = null, currentTask = null;
const $ = s=>document.querySelector(s);
const startBtn=$('#startBtn'), cancelBtn=$('#cancelBtn');
const progressPanel=$('#progressPanel'), bar=$('#bar'), pctText=$('#pctText');
const stageText=$('#stageText'), etaText=$('#etaText'), streetText=$('#streetText');
const errBox=$('#errBox'), resultsHook=$('#resultsHook');

function secsToHHMMSS(s){ if(s==null) return ""; s=Math.max(0, s|0); const h=(s/3600)|0, m=((s%3600)/60)|0, sec=s%60; if(h>0) return `${h}h ${m}m ${sec}s`; if(m>0) return `${m}m ${sec}s`; return `${sec}s`; }

function renderResultCard(block){
  const delta = (block.delta==null) ? "" : `（${block.delta>=0?'↑':'↓'}${(Math.abs(block.delta)*100).toFixed(2)}% 相比上一街）`;
  const src = (block.advice_source==='llm') ? 'LLM' : '规则引擎';
  const reason = block.advice_reason ? ` <span class="ms-2">(回退：${block.advice_reason})</span>` : '';
  const html = `
    <div class="card mt-3"><div class="card-body">
      <div class="d-flex justify-content-between align-items-center">
        <h5 class="card-title mb-0">【结果】${block.title}</h5>
        <span class="badge bg-info text-dark badge-src">建议来源：${src}${reason}</span>
      </div>
      <p class="card-text mt-2 mb-1"><b>手牌：</b>${block.hero}</p>
      <p class="card-text mb-1"><b>公共牌：</b>${block.board}</p>
      <p class="card-text mb-1"><b>牌型：</b>${block.hand_name}（score=${block.score}，越小越强）</p>
      <p class="card-text mb-1"><b>胜率：</b>${(block.equity*100).toFixed(2)}% ${delta}</p>
      <hr/>
      <div class="advice-box">${(block.advice_text||'').replaceAll('\\n','<br/>')}</div>
    </div></div>`;
  resultsHook.insertAdjacentHTML('beforeend', html);
}

async function startTask(e){
  e.preventDefault();
  errBox.style.display='none'; errBox.textContent=''; resultsHook.innerHTML='';
  progressPanel.style.display='block'; startBtn.disabled=true; cancelBtn.disabled=false;

  const fd = new FormData($('#mainForm'));
  // 注意：如果跑在 Vercel 前端 + vercel.json 代理到 /api/*，这里把 '/start' 换成 '/api/start'
  const resp = await fetch('/start', {method:'POST', body:fd});
  if(!resp.ok){ startBtn.disabled=false; cancelBtn.disabled=true; errBox.style.display='block'; errBox.textContent='启动任务失败'; return; }
  const data = await resp.json(); currentTask = data.task_id;

  if(es) es.close();
  // 同上，Vercel 代理时把 `/stream/` 改成 `/api/stream/`
  es = new EventSource(`/stream/${currentTask}`);
  es.onmessage = (evt)=>{
    const state = JSON.parse(evt.data);
    bar.style.width = (state.pct||0) + '%'; pctText.textContent = (state.pct||0) + '%';
    stageText.textContent = state.stage || ''; etaText.textContent = state.eta!=null ? ('ETA ' + secsToHHMMSS(state.eta)) : '';
    streetText.textContent = state.detail && state.detail.street ? ('当前街：' + state.detail.street) : '';
    if(state.results && state.results.length){ resultsHook.innerHTML=''; state.results.forEach(renderResultCard); }
    if(state.done){ es.close(); startBtn.disabled=false; cancelBtn.disabled=true; stageText.textContent='完成'; etaText.textContent=''; bar.style.width='100%'; pctText.textContent='100%'; }
  };
  es.addEventListener('error', ()=>{ startBtn.disabled=false; cancelBtn.disabled=true; });
}

async function cancelTask(){
  if(!currentTask) return;
  // Vercel 代理时把 `/cancel/` 改成 `/api/cancel/`
  await fetch(`/cancel/${currentTask}`, {method:'POST'});
  if(es) es.close(); stageText.textContent='已取消'; cancelBtn.disabled = true; startBtn.disabled = false;
}

document.getElementById('mainForm').addEventListener('submit', startTask);
document.getElementById('cancelBtn').addEventListener('click', cancelTask);
</script>
</body></html>
"""

def float_or_none(s):
    try:
        if s is None or str(s).strip()=="":
            return None
        return float(s)
    except:
        return None

# ========= 公共：返回健康与配置 =========
def _health_payload():
    return {"ok": True, "ts": datetime.utcnow().isoformat()+"Z"}

def _config_payload():
    return llm_runtime_config()

# ========= 路由工具：注册 /path 与 /api/path 双路径 =========
def dual_route(rule, **options):
    """
    同时注册 /rule 和 /api/rule 两条路由，便于前端（Vercel 代理 /api/*）与本地直连。
    用法：
        @dual_route("/start", methods=["POST"])
        def start_task(): ...
    """
    def decorator(f):
        app.add_url_rule(rule, endpoint=f.__name__+"__root", view_func=f, **options)
        api_rule = "/api" + (rule if rule.startswith("/") else ("/"+rule))
        app.add_url_rule(api_rule, endpoint=f.__name__+"__api", view_func=f, **options)
        return f
    return decorator

# ========= 页面（可用于单体调试；Vercel 前端时基本不用） =========
@dual_route("/", methods=["GET"])
def index():
    form = {k: "" for k in ["hero","pos","flop","turn","river","villains","stack_bb","pot_bb","call_flop","call_turn","call_river"]}
    llm = llm_runtime_config()
    return render_template_string(TPL, form=form, error=None, results=[], llm=llm)

# ========= 健康检查 / 配置 =========
@dual_route("/health", methods=["GET"])
def health():
    return jsonify(_health_payload())

@dual_route("/config", methods=["GET"])
def config():
    return jsonify(_config_payload())

# ========= 启动任务 =========
@dual_route("/start", methods=["POST"])
def start_task():
    form = {k: (request.form.get(k) or "") for k in
            ["hero","pos","flop","turn","river","villains","stack_bb","pot_bb","call_flop","call_turn","call_river"]}
    try:
        villains = int(form["villains"] or DEFAULT_VILLAINS)
        hero_cards, hero_std = parse_cards(form["hero"], 2, 2)

        streets = []
        if form["flop"]:
            flop_cards, flop_std = parse_cards(form["flop"], 3, 3)
            streets.append(("Flop", flop_cards, flop_std, TRIALS_FLOP, float_or_none(form["call_flop"])))
        if form["turn"] and streets:
            turn_cards, turn_std = parse_cards(form["turn"], 1, 1)
            board_t = streets[0][1] + turn_cards
            board_t_std = streets[0][2] + turn_std
            streets.append(("Turn", board_t, board_t_std, TRIALS_TURN, float_or_none(form["call_turn"])))
        if form["river"] and len(streets)>=2:
            river_cards, river_std = parse_cards(form["river"], 1, 1)
            board_r = streets[1][1] + river_cards
            board_r_std = streets[1][2] + river_std
            streets.append(("River", board_r, board_r_std, TRIALS_RIVER, float_or_none(form["call_river"])))

        if not streets:
            return jsonify({"error":"请至少填写 Flop（三张）以开始逐街评估。"}), 400

        stack_bb = float_or_none(form["stack_bb"])
        pot_bb   = float_or_none(form["pot_bb"])

        task_id = uuid.uuid4().hex
        PROGRESS[task_id] = {"pct":0,"stage":"排队中…","eta":None,"done":False,"cancel":False,"results":[],"detail":{},"ts":time.time()}

        th = threading.Thread(
            target=_worker_run,
            args=(task_id, hero_cards, hero_std, villains, streets, stack_bb, pot_bb, form.get("pos") or None),
            daemon=True
        )
        th.start()
        return jsonify({"task_id": task_id})
    except Exception as e:
        return jsonify({"error": f"输入错误：{e}"}), 400

# ========= 取消 =========
@dual_route("/cancel/<task_id>", methods=["POST"])
def cancel_task(task_id):
    with LOCK:
        if task_id in PROGRESS:
            PROGRESS[task_id]["cancel"] = True
            PROGRESS[task_id]["ts"] = time.time()
            return jsonify({"ok": True})
    return jsonify({"ok": False}), 404

# ========= SSE 进度流（禁用缓冲/缓存 + keepalive） =========
@dual_route("/stream/<task_id>", methods=["GET"])
def stream_progress(task_id):
    def event_stream():
        prev = None
        last_ping = time.monotonic()
        while True:
            state = get_progress(task_id)
            if not state:
                yield "event: error\ndata: {\"error\":\"task not found\"}\n\n"
                break
            snapshot = (state["pct"], state["stage"], state["eta"], state["done"], len(state.get("results",[])))
            if snapshot != prev:
                payload = json.dumps(state, ensure_ascii=False)
                yield f"data: {payload}\n\n"
                prev = snapshot
            # keepalive：每 15s 发一条 ping，防止代理断流
            now = time.monotonic()
            if now - last_ping > 15:
                yield ": ping\n\n"
                last_ping = now
            if state.get("done") or state.get("cancel"):
                break
            time.sleep(0.2)

    headers = {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",  # 有些代理会识别该头，禁止缓冲
        "Connection": "keep-alive"
    }
    return Response(event_stream(), headers=headers)

# ========= 后台任务 =========
def _worker_run(task_id, hero_cards, hero_std, villains, streets, stack_bb, pot_bb, pos):
    try:
        total_streets = len(streets)
        prev_eq = None
        results_acc = []

        def street_weight(i):
            return 1.0/total_streets

        for idx, (name, board_cards, board_std, trials, call_amt) in enumerate(streets):
            if get_progress(task_id).get("cancel"): break

            # 模拟阶段
            set_progress(task_id, stage=f"{name}：模拟中…", eta=None, detail={"street": name})
            base = sum(street_weight(j) for j in range(idx)) * 100.0
            share = street_weight(idx) * 100.0
            sim_start = base
            sim_end   = base + share * SIM_WEIGHT_PER_STREET

            last_pct_report = -1
            t0 = time.monotonic()
            def cb(pct_inside):
                nonlocal last_pct_report
                mapped = int(sim_start + (sim_end - sim_start) * (pct_inside/100.0))
                if mapped != last_pct_report:
                    elapsed = time.monotonic() - t0
                    done_ratio = max(1e-6, (pct_inside/100.0))
                    eta = int(elapsed/done_ratio*(1-done_ratio))
                    set_progress(task_id, pct=mapped, eta=eta, detail={"street": name})
                    last_pct_report = mapped

            equity = equity_mc_fast(hero_cards, board_cards, villains, trials,
                                    seed=123+idx, eps=EARLYSTOP_EPS, t_budget_s=TIME_BUDGET_S,
                                    progress_cb=cb)
            if get_progress(task_id).get("cancel"): break

            # 组上下文求建议
            hand_name, score = hand_class_zh(hero_cards, board_cards)
            feats = board_features(board_std)
            spr_val = spr(stack_bb, pot_bb)
            ctx = {
                "street": name,
                "hero_hand": hero_std,
                "board": board_std,
                "villains": villains,
                "equity": equity,
                "hand_class": hand_name,
                "score": score,
                "features": feats,
                "position": pos,
                "stack_bb": stack_bb,
                "pot_bb": pot_bb,
                "spr": spr_val,
                "facing_bet": True if call_amt and pot_bb else False,
                "call_bb": call_amt,
                "pot_odds": pot_odds(call_amt, pot_bb) if (call_amt and pot_bb) else None,
                "prev_equity": prev_eq,
                "delta": (None if prev_eq is None else equity - prev_eq),
            }

            # LLM 阶段
            set_progress(task_id, stage=f"{name}：生成建议…", eta=None, detail={"street": name})
            adv = try_llm_guarded(ctx)

            block = {
                "title": f"{name}",
                "hero": " ".join(hero_std),
                "board": " ".join(board_std) if board_std else "(无)",
                "hand_name": hand_name,
                "score": score,
                "equity": equity,
                "delta": ctx["delta"],
                "advice_text": adv["text"],
                "advice_source": adv.get("source","rule"),
                "advice_reason": adv.get("reason"),
            }
            results_acc.append(block)

            llm_end = base + share
            set_progress(task_id, pct=int(llm_end), results=list(results_acc), detail={"street": name})

            prev_eq = equity

        # 结束
        set_progress(task_id, stage="完成", pct=100, eta=None, done=True, detail={"street": streets[-1][0] if streets else ""})
    except Exception as e:
        set_progress(task_id, stage=f"出错：{e}", done=True)
    finally:
        # 轻量清理：给该任务再保留 TASK_TTL 秒
        with LOCK:
            if task_id in PROGRESS:
                PROGRESS[task_id]["ts"] = time.time()

# ========= 错误处理 & 定期清理 =========
@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "not found"}), 404

@app.after_request
def add_common_headers(resp):
    # 对 API/SSE 禁缓存更稳
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp

# 定时清理陈旧任务（简单做法：每次请求后尝试一次）
@app.before_request
def _cleanup_hook():
    cleanup_old_tasks()

# ========= 本地开发入口（生产用 gunicorn -k gthread -w 2 -b 0.0.0.0:$PORT app:app） =========
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False, threaded=True)
