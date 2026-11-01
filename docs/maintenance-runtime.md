# Maintenance runtime

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

Repository maintenance commands are executable
[uv scripts](https://docs.astral.sh/uv/guides/scripts/). Install `uv` using one
of its [documented installation methods](https://docs.astral.sh/uv/getting-started/installation/)
and make sure `uv` is on `PATH`:

```sh
uv --version
```

Each entry point has a `uv run --script` shebang and PEP 723 inline metadata, so
the documented `./scripts/<name>.py` commands need neither an activated virtual
environment nor a separately managed system Python. The scripts currently use
Python's standard library for maintenance logic. The two HTTPS verification
scripts additionally pin `truststore==0.10.5` so certificate validation uses the
operating system's native trust store, including mkcert roots installed there.
The full upgrade suite also locates the standard or `CAROOT`-configured mkcert
root with Python for roots installed only in an NSS/user store. Their adjacent uv
lock files record the exact resolution. `uv` enforces the declared Python version
and can acquire a matching interpreter when one is not installed.

## Why use Python and uv for these scripts?

For this repository, the practical advantages are:

- **Less shell parsing and platform branching.** HTTP, JSON, hashing, paths,
  permissions, temporary directories, timestamps, and retention traversal use
  typed Python APIs instead of pipelines of `curl`, `jq`, `grep`, `find`,
  `sha256sum`, and platform-specific alternatives.
- **Safer process boundaries.** The remaining external boundary is primarily
  Docker Compose. Commands are passed as argument arrays without a shell, which
  avoids quoting ambiguity and prevents command interpolation.
- **One declared runtime.** PEP 723 metadata records the supported Python version
  next to each entry point. According to the uv script documentation, `uv`
  creates managed environments on demand, respects `requires-python`, and keeps
  script dependencies separate from project dependencies.
- **Reproducible growth.** If a future script needs a third-party package, it can
  declare and pin it inline and use `uv lock --script` to create an adjacent lock
  file instead of relying on whatever happens to be installed globally. The
  HTTPS verification scripts already follow this pattern for `truststore`.
- **Testable orchestration.** Parsing, validation, checksums, retention rules,
  HTTP handling, and workflow selection are regular Python functions rather than
  shell blocks that require process-level fixtures to exercise.

These are maintainability and correctness benefits, not proof that Python is
universally better than shell. A tiny command wrapper may start faster as shell,
and this approach adds `uv` as a host prerequisite. uv publishes substantial
package-management speedups, but these scripts install only one small pinned
dependency for two verification entry points, and only when it is not cached.
Their wall-clock time is dominated by Docker, PostgreSQL, and network operations.
The migration therefore claims no meaningful workflow speedup. Its success
criterion is behavioral parity with a smaller dependence on host shell utilities.
