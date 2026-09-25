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

SCORING (0-10, add these up):
- Specificity (0-3): a concrete scene, number, mechanism or observation - not a mood or a slogan.
- Expertise edge (0-3): something a formulator/founder knows that her readers don't.
- Reader relevance (0-2): useful to urban Indian women 28-40 who are tired of being sold to.
- Writable without invention (0-2): the post can be written from the note plus her published facts,
  without making up numbers, studies or events.

HARD REJECT (verdict "reject" regardless of score) if the note:
- names or identifies a customer, employee, supplier contact or any private individual;
- reveals details of unreleased products (she has two in formulation she is not ready to discuss);
- attacks a named competitor brand;
- would require a medical claim (treats/cures/heals a condition) or a diagnosis;
- is a sales pitch, a discount, or purely personal/mood with no idea in it;
- contains confidential commercial terms (pricing with manufacturers, margins, contracts).

VERDICTS:
- "develop": score >= 6, no hard reject, can be drafted now.
- "hold": has a real idea but needs facts only Meera can supply first (list them).
- "reject": score < 4 or any hard reject.

Pillar must be one of: {", ".join(PILLARS)}.

KEYWORDS: 3-6 search terms lifted from the note's own specifics, 1-4 words each, that a journalist or
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

Return ONLY a JSON object, no prose, no code fences:
{{"verdict": "develop|hold|reject", "score": 0, "pillar": "...",
 "angle": "one sentence: the single idea the post would argue",
 "hook": "the concrete detail from the note the post should open on",
 "reason": "one short sentence explaining the verdict",
 "needs_from_meera": ["facts she must confirm or supply"],
 "keywords": ["3-6 specific search keywords - see KEYWORDS below"],
 "hard_reject": null}}"""


def parse_json(text):
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return {"verdict": "hold", "score": 0, "pillar": None, "angle": "", "hook": "",
                "reason": "Triage output could not be parsed - review manually.",
                "needs_from_meera": [], "hard_reject": None}


def triage(note_text):
    out = parse_json(_call(TRIAGE_SYSTEM, f"NOTE:\n{note_text}", max_tokens=800, json_mode=True))
    if out.get("hard_reject"):
        out["verdict"] = "reject"
    try:
        out["score"] = max(0, min(10, int(out.get("score", 0))))
    except (TypeError, ValueError):
        out["score"] = 0
    if out.get("pillar") not in PILLARS:
        out["pillar"] = None
    out["keywords"] = clean_keywords(out.get("keywords"))
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
# 2a. NEWS ANGLE - one current, real, sourced item (B1). Gemini + Google Search grounding.
# =====================================================================
NEWS_SYSTEM = """You research one current reference for a LinkedIn post by Meera Pillai, a Mumbai skincare
founder with a pharma formulation background. Use Google Search. Find ONE real news story, regulatory
update, or published study from the last 6 months that connects directly to the note's angle and would
matter to urban Indian women who buy skincare. Prefer India-specific items (CDSCO, BIS, Indian market,
Indian climate) or peer-reviewed ingredient research. Never invent or guess: if you can't find a
specific, dated, relevant item, return <none/>.

Build your searches from the KEYWORDS taken from her note - they're specific on purpose (named
ingredients, measurements, tests, regulators). Search them as given, alone or paired, adding "India"
where it helps; don't broaden them into generic terms like "skincare regulations". The item must
genuinely cover at least one keyword, not just the general topic.

Return exactly:
<keywords>comma-separated keywords you searched with (the specific ones given, or 3-6 equally specific ones from the note)</keywords>
<matched>comma-separated keywords the item actually covers</matched>
<headline>the item's headline or title</headline>
<source>publication or organisation name</source>
<date>publication date as reported</date>
<fact>the single fact the post could use, stated exactly as the source reports it</fact>
<link>one sentence: how it connects to the note</link>"""


def news_angle(note_text, triage_info=None):
    """Returns {headline, source, date, fact, link, urls} or None. Never raises - drafting must not
    fail because search did."""
    if not NEWS_ANGLE:
        return None
    t = triage_info or {}
    try:
        kw = ", ".join(t.get("keywords") or []) or "(pick 3-6 from the note yourself)"
        text, meta = _call(NEWS_SYSTEM, f"NOTE:\n{note_text}\n\nANGLE: {t.get('angle', '')}\n\nKEYWORDS: {kw}",
                           max_tokens=2000,
                           tools=[{"google_search": {}}], with_meta=True)
    except Exception:
        return None
    if "<none" in text or not _tag(text, "headline"):
        return None
    urls = [c["web"]["uri"] for c in (meta.get("groundingChunks") or []) if c.get("web", {}).get("uri")][:3]
    if not urls:                      # no grounding = no evidence it's real; don't use it
        return None
    split = lambda v: [x.strip() for x in v.split(",") if x.strip()]
    return {k: _tag(text, k) for k in ("headline", "source", "date", "fact", "link")} | {
        "urls": urls, "keywords": clean_keywords(split(_tag(text, "keywords"))) or (t.get("keywords") or []),
        "matched": split(_tag(text, "matched"))}


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
   NEWS ANGLE if one is given, and (d) widely established formulation science stated at the level of
   certainty she would use.
2. NEVER invent a statistic, study, date, quote, event, customer story, test result or sensory detail.
   If the post needs a fact you don't have, write it as [VERIFY: what Meera needs to confirm] inline.
3. NEWS ANGLE: if one is given, use it in one or two sentences at most, attributed to its source by
   name ("A [source] report this month..."), stating only its fact - never embellish it. It supports
   her point; it never becomes the point. If none is given, don't mention any news or outside data.
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
        parts.append(f"NEWS ANGLE (found by search; Meera verifies before posting):\nHeadline: {news['headline']}\n"
                     f"Source: {news['source']} ({news['date']})\nFact: {news['fact']}\nConnection: {news['link']}")
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
Return the corrected post inside <post></post> and nothing else."""


def make_draft(note_text, triage_info=None, feedback=None, previous=None, news=None, find_news=True):
    """News angle (B1) -> draft -> lint -> one repair pass if needed.
    Returns dict with post, verify, hook_idea, why, lint, news, verify_markers."""
    if news is None and find_news and not previous:
        news = news_angle(note_text, triage_info)
    d = draft(note_text, triage_info, feedback, previous, news)
    problems = lint(d["post"])
    if problems:
        fixed = _tag(_call(REPAIR_SYSTEM, "PROBLEMS:\n- " + "\n- ".join(problems) + f"\n\nPOST:\n{d['post']}"), "post")
        if fixed:
            d["post"] = fixed
            problems = lint(fixed)
    d["lint"] = problems
    d["news"] = news
    d["verify_markers"] = re.findall(r"\[VERIFY:[^\]]*\]", d["post"])
    return d
