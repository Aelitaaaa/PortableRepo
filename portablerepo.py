from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

__version__ = "1.0.0"

IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode", ".venv", "venv", "env",
    "node_modules", "vendor", "build", "dist", "target", ".gradle", ".next",
    ".nuxt", ".cache", "coverage", "__pycache__", ".pytest_cache", ".mypy_cache",
}

TEXT_EXTENSIONS = {
    ".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".php", ".java",
    ".kt", ".kts", ".go", ".rs", ".rb", ".json", ".yml", ".yaml", ".toml",
    ".ini", ".cfg", ".conf", ".xml", ".md", ".txt", ".sh", ".ps1", ".bat",
    ".cmd", ".env", ".properties", ".html", ".css", ".scss", ".vue", ".svelte",
}

SPECIAL_TEXT_FILES = {
    "Dockerfile",
    "Makefile",
    "Procfile",
    ".env.example",
    ".env.sample",
    ".env.template",
}

WINDOWS_USER_PATH = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z]:\\Users\\[^\\\s\"'<>|]+(?:\\[^\s\"'<>|]*)?)"
)

UNIX_HOME_PATH = re.compile(
    r"(?<![A-Za-z0-9_])/(?:home|Users)/[^/\s\"']+(?:/[^\s\"']*)?"
)

LOCAL_ENDPOINT = re.compile(
    r"(?:https?://)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::\d{2,5})?",
    re.I,
)

RELATIVE_FILE_REF = re.compile(
    r"[\"']((?:\.\.?[\\/]|(?:assets?|data|config|resources?|public|static|src)[\\/])"
    r"[^\"'\r\n?#]+\.[A-Za-z0-9]{1,10})[\"']"
)

SHELL_ONLY = re.compile(
    r"(?:^|[;&|]\s*)(?:rm\s+-rf\b|cp\s+|mv\s+|export\s+[A-Za-z_][A-Za-z0-9_]*=|chmod\s+)"
)

IGNORE_MARKER = re.compile(
    r"portablerepo:\s*ignore\b",
    re.I,
)

ENV_PATTERNS = [
    re.compile(r"os\.getenv\(\s*[\"']([A-Z][A-Z0-9_]*)[\"']"),
    re.compile(r"os\.environ\[\s*[\"']([A-Z][A-Z0-9_]*)[\"']\s*\]"),
    re.compile(r"process\.env\.([A-Z][A-Z0-9_]*)"),
    re.compile(r"process\.env\[\s*[\"']([A-Z][A-Z0-9_]*)[\"']\s*\]"),
    re.compile(r"System\.getenv\(\s*[\"']([A-Z][A-Z0-9_]*)[\"']"),
    re.compile(r"(?:std::env::var|env::var)\(\s*[\"']([A-Z][A-Z0-9_]*)[\"']"),
    re.compile(r"os\.Getenv\(\s*[\"']([A-Z][A-Z0-9_]*)[\"']"),
    re.compile(r"(?:getenv|env)\(\s*[\"']([A-Z][A-Z0-9_]*)[\"']"),
]

NODE_IMPORT_PATTERNS = [
    re.compile(r"\bfrom\s+[\"']([^\"']+)[\"']"),
    re.compile(r"\brequire\(\s*[\"']([^\"']+)[\"']\s*\)"),
    re.compile(r"\bimport\(\s*[\"']([^\"']+)[\"']\s*\)"),
    re.compile(r"^\s*import\s+[\"']([^\"']+)[\"']", re.M),
]

NODE_BUILTINS = {
    "assert",
    "buffer",
    "child_process",
    "cluster",
    "console",
    "constants",
    "crypto",
    "dgram",
    "diagnostics_channel",
    "dns",
    "domain",
    "events",
    "fs",
    "http",
    "http2",
    "https",
    "module",
    "net",
    "os",
    "path",
    "perf_hooks",
    "process",
    "punycode",
    "querystring",
    "readline",
    "repl",
    "stream",
    "string_decoder",
    "sys",
    "timers",
    "tls",
    "trace_events",
    "tty",
    "url",
    "util",
    "v8",
    "vm",
    "wasi",
    "worker_threads",
    "zlib",
    "test",
}

WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

WINDOWS_FORBIDDEN_CHARS = set('<>:"|?*')


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    file: str
    line: int
    message: str
    suggestion: str


def is_within(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def should_exclude(root: Path, path: Path, patterns: list[str]) -> bool:
    value = rel(root, path)

    return any(
        fnmatch.fnmatch(value, pattern)
        or fnmatch.fnmatch(path.name, pattern)
        for pattern in patterns
    )


def iter_text_files(root: Path, excludes: list[str]):
    for base, dirs, files in os.walk(root, followlinks=False):
        base_path = Path(base)

        dirs[:] = [
            d
            for d in dirs
            if d not in IGNORED_DIRS
            and not should_exclude(root, base_path / d, excludes)
        ]

        for name in files:
            path = base_path / name

            if should_exclude(root, path, excludes):
                continue

            if path.is_symlink():
                continue

            try:
                if path.stat().st_size > 1_000_000:
                    continue
            except OSError:
                continue

            if (
                path.suffix.lower() in TEXT_EXTENSIONS
                or name in SPECIAL_TEXT_FILES
            ):
                yield path


def normalize_without_case_resolution(path: Path) -> Path:
    return Path(os.path.abspath(path))


def resolve_repo_path(
    root: Path,
    target: Path,
) -> tuple[bool, Path] | None:
    if not is_within(root, target):
        return None

    current = root
    exact = True

    for part in target.relative_to(root).parts:
        try:
            entries = list(current.iterdir())
        except OSError:
            return None

        direct = next(
            (
                entry
                for entry in entries
                if entry.name == part
            ),
            None,
        )

        if direct is not None:
            current = direct
            continue

        folded = next(
            (
                entry
                for entry in entries
                if entry.name.casefold() == part.casefold()
            ),
            None,
        )

        if folded is None:
            return None

        exact = False
        current = folded

    return exact, current


def nearest_package_json(
    root: Path,
    path: Path,
) -> Path | None:
    current = path.parent

    while is_within(root, current):
        candidate = current / "package.json"

        if candidate.is_file():
            return candidate

        if current == root:
            break

        current = current.parent

    return None


def load_package_json(path: Path) -> dict | None:
    text = read_text(path)

    if not text:
        return None

    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None

    return value if isinstance(value, dict) else None


def node_package_root(specifier: str) -> str | None:
    if specifier.startswith((".", "/", "node:", "#", "~")):
        return None

    if specifier in NODE_BUILTINS:
        return None

    if specifier.startswith("@"):
        parts = specifier.split("/")

        if len(parts) < 2:
            return None

        if not parts[0][1:] or not parts[1]:
            return None

        return "/".join(parts[:2])

    return specifier.split("/", 1)[0]


def collect_documentation(
    root: Path,
    excludes: list[str],
) -> str:
    chunks: list[str] = []

    candidates = (
        list(root.glob("README*"))
        + list(root.glob(".env.*"))
    )

    docs = root / "docs"

    if docs.is_dir():
        candidates.extend(docs.rglob("*.md"))

    for path in candidates:
        if not path.is_file():
            continue

        if should_exclude(root, path, excludes):
            continue

        if path.name == ".env":
            continue

        if (
            path.name.startswith(".env.")
            and path.name
            not in {
                ".env.example",
                ".env.sample",
                ".env.template",
            }
        ):
            continue

        text = read_text(path)

        if text:
            chunks.append(text)

    return "\n".join(chunks)


def scan_filesystem(
    root: Path,
    excludes: list[str],
    ignored_codes: set[str],
) -> list[Finding]:
    findings: list[Finding] = []

    for base, dirs, files in os.walk(
        root,
        followlinks=False,
    ):
        base_path = Path(base)

        dirs[:] = [
            d
            for d in dirs
            if d not in IGNORED_DIRS
            and not should_exclude(
                root,
                base_path / d,
                excludes,
            )
        ]

        entries = [
            base_path / name
            for name in dirs + files
            if not should_exclude(
                root,
                base_path / name,
                excludes,
            )
        ]

        if "case-collision" not in ignored_codes:
            by_case: dict[str, list[Path]] = {}

            for path in entries:
                by_case.setdefault(
                    path.name.casefold(),
                    [],
                ).append(path)

            for group in by_case.values():
                if len(group) > 1:
                    names = ", ".join(
                        sorted(
                            p.name
                            for p in group
                        )
                    )

                    findings.append(
                        Finding(
                            "BLOCKER",
                            "case-collision",
                            rel(root, group[0]),
                            0,
                            (
                                "Names collide on "
                                "case-insensitive filesystems: "
                                f"{names}"
                            ),
                            (
                                "Rename them so each path "
                                "differs by more than letter case."
                            ),
                        )
                    )

        for path in entries:
            if "windows-name" not in ignored_codes:
                stem = (
                    path.name
                    .split(".", 1)[0]
                    .upper()
                )

                bad_char = next(
                    (
                        c
                        for c in path.name
                        if c in WINDOWS_FORBIDDEN_CHARS
                    ),
                    None,
                )

                if (
                    stem in WINDOWS_RESERVED
                    or bad_char
                    or path.name.endswith((" ", "."))
                ):
                    findings.append(
                        Finding(
                            "BLOCKER",
                            "windows-name",
                            rel(root, path),
                            0,
                            (
                                "Filename is not portable "
                                f"to Windows: {path.name}"
                            ),
                            (
                                "Rename the file using "
                                "Windows-safe characters "
                                "and a non-reserved name."
                            ),
                        )
                    )

            if (
                path.is_symlink()
                and "symlink" not in ignored_codes
            ):
                try:
                    target = path.resolve(strict=True)
                except OSError:
                    findings.append(
                        Finding(
                            "BLOCKER",
                            "symlink",
                            rel(root, path),
                            0,
                            "Broken symbolic link.",
                            (
                                "Fix or remove the link "
                                "before publishing the repository."
                            ),
                        )
                    )
                    continue

                if not is_within(root, target):
                    findings.append(
                        Finding(
                            "BLOCKER",
                            "symlink",
                            rel(root, path),
                            0,
                            (
                                "Symbolic link points "
                                "outside the repository: "
                                f"{target}"
                            ),
                            (
                                "Keep required files inside "
                                "the repository or document "
                                "an explicit external prerequisite."
                            ),
                        )
                    )

    return findings


def scan(
    root: Path,
    excludes: list[str] | None = None,
    ignored_codes: set[str] | None = None,
) -> list[Finding]:
    root = root.resolve()

    excludes = excludes or []
    ignored_codes = ignored_codes or set()

    findings = scan_filesystem(
        root,
        excludes,
        ignored_codes,
    )

    docs = collect_documentation(
        root,
        excludes,
    )

    env_uses: dict[str, tuple[str, int]] = {}
    package_cache: dict[Path, dict | None] = {}

    for path in iter_text_files(
        root,
        excludes,
    ):
        text = read_text(path)

        if text is None:
            continue

        file_name = rel(root, path)

        is_docs = (
            path.suffix.lower()
            in {".md", ".txt"}
        )

        for line_no, line in enumerate(
            text.splitlines(),
            1,
        ):
            if IGNORE_MARKER.search(line):
                continue

            if (
                not is_docs
                and "absolute-path"
                not in ignored_codes
            ):
                for match in WINDOWS_USER_PATH.finditer(line):
                    findings.append(
                        Finding(
                            "BLOCKER",
                            "absolute-path",
                            file_name,
                            line_no,
                            (
                                "Machine-specific Windows "
                                f"user path: {match.group(0)}"
                            ),
                            (
                                "Use a relative path, "
                                "configuration value, "
                                "or environment variable."
                            ),
                        )
                    )

                for match in UNIX_HOME_PATH.finditer(line):
                    findings.append(
                        Finding(
                            "BLOCKER",
                            "absolute-path",
                            file_name,
                            line_no,
                            (
                                "Machine-specific home path: "
                                f"{match.group(0)}"
                            ),
                            (
                                "Use a relative path, "
                                "configuration value, "
                                "or environment variable."
                            ),
                        )
                    )

            is_env_template = (
                path.name
                in {
                    ".env.example",
                    ".env.sample",
                    ".env.template",
                }
            )

            if (
                not is_docs
                and not is_env_template
                and "local-endpoint"
                not in ignored_codes
            ):
                match = LOCAL_ENDPOINT.search(line)

                if match:
                    findings.append(
                        Finding(
                            "WARNING",
                            "local-endpoint",
                            file_name,
                            line_no,
                            (
                                "Hardcoded local endpoint: "
                                f"{match.group(0)}"
                            ),
                            (
                                "Make the endpoint configurable "
                                "if another machine or container "
                                "must connect."
                            ),
                        )
                    )

            if (
                not is_docs
                and "undocumented-env"
                not in ignored_codes
            ):
                for pattern in ENV_PATTERNS:
                    for match in pattern.finditer(line):
                        env_uses.setdefault(
                            match.group(1),
                            (
                                file_name,
                                line_no,
                            ),
                        )

            if (
                not is_docs
                and "file-reference"
                not in ignored_codes
            ):
                for match in RELATIVE_FILE_REF.finditer(line):
                    raw = match.group(1)

                    normalized = (
                        raw
                        .replace("\\", os.sep)
                        .replace("/", os.sep)
                    )

                    if raw.startswith(
                        (
                            "./",
                            "../",
                            ".\\",
                            "..\\",
                        )
                    ):
                        candidates = [
                            normalize_without_case_resolution(
                                path.parent / normalized
                            )
                        ]

                    else:
                        candidates = []
                        current = path.parent

                        while is_within(
                            root,
                            current,
                        ):
                            candidates.append(
                                normalize_without_case_resolution(
                                    current / normalized
                                )
                            )

                            if current == root:
                                break

                            current = current.parent

                    if all(
                        not is_within(root, candidate)
                        for candidate in candidates
                    ):
                        findings.append(
                            Finding(
                                "BLOCKER",
                                "external-path",
                                file_name,
                                line_no,
                                (
                                    "Referenced path escapes "
                                    f"the repository: {raw}"
                                ),
                                (
                                    "Keep required project files "
                                    "inside the repository or make "
                                    "the external dependency explicit."
                                ),
                            )
                        )

                        continue

                    resolved = next(
                        (
                            result
                            for candidate in candidates
                            if is_within(root, candidate)
                            and (
                                result := resolve_repo_path(
                                    root,
                                    candidate,
                                )
                            )
                            is not None
                        ),
                        None,
                    )

                    if resolved is None:
                        findings.append(
                            Finding(
                                "BLOCKER",
                                "missing-file",
                                file_name,
                                line_no,
                                (
                                    "Referenced file "
                                    f"does not exist: {raw}"
                                ),
                                (
                                    "Add the file to the "
                                    "repository or fix the path."
                                ),
                            )
                        )

                    elif not resolved[0]:
                        findings.append(
                            Finding(
                                "BLOCKER",
                                "case-mismatch",
                                file_name,
                                line_no,
                                (
                                    "Path casing differs "
                                    f"from disk: {raw}"
                                ),
                                (
                                    "Use the exact casing: "
                                    f"{rel(root, resolved[1])}"
                                ),
                            )
                        )

        if (
            path.name == "package.json"
            and "shell-specific"
            not in ignored_codes
        ):
            package = load_package_json(path)

            if package:
                scripts = package.get(
                    "scripts",
                    {},
                )

                if isinstance(scripts, dict):
                    for (
                        script_name,
                        command,
                    ) in scripts.items():
                        if (
                            isinstance(command, str)
                            and SHELL_ONLY.search(command)
                        ):
                            line_no = next(
                                (
                                    i
                                    for i, line in enumerate(
                                        text.splitlines(),
                                        1,
                                    )
                                    if f'"{script_name}"'
                                    in line
                                ),
                                1,
                            )

                            findings.append(
                                Finding(
                                    "WARNING",
                                    "shell-specific",
                                    file_name,
                                    line_no,
                                    (
                                        "package.json script "
                                        f"'{script_name}' uses "
                                        "a Unix-specific shell command."
                                    ),
                                    (
                                        "Use a cross-platform "
                                        "Node script or document "
                                        "the required shell."
                                    ),
                                )
                            )

        if (
            path.suffix.lower()
            in {
                ".js",
                ".jsx",
                ".mjs",
                ".cjs",
                ".ts",
                ".tsx",
            }
            and "node-dependency"
            not in ignored_codes
        ):
            package_path = nearest_package_json(
                root,
                path,
            )

            if package_path:
                package = package_cache.setdefault(
                    package_path,
                    load_package_json(
                        package_path
                    ),
                )

                if package:
                    declared = set()

                    for key in (
                        "dependencies",
                        "devDependencies",
                        "peerDependencies",
                        "optionalDependencies",
                    ):
                        value = package.get(
                            key,
                            {},
                        )

                        if isinstance(value, dict):
                            declared.update(value)

                    seen_specs: set[str] = set()

                    for pattern in NODE_IMPORT_PATTERNS:
                        for match in pattern.finditer(text):
                            spec = node_package_root(
                                match.group(1)
                            )

                            if (
                                not spec
                                or spec in seen_specs
                            ):
                                continue

                            seen_specs.add(spec)

                            if spec not in declared:
                                line_no = (
                                    text.count(
                                        "\n",
                                        0,
                                        match.start(),
                                    )
                                    + 1
                                )

                                findings.append(
                                    Finding(
                                        "BLOCKER",
                                        "node-dependency",
                                        file_name,
                                        line_no,
                                        (
                                            "Imported Node package "
                                            "is not declared in "
                                            f"{rel(root, package_path)}: "
                                            f"{spec}"
                                        ),
                                        (
                                            "Declare the package in "
                                            "dependencies/devDependencies "
                                            "or fix the import."
                                        ),
                                    )
                                )

    if "undocumented-env" not in ignored_codes:
        for (
            name,
            (file_name, line_no),
        ) in sorted(env_uses.items()):
            if re.search(
                rf"(?<![A-Z0-9_])"
                rf"{re.escape(name)}"
                rf"(?![A-Z0-9_])",
                docs,
            ):
                continue

            findings.append(
                Finding(
                    "WARNING",
                    "undocumented-env",
                    file_name,
                    line_no,
                    (
                        f"Environment variable {name} "
                        "is used but not documented."
                    ),
                    (
                        "Add it to .env.example, "
                        ".env.sample, or README "
                        "with a safe example value."
                    ),
                )
            )

    if "readme-script" not in ignored_codes:
        root_package = (
            load_package_json(
                root / "package.json"
            )
            if (root / "package.json").is_file()
            else None
        )

        readme = next(
            (
                p
                for p in (
                    root / "README.md",
                    root / "README.txt",
                )
                if p.is_file()
            ),
            None,
        )

        if root_package and readme:
            scripts_value = root_package.get(
                "scripts",
                {},
            )

            scripts = (
                scripts_value
                if isinstance(
                    scripts_value,
                    dict,
                )
                else {}
            )

            readme_text = (
                read_text(readme)
                or ""
            )

            pattern = re.compile(
                r"\b(?:"
                r"npm\s+run\s+|"
                r"pnpm\s+(?:run\s+)?|"
                r"yarn\s+(?:run\s+)?"
                r")"
                r"([A-Za-z0-9:_-]+)"
            )

            for match in pattern.finditer(
                readme_text
            ):
                script = match.group(1)

                if script in {
                    "install",
                    "add",
                    "remove",
                    "exec",
                    "dlx",
                    "create",
                }:
                    continue

                if script not in scripts:
                    findings.append(
                        Finding(
                            "BLOCKER",
                            "readme-script",
                            rel(root, readme),
                            (
                                readme_text.count(
                                    "\n",
                                    0,
                                    match.start(),
                                )
                                + 1
                            ),
                            (
                                "README references missing "
                                f"package script: {script}"
                            ),
                            (
                                "Add the script to package.json "
                                "or correct the README command."
                            ),
                        )
                    )

    unique: dict[tuple, Finding] = {}

    for finding in findings:
        if finding.code in ignored_codes:
            continue

        key = (
            finding.severity,
            finding.code,
            finding.file,
            finding.line,
            finding.message,
        )

        unique[key] = finding

    order = {
        "BLOCKER": 0,
        "WARNING": 1,
    }

    return sorted(
        unique.values(),
        key=lambda f: (
            order.get(
                f.severity,
                9,
            ),
            f.file.casefold(),
            f.line,
            f.code,
        ),
    )


def summary(
    findings: list[Finding],
) -> tuple[int, int]:
    blockers = sum(
        f.severity == "BLOCKER"
        for f in findings
    )

    return (
        blockers,
        len(findings) - blockers,
    )


def print_text(
    root: Path,
    findings: list[Finding],
) -> None:
    blockers, warnings = summary(findings)

    print(
        f"PortableRepo v{__version__} · "
        f"{root.resolve()}"
    )

    print()

    if not findings:
        print(
            "Portable. No obvious "
            "cross-machine blockers found."
        )
        return

    for finding in findings:
        location = (
            f"{finding.file}:{finding.line}"
            if finding.line
            else finding.file
        )

        print(
            f"{finding.severity} "
            f"{location} "
            f"[{finding.code}]"
        )

        print(
            f"  {finding.message}"
        )

        print(
            f"  Fix: {finding.suggestion}"
        )

        print()

    print(
        f"Summary: "
        f"{blockers} blocker(s), "
        f"{warnings} warning(s)"
    )


def github_escape(value: str) -> str:
    return (
        value
        .replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
    )


def print_github(
    findings: list[Finding],
) -> None:
    for finding in findings:
        level = (
            "error"
            if finding.severity == "BLOCKER"
            else "warning"
        )

        attrs = [
            f"file={github_escape(finding.file)}",
            (
                "title=PortableRepo "
                f"{github_escape(finding.code)}"
            ),
        ]

        if finding.line:
            attrs.append(
                f"line={finding.line}"
            )

        message = github_escape(
            (
                f"{finding.message} "
                f"Fix: {finding.suggestion}"
            )
        )

        print(
            f"::{level} "
            f"{','.join(attrs)}"
            f"::{message}"
        )

    blockers, warnings = summary(findings)

    print(
        "PortableRepo: "
        f"{blockers} blocker(s), "
        f"{warnings} warning(s)"
    )


def should_fail(
    findings: list[Finding],
    fail_on: str,
) -> bool:
    if fail_on == "never":
        return False

    if fail_on == "warning":
        return bool(findings)

    return any(
        finding.severity == "BLOCKER"
        for finding in findings
    )


def main(
    argv: list[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="portablerepo",
        description=(
            "Find cross-machine "
            "'works on my machine' problems "
            "without executing project code."
        ),
    )

    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help=(
            "Repository path "
            "(default: current directory)"
        ),
    )

    parser.add_argument(
        "--format",
        choices=(
            "text",
            "json",
            "github",
        ),
        default="text",
        help="Output format",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        dest="legacy_json",
        help="Alias for --format json",
    )

    parser.add_argument(
        "--strict",
        action="store_true",
        help="Alias for --fail-on warning",
    )

    parser.add_argument(
        "--fail-on",
        choices=(
            "blocker",
            "warning",
            "never",
        ),
        default="blocker",
        help="Exit 1 threshold",
    )

    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help=(
            "Exclude a path glob; "
            "repeatable"
        ),
    )

    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="CODE",
        help=(
            "Disable a finding code; "
            "repeatable"
        ),
    )

    parser.add_argument(
        "--version",
        action="version",
        version=(
            f"%(prog)s {__version__}"
        ),
    )

    args = parser.parse_args(argv)

    root = Path(args.path)

    if (
        not root.exists()
        or not root.is_dir()
    ):
        parser.error(
            f"not a directory: {root}"
        )

    output_format = (
        "json"
        if args.legacy_json
        else args.format
    )

    fail_on = (
        "warning"
        if args.strict
        else args.fail_on
    )

    findings = scan(
        root,
        args.exclude,
        set(args.ignore),
    )

    if output_format == "json":
        blockers, warnings = summary(
            findings
        )

        print(
            json.dumps(
                {
                    "version": __version__,
                    "root": str(
                        root.resolve()
                    ),
                    "summary": {
                        "blockers": blockers,
                        "warnings": warnings,
                    },
                    "findings": [
                        asdict(finding)
                        for finding
                        in findings
                    ],
                },
                indent=2,
            )
        )

    elif output_format == "github":
        print_github(findings)

    else:
        print_text(
            root,
            findings,
        )

    return (
        1
        if should_fail(
            findings,
            fail_on,
        )
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
