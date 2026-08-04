from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text()


def test_public_ci_excludes_secret_backed_tests() -> None:
    workflow = _read(".github/workflows/ci.yml")

    assert 'pytest -q -m "not requires_secrets and not models_dev_live"' in workflow


def test_integration_workflow_exists_for_secret_backed_tests() -> None:
    workflow = _read(".github/workflows/integration.yml")

    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert "push:" in workflow
    assert 'pytest -q -m "requires_secrets"' in workflow
    assert 'pytest -q -m "models_dev_live"' in workflow
    assert "OPENAI_API_KEY" in workflow
    assert "Detect required W&B credentials" in workflow
    assert 'if [[ -n "${WANDB_API_KEY:-}" ]]; then' in workflow
    assert 'echo "available=false" >> "$GITHUB_OUTPUT"' in workflow
    assert "if: steps.credentials.outputs.available == 'true'" in workflow


def test_headless_image_workflow_qualifies_immutable_digest() -> None:
    workflow = _read(".github/workflows/headless-agents-image.yml")

    assert "platforms: linux/amd64,linux/arm64" in workflow
    assert "IMAGE_DIGEST: ${{ steps.build.outputs.digest }}" in workflow
    assert "image_ref=${image_ref}" in workflow
    assert "needs: publish" in workflow
    assert "docker pull \"$SHINKA_SECURE_QUALIFICATION_IMAGE\"" in workflow
    assert "tests/test_secure_container_integration.py" in workflow


def test_pytest_markers_are_registered() -> None:
    pyproject = _read("pyproject.toml")

    assert 'addopts = "--strict-markers"' in pyproject
    assert "integration: live external/provider integration coverage" in pyproject
    assert "models_dev_live: live models.dev catalog contract coverage" in pyproject
    assert (
        "requires_secrets: tests that need CI secrets or private credentials"
        in pyproject
    )
