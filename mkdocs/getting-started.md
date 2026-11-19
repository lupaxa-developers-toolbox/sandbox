# Getting Started

Sandbox needs Python 3.11 or newer. The command does not download a
Python interpreter for you.

## Install

```bash
pip install lupaxa-sandbox
```

The text interface is separate:

```bash
pip install "lupaxa-sandbox[tui]"
```

A normal install does not include that extra. `sandbox tui` without it
prints a usage error and exits 2.

Check the data directory and which backends answer a probe:

```bash
sandbox doctor
sandbox --version
```

`sandbox doctor` creates the data directory if it is missing and writes
a probe file, then deletes that file. It does not change your project.

## Run a Sample

From a clone of this repository:

```bash
sandbox validate samples/hello/sandbox.yml
sandbox run samples/hello/sandbox.yml
sandbox diff hello
sandbox destroy hello
```

`sandbox validate` checks the recipe before it creates a workspace.
`sandbox run` copies `samples/hello`, runs `write.py` in that copy, and
prints the files that appeared. `note.txt` in the sample directory is
unchanged. `generated.txt` exists only in the session.

The session name comes from the recipe (`hello`). Run `sandbox destroy
hello` before you run that recipe again. A second create with the same
name is an error.

## One Command Without a Recipe

From `samples/hello`:

```bash
sandbox run --name hello-once -- python3 write.py
sandbox diff hello-once
sandbox logs hello-once
sandbox destroy hello-once
```

Put the command after `--`. Sandbox then treats those words as the
program to run, not as its own options. The default is an argument
array, not a shell. Add `--shell` only when you want a shell to expand
the command.

## Read the Docs Locally

The guide in this site is the `mkdocs/` directory.

```bash
pip install -r requirements.txt
mkdocs serve
```

Open the URL MkDocs prints. The published site is
<https://sandbox.thelupaxaproject.org/>.
