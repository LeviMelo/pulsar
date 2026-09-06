"""Message rendering: campaign data → Jinja2 → HTML theme + plaintext fallback.

Email clients are not browsers. The theme is table-free where possible, inlines
its CSS, uses no JavaScript, no external assets and no media queries that matter
for legibility. Every HTML message ships with a real plaintext alternative that
says the same thing, not a "view this in your browser" stub.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Mapping

from jinja2 import Environment, StrictUndefined, Undefined

from ..semantics.normalize import display_person_name
from .caixa import desgritar
from .panels import decimal
from .saudacao import saudacao

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


def _milhar(value) -> str:
    """15019 -> '15.019'. Brazilian thousands separator, for a Portuguese email."""
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(value)


_EXTENSO = ["zero", "um", "dois", "tres", "quatro", "cinco",
            "seis", "sete", "oito", "nove", "dez"]
_EXTENSO[3] = "tr" + chr(234) + "s"


def _extenso(value) -> str:
    """4 -> 'quatro'. A small count reads as a word in a sentence, not a digit."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value)
    return _EXTENSO[number] if 0 <= number < len(_EXTENSO) else str(number)


# SIGAA stores edital names title-cased, which turns every acronym in them into
# a word: "Edital 03 Pibiti Ufal 2026-2027". A professor reading that in the
# first line of a cold email reads carelessness, so the known acronyms are put
# back. Only exact whole-word matches are touched.
_SIGLAS = ("PIBIC", "PIBITI", "PIBIC-AF", "UFAL", "SUS", "IBGE", "CNPQ", "FAPEAL",
           "PROPEP", "IC", "SIGAA")


def _siglas(value) -> str:
    text = str(value)
    for sigla in _SIGLAS:
        text = re.sub(rf"\b{re.escape(sigla)}\b", sigla, text, flags=re.IGNORECASE)
    # A handful of terms are neither plain words nor plain acronyms; their
    # canonical casing is part of the name, and they land in the second line of
    # a message to the professor who studies them.
    for wrong, right in (("CNPQ", "CNPq"), ("MICRORNAS", "microRNAs"),
                         ("MICRORNA", "microRNA"), ("micrornas", "microRNAs"),
                         ("microrna", "microRNA")):
        text = text.replace(wrong, right)
    return text


def _limpo(value) -> str:
    """Collapse the whitespace SIGAA left inside a scraped title.

    Ten of the 187 plan and project titles carry a hard line break from the page
    they were lifted off, which renders mid-sentence in the middle of a
    paragraph and reads as a broken email.
    """
    return " ".join(str(value).split()).rstrip(".")


def _caixa(value) -> str:
    """Sentence-case a plan title that SIGAA stores shouted in capitals."""
    return desgritar(value, frozenset(_SIGLAS))


def _resumo(value, limit: int = 95) -> str:
    """Cap a title on a word boundary, for the subject line.

    Plan titles run to 200 characters. A subject that long is truncated by every
    client at roughly the point where the sender name would have been, so the
    one thing the recipient needs after their own plan never appears.
    """
    text = str(value)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:—-")
    return cut + "…"


def _minuscula(value) -> str:
    """Lower a skill label for mid-sentence use without destroying acronyms.

    A blind `|lower` turned the R language into a bare "r": the message read
    "o seu plano pede r e bioestatistica". Only the first letter moves, and only
    when the label is not itself an acronym.
    """
    text = str(value).strip()
    if not text or text.isupper() or len(text) <= 2:
        return text
    return text[0].lower() + text[1:]


def _environment(strict: bool = True) -> Environment:
    env = Environment(
        undefined=StrictUndefined if strict else Undefined,
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )
    env.filters["milhar"] = _milhar
    env.filters["decimal"] = decimal
    env.filters["extenso"] = _extenso
    env.filters["siglas"] = _siglas
    env.filters["limpo"] = _limpo
    env.filters["minuscula"] = _minuscula
    env.filters["resumo"] = _resumo
    env.filters["caixa"] = _caixa
    return env


def read_template(name: str) -> str:
    return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


# Above this within-corpus percentile a draft may state *where the plan ranked*.
# Chosen so the claim survives two recipients comparing their emails.
#
# It gates the ranking claim only. Naming a technique that the recipient's own
# plan text asks for is a fact about their plan, not an assertion about how well
# it matches, and stays available at any percentile — it is also the only thing
# that makes a low-ranked draft worth reading.
STRONG_FIT_PERCENTILE = 70.0

# How many tailored items a message may carry. Past this the offer stops reading
# as an offer and starts reading as a catalogue.
MAX_CONTRIBUTIONS = 3
# Shared skills required before an annex may be called the closest match. One is
# enough because the tag lists are curated to be distinctive; what actually
# prevents a spurious match is that no annex is tagged with a broad method.
MIN_EMPHASIS_OVERLAP = 1


def _plan_skill_ids(primary: Mapping[str, Any]) -> list[str]:
    """Skill ids extracted from this plan, most-mentioned first, generics last."""
    return [str(s.get("skill_id")) for s in (primary.get("skills") or [])
            if s.get("skill_id") and not s.get("generic")]


def select_contributions(primary: Mapping[str, Any], profile: Mapping[str, Any]) -> list[str]:
    """What to offer, chosen by what this plan actually asks for.

    Deduplication is by key, not by sentence. Several skills map deliberately to
    the same key — `datasus`, `sinan`, `sih_sia` and `sim_sinasc` all mean "I
    would handle the SUS extraction" — and, more importantly, a default can name
    the same work as a skill-matched line in different words, which once put
    "conduzir a análise estatística" in a message twice.
    """
    by_skill = profile.get("contribution_by_skill") or {}
    texts = profile.get("contribution_texts") or {}
    keys: list[str] = []
    for skill_id in _plan_skill_ids(primary):
        key = by_skill.get(skill_id)
        if key and key not in keys:
            keys.append(key)
        if len(keys) == MAX_CONTRIBUTIONS:
            break
    for key in profile.get("contribution_default") or []:
        if len(keys) == MAX_CONTRIBUTIONS:
            break
        if key not in keys:
            keys.append(key)
    return [texts[k] for k in keys if k in texts]


def select_annexes(primary: Mapping[str, Any], profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every annex, with at most one marked as closest to this plan.

    All four are always named. An earlier version cited only the single best
    match, which meant a week that produced four documents was presented as one,
    and a plan matching nothing was offered nothing at all. Emphasis is the only
    thing the plan decides: a genuine tie or no overlap simply leaves the list
    unmarked, because pointing at an unrelated manuscript is worse than pointing
    at none.
    """
    plan_skills = set(_plan_skill_ids(primary))
    annexes = [dict(a) for a in (profile.get("annexes") or []) if isinstance(a, Mapping)]
    scores = [len(plan_skills & set(a.get("tags") or [])) for a in annexes]
    best = max(scores, default=0)
    # One shared skill is a coincidence, not a match: pointing at a
    # leptospirosis paper because a plant-physiology plan also mentions
    # statistics reads as a system that did not read either.
    if best < MIN_EMPHASIS_OVERLAP:
        best = 0
    winners = [i for i, score in enumerate(scores) if score == best]
    for index, annex in enumerate(annexes):
        annex["emphasised"] = bool(best) and len(winners) == 1 and index == winners[0]
    return annexes


def _works_line(annexes: list[dict[str, Any]], profile: Mapping[str, Any]) -> str:
    """The one paragraph that carries the prior work, as a sentence.

    It was a five-line annotated list of manuscripts followed by a second list
    of links under a heading. Four attachments, two public projects, a list of
    methodological tasks and a quantitative footer stop reading as evidence and
    start reading as insecurity, so the message names the annex that bears on
    this plan, says how many others came with it, and moves on.
    """
    if not annexes:
        return ""
    recent = sum(1 for a in annexes if a.get("recent"))
    sentence = f"Anexei {_extenso(len(annexes))} trabalhos meus"
    if recent:
        sentence += f", {_extenso(recent)} escritos nesta semana"
    marked = next((a for a in annexes if a.get("emphasised") and a.get("short")), None)
    if marked:
        sentence += f"; o mais próximo do seu plano é o de {marked['short']}"
        if marked.get("blurb"):
            sentence += f", {marked['blurb']}"
        sentence += "."
    else:
        sentence += ", para sua apreciação."

    tail = profile.get("works_tail") or ""
    urls = {str(l.get("key")): str(l.get("url")) for l in (profile.get("links") or [])}
    if tail and all(("{" + k + "}") in tail for k in urls):
        sentence += " " + tail.format(**urls)
    return sentence


def build_context(recipient: Mapping[str, Any], signature: str, profile: Mapping[str, Any],
                  *, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The variables a campaign template may use.

    `primary` is the single best-qualifying opportunity; templates that mention
    a specific project should use it so the claim stays checkable against the
    persisted evidence.
    """
    opportunities = list(recipient.get("qualifying_opportunities") or [])
    primary = opportunities[0] if opportunities else {}
    rationale = recipient.get("rationale") or {}
    percentile = float(rationale.get("opportunity_percentile") or 0.0)
    pulsar = dict(extra or {}).get("pulsar")
    fit_is_strong = percentile >= STRONG_FIT_PERCENTILE
    annexes = select_annexes(primary, profile)
    display_name = (recipient.get("professor_display_name")
                    or display_person_name(recipient.get("professor_name", "")))
    contributions = select_contributions(primary, profile)
    expertise_note = profile.get("expertise_note") or ""
    offer_prompt = (profile.get("offer_prompt") or "") if contributions else ""
    return {
        **dict(recipient),
        # The Lattes spelling when it was recoverable, otherwise the
        # title-cased matching key. `display_person_name` cannot put the
        # accents back, so a name it produces is a fallback, not the target.
        "professor_name": display_name,
        "primary": primary,
        "opportunities": opportunities,
        "rationale": rationale,
        "matched_skills": rationale.get("matched_skills", []),
        # A template must not be able to claim more affinity than the ranking
        # found. Below the threshold the honest email leads with availability
        # and capability instead of asserting a shared research interest.
        "opportunity_percentile": percentile,
        "fit_is_strong": fit_is_strong,
        "already_applied": bool(rationale.get("already_applied")),
        "identity_line": profile.get("identity_line") or "",
        # "Prezado(a) Prof(a). Diego Figueiredo Nóbrega" advertises the template.
        # Generating each message individually is pointless if the result still
        # reads as a filled-in placeholder.
        "saudacao": saudacao(display_name),
        "works_line": _works_line(annexes, profile),
        # Chosen against this plan's own extracted skills, not listed wholesale.
        "contributions": contributions,
        "annexes": annexes,
        "about_lines": list(profile.get("about_lines") or []),
        "contacts": list(profile.get("contacts") or []),
        "sending_note": profile.get("sending_note") or "",
        "expertise_note": expertise_note,
        # The general claim and the tailored list are one movement: "this is what
        # I am good at, and here is what that means for your plan." Split across
        # two paragraphs the first one floated, arriving after a paragraph about
        # scholarship logistics with nothing to attach to. Joined in Python
        # rather than in the template because Jinja's block trimming eats the
        # newline off any line that ends in a tag, which is how the two halves
        # got welded together the last three times this was attempted there.
        "offer_lead": " ".join(x for x in (expertise_note, offer_prompt) if x),
        "closing_line": profile.get("closing_line") or "",
        "links": list(profile.get("links") or []),
        "signature": signature,
        "sender_name": profile.get("sender_name") or (signature.splitlines() or [""])[0],
        # Measured at campaign-creation time and frozen into the snapshot, so a
        # message can never quote a corpus size the database no longer has.
        # Declared here (not only injected) so a template guarding on it renders
        # under StrictUndefined even when a caller supplies no statistics.
        "pulsar": None,
        **dict(extra or {}),
    }


def render_text(subject_template: str, body_template: str, context: Mapping[str, Any]) -> tuple[str, str]:
    env = _environment()
    subject = env.from_string(subject_template).render(**context).strip()
    body = env.from_string(body_template).render(**context).strip() + "\n"
    return subject, body


_PARAGRAPH = re.compile(r"\n\s*\n")


# A text-drawn chart only survives in a monospaced box that does not reflow, so
# the HTML alternative must not turn one into a <p> of <br>-separated lines. The
# marker is indentation: every line of every panel is indented (see
# `panels.INDENT`), and ordinary prose in the templates never is.
_PRE_STYLE = ("font-family:'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace;"
              "font-size:12px;line-height:1.45;white-space:pre;overflow-x:auto;"
              "margin:0 0 16px;padding:12px 14px;background:#f6f7f9;"
              "border-left:3px solid #d6dae0;color:#24292f")


def _is_preformatted(paragraph: str) -> bool:
    lines = [ln for ln in paragraph.split("\n") if ln.strip()]
    return bool(lines) and all(ln.startswith("  ") for ln in lines)


def text_to_html(body: str) -> str:
    """Escape and paragraph-wrap a plaintext body for the HTML alternative.

    Consecutive preformatted paragraphs are merged into one block, so a panel
    containing a blank line does not render as two boxes with a gap.
    """
    paragraphs = [p for p in _PARAGRAPH.split(body) if p.strip()]
    out: list[str] = []
    pending: list[str] = []

    def flush() -> None:
        if pending:
            out.append(f'<pre style="{_PRE_STYLE}">' +
                       html.escape("\n\n".join(pending)) + "</pre>")
            pending.clear()

    for paragraph in paragraphs:
        if _is_preformatted(paragraph):
            # Trailing whitespace only widens the box; leading indentation is
            # the panel's own layout and is kept.
            pending.append(paragraph.rstrip())
            continue
        flush()
        out.append("<p>" + html.escape(paragraph.strip()).replace("\n", "<br>") + "</p>")
    flush()
    return "\n".join(out)


def render_html(body_text: str, context: Mapping[str, Any], *, theme: str = "default_email.html") -> str:
    """Wrap a rendered plaintext body in the HTML theme."""
    env = _environment(strict=False)
    template = env.from_string(read_template(theme))
    return template.render(
        body_html=text_to_html(body_text),
        subject=context.get("subject", ""),
        **{k: v for k, v in context.items() if k != "subject"},
    )


def render_message(
    subject_template: str,
    body_template: str,
    recipient: Mapping[str, Any],
    signature: str,
    profile: Mapping[str, Any],
    *,
    theme: str = "default_email.html",
    extra: Mapping[str, Any] | None = None,
) -> tuple[str, str, str]:
    """Returns ``(subject, plaintext, html)`` for one recipient.

    There is no measurement footer under either alternative any more. The
    corpus counts, the retrieval MRR and the per-facet percentile bars were
    telemetry: they told a professor reading on deadline day where he sat
    inside a ranking, which implies the sender is scoring his colleagues
    against one another. The evaluation is in the attached report, where a
    reader who wants it will look.
    """
    context = build_context(recipient, signature, profile, extra=extra)
    subject, body = render_text(subject_template, body_template, context)
    return subject, body, render_html(body, {**context, "subject": subject}, theme=theme)
