## Summary

<!-- What changes and why. Link the issue, if any. -->

## Operational impact

<!-- Affected profiles (prod, dev, grafana, langfuse, security), services,
     data stores, and whether a deployment needs a restart, migration, or
     backup first. Write "None" if the change is documentation only. -->

## Checklist

- [ ] `./scripts/ci/validate.sh` passes.
- [ ] Connected surfaces from `SKILL.md` are updated (Compose, LiteLLM config,
      OpenSearch and Grafana assets, `.env*.example`, `docs/`).
- [ ] `diagrams/*.mmd` are updated and rendered, or no service, network,
      profile, data store, or flow changed.
- [ ] `mkdocs.yml` `nav:` is updated and `./scripts/build-docs.py build` passes,
      or no Markdown file was added, moved, or removed.
- [ ] New images are pinned by digest and new actions by commit SHA.
- [ ] `CHANGELOG.md` has an entry under `[Unreleased]`.
- [ ] No secrets, `.env` files, or private keys are committed.
