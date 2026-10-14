"""Probe backend selection is explicit; execution outcomes are never replaced."""
import pytest
from scripts.run_native_terminal_acceptance import bootstrap_program


@pytest.mark.parametrize("backend", ["mxc", "unelevated"])
def test_native_backend_probe_selects_only_requested_sandbox(backend):
    program = bootstrap_program(backend)
    compile(program, "synthetic probe launcher", "exec")
    assert f'windows.sandbox="{backend}"' in program
    assert "runtime.serve_bootstrap()" in program
    assert "danger-full-access" not in program
    assert "sandbox_mode" not in program
    assert "features.prefer_mxc=false" in program


@pytest.mark.parametrize("backend", ["off", "elevated", "mxc;unsafe", None])
def test_probe_cannot_inject_flags_or_select_unsandboxed_execution(backend):
    with pytest.raises(ValueError):
        bootstrap_program(backend)
