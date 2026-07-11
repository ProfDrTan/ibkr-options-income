"""
orchestrator.py - The AI Board Multi-Agent Market Intelligence Orchestrator
Prof Dr Tan | github.com/ProfDrTan/ibkr-options-income
Calls ChatGPT, Gemini, DeepSeek in parallel every Monday.
Zero human involvement in intelligence gathering.
"""
import os, json, datetime, urllib.request, urllib.error, concurrent.futures, time

def build_brief():
    today = datetime.date.today()
    monthly = {1:62,2:55,3:60,4:68,5:52,6:55,7:63,8:48,9:42,10:53,11:68,12:70}
    dow_adj = {0:-3,1:0,2:2,3:1,4:-1}
    almanac = min(100, max(0, monthly.get(today.month,55) + dow_adj.get(today.weekday(),0)))
    return f"""You are a market intelligence agent for The AI Board algorithmic trading system.
DATE: {today.strftime('%A %B %d %Y')}
TASK: Assess S&P 500 (SPX) for this week bull put spread trade viability.
STRATEGY: We sell 10 SPX bull put spreads at 5 delta every Monday.
Skip if VIX above 25 or composite below 45. Full size if composite 55+.
Almanac seasonal score this week: {almanac}/100

Respond ONLY in this exact format with no other text:
SCORE: [0-100]
BIAS: [BULLISH or NEUTRAL or BEARISH]
CONFIDENCE: [HIGH or MEDIUM or LOW]
KEY_FACTOR: [max 15 words]
RISK: [max 15 words]
WEEK_OUTLOOK: [max 25 words]""", almanac

def api_call(url, payload, headers, agent):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:300]
        except Exception:
            pass
        msg = f"HTTP {e.code}: {body}" if body else str(e)
        print(f"  {agent} API error: {msg}")
        return None, msg
    except Exception as e:
        print(f"  {agent} API error: {e}")
        return None, str(e)

def call_chatgpt(brief):
    key = os.environ.get("OPENAI_API_KEY","")
    if not key: return default_result("chatgpt","No API key")
    data, err = api_call(
        "https://api.openai.com/v1/chat/completions",
        {"model":"gpt-4o","messages":[{"role":"user","content":brief}],"max_tokens":200,"temperature":0.3},
        {"Authorization":f"Bearer {key}","Content-Type":"application/json"},
        "chatgpt"
    )
    if not data: return default_result("chatgpt", err)
    return parse_output("chatgpt", data["choices"][0]["message"]["content"])

def call_deepseek(brief):
    key = os.environ.get("DEEPSEEK_API_KEY","")
    if not key: return default_result("deepseek","No API key")
    data, err = api_call(
        "https://api.deepseek.com/v1/chat/completions",
        {"model":"deepseek-chat","messages":[{"role":"user","content":brief}],"max_tokens":200,"temperature":0.3},
        {"Authorization":f"Bearer {key}","Content-Type":"application/json"},
        "deepseek"
    )
    if not data: return default_result("deepseek", err)
    return parse_output("deepseek", data["choices"][0]["message"]["content"])

def call_gemini(brief):
    key = os.environ.get("GOOGLE_AI_API_KEY","")
    if not key: return default_result("gemini","No API key")
    # Google AI Studio API keys (AIzaSy... or AQ... format) both authenticate
    # via the ?key= query param, not an Authorization header.
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={key}"
    headers = {"Content-Type":"application/json"}
    payload = {"contents":[{"parts":[{"text":brief}]}],
               "generationConfig":{"maxOutputTokens":300,"temperature":0.3,
                                   "thinkingConfig":{"thinkingBudget":0}}}
    data, err = api_call(url, payload, headers, "gemini")
    if not data: return default_result("gemini", err)
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return parse_output("gemini", text)
    except Exception as e:
        print(f"  gemini response parse: {e}")
        return default_result("gemini", str(e))

def default_result(agent, reason="unavailable"):
    return {"agent":agent,"score":55,"bias":"NEUTRAL","confidence":"LOW",
            "key_factor":f"{agent} {reason}","risk":"No data",
            "week_outlook":f"{agent} unavailable","parse_success":False,"weight_multiplier":0.3}

def parse_output(agent, text):
    r = {"agent":agent,"score":55,"bias":"NEUTRAL","confidence":"MEDIUM",
         "key_factor":"Parse failed","risk":"Unknown","week_outlook":"Unavailable",
         "parse_success":False,"weight_multiplier":1.0,"raw":text}
    try:
        for line in text.strip().split("\n"):
            line = line.strip()
            if line.startswith("SCORE:"):
                v = int(''.join(filter(str.isdigit, line.split(":",1)[1][:5])))
                r["score"] = max(0, min(100, v))
            elif line.startswith("BIAS:"):
                b = line.split(":",1)[1].strip().upper()
                if b in ["BULLISH","NEUTRAL","BEARISH"]: r["bias"] = b
            elif line.startswith("CONFIDENCE:"):
                c = line.split(":",1)[1].strip().upper()
                if c in ["HIGH","MEDIUM","LOW"]: r["confidence"] = c
            elif line.startswith("KEY_FACTOR:"): r["key_factor"] = line.split(":",1)[1].strip()
            elif line.startswith("RISK:"): r["risk"] = line.split(":",1)[1].strip()
            elif line.startswith("WEEK_OUTLOOK:"): r["week_outlook"] = line.split(":",1)[1].strip()
        r["parse_success"] = True
        if r["confidence"] == "LOW": r["weight_multiplier"] = 0.7
        print(f"  {agent}: {r['score']}/100 {r['bias']} ({r['confidence']})")
    except Exception as e:
        print(f"  {agent} parse error: {e}")
    return r

def synthesise(results, almanac, human_score=55):
    weights = {"chatgpt":0.30,"deepseek":0.25,"gemini":0.25}
    almanac_w = 0.20
    weighted_sum = almanac * almanac_w
    total_w = almanac_w
    for r in results:
        w = weights.get(r["agent"],0.25) * r.get("weight_multiplier",1.0)
        weighted_sum += r["score"] * w
        total_w += w
    agent_comp = weighted_sum / total_w if total_w > 0 else 55
    final = round(agent_comp * 0.90 + human_score * 0.10, 1)
    bias = "BULLISH" if final >= 62 else "BEARISH" if final <= 42 else "NEUTRAL"
    size = 10 if final >= 60 else 7 if final >= 52 else 5 if final >= 45 else 0
    reason = ("Full 10 spreads" if size==10 else "Reduced 7 spreads" if size==7
              else "Half size 5 spreads" if size==5 else "SKIP — bearish composite")
    return {"composite":final,"bias":bias,"recommended_size":size,"size_reason":reason,
            "individual_scores":{"chatgpt":next((r["score"] for r in results if r["agent"]=="chatgpt"),55),
                                  "gemini":next((r["score"] for r in results if r["agent"]=="gemini"),55),
                                  "deepseek":next((r["score"] for r in results if r["agent"]=="deepseek"),55),
                                  "almanac":almanac,"human":human_score},
            "agent_summaries":{r["agent"]:{"score":r["score"],"bias":r["bias"],
                "confidence":r["confidence"],"key_factor":r["key_factor"],
                "week_outlook":r["week_outlook"]} for r in results},
            "timestamp":datetime.datetime.utcnow().isoformat()}

def send_telegram(intel):
    tok = os.environ.get("TELEGRAM_BOT_TOKEN","")
    cid = os.environ.get("TELEGRAM_CHAT_ID","")
    if not tok or not cid: return
    sc = intel["individual_scores"]
    msg = (f"*AI Board Intelligence Report*\n"
           f"_{datetime.date.today().strftime('%A %B %d %Y')}_\n\n"
           f"*Composite: {intel['composite']}/100 — {intel['bias']}*\n"
           f"*Trade: {intel['recommended_size']} spreads*\n"
           f"_{intel['size_reason']}_\n\n"
           f"*Individual Scores:*\n"
           f"ChatGPT:  {sc.get('chatgpt','—')}/100\n"
           f"Gemini:   {sc.get('gemini','—')}/100\n"
           f"DeepSeek: {sc.get('deepseek','—')}/100\n"
           f"Almanac:  {sc.get('almanac','—')}/100\n"
           f"Human:    {sc.get('human',55)}/100\n\n"
           f"_profdrtan.github.io/ibkr-options-income_")
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            data=json.dumps({"chat_id":cid,"text":msg,"parse_mode":"Markdown"}).encode(),
            headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            print("Telegram:", json.loads(r.read()).get("ok"))
    except Exception as e:
        print(f"Telegram error: {e}")

def run():
    print(f"[{datetime.datetime.utcnow().isoformat()}] AI Board Orchestrator")
    print("="*50)
    brief, almanac = build_brief()
    print("Calling ChatGPT, Gemini, DeepSeek in parallel...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(call_chatgpt,brief):"chatgpt",
                ex.submit(call_deepseek,brief):"deepseek",
                ex.submit(call_gemini,brief):"gemini"}
        results = [f.result() for f in concurrent.futures.as_completed(futs)]
    try:
        with open("data/trade_state.json","r") as f: state = json.load(f)
    except: state = {}
    human_score = state.get("recommendation",{}).get("human_score",55)
    intel = synthesise(results, almanac, human_score)
    print(f"\nComposite: {intel['composite']}/100 | {intel['bias']} | {intel['recommended_size']} spreads")
    state["last_updated"] = datetime.datetime.utcnow().isoformat()
    if "recommendation" not in state: state["recommendation"] = {}
    state["recommendation"]["intel"] = intel
    state["recommendation"]["intel_source"] = "ai_board_orchestrator"
    with open("data/trade_state.json","w") as f: json.dump(state, f, indent=2)
    send_telegram(intel)
    print("Done.")
    return intel

if __name__ == "__main__":
    run()
