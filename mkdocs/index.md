# Sandbox

Sandbox runs a command in a disposable copy of a project and shows what
that command changed. The original project stays where it is. You can
keep the copy, export files from it, or write selected changes back when
the originals still match the snapshot taken at the start.

The package name is `lupaxa-sandbox`. The import path is
`lupaxa.sandbox`. The command is `sandbox`.

> **Note:** A workspace is a copied directory, not a security boundary.
> The command still runs with your user permissions unless you select a
> backend that a probe says can enforce a tighter limit.

## What it Does

- Copies a project into a session and runs a command there
- Records stdout, stderr, the exit code, and a SHA-256 manifest
- Shows added, changed, and deleted files, including text diffs
- Keeps the session until you destroy it
- Repeats a run from a YAML recipe, with an optional virtual environment
- Exports selected files, or applies changes that still match the source
- Compares two runs that started from the same snapshot
- Offers `restricted`, Docker, and Podman only when those tools answer a probe

## Samples

The repository includes three projects you can validate and run as they
stand. Each one is a normal directory with a `sandbox.yml`.

| Sample                    | What it Shows                                      |
| ------------------------- | -------------------------------------------------- |
| `samples/hello`           | One command that writes a new file                 |
| `samples/report`          | Two commands, then an export of `dist/`            |
| `samples/compare-notes`   | The same snapshot run twice, then compared         |

## The Guide

[Getting Started](getting-started.md) installs the package and runs
`samples/hello`. [Usage](usage.md) covers sessions, recipes, export, and
compare. [Reference](reference.md) lists commands, exit codes, and recipe
keys. [Examples](examples.md) walks through all three samples with
commands you can paste.
