# Agent instructions

Follow [SKILL.md](SKILL.md) for every change to this stack.

Keep the diagrams current. When a change adds, removes, renames, or reconnects
a service, network, profile, data store, external dependency, or flow step,
update the affected Mermaid sources in [diagrams/](diagrams/README.md) in the
same change. Edit only the `.mmd` files, then run:

```sh
./scripts/render-diagrams.py
```

If the dashmotion skill or Graphviz (`dot`) is unavailable, commit the Mermaid changes alone and
say that the HTML still needs rendering. Never edit the generated HTML by hand.
