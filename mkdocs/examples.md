# Examples

These three directories are in `samples/`. Each one validates, and each
one can be run with the commands below. They do not change the files in
the sample directory. New files appear in the session workspace.

Destroy the named session before you run the same sample again.

## Hello

`samples/hello` is a note and a script that writes one new file.

```yaml
--8<-- "samples/hello/sandbox.yml"
```

```python
--8<-- "samples/hello/write.py"
```

```bash
sandbox validate samples/hello/sandbox.yml
sandbox run samples/hello/sandbox.yml
sandbox diff hello
sandbox logs hello
sandbox destroy hello
```

`sandbox diff hello` lists `generated.txt` as added. `note.txt` is in
the copy and is not listed, because the script does not change it. The
`note.txt` in `samples/hello` is still the original file.

The same script without a recipe, from `samples/hello`:

```bash
sandbox run --name hello-once -- python3 write.py
sandbox show hello-once
sandbox destroy hello-once
```

## Report

`samples/report` runs two commands. The first creates `dist/results.txt`.
The second reads it and exits 0 only when the text is `ok`. The recipe
stops after a failure, so a broken build does not pretend the check
passed.

```yaml
--8<-- "samples/report/sandbox.yml"
```

```python
--8<-- "samples/report/build.py"
```

```bash
sandbox validate samples/report/sandbox.yml
sandbox run samples/report/sandbox.yml
sandbox diff report
sandbox export report --include "dist/**" --to ./output --dry-run
sandbox export report --include "dist/**" --to ./output
sandbox destroy report
```

The dry run creates nothing, including the destination directory. The
real export copies `dist/results.txt` into `./output` and leaves the
file in the workspace as well. Run the export from a directory where
`./output` is a place you want those files.

To see a refused apply, change nothing in the sample and ask for a dry
run after the session exists:

```bash
sandbox apply report --all --dry-run
```

Do that before `sandbox destroy report`. Apply will offer the new
`dist/results.txt` only when that path is absent from the original
sample. It will not overwrite a source file that no longer matches the
snapshot from session creation.

## Compare Notes

`samples/compare-notes` records `sys.version` into `python-version.txt`.
Sandbox stages the sample once and runs that snapshot on both sides.

```yaml
--8<-- "samples/compare-notes/sandbox.yml"
```

```python
--8<-- "samples/compare-notes/record.py"
```

```bash
sandbox validate samples/compare-notes/sandbox.yml
sandbox compare samples/compare-notes/sandbox.yml \
  --python-a python3 \
  --python-b python3
```

Both sides use the same interpreter, so `python-version.txt` is reported
as identical. Point the two flags at two interpreters you already have
installed when you want the file to differ:

```bash
sandbox compare samples/compare-notes/sandbox.yml \
  --python-a python3.11 \
  --python-b python3.12
```

Sandbox does not download those interpreters. If one of them is missing,
the command stops before it creates the sessions. A difference in the
report is the text each interpreter wrote. It is not a verdict that the
program is incompatible.

The sessions are named `compare-notes-a` and `compare-notes-b`.

```bash
sandbox diff compare-notes-a
sandbox destroy compare-notes-a
sandbox destroy compare-notes-b
```
