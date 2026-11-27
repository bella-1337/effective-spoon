# Agent instructions

Follow [SKILL.md](SKILL.md) for every change to this stack.

Before implementing a feature request, compare it with
[BerriAI Inference Stack](https://github.com/BerriAI/inference-stack). Then ask
the user which approach to implement, following
[Reference stack](docs/reference-inference-stack.md#feature-requests).

Keep the diagrams current. When a change adds, removes, renames, or reconnects
a service, network, profile, data store, external dependency, or flow step,
update the affected Mermaid sources in [diagrams/](diagrams/README.md) in the
same change. Edit and commit only the `.mmd` files; the HTML is generated,
ignored by Git, and rendered by CI. Check that they render with:

```sh
./scripts/render-diagrams.py
```

If Graphviz (`dot`) is unavailable, commit the Mermaid changes and say that
they were not rendered locally; the CI documentation build renders them and
fails on a source that does not render.

Keep the reader versions current. The Markdown guides are the reference text
for agents; the documentation site shows a plainer reader version of a guide
from `docs-site/human/` at the same path. When you change a guide that has
one, update the reader version with the same facts, keep every heading of the
guide, and stamp it, as described in
[Reader versions](docs/documentation-site.md#reader-versions-and-copy-for-llm):

```sh
./scripts/build-docs.py human-stamp docs/<changed-guide>.md
./scripts/build-docs.py human-status
```
