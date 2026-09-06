"""Composing and sending letters to people the platform has ranked.

An application, not a subsystem. It selects an audience from the semantic run,
renders a message per recipient, holds the drafts for review, and — only from a
terminal, only behind an explicit confirmation — sends them. Everything it knows
about the recipients it reads from the core; it contributes no facts back except
the record of what was sent and what came of it.
"""
