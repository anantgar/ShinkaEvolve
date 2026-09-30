import hashlib
from types import SimpleNamespace

import pytest

from shinka.launch.secure import SecureEvaluationScheduler, SecureJobConfig
from shinka.secure.artifacts import ContentAddressedStore
from shinka.secure.contracts import ResourceLimits
from shinka.secure.dependencies import (
    DependencyArtifact,
    DependencyManifest,
    DependencyPreparer,
)
from shinka.secure.errors import SecurityPolicyError
from shinka.secure.runtime import ContainerCandidateRunner


def dependency(path, *, runtime=True):
    return DependencyArtifact(
        name=path.name,
        url=path.as_uri(),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        size=path.stat().st_size,
        runtime=runtime,
    )


def test_mutation_runtime_scope_withholds_build_only_inputs(tmp_path):
    public = tmp_path / "public.dat"
    private = tmp_path / "build-only.dat"
    public.write_bytes(b"public runtime dependency")
    private.write_bytes(b"PRIVATE_BUILD_CANARY")
    store = ContentAddressedStore(tmp_path / "objects")
    bundle = DependencyPreparer(store).prepare(
        DependencyManifest(artifacts=(dependency(public), dependency(private, runtime=False)))
    )
    scheduler = object.__new__(SecureEvaluationScheduler)
    scheduler.config = SecureJobConfig(mutation_dependency_scope="runtime")
    scheduler.prepared = SimpleNamespace(dependencies=bundle)
    store.materialize_archive(scheduler.mutation_dependency_artifact, tmp_path / "view")
    assert (tmp_path / "view/files/public.dat").read_bytes() == public.read_bytes()
    assert not (tmp_path / "view/files/build-only.dat").exists()
    assert b"PRIVATE_BUILD_CANARY" not in store.verify(
        scheduler.mutation_dependency_artifact.digest
    ).read_bytes()
    scheduler.config.mutation_dependency_scope = "all"
    assert scheduler.mutation_dependency_artifact == bundle.artifact


def test_large_declared_dependency_includes_manifest_size(tmp_path):
    artifact = tmp_path / "payload.dat"
    artifact.write_bytes(b"0" * (1024 * 1024 + 1))
    store = ContentAddressedStore(tmp_path / "objects")
    bundle = DependencyPreparer(store).prepare(
        DependencyManifest(artifacts=(dependency(artifact),))
    )
    store.materialize_archive(bundle.runtime_artifact, tmp_path / "runtime")
    assert (tmp_path / "runtime/files/payload.dat").read_bytes() == artifact.read_bytes()


def test_runtime_suffix_does_not_change_cleanup_identity(tmp_path):
    store = ContentAddressedStore(tmp_path / "objects")
    ref = store.put_bytes(b"runtime", kind="runtime")
    kwargs = dict(
        engine=SimpleNamespace(), artifacts=store, runtime_artifact=ref,
        dependency_artifact=ref, image="example.invalid/runtime@sha256:" + "a" * 64,
        command=("run",), limits=ResourceLimits(cpus=1, memory_bytes=128 * 1024**2, pids=64),
        job_id="job", attempt_id="attempt", startup_timeout_seconds=60,
        request_timeout_seconds=60,
    )
    primary = ContainerCandidateRunner(**kwargs)
    reference = ContainerCandidateRunner(**kwargs, container_suffix="reference")
    assert primary.attempt_id == reference.attempt_id
    assert primary.job_id == reference.job_id
    assert primary.container_suffix != reference.container_suffix
    with pytest.raises(SecurityPolicyError, match="suffix"):
        ContainerCandidateRunner(**kwargs, container_suffix="../escape")
