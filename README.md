<p align="center">
  <a href="https://github.com/lupaxa-developers-toolbox">
    <img src="https://raw.githubusercontent.com/the-lupaxa-project/brand-assets/master/logos/organisations/developers-toolbox/readme-logo.png" alt="Developers Toolbox" />
  </a>
</p>

<h1 align="center">Sandbox</h1>

Sandbox runs commands in a disposable copy of a project and shows what
those commands changed. The original project stays in place.

> **Note:** A workspace is a copied directory, not a security boundary. A
> command still runs with your user permissions.

The PyPI name is `lupaxa-sandbox`. The import path is `lupaxa.sandbox`.
The console script is `sandbox`. `lupaxa` is a namespace package — there
is no `lupaxa/__init__.py`.

## Install

```bash
pip install lupaxa-sandbox
```

Python 3.11 or newer is required.

## Run

```bash
sandbox run -- python -c "open('generated.txt', 'w').write('ok\n')"
sandbox list
sandbox diff run-name
sandbox logs run-name
sandbox destroy run-name
```

`sandbox run` copies the current directory, runs the command in that copy,
and prints what was added, changed, or deleted. The copy is kept until
you destroy the session. Put the command after `--` so Sandbox does not
treat it as its own options. Add `--shell` only when you want a shell to
expand the command.

`sandbox --help` and `sandbox --version` print usage and the package
version. `--json` prints a machine-readable report. A normal command
failure returns that command's exit code. A timeout returns exit code 5.

## Isolation

```bash
sandbox backend list
sandbox run --backend restricted --network deny -- python -c "open('out.txt', 'w').write('ok')"
```

The local backend is the default. It runs as you and does not restrict
files, the network, or processes. `restricted`, `docker`, and `podman`
are used only when a probe says they work. A limit the selected backend
cannot enforce is refused. Sandbox does not switch to a weaker backend,
and it does not pull container images.

## Compare

```bash
sandbox compare sandbox.yml --python-a python3.11 --python-b python3.12
sandbox tui
```

`sandbox compare` stages the project once and runs that same snapshot in
two sessions. The report lists exit codes, output, and files that
differ. A difference is an observation. It does not mean the commands
are incompatible. `sandbox tui` browses sessions, diffs, and export.
Install it with `pip install "lupaxa-sandbox[tui]"`. A normal install
does not include it.

## Recipes

```bash
sandbox validate sandbox.yml
sandbox run sandbox.yml
sandbox config show --resolved
```

A recipe lists commands as argument arrays and can create a virtual
environment with an interpreter that is already installed. Sandbox does
not download Python. The environment is not a security boundary.
`sandbox validate` checks the file before it creates a workspace.

## Keep Results

```bash
sandbox export experiment-1 --include "dist/**" --to ./output/
sandbox apply experiment-1 --all --dry-run
sandbox clean --dry-run
sandbox storage
```

Export copies the files you name. Apply writes changes back only when
the original files still match the snapshot from session creation. A
file you edited after that is left alone and reported as a conflict.
Deletes need `--delete`. `sandbox clean` does not remove a session that
is still running.

## Samples

The repository has three ready-to-run projects under `samples/`. This is
the smallest:

```bash
sandbox validate samples/hello/sandbox.yml
sandbox run samples/hello/sandbox.yml
sandbox diff hello
sandbox destroy hello
```

`samples/report` builds a file and checks it. `samples/compare-notes`
runs one snapshot twice so you can compare the results.

## Documentation

The guide lives in `mkdocs/` and publishes to
<https://sandbox.thelupaxaproject.org/>.
It has install steps, session and recipe usage, a command reference, and
a walkthrough of each sample with commands you can paste.

```bash
pip install -r requirements.txt
mkdocs serve
```

## Development

```bash
make init
make python-install-dev
make python-check
```

<a href="https://github.com/the-lupaxa-project">
    <img src="https://raw.githubusercontent.com/the-lupaxa-project/brand-assets/master/logos/components/footer-for-child-orgs.svg" alt="The Lupaxa Project Footer" width="100%" />
</a>
