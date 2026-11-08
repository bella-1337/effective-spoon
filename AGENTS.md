# Agent instructions

Follow [SKILL.md](SKILL.md) for every change to this stack.

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
