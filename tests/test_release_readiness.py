"""Public-release metadata and packaging guardrails."""
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def test_repository_ships_public_project_documents():
    for name in ("LICENSE", "SECURITY.md", "CONTRIBUTING.md"):
        assert (ROOT / name).is_file(), f"missing public project document: {name}"


def test_package_metadata_uses_the_shipped_license_file():
    project = _pyproject()["project"]

    assert project["license"] == {"file": "LICENSE"}
    assert "Operating System :: POSIX :: Linux" not in project["classifiers"]


def test_sdist_has_an_explicit_public_include_policy():
    sdist = _pyproject()["tool"]["hatch"]["build"]["targets"]["sdist"]
    included = set(sdist["include"])
    excluded = set(sdist["exclude"])

    assert "src/nhj" in included
    assert "/install.sh" in included
    assert "/LICENSE" in included
    assert "finetune" not in included
    assert "integrations" not in included
    assert "services" not in included
    assert "**/._*" in excluded
    assert "**/.DS_Store" in excluded
    # Local-only working trees are explicitly excluded from the public sdist.
    assert "/finetune" in excluded
    assert "/ocker-dataset" in excluded


def test_ci_and_secret_scan_workflows_are_present():
    workflows = ROOT / ".github" / "workflows"

    assert (workflows / "ci.yml").is_file()
    secret_scan = (workflows / "secret-scan.yml").read_text()
    assert "pull-requests: read" in secret_scan


def test_gitignore_blocks_all_appledouble_metadata():
    gitignore = (ROOT / ".gitignore").read_text().splitlines()

    assert "._*" in gitignore
