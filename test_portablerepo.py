import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import portablerepo
from portablerepo import main, scan


class PortableRepoTests(unittest.TestCase):
    def make_repo(self):
        tmp = tempfile.TemporaryDirectory()
        return tmp, Path(tmp.name)

    def codes(self, root):
        return {finding.code for finding in scan(root)}

    def test_absolute_path_and_undocumented_env(self):
        tmp, root = self.make_repo()

        with tmp:
            (root / "app.py").write_text(
                'MODEL = r"C:\\Users\\Alice\\model.bin"\n'
                'TOKEN = os.getenv("API_TOKEN")\n',
                encoding="utf-8",
            )

            codes = self.codes(root)

            self.assertIn("absolute-path", codes)
            self.assertIn("undocumented-env", codes)

    def test_documented_env_is_clean(self):
        tmp, root = self.make_repo()

        with tmp:
            (root / "app.py").write_text(
                'TOKEN = os.getenv("API_TOKEN")\n',
                encoding="utf-8",
            )

            (root / ".env.example").write_text(
                "API_TOKEN=example\n",
                encoding="utf-8",
            )

            self.assertNotIn(
                "undocumented-env",
                self.codes(root),
            )

    def test_missing_file_and_case_mismatch(self):
        tmp, root = self.make_repo()

        with tmp:
            assets = root / "assets"
            assets.mkdir()

            (assets / "Logo.png").write_bytes(b"x")

            (root / "app.js").write_text(
                "const a = 'assets/missing.png';\n"
                "const b = 'assets/logo.png';\n",
                encoding="utf-8",
            )

            codes = self.codes(root)

            self.assertIn("missing-file", codes)
            self.assertIn("case-mismatch", codes)

    def test_local_endpoint_is_warning(self):
        tmp, root = self.make_repo()

        with tmp:
            (root / "app.ts").write_text(
                'const api = "http://localhost:8080";\n',
                encoding="utf-8",
            )

            item = next(
                finding
                for finding in scan(root)
                if finding.code == "local-endpoint"
            )

            self.assertEqual(
                "WARNING",
                item.severity,
            )

    def test_ignore_marker_suppresses_line(self):
        tmp, root = self.make_repo()

        with tmp:
            (root / "app.py").write_text(
                'x = r"C:\\Users\\Alice\\x.txt" '
                "# portablerepo: ignore\n",
                encoding="utf-8",
            )

            self.assertNotIn(
                "absolute-path",
                self.codes(root),
            )

    def test_ignore_specific_finding_code(self):
        tmp, root = self.make_repo()

        with tmp:
            (root / "app.js").write_text(
                "const x = 'assets/missing.png';\n",
                encoding="utf-8",
            )

            self.assertIn(
                "missing-file",
                self.codes(root),
            )

            ignored = {
                finding.code
                for finding in scan(
                    root,
                    ignored_codes={"missing-file"},
                )
            }

            self.assertNotIn(
                "missing-file",
                ignored,
            )

    def test_node_missing_dependency(self):
        tmp, root = self.make_repo()

        with tmp:
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "dependencies": {
                            "react": "^1",
                        }
                    }
                ),
                encoding="utf-8",
            )

            (root / "app.js").write_text(
                "import React from 'react';\n"
                "import axios from 'axios';\n",
                encoding="utf-8",
            )

            findings = [
                finding
                for finding in scan(root)
                if finding.code == "node-dependency"
            ]

            self.assertEqual(
                1,
                len(findings),
            )

            self.assertIn(
                "axios",
                findings[0].message,
            )

    def test_node_builtin_not_dependency(self):
        tmp, root = self.make_repo()

        with tmp:
            (root / "package.json").write_text(
                "{}",
                encoding="utf-8",
            )

            (root / "app.js").write_text(
                "import fs from 'node:fs';\n"
                "const path = require('path');\n",
                encoding="utf-8",
            )

            self.assertNotIn(
                "node-dependency",
                self.codes(root),
            )

    def test_shell_specific_package_script(self):
        tmp, root = self.make_repo()

        with tmp:
            (root / "package.json").write_text(
                '{"scripts":{"clean":"rm -rf dist"}}',
                encoding="utf-8",
            )

            self.assertIn(
                "shell-specific",
                self.codes(root),
            )

    def test_readme_missing_script(self):
        tmp, root = self.make_repo()

        with tmp:
            (root / "package.json").write_text(
                '{"scripts":{"test":"node test.js"}}',
                encoding="utf-8",
            )

            (root / "README.md").write_text(
                "Run `npm run build`.\n",
                encoding="utf-8",
            )

            self.assertIn(
                "readme-script",
                self.codes(root),
            )

    @unittest.skipIf(
        os.name == "nt",
        "Windows cannot create reserved filenames",
    )
    def test_windows_reserved_filename(self):
        tmp, root = self.make_repo()

        with tmp:
            (root / "CON.txt").write_text(
                "x",
                encoding="utf-8",
            )

            self.assertIn(
                "windows-name",
                self.codes(root),
            )

    def test_case_collision(self):
        tmp, root = self.make_repo()

        with tmp:
            upper = root / "A.txt"
            lower = root / "a.txt"

            upper.write_text(
                "a",
                encoding="utf-8",
            )

            if lower.exists():
                self.skipTest(
                    "Filesystem is case-insensitive"
                )

            lower.write_text(
                "b",
                encoding="utf-8",
            )

            self.assertIn(
                "case-collision",
                self.codes(root),
            )

    def test_json_output_shape_and_exit(self):
        tmp, root = self.make_repo()

        with tmp:
            buf = io.StringIO()

            with redirect_stdout(buf):
                code = main(
                    [
                        str(root),
                        "--format",
                        "json",
                    ]
                )

            payload = json.loads(
                buf.getvalue()
            )

            self.assertEqual(
                0,
                code,
            )

            self.assertEqual(
                portablerepo.__version__,
                payload["version"],
            )

            self.assertEqual(
                {
                    "blockers": 0,
                    "warnings": 0,
                },
                payload["summary"],
            )

    def test_warning_only_passes_default_but_fails_strict(self):
        tmp, root = self.make_repo()

        with tmp:
            (root / "app.js").write_text(
                'const x = "localhost:3000";\n',
                encoding="utf-8",
            )

            with redirect_stdout(
                io.StringIO()
            ):
                self.assertEqual(
                    0,
                    main([str(root)]),
                )

                self.assertEqual(
                    1,
                    main(
                        [
                            str(root),
                            "--strict",
                        ]
                    ),
                )


if __name__ == "__main__":
    unittest.main()
