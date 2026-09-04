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
from .panels import FACET_LABELS, card_lines, decimal, fit_bars, rank_scale

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


def _milhar(value) -> str:
    """15019 -> '15.019'. Brazilian thousands separator, for a Portuguese email."""
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(value)


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


def _rank_from_percentile(percentile: float, pulsar: Mapping[str, Any] | None) -> int:
    """Percentile back to a position, for a sentence a reader parses instantly."""
    total = int((pulsar or {}).get("n_opportunities") or 0)
    if total <= 0:
        return 0
    return max(1, min(total, int(round((100.0 - percentile) / 100.0 * total)) + 1))


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
    rank = _rank_from_percentile(percentile, pulsar)
    annexes = select_annexes(primary, profile)
    return {
        **dict(recipient),
        "professor_name": display_person_name(recipient.get("professor_name", "")),
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
        # The message body carries no chart. The retrieval battery lives in the
        # attached report, where a professor who wants it can find it; in the
        # body it was four paragraphs of method between the recipient and the
        # question being asked. What survives is the compact summary in the
        # footer card, which is the one piece of HTML in the message.
        "card": {
            "stats": pulsar,
            "reading": (rationale.get("reading") or {}) if fit_is_strong else {},
            "percentile": percentile if fit_is_strong else 0.0,
        },
        "identity_line": profile.get("identity_line") or "",
        # A position is concrete where a percentile is jargon: "4º de 187" is
        # read correctly by everyone, "percentil 98" by fewer.
        "opportunity_rank": rank,
        "facet_labels": list(FACET_LABELS.items()),
        # Chosen against this plan's own extracted skills, not listed wholesale.
        "contributions": select_contributions(primary, profile),
        "annexes": annexes,
        "annex_recent_count": sum(1 for a in annexes if a.get("recent")),
        "about_lines": list(profile.get("about_lines") or []),
        "contacts": list(profile.get("contacts") or []),
        "sending_note": profile.get("sending_note") or "",
        "expertise_note": profile.get("expertise_note") or "",
        # One line, and only where a rank may be stated at all.
        # Carries its own blank line: leaving the spacing to a Jinja {% if %}
        # made the paragraph break disappear whenever the scale was absent.
        "rank_block": ("\n\n" + rank_scale(rank, int((pulsar or {}).get("n_opportunities") or 0)))
                      if fit_is_strong else "",
        "fit_bars": fit_bars(rationale.get("reading")) if fit_is_strong else [],
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

    The footer exists twice, from one set of values: as a styled card in the
    HTML alternative and as plain lines in the text one. It is appended after
    the HTML is rendered so the card is not also spelled out inside it.
    """
    context = build_context(recipient, signature, profile, extra=extra)
    subject, body = render_text(subject_template, body_template, context)
    body_html = render_html(body, {**context, "subject": subject}, theme=theme)

    footer = card_lines(context.get("card"))
    for link in context.get("links") or []:
        footer.append(f"{link['label']}: {link['url']}")
    if footer:
        rule = "—" * 30
        body = body.rstrip() + "\n\n" + rule + "\n" + "\n".join(footer) + "\n"
    return subject, body, body_html
