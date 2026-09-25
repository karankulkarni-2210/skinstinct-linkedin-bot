"""AI layer (Gemini): triage a note, draft a post in Meera's voice, lint the draft.
No Telegram code here, so it can be tested offline."""
import json
import os
import re

from _http import HttpError, request
from _voice import EXEMPLARS, VOICE_GUIDE

MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
NEWS_ANGLE = os.getenv("NEWS_ANGLE", "true").lower() == "true"
FALLBACK_MODELS = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.0-flash"]
MAX_CHARS = 3000  # LinkedIn hard limit
SCORE_THRESHOLD = int(os.getenv("SCORE_THRESHOLD", "6"))

PILLARS = [
    "Ingredient Deep-Dive", "Founder Story", "India-Specific Context", "Industry Transparency",
    "Formulation Science", "Consumer Education", "Brand Philosophy",
]

AI_STUDIO = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
VERTEX_EXPRESS = "https://aiplatform.googleapis.com/v1/publishers/google/models/{m}:generateContent"
_endpoint = None   # remembered after the first successful call
_model = None


def _gemini(url, system, user, max_tokens, json_mode, tools=None):
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        # 2.5+ models spend part of this budget on thinking, so keep it generous.
        "generationConfig": {"maxOutputTokens": max(max_tokens, 8192),
                             "temperature": float(os.getenv("LLM_TEMPERATURE", "0.6"))},
    }
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    if tools:
        body["tools"] = tools
    out = request("POST", url, body=body, headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]}, timeout=120)
    cands = out.get("candidates") or []
    if not cands:
        raise RuntimeError(f"Gemini returned no candidates: {json.dumps(out)[:300]}")
    parts = (cands[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    return text, cands[0].get("groundingMetadata") or {}


def _call(system, user, max_tokens=2500, json_mode=False, tools=None, with_meta=False):
    """One Gemini call. Works with AI Studio keys and Vertex AI express-mode keys; if the configured
    model name is unknown it falls back to a current Flash model."""
    global _endpoint, _model
    endpoints = [_endpoint] if _endpoint else [AI_STUDIO, VERTEX_EXPRESS]
    models = [_model] if _model else [MODEL] + [m for m in FALLBACK_MODELS if m != MODEL]
    last = None
    for ep in endpoints:
        for m in models:
            try:
                text, meta = _gemini(ep.format(m=m), system, user, max_tokens, json_mode, tools)
                _endpoint, _model = ep, m
                return (text, meta) if with_meta else text
            except HttpError as e:
                last = e
                if e.status == 404:
                    continue          # model name not available here - try the next one
                if e.status in (400, 401, 403) and ep != endpoints[-1]:
                    break             # key not valid for this endpoint - try the other one
                raise
    raise last


def active_model():
    return _model or MODEL


# =====================================================================
# 1. TRIAGE - is this note worth a post?
# =====================================================================
TRIAGE_SYSTEM = f"""You are the editorial filter for Meera Pillai, founder of Skinstinct (D2C skincare, Mumbai).
She drops raw notes into Telegram: factory observations, reactions to customer DMs, late-night reading.
Most notes will never be posts. Your job is to decide which ones can become a LinkedIn post that
sounds like her and says something only she could say.

VOICE AND FACTS REFERENCE:
{VOICE_GUIDE}

SCORE (0-10) - be strict. Most raw notes are NOT posts; if every note passes, you're too lenient.
Anchors:
- 0-1: a task reminder, logistics or to-do ("call supplier re invoice", "order labels"), a mood
  ("tired, long day"), or a sales push ("50% off this weekend").
- 2-3: an abandoned half-thought or bare topic with no observation ("something about retinol??",
  "ceramides - write about this"), or a note that only works by attacking a named brand.
- 4-5: a real topic, but generic - no specific detail, number, scene or mechanism from her own work.
- 6-7: a specific observation or experience (a batch, a test, a customer question, a number) with an
  idea a reader would learn from.
- 8-10: specific AND only she could write it: formulation expertise, a real number or mechanism, and
  a clear point for the reader.
Within that, weigh: specificity, her expertise edge, relevance to urban Indian women 28-40 who are
tired of being sold to, and whether it can be written without inventing anything.

HARD REJECT - score 3 or below regardless of quality, and name the reason, if the note:
- names or identifies a customer, employee, supplier contact or any private individual;
- reveals details of unreleased products (she has two in formulation she is not ready to discuss);
- attacks a named competitor brand;
- would require a medical claim (treats/cures/heals a condition) or a diagnosis;
- is a sales pitch or discount;
- contains confidential commercial terms (pricing with manufacturers, margins, contracts).

Pillar must be one of: {", ".join(PILLARS)}.

KEYWORDS: 3-5 search terms lifted from the note's own specifics, 1-4 words each, that a journalist or
researcher would actually use. Each must name a specific thing the note mentions or directly implies:
- a named ingredient or ingredient system: "niacinamide", "phenoxyethanol", "preservative blend"
- a measurement or property: "finished product pH", "0.4 pH drop", "emollient texture"
- a test, document or process step: "certificate of analysis", "raw material specification change",
  "batch-to-batch variation", "stability testing"
- a named regulator, rule or market: "CDSCO cosmetics rules", "BIS", "India humidity"
NEVER use category words on their own: "ingredients", "processes", "regulations", "skincare",
"quality", "formulation", "brand", "customers", "industry". Example for a note about a supplier quietly
changing a preservative and the batch pH dropping: ["preservative blend change", "finished product pH",
"raw material specification change", "certificate of analysis", "batch-to-batch variation"].
SEARCH PHRASE: one short news-search phrase (3-6 words) built from the keywords, e.g.
"cosmetic preservative supplier change pH".

Return ONLY a JSON object, no prose, no code fences:
{{"score": 0, "reason": "one line: why this score",
 "pillar": "...",
 "angle": "one sentence: the single idea the post would argue",
 "hook": "the concrete detail from the note the post should open on",
 "needs_from_meera": ["facts she must confirm or supply"],
 "keywords": ["3-5 specific search keywords - see KEYWORDS above"],
 "search_phrase": "short news-search phrase",
 "hard_reject": null}}"""


def parse_json(text):
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return {"score": 0, "pillar": None, "angle": "", "hook": "",
                "reason": "I couldn't read the scoring result - send /draft <id> to draft it anyway.",
                "needs_from_meera": [], "hard_reject": None}


def triage(note_text):
    out = parse_json(_call(TRIAGE_SYSTEM, f"NOTE:\n{note_text}", max_tokens=800, json_mode=True))
    try:
        out["score"] = max(0, min(10, int(out.get("score", 0))))
    except (TypeError, ValueError):
        out["score"] = 0
    if out.get("hard_reject"):
        out["score"] = min(out["score"], 3)
        out["reason"] = out.get("reason") or str(out["hard_reject"])
    # B1-1: the score alone decides. 6+ drafts; below 6 gets a one-line "why not" and stops.
    out["verdict"] = "develop" if out["score"] >= SCORE_THRESHOLD else "reject"
    if out.get("pillar") not in PILLARS:
        out["pillar"] = None
    out["keywords"] = clean_keywords(out.get("keywords"))[:5]
    out["search_phrase"] = re.sub(r"\s+", " ", str(out.get("search_phrase") or " ".join(out["keywords"][:3]))).strip()
    return out


GENERIC_KEYWORDS = {"ingredient", "ingredients", "process", "processes", "regulation", "regulations", "skincare",
                    "skin care", "quality", "formulation", "formulations", "brand", "brands", "customer", "customers",
                    "industry", "product", "products", "beauty", "cosmetics", "science", "manufacturing", "health"}


def clean_keywords(kw):
    """Keep specific, short keywords; drop category words and duplicates."""
    if not isinstance(kw, list):
        return []
    out, seen = [], set()
    for k in kw:
        k = re.sub(r"\s+", " ", str(k)).strip(" .,;\"'")
        low = k.lower()
        if not k or low in GENERIC_KEYWORDS or len(k.split()) > 5 or low in seen:
            continue
        seen.add(low)
        out.append(k)
    return out[:6]


# =====================================================================
# 2a. NEWS ANGLE (B1-2) - top Google News result for the note's search phrase. No account, no key.
# =====================================================================
GOOGLE_NEWS = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"


def _strip_html(s):
    import html
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def fetch_news(phrase):
    """Top Google News result for `phrase`: {headline, source, date, summary, url} or None."""
    import urllib.parse
    import xml.etree.ElementTree as ET
    if not phrase:
        return None
    raw = request("GET", GOOGLE_NEWS.format(q=urllib.parse.quote(phrase)), raw=True, timeout=20,
                  headers={"User-Agent": "Mozilla/5.0 (skinstinct-bot)"})
    item = ET.fromstring(raw).find("./channel/item")
    if item is None:
        return None
    source = (item.findtext("source") or "").strip()
    title = (item.findtext("title") or "").strip()
    if source and title.endswith(" - " + source):
        title = title[: -len(" - " + source)]
    summary = _strip_html(item.findtext("description"))
    if not summary or summary.startswith(title[:40]):     # Google's blurb often just repeats the title
        summary = title
    return {"headline": title, "source": source, "date": (item.findtext("pubDate") or "").strip()[:16],
            "summary": summary[:240], "url": (item.findtext("link") or "").strip()}


def news_angle(note_text, triage_info=None):
    """Search Google News with the note's phrase (falling back to its first two keywords).
    Returns the top item plus the keywords/phrase used, or None. Never raises."""
    if not NEWS_ANGLE:
        return None
    t = triage_info or {}
    kws = t.get("keywords") or []
    quoted = [f'"{k}"' for k in kws if " " in k][:2]         # exact-phrase search stops "masking" matching "mask"
    tries = ([" ".join(quoted[:1] + ["cosmetics"])] if quoted else []) + [t.get("search_phrase")] + \
            ([" ".join(quoted)] if len(quoted) == 2 else []) + ([" ".join(kws[:2])] if len(kws) >= 2 else [])
    for phrase in [p for p in tries if p]:
        try:
            item = fetch_news(phrase)
        except Exception:
            item = None
        if item:
            return item | {"keywords": kws, "search_phrase": phrase}
    return None


def verify_flag(news):
    return (f"[VERIFY NEWS: this draft uses \"{news['headline']}\" ({news['source']}, {news['date']}). "
            f"Check the article before posting: {news['url']}]")


# =====================================================================
# 2. DRAFT - write the post in her voice
# =====================================================================
DRAFT_SYSTEM = f"""You ghost-draft LinkedIn posts for Meera Pillai, founder of Skinstinct. She will read,
edit and publish every post herself. Your draft is a starting point she should need to change
very little - her last content writer failed because she spent more time rewriting than writing.

{VOICE_GUIDE}

{EXEMPLARS}

NON-NEGOTIABLE RULES:
1. Write only from (a) the note, (b) the facts listed in "Facts she has already published", (c) the
   NEWS ITEM if one is given and you use it, and (d) widely established formulation science stated at the level of
   certainty she would use.
2. NEVER invent a statistic, study, date, quote, event, customer story, test result or sensory detail.
   If the post needs a fact you don't have, write it as [VERIFY: what Meera needs to confirm] inline.
3. NEWS ITEM: if this news item is genuinely relevant, use it to make the post timely. If it doesn't
   fit naturally, ignore it. If you use it: one or two sentences at most, attributed to its source by
   name, stating only what the headline and summary say - never embellish it. It supports her point;
   it never becomes the point. Say whether you used it in <news_used>.
4. CONTRACTIONS like she writes: it's, doesn't, I'm, we're, isn't, that's, won't, I've. Spell a form
   out only for deliberate emphasis. A draft full of "it is / does not / I am" is wrong.
5. KEEP HER WORDS: carry the note's sharpest phrases into the post nearly verbatim. Keep her hedges
   exactly ("I think customers will notice" stays "I think"). Never upgrade her certainty.
6. EXPLAIN THE MECHANISM: say why the cause produces the effect, in one clear causal chain, like a
   formulator explaining to a smart friend. Chemistry you're unsure of becomes [VERIFY: ...].
7. Never reuse a sentence from her published pieces. Use at most one boundary move, in fresh words.
8. Prose paragraphs only. No emojis, hashtags, bullet points, headers or exclamation marks. Spaced
   hyphen " - " instead of em dashes. British/Indian spelling. Don't spell out CoA or INCI.
9. 1,600-2,800 characters. Hard maximum 3,000.
10. Write to the woman using the product unless the note is about building a brand. End on ONE
    practical action for her or a flat closing statement - never a question to the audience.

OUTPUT FORMAT (exactly these tags, nothing outside them):
<post>
the full post text
</post>
<verify>
- one line per fact Meera must confirm before posting (or "none")
</verify>
<hook_idea>
one optional line: a current angle she could look up herself, or "none"
</hook_idea>
<news_used>
yes or no
</news_used>
<why>
one line: why this note is worth a post
</why>"""


def _tag(text, name):
    m = re.search(rf"<{name}>\s*(.*?)\s*</{name}>", text, re.S)
    return m.group(1).strip() if m else ""


def parse_draft(raw):
    post = _tag(raw, "post") or raw.strip()
    verify = [l.lstrip("-• ").strip() for l in _tag(raw, "verify").splitlines()]
    verify = [v for v in verify if v and v.lower() != "none"]
    hook = _tag(raw, "hook_idea")
    return {
        "post": post,
        "verify": verify,
        "hook_idea": "" if hook.lower() in ("", "none") else hook,
        "why": _tag(raw, "why"),
        "news_used": _tag(raw, "news_used").lower().startswith("y"),
    }


def draft(note_text, triage_info=None, feedback=None, previous=None, news=None):
    t = triage_info or {}
    parts = [f"NOTE FROM MEERA (her own words - keep the sharpest phrases):\n{note_text}"]
    if t:
        parts.append(
            f"EDITORIAL DIRECTION:\nPillar: {t.get('pillar')}\nAngle: {t.get('angle')}\n"
            f"Open on: {t.get('hook')}\nFacts still needed: {t.get('needs_from_meera') or 'none'}"
        )
    if news:
        parts.append(f"NEWS ITEM (top Google News result for \"{news.get('search_phrase', '')}\"). If this news item is "
                     f"genuinely relevant, use it to make the post timely. If it doesn't fit naturally, ignore it.\n"
                     f"Headline: {news['headline']}\nSource: {news['source']} ({news['date']})\nSummary: {news['summary']}")
    if previous and feedback:
        parts.append(
            f"YOUR PREVIOUS DRAFT:\n{previous}\n\nMEERA'S FEEDBACK (this overrides the guide):\n{feedback}\n\n"
            "Revise the draft to address her feedback. Keep what she didn't object to."
        )
    return parse_draft(_call(DRAFT_SYSTEM, "\n\n".join(parts), max_tokens=2500))


# =====================================================================
# 3. LINT - deterministic voice checks (no AI)
# =====================================================================
BANNED = [
    "glow", "radiant", "holy grail", "game-changer", "game changer", "miracle", "skin-loving",
    "pamper", "self-care ritual", "thank you later", "will thank you", "unlock", "transform",
    "journey", "secret to", "here's the thing", "let that sink in", "agree?", "thoughts?",
    "follow for more", "humbled", "in today's world", "let's dive", "dive in", "buckle up",
    "chemical-free", "toxin", "guaranteed", "cures", "heals",
]
US_SPELLINGS = {
    "moisturizer": "moisturiser", "oxidize": "oxidise", "oxidizes": "oxidises", "color": "colour",
    "optimize": "optimise", "stabilize": "stabilise", "stabilized": "stabilised", "fiber": "fibre",
    "program": "programme", "behavior": "behaviour", "flavor": "flavour", "maximize": "maximise",
    "minimize": "minimise", "sensitization": "sensitisation", "analyze": "analyse",
}
FORMAL = re.compile(r"\b(it is|i am|do not|does not|is not|are not|cannot|we are|that is|you are|did not|was not|"
                    r"will not|i have|we have|there is|would not|could not|should not)\b", re.I)
CONTRACTION = re.compile(r"\b\w+'(?:t|s|re|ve|ll|d|m)\b", re.I)


def _shingles(text, n=9):
    w = re.findall(r"[a-z0-9']+", text.lower())
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


_PUBLISHED = None


def copied_phrases(post):
    global _PUBLISHED
    if _PUBLISHED is None:
        _PUBLISHED = _shingles(EXEMPLARS)
    return sorted(_shingles(post) & _PUBLISHED)


EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]")


def lint(post):
    problems = []
    low = post.lower()
    if len(post) > MAX_CHARS:
        problems.append(f"Too long: {len(post)} characters (LinkedIn max {MAX_CHARS}).")
    if len(post) < 900:
        problems.append(f"Too short for her style: {len(post)} characters.")
    if EMOJI.search(post):
        problems.append("Contains emoji.")
    if "!" in post:
        problems.append("Contains exclamation mark.")
    if re.search(r"(^|\s)#\w", post):
        problems.append("Contains hashtags.")
    if "—" in post or "–" in post:
        problems.append("Uses em/en dash - Meera uses a spaced hyphen ' - '.")
    if re.search(r"^\s*([-*•]|\d+[.)])\s", post, re.M):
        problems.append("Contains a bulleted or numbered list.")
    for w in BANNED:
        if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", low):
            problems.append(f"Off-voice phrase: '{w}'.")
    for us, uk in US_SPELLINGS.items():
        if re.search(rf"\b{us}\b", low):
            problems.append(f"US spelling '{us}' - use '{uk}'.")
    formal, contr = len(FORMAL.findall(post)), len(CONTRACTION.findall(post.replace("\u2019", "'")))
    if formal >= 4 and contr < 2 * formal:
        problems.append(f"Too formal: {formal} spelled-out forms (it is, does not...) vs {contr} contractions - "
                        "Meera uses about 4 contractions for every spelled-out form.")
    copied = copied_phrases(post)
    if copied:
        problems.append(f"Copies a phrase from her published work: '{copied[0]}...' - reword it.")
    last = post.strip().splitlines()[-1] if post.strip() else ""
    if last.rstrip().endswith("?"):
        problems.append("Ends on a question to the audience.")
    return problems


REPAIR_SYSTEM = """You are a copy editor for Meera Pillai. Fix ONLY the listed problems in the post. Change nothing
else: keep every fact, hedge and [VERIFY: ...] marker. For "too formal", switch spelled-out forms to
contractions (it's, doesn't, I'm, we're, isn't, that's) except where one is deliberate emphasis.
For "too long", cut to about 2,500 characters by trimming explanation and repetition - keep the
opening, her own phrases from the note, the boundary move and the closing action.
Return the corrected post inside <post></post> and nothing else."""


def make_draft(note_text, triage_info=None, feedback=None, previous=None, news=None, find_news=True):
    """News item (B1-2) -> draft -> lint -> one repair pass if needed -> verify flag if news used.
    Returns dict with post, verify, hook_idea, why, lint, news, verify_markers."""
    if news is None and find_news and not previous:
        news = news_angle(note_text, triage_info)
    d = draft(note_text, triage_info, feedback, previous, news)
    problems = lint(d["post"])
    for _ in range(2):                      # up to two repair passes (length often needs a second)
        if not problems:
            break
        fixed = _tag(_call(REPAIR_SYSTEM, "PROBLEMS:\n- " + "\n- ".join(problems) + f"\n\nPOST:\n{d['post']}"), "post")
        if not fixed:
            break
        d["post"] = fixed
        problems = lint(fixed)
    d["lint"] = problems
    d["news"] = news
    d["news_used"] = bool(news) and d.get("news_used", False)
    if d["news_used"]:
        d["post"] = d["post"].rstrip() + "\n\n" + verify_flag(news)     # B1-2: every news draft carries the flag
    d["verify_markers"] = re.findall(r"\[VERIFY[^\]]*\]", d["post"])
    return d
