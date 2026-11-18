"""Every third-party package imported by app/ must be declared in pyproject.toml.

Guards against 'works on my machine': a package installed locally but missing from the dependency list
passes local tests and fails in CI or production. Lazy imports inside functions are checked too, since
those only fail when the code path runs (e.g. google-auth on the first push send).
"""

import ast
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _declared() -> set[str]:
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    return {re.split(r"[\[<>=!~ ;]", d, maxsplit=1)[0].lower().replace("_", "-") for d in deps}


def _imported_top_level() -> set[str]:
    names: set[str] = set()
    for path in (ROOT / "app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names - {"app"} - set(sys.stdlib_module_names)


def test_every_import_is_a_declared_dependency():
    declared = _declared()
    dists = packages_distributions()  # import name -> distribution names, e.g. PIL -> Pillow
    # Packages that arrive with a declared dependency (extras / framework internals).
    provided_by_declared = {"starlette", "email_validator", "argon2", "jwt", "pydantic_core", "typing_extensions"}
    missing = []
    for mod in sorted(_imported_top_level() - provided_by_declared):
        names = {d.lower().replace("_", "-") for d in dists.get(mod, [mod])}
        if not names & declared:
            missing.append(f"{mod} (distribution: {', '.join(sorted(names))})")
    assert not missing, "Imported but not declared in pyproject.toml: " + "; ".join(missing)
