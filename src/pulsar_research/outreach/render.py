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
    return env


def read_template(name: str) -> str:
    return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


# Above this within-corpus percentile a draft may assert topical alignment.
# Chosen so the claim survives two recipients comparing their emails.
STRONG_FIT_PERCENTILE = 70.0


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
        "fit_is_strong": percentile >= STRONG_FIT_PERCENTILE,
        "already_applied": bool(rationale.get("already_applied")),
        "capability_lines": list(profile.get("capability_lines") or []),
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


def text_to_html(body: str) -> str:
    """Escape and paragraph-wrap a plaintext body for the HTML alternative."""
    paragraphs = [p.strip() for p in _PARAGRAPH.split(body) if p.strip()]
    return "\n".join(
        "<p>" + html.escape(p).replace("\n", "<br>") + "</p>" for p in paragraphs
    )


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
    """Returns ``(subject, plaintext, html)`` for one recipient."""
    context = build_context(recipient, signature, profile, extra=extra)
    subject, body = render_text(subject_template, body_template, context)
    body_html = render_html(body, {**context, "subject": subject}, theme=theme)
    return subject, body, body_html
