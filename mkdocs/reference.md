# Reference

## Commands

| Command                                      | What it Does                                      |
| -------------------------------------------- | ------------------------------------------------- |
| `sandbox run`                                | Copy a project and run one command or a recipe    |
| `sandbox create NAME`                        | Copy a project and do not run a command           |
| `sandbox exec NAME`                          | Run a command in a session that already exists    |
| `sandbox list`                               | List sessions                                     |
| `sandbox show NAME`                          | Show one session                                  |
| `sandbox diff NAME`                          | Show changes since the session was created        |
| `sandbox logs NAME`                          | Show captured output                              |
| `sandbox history NAME`                       | Show the commands recorded for a session          |
| `sandbox destroy NAME`                       | Delete a session that is not still running        |
| `sandbox export NAME`                        | Copy selected files out of the workspace          |
| `sandbox apply NAME`                         | Write eligible changes back to the source         |
| `sandbox compare RECIPE`                     | Run one snapshot twice and compare the results    |
| `sandbox clean`                              | Remove old or failed sessions                     |
| `sandbox storage`                            | Show how much session data is on disk             |
| `sandbox validate RECIPE`                    | Check a recipe before creating a workspace        |
| `sandbox config show`                        | Show user configuration                           |
| `sandbox config path`                        | Print the user configuration path                 |
| `sandbox doctor`                             | Check the data directory and backend probes       |
| `sandbox backend list`                       | List backends and whether a probe succeeded       |
| `sandbox backend info NAME`                  | Show one backend's probe result and threat model  |
| `sandbox tui`                                | Open the optional text interface                  |

`--help` works on the program and on each command. `--version` prints
the package version. `--json` is available on the commands that print a
report.

## Exit Codes

| Code | Meaning                                                      |
| ---- | ------------------------------------------------------------ |
| 0    | The Sandbox operation succeeded                              |
| 1    | A general Sandbox error, or a partial apply                  |
| 2    | Invalid arguments, an unknown flag, or invalid configuration |
| 3    | The session or another named resource was not found          |
| 4    | An apply conflict, or another filesystem conflict            |
| 5    | The command timed out                                        |
| 6    | The command was interrupted                                  |

`sandbox run` and `sandbox exec` return the child process exit code when
it is between 0 and 255. A timeout still returns 5. `sandbox compare`
returns 0 when the report was produced, including when the two sides
differ. It returns 5 when either side times out.

An unknown flag is a usage error. It is not a traceback. `sandbox
--doctor` suggests `sandbox doctor`.

## Run and Exec

| Flag           | Meaning                                                         |
| -------------- | --------------------------------------------------------------- |
| `--source`     | Directory to copy. The default is the current directory         |
| `--name`       | Session name. Omitted names are generated                       |
| `--timeout`    | Seconds before the command is terminated. The default is 300    |
| `--cwd`        | Working directory inside the workspace. `exec` only             |
| `--shell`      | Run one command string in a shell                               |
| `--venv`       | Create a virtual environment in the workspace                   |
| `--python`     | Interpreter that is already installed                           |
| `--backend`    | `local`, `restricted`, `docker`, or `podman`                    |
| `--network`    | `allow` or `deny`. An unenforceable deny is refused             |
| `--memory-mb`  | Memory limit. Refused when the backend cannot enforce it        |
| `--cpus`       | CPU limit. Refused when the backend cannot enforce it           |
| `--image`      | Local container image. Sandbox does not pull images             |

The command itself goes after `--`.

## Export and Apply

| Flag              | Meaning                                                     |
| ----------------- | ----------------------------------------------------------- |
| `--include`       | A path pattern. Export requires at least one                |
| `--to`            | Destination directory for export                            |
| `--dry-run`       | Print the plan and do not write                             |
| `--yes`           | Overwrite an existing destination file                      |
| `--force`         | Also overwrite a destination file that is newer             |
| `--all`           | Apply every eligible change. This does not bypass conflicts |
| `--delete`        | Allow apply to delete a source file                         |
| `--replace-links` | Allow apply to replace a link or a changed file type        |

## Clean

| Flag           | Meaning                                                         |
| -------------- | --------------------------------------------------------------- |
| `--older-than` | An age such as `14d`, `12h`, or `30m`. The default is 14 days   |
| `--failed`     | Remove sessions whose status is failed, with no age filter      |
| `--dry-run`    | List the sessions that would be removed                         |

A session whose status is running and whose process is still alive is
never removed.

## Recipe Keys

Unknown keys are errors. `version` must be `1`.

| Key                           | Default        | Meaning                                      |
| ----------------------------- | -------------- | -------------------------------------------- |
| `sandbox.name`                | generated      | Session name                                 |
| `sandbox.description`         | empty          | Text stored with the resolved configuration  |
| `workspace.source`            | `.`            | Directory to copy, relative to the recipe    |
| `workspace.strategy`          | `copy`         | `copy` or `empty`                            |
| `workspace.staging.exclude`   | see below      | Paths left out of the copy                   |
| `environment.type`            | `none`         | `none`, `venv`, or `existing`                |
| `environment.python`          |                | Interpreter name or path                     |
| `environment.install`         | none           | Packages installed into a new virtualenv     |
| `execution.commands`          | required       | Commands, each with `id` and `run`           |
| `execution.stop_on_error`     | `true`         | Skip later commands after a failure          |
| `execution.timeout_seconds`   | `300`          | Per-command timeout                          |
| `execution.backend`           | `local`        | Execution backend                            |
| `execution.network`           | `allow`        | `allow` or `deny`                            |
| `execution.image`             |                | Required for `docker` and `podman`           |
| `execution.resources`         | none           | `memory_mb` and `cpus`, when the backend can |
| `execution.environment.set`   | none           | Environment variables for the command        |
| `execution.environment.unset` | none           | Names removed from the inherited environment |
| `cleanup.on_success`          | `retain`       | `retain` or `destroy`                        |
| `cleanup.on_failure`          | `retain`       | `retain` or `destroy`                        |

The default copy excludes `.git/`, `.venv/`, `__pycache__/`, and
`*.pyc`. The change report uses the same list without `.git/`.

`run` is a list of strings. Command ids must be unique. `workspace.source`
must be a directory. `docker` and `podman` require `execution.image`.
A memory or CPU limit on the local or restricted backend is an error.

## Backends

| Backend      | When it is Available                         | What a Probe Allows it to Claim                                  |
| ------------ | -------------------------------------------- | ---------------------------------------------------------------- |
| `local`      | Always                                       | A separate directory only                                        |
| `restricted` | `sandbox-exec` or `bwrap` completes a probe  | Writes outside the workspace, and network when you ask           |
| `docker`     | `docker info` succeeds                       | A container, a local image, optional network and resource limits |
| `podman`     | `podman info` succeeds                       | The same contract as Docker                                      |

Capability flags are `workspace_separation`, `filesystem_restrictions`,
`network_restrictions`, `process_restrictions`, and `resource_limits`.
A flag is true only when that backend can enforce it. The report for a
run records the limits that were actually applied.

## Data

Session identity and history live in SQLite under the platform data
directory for `lupaxa-sandbox`. Workspaces, manifests, and logs stay as
files. Set `LUPAXA_SANDBOX_DATA` to point at another data directory. Set
`LUPAXA_SANDBOX_CONFIG` to point at another user configuration file.
