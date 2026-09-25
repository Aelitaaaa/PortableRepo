# PortableRepo

**Make “works on my machine” fail before merge.**

PortableRepo is a read-only, zero-runtime-dependency CLI that finds common cross-machine repository problems without executing project code.

Created and maintained by ([@Aelitaaaa](https://github.com/Aelitaaaa)).

## Why

A repository can work perfectly on its author's machine and still fail after a fresh clone because it depends on a local path, missing file, undocumented environment variable, case-insensitive filesystem behavior, undeclared Node package, or OS-specific command.

PortableRepo catches those mistakes before another person has to discover them.

## Checks in v1.0

- machine-specific Windows user paths (`C:\Users\Alice\...`)
- machine-specific Linux/macOS home paths (`/home/alice/...`, `/Users/alice/...`)
- missing referenced project files
- referenced file casing mismatches
- paths that escape the repository
- case-colliding filenames (`Logo.png` and `logo.png`)
- filenames invalid or reserved on Windows
- broken or external symbolic links
- hardcoded local endpoints (`localhost`, `127.0.0.1`, `0.0.0.0`)
- environment variables used but not documented
- Node packages imported but not declared in the nearest `package.json`
- Unix-only commands in `package.json` scripts
- README commands that reference missing package scripts

PortableRepo does **not** run the repository, install its dependencies, upload source code, or modify files.

## Install

From GitHub:

```bash
pip install git+https://github.com/Aelitaaaa/PortableRepo.git
```

For local development:

```bash
git clone https://github.com/Aelitaaaa/PortableRepo.git
cd PortableRepo
pip install .
```

## Use

Scan the current repository:

```bash
portablerepo .
```

Scan another repository:

```bash
portablerepo /path/to/project
```

Default behavior fails only on blockers. To make warnings fail CI too:

```bash
portablerepo . --strict
```

JSON output:

```bash
portablerepo . --format json
```

GitHub Actions annotations:

```bash
portablerepo . --format github
```

Never fail the process, useful for reports:

```bash
portablerepo . --fail-on never
```

Exclude generated or project-specific paths:

```bash
portablerepo . --exclude "generated/*" --exclude "fixtures/*"
```

Disable a check category:

```bash
portablerepo . --ignore local-endpoint
```

Suppress a deliberate finding on one source line:

```python
API = "http://localhost:3000"  # portablerepo: ignore
```

## Example

```text
PortableRepo v1.0.0 · C:\project

BLOCKER src/config.py:12 [absolute-path]
  Machine-specific Windows user path: C:\Users\Alice\models\model.bin
  Fix: Use a relative path, configuration value, or environment variable.

WARNING src/api.ts:4 [local-endpoint]
  Hardcoded local endpoint: http://localhost:8000
  Fix: Make the endpoint configurable if another machine or container must connect.

Summary: 1 blocker(s), 1 warning(s)
```

## GitHub Actions

Add this to your workflow after checkout and Python setup:

```yaml
- name: Install PortableRepo
  run: pip install git+https://github.com/Aelitaaaa/PortableRepo.git@v1.0.0

- name: Check portability
  run: portablerepo . --format github --strict
```

PortableRepo's own repository tests on Windows, Linux, and macOS.

## Exit behavior

| Mode | Exit 1 when |
|---|---|
| default / `--fail-on blocker` | at least one blocker exists |
| `--strict` / `--fail-on warning` | any blocker or warning exists |
| `--fail-on never` | never |

Argument or usage errors use the normal CLI exit code `2`.

## Finding codes

Use these values with `--ignore CODE`:

`absolute-path`, `case-collision`, `case-mismatch`, `external-path`, `local-endpoint`, `missing-file`, `node-dependency`, `readme-script`, `shell-specific`, `symlink`, `undocumented-env`, `windows-name`.

`file-reference` disables the missing/case/external referenced-file scan as a group.

## Safety

PortableRepo is intentionally static and read-only. It scans filenames and UTF-8 text files up to 1 MB. It does not execute code from the target repository and does not make network requests.

Like any static analyzer, it can produce false positives. Prefer `# portablerepo: ignore`, `--ignore`, or `--exclude` for intentional cases rather than weakening the repository itself.

## Development

```bash
python -m unittest -v
python portablerepo.py .
```

## License

MIT © 2026 Muhamad Dzaky Putra Fardian
