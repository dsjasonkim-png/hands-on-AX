"""모든 job의 평가 사례가 가짜 판단기로는 기대 경로를 그대로 따르는지 확인한다.

새 job을 만들면 eval_cases.py를 두고, 이 테스트가 통과해야 한다.
"""
import pytest

from axcore.evaluate import run_suite
from jobs import job_names, load_eval

JOBS = job_names(include_template=True)


def test_every_job_has_eval_cases():
    assert "_template" in JOBS   # 새 job을 만들면 여기에 더해진다


@pytest.mark.parametrize("name", JOBS)
def test_eval_cases_pass_with_fake_decider(name, tmp_path):
    ev = load_eval(name)
    assert ev.CASES, f"{name}: 평가 사례가 비어 있음"
    results = run_suite(ev.make_graph(model=None, runtime=tmp_path), ev.CASES)
    failed = [(r["case_id"], r["checks"], r["result"]) for r in results if not r["passed"]]
    assert not failed, f"{name}: 기대 경로와 다른 사례 {failed}"
    assert len(ev.CASES) >= 3, f"{name}: 평가 사례는 최소 3건(정상·사람 확인·예외)이 필요함"
