"""Generate reproducible SAMPLE data for the README screenshots — a scored board,
one fully pre-generated application package, and a sample learning log. Uses the
demo profile/CV shipped with the repo; no real candidate data. Run from repo root."""
import sys
from engine import store, draft as D, fitscore
from engine.models import Job, Draft, Analysis, JDDoc, Requirement, FactorScore

TODAY = "2026-06-19"
profile = store.load_profile()
base_cv = store.read_base_cv()
base_cl = store.read_base_cl()


def fs(key, label, weight, points, detail):
    return FactorScore(key=key, label=label, weight=weight, points=points, detail=detail)


# ---- 1) the showcase job: a full, authentic application package ----------------
SHOW = dict(
    id="pkg0a1b2c", role="Principal Product Manager", company="Vellum AI",
    mode="remote", location="Remote (UK / EU)", salary="£110k–£140k",
    url="https://example.com/vellum/principal-pm",
    source="Greenhouse", posted=TODAY,
)
SHOW_JD = """Principal Product Manager — Vellum AI (Remote, UK/EU)

Vellum is the platform teams use to build, evaluate and ship LLM-powered products.
We're post-Series A and growing fast. We're looking for a Principal PM to own our
evaluation & observability surface 0 to 1 — the tools customers use to measure and
trust their AI features in production.

What you'll do
- Own the eval/observability product area end to end: discovery, strategy, roadmap.
- Run structured discovery with technical customers (AI engineers, ML leads).
- Work hands-on with engineering on agentic pipelines and prompt workflows.
- Define success metrics and ship iteratively; bring product taste grounded in data.

What we're looking for
- 6+ years in product, including taking a technical product from 0 to 1.
- Strong grasp of LLMs, evals, RAG and agentic systems.
- Comfortable being hands-on; some coding (TypeScript/Python) is a plus.
- Experience with developer-facing or B2B SaaS products.
- Excellent written communication.
"""
SHOW_QS = [
    {"text": "Why do you want to work at Vellum?", "type": "text", "options": []},
    {"text": "Describe a technical product you took from 0 to 1. What was your role?",
     "type": "text", "options": []},
    {"text": "What's your hands-on experience with LLM evaluation or observability?",
     "type": "text", "options": []},
    {"text": "What are your salary expectations?", "type": "text", "options": []},
]


def build_showcase():
    print("research…", flush=True)
    unmet = ["Some coding (TypeScript/Python)"]
    ctx = D.research_application_context(
        {"role": SHOW["role"], "company": SHOW["company"], "mode": SHOW["mode"],
         "location": SHOW["location"], "description": SHOW_JD},
        profile, base_cv, unmet, app_questions=[q["text"] for q in SHOW_QS])
    print("draft…", flush=True)
    drv = D.draft_documents(
        {"role": SHOW["role"], "company": SHOW["company"], "mode": SHOW["mode"],
         "description": SHOW_JD},
        base_cv, base_cl,
        app_ctx={"opener": "standard", "angle": ctx.get("angle", ""),
                 "why_excited": ctx.get("why_excited", ""), "gap": ctx.get("gap", ""),
                 "cultural_fit": ctx.get("cultural_fit", ""), "emphasis": ctx.get("emphasis", "")},
        questions=SHOW_QS,
        role_fit={"matched": ["0 to 1", "evals", "agentic", "product strategy"], "unmet": unmet})
    drv.ctx = {"opener": "standard", "angle": ctx.get("angle", ""),
               "why_excited": ctx.get("why_excited", ""), "gap": ctx.get("gap", ""),
               "cultural_fit": ctx.get("cultural_fit", ""), "emphasis": ctx.get("emphasis", ""),
               "hooks": ctx.get("hooks", [])}
    drv.cv_used = {"id": "", "name": "Matching CV"}
    print("analyze…", flush=True)
    an = fitscore.analyze({"role": SHOW["role"], "company": SHOW["company"],
                           "location": SHOW["location"], "mode": SHOW["mode"],
                           "description": SHOW_JD}, profile, base_cv, score=89)
    print("classify reqs…", flush=True)
    rq = fitscore.classify_requirements(SHOW_JD, profile, base_cv)
    reqs = [Requirement(**r) for r in rq["requirements"]]
    job = Job(
        id=SHOW["id"], date=TODAY, role=SHOW["role"], company=SHOW["company"],
        url=SHOW["url"], mode=SHOW["mode"], location=SHOW["location"], salary=SHOW["salary"],
        posted=SHOW["posted"], description=SHOW_JD, status="review", score=89, weight_score=84,
        reason="Exceptional 0→1 + evals/agentic fit; developer-facing SaaS match.",
        factors=[fs("skills", "Skills & quals", 50, 46, "strong: 0→1, evals, agentic, discovery"),
                 fs("domain", "Domain match", 30, 27, "AI / LLM tooling, B2B SaaS"),
                 fs("stage", "Stage / operating style", 20, 18, "post-Series A, 0→1 mandate")],
        drivers=["take a technical product 0 to 1", "evals, RAG and agentic systems",
                 "developer-facing or B2B SaaS"],
        unmet=["Some coding (TS/Python)"], flags=["zero_to_one"], source=SHOW["source"],
        analysis=Analysis(**an), jd=JDDoc(text=SHOW_JD, url=SHOW["url"], fetched_at=TODAY,
                                          requirements=reqs, error=rq.get("error", "")),
        questions=SHOW_QS, draft=drv)
    store.save_job(job)
    print("showcase saved:", job.id)


# ---- 2) the rest of the board (no draft; plausible scored rows) -----------------
BOARD = [
    dict(id="b1d4e7a0", role="Senior Product Manager, AI", company="Brightwave",
         mode="remote", location="Remote (EU)", score=86, ws=80, flags=[],
         reason="Strong AI-platform + 0→1 fit; B2B SaaS domain match.",
         drivers=["own the AI roadmap 0 to 1", "B2B SaaS platform"], unmet=["10+ yrs people mgmt"],
         f=[(50,45,"0→1, platform, discovery"),(30,26,"AI / B2B SaaS"),(20,9,"scale-up, not early")],
         source="Lever", salary="€95k–€120k"),
    dict(id="c2e5f8b1", role="Founding Product Manager", company="Loop Labs",
         mode="hybrid", location="London, UK (hybrid)", score=83, ws=78, flags=["founder_welcome","zero_to_one"],
         reason="Founding mandate + 0→1 builder match; early-stage operating style.",
         drivers=["first product hire", "0 to 1 ownership"], unmet=["fintech domain"],
         f=[(50,42,"0→1, founding, hands-on"),(30,21,"fintech adjacent"),(20,20,"pre-seed, founding")],
         source="Ashby", salary="£90k–£115k + equity"),
    dict(id="d3f6a9c2", role="Group Product Manager", company="Northwind",
         mode="remote", location="Remote (UK/EU)", score=78, ws=72, flags=[],
         reason="Good platform/PM fit; role leans more execution than 0→1.",
         drivers=["lead a product group", "platform roadmap"], unmet=["0→1 emphasis low"],
         f=[(50,38,"platform, roadmap"),(30,24,"B2B SaaS"),(20,10,"growth stage")],
         source="Greenhouse", salary="£105k–£130k"),
    dict(id="e4a7b0d3", role="Principal PM, Voice AI", company="Sonari",
         mode="remote", location="Remote (Global)", score=80, ws=74, flags=["voice_ai","zero_to_one"],
         reason="Voice-AI + 0→1 builder match; conversational product depth.",
         drivers=["voice AI agents 0 to 1", "conversational product"], unmet=["telephony infra"],
         f=[(50,40,"voice AI, 0→1"),(30,24,"AI / agentic"),(20,10,"Series A")],
         source="Lever", salary="$140k–$170k"),
    dict(id="f5b8c1e4", role="Lead Product Manager", company="Cadence Health",
         mode="hybrid", location="London, UK (hybrid)", score=72, ws=66, flags=[],
         reason="Decent SaaS PM fit; healthtech domain is a stretch.",
         drivers=["own product strategy", "B2B SaaS"], unmet=["healthtech domain","regulated"],
         f=[(50,36,"strategy, discovery"),(30,15,"healthtech stretch"),(20,15,"scale-up")],
         source="Workable", salary="£95k–£120k"),
    dict(id="a6c9d2f5", role="Senior PM, Platform", company="Gridstone",
         mode="remote", location="Remote (EU)", score=69, ws=63, flags=[],
         reason="Platform PM fit; infra-heavy role underuses 0→1 strength.",
         drivers=["platform & APIs", "developer experience"], unmet=["deep infra background"],
         f=[(50,33,"platform, DX"),(30,21,"dev tools / SaaS"),(20,9,"growth stage")],
         source="Greenhouse", salary="€90k–€110k"),
]


def build_board():
    for b in BOARD:
        job = Job(id=b["id"], date=TODAY, role=b["role"], company=b["company"],
                  url=f"https://example.com/{b['id']}", mode=b["mode"], location=b["location"],
                  salary=b.get("salary", ""), posted=TODAY, description=f"{b['role']} at {b['company']}.",
                  status="new", score=b["score"], weight_score=b["ws"], reason=b["reason"],
                  factors=[fs("skills", "Skills & quals", b["f"][0][0], b["f"][0][1], b["f"][0][2]),
                           fs("domain", "Domain match", b["f"][1][0], b["f"][1][1], b["f"][1][2]),
                           fs("stage", "Stage / operating style", b["f"][2][0], b["f"][2][1], b["f"][2][2])],
                  drivers=b["drivers"], unmet=b["unmet"], flags=b["flags"], source=b["source"])
        store.save_job(job)
    print(f"board saved: {len(BOARD)} jobs")


# ---- 3) sample learning log (so the learning-recap + panel render) --------------
def build_learning():
    store.append_style_entry(
        SHOW["role"], SHOW["company"],
        base="Led products from zero to revenue across AI, PropTech and B2B SaaS.",
        suggested="Spearheaded transformative product initiatives that drove exceptional growth.",
        actual="Took two AI products from zero to first revenue; one now at $1.2M ARR.",
        reason="Cut the buzzwords. Use concrete, verifiable outcomes and real numbers, not adjectives.")
    store.append_style_entry(
        SHOW["role"], SHOW["company"],
        base="I am passionate about leveraging cutting-edge AI to deliver impactful solutions.",
        suggested="I'm excited to leverage my expertise to drive impact at Vellum.",
        actual="I've shipped eval tooling myself, so I know where teams lose trust in their AI.",
        reason="No 'leverage'/'excited to'. Open with a specific, earned point of view instead.")
    # negative + positive role anchors (for the Skip + Train panel)
    store.append_skip("Product Manager", "AdTech Co",
                      "Adtech domain I don't want; role is execution-only, no 0→1 scope.")
    store.append_like("Founding Product Manager", "Loop Labs",
                      "Early-stage founding mandate with a real 0→1 build — more of these.")
    print("learning log written")


if __name__ == "__main__":
    build_board()
    build_learning()
    build_showcase()
    print("DONE")
