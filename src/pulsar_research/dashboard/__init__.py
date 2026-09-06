"""Data access for the console.

The Streamlit application that used to live here is gone; `queries.Repository`
survived it, because all the SQL was kept out of the presentation layer from
the start. The console in `pulsar_research.webapp` reads through it unchanged.
"""

from .queries import Repository, lorenz

__all__ = ["Repository", "lorenz"]
