from concurrent.futures import ThreadPoolExecutor

from forge.adapters.local.sandbox import SubprocessSandbox
from forge.agent import load_tasks

from .conftest import ROOT

TASKS = load_tasks(ROOT / "benchmarks")


def test_benchmark_shape():
    assert len(TASKS) == 12
    assert {t.split for t in TASKS} == {"train", "holdout"}
    assert sum(t.split == "holdout" for t in TASKS) == 4


def test_every_task_fails_buggy_and_passes_fixed():
    sbx = SubprocessSandbox(timeout_seconds=10)

    def check(task):
        fix = {p.name: p.read_text() for p in (task.repo_dir.parent / "fix").glob("*.py")}
        return task.name, sbx.run_tests(task.repo_dir, {}).passed, sbx.run_tests(task.repo_dir, fix)

    with ThreadPoolExecutor(6) as ex:
        for name, buggy_passed, fixed in ex.map(check, TASKS):
            assert not buggy_passed, f"{name}: buggy version should fail"
            assert fixed.passed, f"{name}: fix should pass\n{fixed.output}"


def test_sandbox_blocks_network_and_path_escape(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "test_net.py").write_text(
        "import socket, pytest\n"
        "def test_net():\n"
        "    with pytest.raises(OSError, match='disabled'):\n"
        "        socket.create_connection(('example.com', 80))\n"
    )
    sbx = SubprocessSandbox(timeout_seconds=10)
    assert sbx.run_tests(repo, {}).passed
    assert not sbx.run_tests(repo, {"../evil.py": "x"}).passed
