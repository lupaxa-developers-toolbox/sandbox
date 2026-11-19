# Usage

A session is one copied workspace plus the commands that have run in it.
The copy is kept until you destroy the session. A failed command does
not delete the workspace, so you can still inspect the files and the log.

## Sessions

```bash
sandbox list
sandbox show hello
sandbox history hello
sandbox logs hello
sandbox diff hello
sandbox destroy hello
```

`sandbox diff` is the change since the session was created. Text files
get a unified diff. A file that is not valid UTF-8, or that contains a
NUL byte, is reported as binary. Files larger than 10 MiB are hashed and
marked skipped instead of being printed as a diff.

`sandbox create NAME --source .` copies a project and does not run a
command. `sandbox exec NAME -- python3 script.py` runs another command
in a session that already exists. One filesystem-changing command runs
at a time in a session. A second command while the first is still
running is refused.

`--json` prints a machine-readable report. `--quiet` prints nothing
when the Sandbox operation itself succeeds.

## Recipes

A recipe is a YAML file. The usual name is `sandbox.yml`. Commands are
lists of arguments. A single string is rejected, so a recipe does not
imply a shell.

```bash
sandbox validate samples/report/sandbox.yml
sandbox run samples/report/sandbox.yml
```

Validation happens before the workspace is created. Unknown keys,
duplicate command ids, a missing source directory, and a backend limit
the backend cannot enforce are errors.

Commands run in order. The default is to stop after the first failure.
Set `stop_on_error: false` when a later command should still run.

Settings apply in this order, from lowest to highest: built-in defaults,
your config file, the recipe, then explicit flags such as `--timeout`
and `--python`. The session stores that resolved configuration. Values
whose names look like keys, secrets, tokens, or passwords are stored as
`***`.

```bash
sandbox config path
sandbox config show
sandbox config show --resolved
```

An optional virtual environment uses an interpreter that is already
installed:

```yaml
environment:
  type: venv
  python: python3
  install:
    - pytest
```

`type` may be `none` (the default), `venv`, or `existing`. Sandbox does
not download Python. A virtual environment is not a security boundary,
and `.venv` is left out of the change report.

## Keep Results

Export copies files out of the workspace and leaves them there.

```bash
sandbox export report --include "dist/**" --to ./output --dry-run
sandbox export report --include "dist/**" --to ./output
```

An existing destination file is kept unless you pass `--yes`. A newer
destination file is kept unless you pass `--force`. A link that points
outside the workspace is rejected before anything is copied.

Apply writes a change back only when the original file still matches the
snapshot from session creation.

```bash
sandbox apply report --all --dry-run
sandbox apply report --include "dist/results.txt"
```

If you edited that source file after the session was created, it is left
alone and the command exits 4. Deletes need `--delete`. Replacing a link
or a file type needs `--replace-links`. The write is recorded in a
journal with backups. The report says the apply is not atomic.

```bash
sandbox clean --dry-run
sandbox clean --older-than 14d
sandbox storage
```

`sandbox clean` removes sessions older than 14 days unless you pass
another age. `--failed` removes sessions whose status is failed and does
not also apply the age filter. A session with a live command is skipped.

## Compare

`sandbox compare` copies the project once and starts both sessions from
that snapshot. A file that changes in the original tree during the run
does not land in only one side.

```bash
sandbox compare samples/compare-notes/sandbox.yml \
  --python-a python3 \
  --python-b python3
```

The report lists each side's exit code, duration, whether stdout and
stderr match, and the files that were added, changed, or removed. A
difference is an observation. It is not treated as a compatibility
verdict. Pass both `--python-a` and `--python-b`, or neither.

## Isolation

The local backend is the default. It runs as you. It does not restrict
files, the network, or other processes, and it is not a way to run
untrusted code.

```bash
sandbox backend list
sandbox backend info local
sandbox run --backend restricted --network deny -- python3 -c "print('ok')"
```

`restricted` is offered only after `sandbox-exec` or `bwrap` succeeds at
a probe. It denies writes outside the workspace. `--network deny` adds a
network denial when that backend can enforce one. It still runs as you
and does not limit CPU or memory.

Docker and Podman are offered only when that engine answers `info`. You
name a local image with `--image`. Sandbox does not pull images and does
not add privileged mode. If the backend is missing, or it cannot enforce
the limit you asked for, the run stops. Sandbox does not switch to a
weaker backend.

## The Text Interface

```bash
sandbox tui
```

The interface lists sessions, filters the change list, opens a diff, and
copies a selected file out. Apply shows conflicts first and writes only
after you confirm. It calls the same session, diff, export, and apply
code as the command line.
