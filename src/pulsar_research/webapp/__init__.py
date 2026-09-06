"""The PULSAR operator console: a static front end over a standard-library server."""

from .server import Console, serve

__all__ = ["Console", "serve"]
