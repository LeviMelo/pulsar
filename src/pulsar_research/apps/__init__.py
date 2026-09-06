"""Things built *on* PULSAR, as opposed to PULSAR itself.

The distinction this directory draws is the whole point of it. Below this level
sit the parts that model an academic network and keep it current — the entity
graph, the semantic space, acquisition, the pipeline, the structural measures.
None of them know that anybody is ever written to. Inside this directory sit
programs that consume all of that to do one specific job.

Outreach was the first such job and for a while it was indistinguishable from
the project: the ranking existed to pick recipients, and the console's serving
layer imported the email package to find out how to spell someone's name. That
is the wrong way round. A platform that can answer "who bridges these two groups"
should not have its identity resolution living inside a letter writer, and the
second application built here — a collaboration map, a market report, a
supervisor search — should not have to import the first one to get at it.

So: nothing under `apps/` may be imported by anything above it. The dependency
only ever points down.
"""
