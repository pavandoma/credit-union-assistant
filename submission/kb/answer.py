"""Answer path. Short on purpose: no extraction runs here.

question -> snapshot for the date -> retrieve checked chunks -> rank by meaning, conditions and source
purpose -> let stronger or newer sources override contradicted values -> concise answer with sources,
or an honest 'unknown'."""
from __future__ import annotations

import re
from datetime import date

from .contract_types import Answer, Source
from .agents.history import normval
from .text import STOP, month_end, norm_num, tokens

EXTRA_STOP = frozenset("repeat again provide list show link url website web page site online member members "
                       "offer offers offered tell give see find use change changed changes changing increase "
                       "increased decrease decreased rose risen dropped fell previous previously earlier history "
                       "compare compared difference different updated since".split())
SYN = {"cd": ["certificate"], "cds": ["certificate"], "yield": ["apy"]}
# Words that shape a question but do not name its subject. They are searched, but the evidence need not
# contain them ("top Share Savings APY and the balance needed").
FILLER = frozenset("top best highest lowest max maximum min minimum need needed required require available "
                   "latest balance amount total number quote much".split())
RATE_PCT = re.compile(
    r"(?:rate|apr|apy|dividend|yield)\)?\s*(?:is|of|at|:|=|-)?\s*(?:as low as\s*)?\d+(?:\.\d+)?\s*%|"
    r"\d+(?:\.\d+)?\s*%\s*(?:[-–]\s*\d+(?:\.\d+)?\s*%\s*)?(?:apr|apy|annual percentage|intro|introductory|"
    r"(?:interest|dividend|fixed|variable) rate)", re.I)
SOFT = {
    "mortgage": ["home", "loan", "conventional", "fixed"],
    "card": ["visa"],
    "wallet": ["pay", "apple", "google", "samsung"],
    "recognition": ["recognized", "named", "ranked", "award"],
    "received": ["recognized", "named", "ranked"],
    "eligible": ["join", "membership", "live", "work", "charter"],
    "eligibility": ["join", "membership", "live", "work", "charter"],
    "student": ["student", "loan", "refinance"],
    "update": ["change", "log", "call"],
    "car": ["auto", "vehicle"],
    "vehicle": ["auto"],
    "truck": ["auto"],
    "membership": ["join", "member"],
}
# Words that name a product type. The word in front of one of them ("construction loan", "auto loan",
# "Platinum Visa card") must appear in the rate row that answers the question.
PRODUCT_HEADS = frozenset("loan account certificate card mortgage ira cd saving checking".split())
NOT_MODIFIER = frozenset("month year term new used current best top regular standard my your our a the this "
                         "each every all some other both more many own its his her per one two "
                         "that their any what which".split())
# Credit-union names that only restate the product type: a "share certificate" is a certificate.
PLAIN_NAMES = frozenset({("share", "certificate"), ("term", "certificate"), ("deposit", "certificate"),
                         ("share", "draft"), ("credit", "card")})
SCHEDULE = re.compile(r"^\s*(?:lobby|drive[- ]?up|mon|tue|wed|thu|fri|sat|sun)[a-z]*\b[^.]{0,80}?"
                      r"(?:\d{1,2}(?::\d{2})?\s*(?:a\.?m|p\.?m)|noon|closed)", re.I)
# "Do any savings tiers pay 0.35% APY?" asks whether a value exists among a product's tiers. The nearest row
# with that number is not an answer to it: the tiers of the product are.
EXISTS = re.compile(r"^\s*(?:do|does|is|are)\s+(?:there\s+)?(?:any|a|an)\b(?P<subject>.*?)"
                    r"(?P<value>\d+(?:\.\d+)?)\s*%", re.I)
EXISTS_SKIP = frozenset("tier pay paying earn earning offer offering account product rate apy apr dividend yield "
                        "interest annual percentage".split())      # stemmed: "tiers" -> "tier"
# Exact identifiers and schedules. Another page of the same snapshot that states the same one is more
# evidence for the answer, even when its wording differs.
ID_NUM = re.compile(r"(?<![\d.,$])\d{7,12}(?![\d,%]|\.\d)")
PHONE = re.compile(r"(?<!\d)\(?\d{3}\)?[-. ]?\d{3}[-. ]\d{4}(?!\d)")
_DAY = r"(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?"
_TIME = r"(\d{1,2}(?::\d{2})?\s*[ap]\.?\s?m\.?|noon)"
DAY_SPAN = re.compile(_DAY + r"\s*(?:-|–|—|to|through)\s*" + _DAY + r"[,:]?\s*" + _TIME + r"\s*(?:-|–|—|to)\s*" + _TIME,
                      re.I)
# "You live or work within our charter area" names no place. The page's own sentence that does is added.
AREA_REF = re.compile(r"\b(?:charter|service|membership)\s+area\b|\bfield of membership\b", re.I)
STATE_RE = re.compile(r"\b(?:Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|"
                      r"Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|"
                      r"Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|"
                      r"New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|"
                      r"Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|"
                      r"Virginia|Washington|West Virginia|Wisconsin|Wyoming)\b")
MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def variants(t: str) -> list[str]:
    """Other forms of a word: ranked -> rank, closing -> close, rank -> ranked. Used on the question side only."""
    if len(t) < 4 or t[0].isdigit():
        return []
    if t.endswith("ed") and len(t) > 4:
        out = [t[:-2], t[:-1]]
    elif t.endswith("ing") and len(t) > 5:
        out = [t[:-3], t[:-3] + "e"]
    else:
        out = [t + "ed", t + "d", (t[:-1] if t.endswith("e") else t) + "ing"]
    return [x for x in dict.fromkeys(out) if x != t and len(x) >= 3]

AMOUNT = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(k|m)?\b", re.I)
KAMOUNT = re.compile(r"\b(\d+(?:\.\d+)?)\s*k\b", re.I)
RANGE = re.compile(r"\$?(\d[\d,]*(?:\.\d+)?)\s*(?:-|–|—|to)\s*\$?(\d[\d,]*(?:\.\d+)?)")
PLUS = re.compile(r"\$?(\d[\d,]*(?:\.\d+)?)\s*\+")
ORLESS = re.compile(r"\$?(\d[\d,]*(?:\.\d+)?)\s*or\s*less", re.I)
YEAR_RANGE = re.compile(r"\b(20\d\d|19\d\d)\s*-\s*(\d{2,4})\b")
YEAR_OLDER = re.compile(r"\b(20\d\d|19\d\d)\b[^|;]{0,25}\(?and older\)?", re.I)
YEAR = re.compile(r"\b(19\d\d|20\d\d)\b")
TIME_RANGE = re.compile(r"\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|noon)", re.I)
ADDR = re.compile(r"\b\d+\s+[A-Z][\w .'-]+(?:St|Ave|Rd|Dr|Ln|Blvd|Way|Ct|Pkwy|Hwy)\b")
PCT = re.compile(r"\d\s*%")
NATIONAL = re.compile(r"\bu\.\s?s\.|united states|nationwide|nationally|across the (?:country|nation)|industry-wide",
                      re.I)
PHONE = re.compile(r"\b\d{3}[-. ]\d{3}[-. ]\d{4}\b|\(?\d{3}\)?[-. ]?\d{3}-\d{4}")

DISTINCT = frozenset("business jumbo roth traditional refinance purchase personal student new used special "
                     "regular".split())
ROW_NOISE = frozenset("interest rate apr apy point monthly payment effective dividend minimum open earn".split())

NO_ANSWER = ("I don't know. The website does not say this. Please contact us or visit a branch "
             "for help.")
VAGUE = ("I don't know which product or service you mean. Please name it, for example "
         "\"12-month certificate APY\" or \"Galena branch address\".")

# ---- guardrails --------------------------------------------------------------------------------
MAX_QUESTION = 1000                     # characters; longer input is cut (real questions are short)
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")
GENERIC_Q = frozenset("rate apr apy interest dividend yield fee cost price charge today current".split())


def clean_question(question) -> str:
    if question is None:
        return ""
    if not isinstance(question, str):
        question = str(question)
    return re.sub(r"\s+", " ", CONTROL.sub(" ", question[:MAX_QUESTION * 2])).strip()[:MAX_QUESTION]


def parse_as_of(value):
    """Returns (ISO date or None, error message or None). Accepts 'YYYY-MM-DD' and ISO date-times."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None
    s = str(value).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat(), None
        except ValueError:
            pass
    return None, (f"I could not read the date \"{s[:40]}\". Please use the form YYYY-MM-DD, "
                  "for example 2026-05-31.")


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def recency(pubdate: str | None) -> float:
    if not pubdate:
        return 0.0
    try:
        d = (date.fromisoformat(pubdate) - date(2018, 1, 1)).days
    except ValueError:
        return 0.0
    return max(0.0, min(1.0, d / 3200.0))


def eff_recency(eff: str | None) -> float:
    """How recent a stated effective date is within the snapshot period (2026)."""
    try:
        d = (date.fromisoformat(eff) - date(2026, 1, 1)).days
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, d / 300.0))


DATE_PHRASE = re.compile(
    r"\b(?:as of|in|during|back in|since|by|on|for)?\s*(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
    r"[a-z]*\.?\s+(?:\d{1,2}(?:st|nd|rd|th)?,?\s+)?\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b", re.I)


class Question:
    def __init__(self, text: str):
        self.text = DATE_PHRASE.sub(" ", text)        # a date selects the snapshot; it is not a search word
        self.low = self.text.lower()
        text = self.text
        self.toks = tokens(text)
        base = [t for t in self.toks if t not in STOP and t not in EXTRA_STOP]
        self.ctoks = list(dict.fromkeys(base))
        for t in list(self.ctoks):
            for s in SYN.get(t, []):
                if s not in self.ctoks:
                    self.ctoks.append(s)
        self.soft = list(dict.fromkeys(x for t in self.ctoks for x in SOFT.get(t, []) if x not in self.ctoks))
        self.business = "business" in self.low
        self.apy = bool(re.search(r"\bapy\b|annual percentage yield|\byield\b", self.low))
        self.apr = bool(re.search(r"\bapr\b|annual percentage rate", self.low))
        self.rate = bool(re.search(r"\brates?\b|\bapy\b|\bapr\b|interest|dividend|yield|"
                                   r"how much (?:does|do|will|would|can).*\b(?:pay|earn|yield)\b", self.low))
        self.fee = bool(re.search(r"\bfees?\b|\bcosts?\b", self.low))
        self.hours = bool(re.search(r"\bhours?\b|lobby|drive[- ]?up|when (?:is|are|do|does)\b.*\bopen\b|what time|"
                                    r"open on (?:mon|tue|wed|thu|fri|sat|sun)|opening times?", self.low))
        self.address = bool(re.search(r"\baddress\b|located|\bwhere\b", self.low))
        self.url = bool(re.search(r"\blink\b|\burl\b|web ?site|web address|\bjoin\b.*\bonline\b|\bonline\b.*\bjoin\b|"
                                  r"which (?:web )?page|what page|where (?:do|can) i (?:apply|join|sign up|enroll)|"
                                  r"(?:apply|sign up|enroll)\b.*\bonline", self.low))
        self.applying = bool(re.search(r"\bopen\b|\bapply|\bjoin|sign up|enroll|membership|become a member", self.low))
        self.count = bool(re.search(r"how many|number of", self.low))
        self.phone = bool(re.search(r"phone|telephone|call|dial|contact number", self.low))
        self.change = bool(re.search(r"chang(?:e|ed|es|ing)|increas|decreas|"
                                     r"(?:went|gone|go|going) (?:up|down)|rose|risen|dropped|fell|"
                                     r"used to|previous|earlier|history|over time|compared? (?:to|with)|"
                                     r"differen(?:ce|t) (?:from|than|between)|updated|how has", self.low))
        self.listall = bool(re.search(r"\ball\b|\bevery\b|\blist\b|rates for the|\boptions\b|credit cards\b|"
                                      r"certificates\b|accounts\b|certificate of accounts", self.low))
        self.promo = bool(re.search(r"promo|special|offer|bonus|introductory|expired", self.low))
        amounts = [_num(m.group(1)[:15]) * (1000 if (m.group(2) or "").lower() == "k" else 1)
                   for m in AMOUNT.finditer(text)]
        amounts += [_num(m.group(1)[:12]) * 1000 for m in KAMOUNT.finditer(text)]
        self.amounts = list(dict.fromkeys(a for a in amounts if 0 < a < 1e12))[:4]      # bounded: no overflow
        self.years = list(dict.fromkeys(int(y) for y in YEAR.findall(text)))[:3]
        self.terms = []
        for i, t in enumerate(self.toks[:-1]):
            if t.isdigit() and len(t) <= 3 and self.toks[i + 1] in ("month", "year", "mo", "yr") \
                    and (t, self.toks[i + 1]) not in self.terms and len(self.terms) < 3:
                self.terms.append((t, self.toks[i + 1]))
        caps = re.findall(r"(?<!^)(?<![.?!]\s)\b([A-Z][a-z][\w'$-]+)", text)
        skip = {"dupaco", "what", "how", "when", "where", "who", "does", "can", "october", "september", "august",
                "may", "june", "july", "january", "february", "march", "april", "november", "december", "apy", "apr",
                "i", "is", "are", "do", "provide", "get", "tell", "give", "why", "which", "will", "should", "could"}
        self.entities = []
        for c in caps:
            c = re.sub(r"'s$", "", c.lower())
            if c not in skip:
                self.entities.append(c)
        # A capitalized first word can be a name too ("Mullan Avenue branch ..."). The Answerer accepts it as a
        # name only when the website rarely uses the word (common words such as "Interest" are not names).
        m = re.match(r"\s*([A-Z][a-z][\w'$-]+)", text)
        lead = re.sub(r"'s$", "", m.group(1).lower()) if m else ""
        self.lead = lead if lead and lead not in skip and lead not in STOP and lead not in EXTRA_STOP \
            and lead not in FILLER and lead not in self.entities else ""
        amt_tokens = {norm_num(str(int(a)) if a == int(a) else str(a)) for a in self.amounts}
        for n, u in list(self.terms):
            alt = str(int(n) * 12) if u == "year" and int(n) * 12 <= 120 else (
                str(int(n) // 12) if u == "month" and int(n) % 12 == 0 and int(n) > 0 else None)
            if alt and alt not in self.ctoks and alt not in self.soft:
                self.soft.append(alt)
        term_nums = {n for n, u in self.terms}
        self.specifiers = [t for t in self.ctoks if t.isdigit() and t not in amt_tokens and t not in term_nums
                           and not re.fullmatch(r"(19|20)\d\d", t)]
        self.modifiers = []
        for i, t in enumerate(self.toks[1:], start=1):
            prev = self.toks[i - 1]
            if (prev, t) in PLAIN_NAMES:
                continue
            if t in PRODUCT_HEADS and not prev[0].isdigit() and prev not in STOP and prev not in NOT_MODIFIER \
                    and prev not in PRODUCT_HEADS and prev not in EXTRA_STOP:
                self.modifiers.append(prev)
        self.var = {t: variants(t) for t in self.ctoks}


def _in_ranges(text: str, amount: float):
    found = False
    for m in RANGE.finditer(text):
        lo, hi = _num(m.group(1)), _num(m.group(2))
        if hi < lo or (lo < 100 and hi < 100):
            continue
        found = True
        if lo <= amount <= hi + 0.995:
            return True
    for m in PLUS.finditer(text):
        found = True
        if amount >= _num(m.group(1)):
            return True
    for m in ORLESS.finditer(text):
        found = True
        if amount <= _num(m.group(1)):
            return True
    return False if found else None


def _year_match(text: str, year: int):
    found = False
    for m in YEAR_RANGE.finditer(text):
        lo = int(m.group(1))
        hi = m.group(2)
        hi = int(hi) if len(hi) == 4 else int(str(lo)[:2] + hi)
        found = True
        if lo <= year <= hi:
            return True
    for m in YEAR_OLDER.finditer(text):
        found = True
        if year <= int(m.group(1)):
            return True
    if not found:
        ys = [int(y) for y in YEAR.findall(text)]
        if ys:
            return year in ys
    return False if found else None


class Answerer:
    def __init__(self, coordinator):
        self.co = coordinator
        self.store = coordinator.store
        self._rows = None                   # chunk ids of rate-table rows, listed on first use

    # ------------------------------------------------------------------------------------
    def answer(self, question: str, as_of=None) -> Answer:
        st = self.store
        if not st.snaps:
            return Answer(text=NO_ANSWER, confident=False)
        question = clean_question(question)
        as_of, bad_date = parse_as_of(as_of)
        if bad_date:
            return Answer(text=bad_date, confident=False)
        if not question:
            return Answer(text=VAGUE, confident=False)
        asked = as_of or month_end(question)
        si = st.snapshot_for(asked)
        if si == -1:
            first = st.snaps[0].date
            return Answer(text=f"I don't know. My earliest website copy is dated {first}. "
                               "I cannot confirm what was true before that date. Please contact us.",
                          confident=False)
        snap = st.snaps[si]
        q = Question(question)
        if q.lead and all(st.df(t) <= 400 for t in tokens(q.lead)):
            q.entities.append(q.lead)
        if not q.ctoks:
            return Answer(text=NO_ANSWER, confident=False)
        subject = [t for t in q.ctoks if len(t) > 1 and not t[0].isdigit() and t not in GENERIC_Q and t not in FILLER]
        if not subject:
            return Answer(text=VAGUE, confident=False)     # "rate?" or "' OR 1=1" names nothing to look up
        prefix = ""
        if asked and si != len(st.snaps) - 1:
            prefix = f"As of {snap.date} (latest website copy on or before {asked[:10]}): "
        exists = self.exists_answer(question, si, prefix)
        if exists is not None:
            return exists
        if q.change and not as_of:
            hist = self.history_answer(q, si)
            if hist is not None:
                return hist
        res = self.attempt(q, si, None, prefix)
        if res is None:
            # Nothing on the website at that date. A page that has since been removed can still be the only
            # source for a fact. Use it, and say clearly that it is old.
            removed = st.removed_pages(si)
            if removed:
                res = self.attempt(q, si, removed, prefix, strict=True)
        return res or Answer(text=NO_ANSWER, confident=False)

    def history_answer(self, q: Question, si: int):
        """Questions about change: answer the question in each snapshot and report where the answer moved."""
        st = self.store
        steps = []
        for j in range(si + 1):
            a = self.attempt(q, j, None, "")
            if a is None:
                continue
            body = re.sub(r"\[effective [^\]]*\]|[Nn]ote: this comes from[^\n]*", "", a.text)
            sig = frozenset(normval(x) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", body))
            if not steps or steps[-1][2] != sig:
                steps.append((st.snaps[j], a, sig))
        if not steps:
            return None
        if len(steps) == 1:
            snap, a, _ = steps[0]
            first = st.snaps[0].date
            a.text = (f"The snapshots from {first} to {st.snaps[si].date} show no change. Current value: "
                      + a.text)
            return a
        lines, srcs, seen = [], [], set()
        for k, (snap, a, _) in enumerate(steps):
            head = "\n".join(a.text.split("\n")[:2])
            label = "Now" if k == len(steps) - 1 else "Earlier"
            lines.append(f"{label}, as of {snap.date}: {head[:420]}")
            for s_ in a.sources:
                if s_.url not in seen:
                    seen.add(s_.url)
                    srcs.append(s_)
        note = "The value changed between snapshots: " + ", ".join(f"{s.date}" for s, _, _ in steps) + "."
        return Answer(text="\n".join(lines), sources=srcs[:6], confident=True, conflict_note=note)

    def exists_answer(self, question: str, si: int, prefix: str):
        """'Do any savings tiers pay 0.35% APY?': read every matching product table on the most authoritative
        rate page. Report the top tier of the first-listed (main) product and every tier that has the value.
        Returns None when no rate table matches, so the normal answer path runs."""
        m = EXISTS.match(question)
        if not m:
            return None
        target = float(m.group("value"))
        low = question.lower()
        measure = ("apy" if re.search(r"\bapy\b|\byield", low) else "apr" if re.search(r"\bapr\b", low)
                   else "dividend rate" if "dividend" in low else None)
        words = {t for t in tokens(m.group("subject")) if len(t) > 1 and not t[0].isdigit()
                 and t not in STOP and t not in EXISTS_SKIP}
        if not words:
            return None
        st = self.store
        vis = st.visible(si)
        business = "business" in low
        if self._rows is None:
            self._rows = [c.cid for c in st.chunks if c.kind == "row" and c.facts]
        tables = {}                                            # (page, product) -> [(order, value, fact, chunk)]
        for cid in self._rows:
            ch = st.chunks[cid]
            if ch.pk not in vis:
                continue
            for f in ch.facts:
                attr = (f.attribute or "").lower()
                if (attr != measure) if measure else attr not in ("apy", "apr", "dividend rate", "interest rate"):
                    continue
                etoks = set(tokens(f.entity or ""))
                if not words <= etoks or ("business" in etoks or "business" in ch.url) != business:
                    continue
                v = re.search(r"\d+(?:\.\d+)?", f.value or "")
                if v:
                    tables.setdefault((ch.pk, f.entity), []).append((ch.order, float(v.group()), f, ch))
        if not tables:
            return None
        pages = {}
        for (pk, ent), rows in tables.items():
            pages.setdefault(pk, []).append((min(r[0] for r in rows), ent, rows))
        pk = max(pages, key=lambda p: (pages[p][0][2][0][3].authority, len(pages[p])))
        listed = sorted(pages[pk], key=lambda t: t[0])            # the products in page order
        main, main_rows = listed[0][1], listed[0][2]
        unit = {"apy": " APY", "apr": " APR", "dividend rate": " dividend rate"}.get(measure, "")

        def tier(r):
            cond = "; ".join(str(x) for x in (r[2].conditions or {}).values())
            return f" ({cond})" if cond else ""

        hits = [(ent, r) for _, ent, rows in listed for r in rows if abs(r[1] - target) < 1e-9]
        top = max(main_rows, key=lambda r: r[1])
        asked = f"{m.group('value')}%{unit}"
        main_hit = next((r for ent, r in hits if ent == main), None)
        if main_hit is not None:
            lines = [f"Yes. {main}{tier(main_hit)} pays {main_hit[2].value}{unit}."]
            used = [main_hit]
        else:
            lines = [f"{main} tiers top out at {top[2].value}{unit}{tier(top)}."]
            used = [top]
            others = [(ent, r) for ent, r in hits if ent != main]
            if others:
                names = ", ".join(f"{ent}{tier(r)}" for ent, r in others[:3])
                lines.append(f"On this rate sheet, only {names} {'pays' if len(others) == 1 else 'pay'} {asked}.")
                used += [r for _, r in others[:3]]
            else:
                lines.append(f"No tier on this rate sheet pays {asked}.")
        lines += [CONTROL.sub(" ", r[3].text) for r in used]
        url = used[0][3].url
        srcs = [Source(url=url, snapshot_id=st.snaps[si].sid, quote=used[0][3].text[:240])]
        return Answer(text=prefix + "\n".join(lines), sources=srcs, confident=True, conflict_note=None)

    def attempt(self, q: Question, si: int, removed: dict | None, prefix: str, strict: bool = False):
        st = self.store
        snap = st.snaps[si]
        vis = frozenset(removed) if removed is not None else None
        ranked = self.rank(q, si, vis)
        if not ranked:
            return None
        chosen, notes = self.select(q, ranked, snap)
        link = self.pick_link(q, ranked, si) if q.url else None
        if not chosen or not self.supported(q, chosen, ranked, link, strict):
            return None
        lines, srcs = self.compose(q, chosen, ranked, snap, link)
        if removed is not None:
            seen = {st.snaps[removed[st.chunks[c].pk]].date for c in chosen if st.chunks[c].pk in removed}
            when = max(seen) if seen else "an earlier date"
            lines.insert(0, f"Note: this comes from a page that was last seen on {when} and is no longer on the "
                            "website, so it may be out of date.")
            notes.append(f"Source page(s) removed after snapshot dated {when}; used as historical evidence only.")
            for s_ in srcs:
                ch_ = next((st.chunks[c] for c in chosen if st.chunks[c].url == s_.url), None)
                if ch_ is not None and ch_.pk in removed:
                    s_.snapshot_id = st.snaps[removed[ch_.pk]].sid
        return Answer(text=prefix + "\n".join(lines), sources=srcs, confident=True,
                      conflict_note=" ".join(notes) or None)

    # ---- ranking ------------------------------------------------------------------------
    def rank(self, q: Question, si: int, vis=None):
        st = self.store
        snap = st.snaps[si]
        weights = {}
        for amount in q.amounts:
            weights[norm_num(str(int(amount)) if amount == int(amount) else str(amount))] = 1.5
        for n, u in q.terms:
            weights[n] = 1.4
        for t in q.soft:
            weights[t] = 0.35
        for t in q.ctoks:
            if t in FILLER and len(q.ctoks) > 1:
                weights[t] = 0.3        # "total number of ...", "top APY": shape words, not the subject
        extra = []
        for t, forms in q.var.items():
            for x in forms:
                if st.df(x) and x not in q.ctoks and x not in weights:
                    weights[x] = 0.5
                    extra.append(x)
        hits = st.search(q.ctoks + q.soft + extra, si, topn=150, weights=weights, vis=vis)
        nq = max(1, len(set(q.ctoks)))
        count_noun = None
        if q.count:
            m = re.search(r"(?:how many|number of)\s+(.{0,60})", q.low)
            after = [t for t in tokens(m.group(1)) if t not in STOP and t not in EXTRA_STOP] if m else []
            nouns = after[:2]                     # "branch locations" -> branch; "Dupaco branches" -> branch
            count_noun = nouns[0] if nouns else (q.ctoks[-1] if q.ctoks else None)
        ents = {t for e in q.entities for t in tokens(e)}
        out = []
        back = {x: t for t, forms in q.var.items() for x in forms}
        for cid, bm, matched in hits:
            ch = st.chunks[cid]
            direct = set(matched) & set(q.ctoks)
            via_form = {back[x] for x in matched if x in back} - direct       # matched only through another form
            matched = set(matched) | via_form
            cover = (len(direct) + 0.5 * len(via_form)) / nq
            s = bm * (0.35 + cover) * (0.55 + 0.6 * ch.authority)
            text = ch.text
            tl = text.lower()
            if q.rate and ch.kind == "row" and ch.facts:
                s *= 1.8
                attrs = {f.attribute for f in ch.facts}
                if q.apy and "APY" in attrs:
                    s *= 1.25
                elif q.apy and "APR" in attrs and "APY" not in attrs:
                    s *= 0.7
                if q.apr and "APR" in attrs:
                    s *= 1.25
                elif q.apr and "APY" in attrs and "APR" not in attrs:
                    s *= 0.7
            if q.rate and not q.fee and ch.kind == "row" and not ch.facts:
                s *= 0.8
            for amount in q.amounts:
                r = _in_ranges(text, amount)
                if r is True:
                    s *= 1.9
                elif r is False and ch.kind == "row":
                    s *= 0.45
            for y in q.years:
                r = _year_match(text, y)
                if r is True:
                    s *= 1.9
                elif r is False:
                    s *= 0.35
            for n, u in q.terms:
                pats = [rf"\b{n}[- ]*{u}s?\b"]
                if u == "year" and int(n) * 12 <= 120:
                    pats.append(rf"\b{int(n) * 12}[- ]*months?\b")
                if u == "month" and int(n) % 12 == 0 and int(n) > 0:
                    pats.append(rf"\b{int(n) // 12}[- ]*years?\b")
                if any(re.search(pt, tl) for pt in pats):
                    s *= 1.5
                elif ch.kind == "row" and re.search(r"\b\d+[- ]*(month|year)", tl):
                    s *= 0.45
            if ents:
                ctoks_chunk = set(tokens(ch.text + " " + ch.context))
                if not ents <= ctoks_chunk:
                    s *= 0.4
                if ents <= self.title_words(ch.pk):
                    s *= 1.4                # the page is about the thing the member named
            if (ch.stale_hint or (ch.expiry and ch.expiry < snap.date)) and not q.promo:
                s *= 0.45
            biz = "business" in tl or "/business" in ch.url or "business" in ch.context.lower()
            if biz and not q.business:
                s *= 0.55
            elif q.business and not biz:
                s *= 0.7
            if re.search(r"\bintro(?:ductory)?\b|promotion", tl) and not q.promo and "intro" not in q.low:
                s *= 0.6
            if q.hours:
                s *= 1.3 if TIME_RANGE.search(text) else 0.3
            if q.address and (ch.kind == "branch" or ADDR.search(text)):
                s *= 1.3
            if count_noun:
                about_us = ("dupaco" in tl or "dupaco" in ch.context.lower() or re.match(r"\s*(?:it|we) ha", tl)) \
                    and not NATIONAL.search(tl)
                if about_us and re.search(rf"(?<![\d,])(?!(?:19|20)\d\d\b)\d[\d,]*\+?\s+(?:[\w-]+\s+){{0,3}}"
                                          rf"{re.escape(count_noun)}", tl):
                    s *= 2.2
                elif not re.search(r"\d", tl) or NATIONAL.search(tl):
                    s *= 0.5
            if ch.pubdate:
                s *= 1 + 0.25 * recency(ch.pubdate)
            if ch.effective and ch.kind == "row":
                s *= 1 + 0.35 * eff_recency(ch.effective)
            out.append((s, cid, matched))
        out.sort(key=lambda x: (-round(x[0], 2), st.chunks[x[1]].order, x[1]))
        return out

    # ---- selection ----------------------------------------------------------------------
    def select(self, q: Question, ranked, snap):
        st = self.store
        notes: list[str] = []
        top = ranked[0][0]
        top_ch = st.chunks[ranked[0][1]]
        if top_ch.kind in ("row", "branch"):
            same = [(s, cid) for s, cid, _ in ranked if st.chunks[cid].pk == top_ch.pk
                    and st.chunks[cid].group == top_ch.group and st.chunks[cid].kind == top_ch.kind]
            constrained = bool(q.amounts or q.years or q.terms)
            broad = q.listall or (q.rate and not constrained and not q.apy and not q.apr)
            frac = 0.8 if constrained else (0.3 if broad else 0.62)
            picked = [cid for s, cid in same if s >= top * frac][: (10 if broad else 4)]
            picked.sort(key=lambda c: st.chunks[c].order)
            if (broad and not constrained) or top_ch.kind == "branch":
                others = [cid for s, cid, _ in ranked if st.chunks[cid].kind == top_ch.kind
                          and cid not in picked and s >= top * 0.5 and st.chunks[cid].group != top_ch.group
                          and (q.business or q.listall or "business" not in st.chunks[cid].text.lower())]
                picked += others[:6]
            if q.modifiers and top_ch.kind == "row":
                # "Bright Savings account": keep only rows of the named product
                named = [c for c in picked if all(m in self.chunk_words(c) or
                                                   any(x in self.chunk_words(c) for x in SOFT.get(m, ())) for m in q.modifiers)]
                picked = named or picked
            if top_ch.kind == "branch" and not q.listall:
                picked = picked[:2]
            picked = self.drop_overridden_rows(picked, ranked, snap, notes)
            picked = self.refresh_rows(picked, ranked, notes)
            picked = self.hard_filter(q, picked)
            return picked, notes

        # text answers: pick the best page, then its best chunks in page order
        pages: dict[int, list] = {}
        for s, cid, _ in ranked[:80]:
            pages.setdefault(st.chunks[cid].pk, []).append((s, cid))
        pscore = {pk: lst[0][0] + 0.25 * sum(x[0] for x in lst[1:3]) for pk, lst in pages.items()}
        best_pk = max(pscore, key=lambda k: (round(pscore[k], 2), -k))
        # Among dated articles that fit about equally well, the newest release wins (it states the latest count,
        # award or date). Undated pages keep the score order.
        best_ch = st.chunks[pages[best_pk][0][1]]
        if best_ch.pubdate:
            close = [pk for pk in pscore if pscore[pk] >= 0.8 * pscore[best_pk]
                     and st.chunks[pages[pk][0][1]].pubdate and st.chunks[pages[pk][0][1]].page_type == best_ch.page_type]
            best_pk = max(close, key=lambda k: (st.chunks[pages[k][0][1]].pubdate, round(pscore[k], 2), -k))
        lst = pages[best_pk]
        ptop = lst[0][0]
        picked, heads = [], set()
        for s, cid in lst:
            if s < 0.5 * ptop or len(picked) >= 4:
                break
            head = st.chunks[cid].text[:30]
            if head in heads:
                continue
            heads.add(head)
            picked.append(cid)

        top_page = st.chunks[lst[0][1]]
        if top_page.page_type in ("news", "blog") and not q.count and not q.hours:
            # an article opens with a summary paragraph; keep it with the matching passages
            lede = next((c for c in (st.chunks[i] for i in st.pages[best_pk].cids)
                         if c.kind == "para" and len(c.text) > 80), None)
            if lede is not None and lede.cid not in picked and \
                    len(set(tokens(lede.text)) & {t for t in q.ctoks if not t.isdigit()}) >= 1:
                picked.insert(0, lede.cid)
        if q.count:
            picked = [ranked[0][1]]
            best_pk = st.chunks[picked[0]].pk
            picked, notes = self.override_by_conflicts(picked, ranked, snap, notes, wide=True)
            keys = {f.key() for c in picked for f in st.chunks[c].facts}
            for s, cid, _ in ranked[:60]:               # other figures that are not the same fact (other years)
                och = st.chunks[cid]
                if cid in picked or s < 0.7 * ranked[0][0] or len(picked) >= 3:
                    continue
                same_title = st.pages[och.pk].title == st.pages[st.chunks[picked[0]].pk].title
                if (och.facts and all(f.key() not in keys for f in och.facts if f.status == "verified")) or \
                        (not och.facts and same_title and re.search(r"\d", och.text)):
                    picked.append(cid)          # e.g. the 2023 and the 2024 figure from the same article
                    keys |= {f.key() for f in och.facts}
        else:
            picked, notes = self.override_by_conflicts(picked, ranked, snap, notes)
        anchor = st.chunks[picked[0]]
        if q.hours:
            pass
        if q.hours or (False and re.match(r"^[\w\- ,–]+:\s", anchor.text)):
            # schedule lines are short list items under one heading: keep them together
            sibs = [c for c in (st.chunks[i] for i in st.pages[anchor.pk].cids)
                    if c.context == anchor.context and c.kind in ("list", "para") and len(c.text) < 160]
            if len(sibs) > 1:
                picked = [c.cid for c in sibs][:8]
            if q.hours:
                picked += self.other_schedules(q, ranked, anchor, picked)
        picked.sort(key=lambda c: (st.chunks[c].pk != best_pk, st.chunks[c].pk, st.chunks[c].order))
        return picked, notes

    def other_schedules(self, q, ranked, anchor, picked):
        """Branches can keep different hours. Add one more page whose schedule differs."""
        st = self.store
        have = {tuple(TIME_RANGE.findall(st.chunks[c].text)) for c in picked}
        extra, pages_done = [], {anchor.pk}
        for s, cid, _ in ranked[:150]:
            ch = st.chunks[cid]
            if ch.pk in pages_done or not TIME_RANGE.search(ch.text):
                continue
            sig = tuple(TIME_RANGE.findall(ch.text))
            if sig in have:
                continue
            have.add(sig)
            pages_done.add(ch.pk)
            sibs = [c.cid for c in (st.chunks[i] for i in st.pages[ch.pk].cids)
                    if c.kind in ("list", "para") and len(c.text) < 120
                    and (TIME_RANGE.search(c.text) or re.search(r"closed|atm only", c.text, re.I))][:14]
            extra += sibs or [cid]
            if len(pages_done) >= (7 if re.search(r"\b(all|same|differ|each|every|vary|varies)\b", q.low) else 3):
                break
        return extra

    def hard_filter(self, q: Question, picked):
        """Rows must satisfy the vehicle year, loan amount and term the member asked for."""
        st = self.store
        out = []
        for cid in picked:
            ch = st.chunks[cid]
            tl = ch.text.lower()
            ok = True
            if ch.kind == "row":
                for y in q.years:
                    if _year_match(ch.text, y) is False:
                        ok = False
                for amount in q.amounts:
                    if _in_ranges(ch.text, amount) is False:
                        ok = False
                for n, u in q.terms:
                    pats = [rf"\b{n}[- ]*{u}s?\b"]
                    if u == "year" and int(n) * 12 <= 120:
                        pats.append(rf"\b{int(n) * 12}[- ]*months?\b")
                    if u == "month" and int(n) % 12 == 0 and int(n) > 0:
                        pats.append(rf"\b{int(n) // 12}[- ]*years?\b")
                    if re.search(r"\b\d+[- ]*(?:month|year)", tl) and not any(re.search(pt, tl) for pt in pats):
                        ok = False
            if ok:
                out.append(cid)
        return out

    def refresh_rows(self, picked, ranked, notes):
        """A row on a page can repeat an older copy of a rate table. When another page shows the same row
        (same wording, other numbers) with a clearly newer effective date, use the newer row."""
        st = self.store

        def words(ch):
            return {t for t in tokens(ch.text) if not t[0].isdigit() and t not in ROW_NOISE and t not in STOP}

        pool = [(st.chunks[oid], words(st.chunks[oid])) for _, oid, _ in ranked[:150]
                if st.chunks[oid].kind == "row" and st.chunks[oid].effective]
        out = []
        for cid in picked:
            ch = st.chunks[cid]
            best = ch
            if ch.kind == "row" and ch.effective:
                base = words(ch)
                for och, ow in pool:
                    if och.effective <= best.effective or (och.pk, och.group) == (ch.pk, ch.group):
                        continue
                    if (date.fromisoformat(och.effective) - date.fromisoformat(ch.effective)).days < 14:
                        continue
                    if och.authority < ch.authority - 0.25 or not base or not ow:
                        continue
                    if (base ^ ow) & DISTINCT:
                        continue            # personal vs business, Roth vs traditional, jumbo vs standard ...
                    if len(base & ow) / len(base | ow) >= 0.6:
                        best = och
            if best is not ch:
                notes.append(f"A copy of this row on {ch.url} (effective {ch.effective}) is older than the row on "
                             f"{best.url} (effective {best.effective}); the newer row is used.")
            if best.cid not in out:
                out.append(best.cid)
        return out

    def drop_overridden_rows(self, picked, ranked, snap, notes):
        """Remove table rows that a stronger or newer row (same entity, attribute, conditions) contradicts.
        A table with one overridden row is stale as a whole, so its other rows go too."""
        st = self.store
        stale_groups = set()
        for cid in picked:
            ch = st.chunks[cid]
            for f in ch.facts:
                if f.status != "verified":
                    continue
                for s, oid, _ in ranked[:150]:
                    och = st.chunks[oid]
                    if och.pk == ch.pk and och.group == ch.group:
                        continue
                    for of in och.facts:
                        if of.status == "verified" and of.key() == f.key() and normval(of.value) != normval(f.value):
                            win = self.co.history.winner([(f, ch), (of, och)], snap.date)
                            if win and win[1] is och:
                                stale_groups.add((ch.pk, ch.group))
                                notes.append(f"{f.entity} {f.attribute}: {f.value} on {ch.url} is overridden by "
                                             f"{of.value} on {och.url} ({win[2]}).")
        if not stale_groups:
            return picked
        kept = [c for c in picked if (st.chunks[c].pk, st.chunks[c].group) not in stale_groups]
        if kept:
            return kept
        # every picked row is stale: use the overriding table instead
        for s, oid, _ in ranked:
            och = st.chunks[oid]
            if och.kind == "row" and (och.pk, och.group) not in stale_groups:
                same = [c for s2, c, _ in ranked if st.chunks[c].pk == och.pk and st.chunks[c].group == och.group]
                return sorted(same, key=lambda c: st.chunks[c].order)[:6]
        return picked

    def override_by_conflicts(self, picked, ranked, snap, notes, wide=False):
        """If a stronger or newer source gives a different value for the same fact key, use that source."""
        st = self.store
        out = list(picked)
        top = ranked[0][0]
        lim = 0.0 if wide else 0.6          # wide: any source with the same fact key may compete
        pool = [(s, oid) for s, oid, _ in ranked[:150 if wide else 60] if s >= top * lim]
        for cid in list(out):
            ch = st.chunks[cid]
            for f in ch.facts:
                if f.status != "verified":
                    continue
                # all sources that state the same fact, judged together (never two at a time)
                rivals = [(of, st.chunks[oid]) for _, oid in pool for of in st.chunks[oid].facts
                          if oid != cid and of.status == "verified" and of.key() == f.key()]
                if not any(normval(of.value) != normval(f.value) for of, _ in rivals):
                    continue
                win = self.co.history.winner([(f, ch)] + rivals, snap.date)
                if win and win[1] is not ch and normval(win[0].value) != normval(f.value) and cid in out:
                    out[out.index(cid)] = win[1].cid
                    notes.append(f"{f.entity} {f.attribute}: {f.value} ({ch.url}) is overridden by "
                                 f"{win[0].value} ({win[1].url}); {win[2]}.")
                    break
        return list(dict.fromkeys(out)), notes

    def chunk_words(self, cid: int) -> set:
        ch = self.store.chunks[cid]
        return set(tokens(ch.text + " " + ch.context))

    def title_words(self, pk: int) -> set:
        cache = self.__dict__.setdefault("_title_words", {})
        if pk not in cache:
            cache[pk] = set(tokens(self.store.pages[pk].title))
        return cache[pk]

    def page_words(self, pk: int) -> set:
        """Words of a page's title and opening text: what the page is about."""
        cache = self.__dict__.setdefault("_page_words", {})
        if pk not in cache:
            pr = self.store.pages[pk]
            head = " ".join(self.store.chunks[c].text for c in pr.cids[:3])
            cache[pk] = set(tokens(pr.title + " " + pr.url.rsplit("/", 1)[-1].replace("-", " ") + " " + head))
        return cache[pk]

    def supported(self, q: Question, chosen, ranked, link, strict: bool = False) -> bool:
        st = self.store
        chunks = [st.chunks[c] for c in chosen]
        btoks = set()
        for ch in chunks:
            btoks |= set(tokens(ch.text + " " + ch.context + " " + ch.title))
        qt = set(q.ctoks)
        if q.terms:                     # "5 year" is satisfied by a "60 month" row: the hard filter checks terms
            qt -= {n for n, u in q.terms} | {u for n, u in q.terms}
        if q.rate:
            qt -= {"pay", "earn"}
        qt = (qt - FILLER) or qt or set(q.ctoks)
        top_idf = max((st.idf(t) for t in qt if st.df(t)), default=1.0)

        def w(t):                       # a word the site never uses counts, but less than a rare word
            return st.idf(t) if st.df(t) else 0.5 * top_idf

        def has(t, toks):               # a word counts as present when a synonym or another form is present
            return t in toks or any(x in toks for x in SOFT.get(t, ())) or any(x in toks for x in SYN.get(t, ())) \
                or any(x in toks for x in variants(t))

        wsum = sum(w(t) for t in qt) or 1.0
        cover = sum(w(t) for t in qt if has(t, btoks)) / wsum
        if cover < (0.62 if strict else 0.45) and not link:
            return False
        for e in q.entities:
            et = set(tokens(e))
            if et and not et <= btoks:
                return False
        if q.rate and not q.fee and not q.url:
            ok = False
            for ch in chunks:
                if RATE_PCT.search(ch.text):          # a rate statement, not any percent ("3% down payment")
                    ct = set(tokens(ch.text + " " + ch.context))
                    if all(t in ct for t in q.specifiers) and all(has(m, ct) for m in q.modifiers) and \
                            sum(w(t) for t in qt if has(t, ct)) / wsum >= 0.5:
                        ok = True
            if not ok:
                return False
        for n, u in q.terms:            # the asked term (60 month = 5 year) must appear in the evidence
            pats = [rf"\b{n}[- ]*{u}s?\b"]
            if u == "year" and int(n) * 12 <= 120:
                pats.append(rf"\b{int(n) * 12}[- ]*months?\b")
            if u == "month" and int(n) % 12 == 0 and int(n) > 0:
                pats.append(rf"\b{int(n) // 12}[- ]*years?\b")
            if not any(re.search(pt, ch.text.lower()) for ch in chunks for pt in pats):
                return False
        if q.hours:
            # real schedule lines ("Saturday: 8 a.m.-1 p.m."), not a time mentioned in a story
            sched = [ch for ch in chunks if SCHEDULE.search(ch.text) or
                     (TIME_RANGE.search(ch.text) and re.search(r"lobby|drive|hours", ch.context, re.I))]
            if not sched:
                return False
            ents = {t for e in q.entities for t in tokens(e)}
            if ents and not any(ents <= self.page_words(ch.pk) | set(tokens(ch.context)) for ch in sched):
                return False            # the named branch must be the page the schedule belongs to
        if q.fee and not q.url and not any("$" in ch.text for ch in chunks):
            return False
        if q.phone and not any(PHONE.search(ch.text) for ch in chunks):
            return False
        return True

    # ---- composition ----------------------------------------------------------------------
    def compose(self, q: Question, chosen, ranked, snap, link):
        st = self.store
        chunks = [st.chunks[c] for c in chosen]
        lines, srcs, seen = [], [], set()
        for ch in chunks:
            txt = CONTROL.sub(" ", ch.text)
            txt = txt if len(txt) <= 520 else txt[:520].rsplit(" ", 1)[0] + "..."
            cap = ch.context.split(" > ")[-1].strip() if ch.kind in ("para", "list") else ""
            if cap and re.search(r"[A-Za-z]{3}", cap) and cap.lower() not in txt.lower() and len(cap) < 60:
                txt = f"{cap}: {txt}"
            if txt not in lines:
                lines.append(txt)
            if ch.url not in seen:
                seen.add(ch.url)
                srcs.append(Source(url=ch.url, snapshot_id=snap.sid, quote=ch.text[:240]))
        if any(AREA_REF.search(x) for x in lines) and not any(STATE_RE.search(x) for x in lines):
            place = self.area_sentence(chunks)
            if place and place not in lines:
                lines.append(place)
        for ch in self.same_statement(chunks, snap, seen):
            if len(srcs) >= 8:
                break
            seen.add(ch.url)
            srcs.append(Source(url=ch.url, snapshot_id=snap.sid, quote=ch.text[:240]))
        if link:
            url, lch = link
            if lch.pk not in {ch.pk for ch in chunks}:
                # the link sits on another page: answer from the text around the link, not from unrelated text
                lines = [lch.text[:300]]
                srcs = [Source(url=lch.url, snapshot_id=snap.sid, quote=lch.text[:240])]
                seen = {lch.url}
            lines = lines[:2] + [f"Link: {url}"]
            target = url.split("#")[0].rstrip("/")
            if target and target not in seen:
                seen.add(target)
                srcs.append(Source(url=url, snapshot_id=snap.sid, quote=None))
        # the same statement on other pages is more evidence for it
        want_text = {ch.text for ch in chunks}
        want_facts = {(f.key(), normval(f.value)) for ch in chunks for f in ch.facts}
        same = []
        for s, cid, _ in ranked[:100]:
            ch = st.chunks[cid]
            if ch.url in seen:
                continue
            if ch.text in want_text or any((f.key(), normval(f.value)) in want_facts for f in ch.facts):
                same.append(ch)
        same.sort(key=lambda c: -c.authority)
        for ch in same:
            if len(srcs) >= 6 or ch.url in seen:
                continue
            seen.add(ch.url)
            srcs.append(Source(url=ch.url, snapshot_id=snap.sid, quote=ch.text[:240]))
        for s, cid, _ in ranked[:8]:
            ch = st.chunks[cid]
            if len(srcs) >= 4:
                break
            if ch.url not in seen and s >= ranked[0][0] * 0.7 and ch.page_type in ("rates", "product", "contact"):
                seen.add(ch.url)
                srcs.append(Source(url=ch.url, snapshot_id=snap.sid, quote=ch.text[:240]))
        return lines, srcs

    def area_sentence(self, chunks) -> str | None:
        """The first sentence on the same page that names a state ("You can find us in Iowa, Illinois and
        Wisconsin."), for an answer that refers to an area without naming it."""
        st = self.store
        best = None
        for pk in dict.fromkeys(ch.pk for ch in chunks):
            for cid in st.pages[pk].cids:
                for sent in re.split(r"(?<=[.!?])\s+", MD_LINK.sub(r"\1", st.chunks[cid].text)):
                    n = len(set(STATE_RE.findall(sent)))
                    if n and len(sent) <= 220 and (best is None or n > best[0]):
                        best = (n, sent.strip())
            if best:
                return best[1]
        return None

    @staticmethod
    def _span_key(m) -> str:
        t = [re.sub(r"[\s.]", "", x.lower()) for x in (m.group(3), m.group(4))]
        return f"{m.group(1).lower()}-{m.group(2).lower()} {t[0]}-{t[1]}"

    def same_statement(self, chunks, snap, seen) -> list:
        """Pages of this snapshot that state the same routing or phone number, or the same days-and-hours span,
        in other words. Numbers are found through the index; spans only on contact, home and info pages."""
        st = self.store
        text = " ".join(ch.text for ch in chunks)
        ids = set(ID_NUM.findall(text)) | {re.sub(r"\D", "", p) for p in PHONE.findall(text)}
        spans = {self._span_key(m) for m in DAY_SPAN.finditer(text)}
        if not ids and not spans:
            return []
        si = next(i for i, s in enumerate(st.snaps) if s is snap)
        vis = st.visible(si)
        found = {}
        for num in ids:
            term = num[-4:] if len(num) == 10 else norm_num(num)    # a phone is indexed as three numbers
            for cid, _ in st.post.get(term, []):
                ch = st.chunks[cid]
                if ch.pk in vis and ch.url not in seen and num in re.sub(r"\D", "", ch.text):
                    found.setdefault(ch.url, ch)
        if spans:
            for pk in vis:
                cids = st.pages[pk].cids if pk in st.pages else []
                if not cids or st.chunks[cids[0]].page_type not in ("contact", "home", "info"):
                    continue
                for cid in cids:
                    ch = st.chunks[cid]
                    if ch.url not in seen and any(self._span_key(m) in spans for m in DAY_SPAN.finditer(ch.text)):
                        found.setdefault(ch.url, ch)
        return sorted(found.values(), key=lambda c: -c.authority)

    def pick_link(self, q: Question, ranked, si: int):
        """Best link near the evidence: links on the top pages, scored by anchor, address and nearby words.
        Pages whose own address names the topic (for example /about/join) are searched too."""
        st = self.store
        best, bs = None, 0.0
        qt = set(q.ctoks) | set(q.soft)
        pages, order = {}, []
        for s, cid, _ in ranked[:40]:
            pk = st.chunks[cid].pk
            if pk not in pages:
                pages[pk] = s
                order.append(pk)
        order = order[:4]
        by_path = []
        own = set(q.ctoks)
        for url, pk in st.snaps[si].pk_by_url.items():
            path = re.sub(r"^https?://[^/]+", "", url).split("?")[0]
            pt = set(tokens(path.replace("-", " ").replace("/", " ")))
            hit = 2 * len(own & pt) + len((qt - own) & pt)       # the member's own words count double
            if hit and path.count("/") <= 3 and "/newsroom" not in path and "/blog" not in path:
                by_path.append((-hit, len(path), pk))
        for _, _, pk in sorted(by_path)[:5]:
            if pk not in order:
                pages.setdefault(pk, ranked[0][0] * 0.5)
                order.append(pk)
        for pk in order:
            for cid in st.pages[pk].cids:
                ch = st.chunks[cid]
                if not ch.links:
                    continue
                ctx_t = set(tokens(ch.text + " " + ch.context))
                for anchor, url in ch.links:
                    if url.startswith(("tel:", "mailto:")) or "hs-fs" in url or url.endswith((".png", ".jpg", ".svg")):
                        continue
                    lt = set(tokens(anchor + " " + re.sub(r"https?://[^/]+", "", url)))
                    ov = len(qt & lt) + 1.0 * len(qt & ctx_t) / max(1, len(qt))
                    host = re.sub(r"^https?://", "", url).split("/")[0]
                    a_low = anchor.lower().strip()
                    if host.startswith("info."):
                        ov *= 0.2       # the old info.* site holds retired pages
                    if re.match(r"(?:open|apply|start|continue|get started|enroll|sign up)\b", a_low) \
                            and not q.applying:
                        ov *= 0.3       # an application link is not the page that shows the facts
                    if q.rate and re.search(r"see details|compare|\brates?\b|learn more|view", a_low):
                        ov += 0.8
                    if q.applying and (host.startswith(("join.", "apply.")) or "/apply" in url):
                        ov += 2         # the online application itself
                    if re.search(r"join|membership|become a member", q.low) and host.startswith("join."):
                        ov += 2
                    sc = (ov + 0.3 * ch.authority) * (1 + 0.004 * pages[pk])
                    if ov >= 1 and sc > bs:
                        best, bs = (url, ch), sc
        return best
