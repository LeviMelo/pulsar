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
from .panels import fit_panel, method_caption, method_panel

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
    pulsar = dict(extra or {}).get("pulsar")
    fit_is_strong = percentile >= STRONG_FIT_PERCENTILE
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
        # Text-drawn analytics. The method panel describes the engine and is the
        # same in every draft; the fit panel describes one work plan and is
        # therefore gated on the same threshold as the prose claim above — a
        # chart asserting alignment is still an assertion of alignment.
        "panel_method": method_panel(pulsar),
        "panel_method_caption": method_caption(pulsar),
        "panel_fit": fit_panel(rationale.get("reading"),
                               total=int((pulsar or {}).get("n_opportunities") or 0))
                     if fit_is_strong else "",
        # Deliberately two lists, never one. Credentials say the work can be
        # trusted to him; contributions say what work he would actually take on.
        # A single list under either heading answers the wrong question.
        "about_lines": list(profile.get("about_lines") or []),
        "work_lines": list(profile.get("work_lines") or []),
        "work_intro": profile.get("work_intro") or "",
        "contribution_lines": list(profile.get("contribution_lines") or []),
        "capability_lines": list(profile.get("capability_lines") or []),
        "annexes": list(profile.get("annexes") or []),
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
    """Returns ``(subject, plaintext, html)`` for one recipient."""
    context = build_context(recipient, signature, profile, extra=extra)
    subject, body = render_text(subject_template, body_template, context)
    body_html = render_html(body, {**context, "subject": subject}, theme=theme)
    return subject, body, body_html
