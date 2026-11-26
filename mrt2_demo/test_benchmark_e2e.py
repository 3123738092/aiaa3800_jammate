import json
from dataclasses import replace

from . import benchmark_e2e as benchmark


def test_host_only_scenarios_pass_without_invoking_mrt2(tmp_path, monkeypatch):
    def unexpected_generator(*args, **kwargs):
        raise AssertionError('Strict rejection and stop must bypass the model')

    monkeypatch.setattr(benchmark, 'generate', unexpected_generator)
    scenarios = tuple(case for case in benchmark.SCENARIOS if case.outcome in {'stop', 'strict'})
    output = tmp_path / 'run'
    summary = benchmark.run_suite(output, 3, scenarios=scenarios)

    assert summary['status'] == 'passed' and summary['passed'] == 2
    strict, stop = (summary['scenarios'][case.name]['trials'][0] for case in scenarios)
    assert strict['generator_calls'] == 0 and strict['state_committed'] is False
    assert stop['generator_calls'] == 0 and stop['state_committed'] is True
    assert stop['audio']['duration_s'] == 2 and stop['audio']['rms'] == 0
    assert json.loads((output / 'summary.json').read_text(encoding='utf-8')) == summary


def test_suite_records_failures_and_continues(tmp_path):
    stop = next(case for case in benchmark.SCENARIOS if case.outcome == 'stop')
    mismatch = replace(stop, name='invalid_expectation', action='BUILD')
    summary = benchmark.run_suite(tmp_path / 'run', 1, scenarios=(mismatch, stop))

    assert summary['status'] == 'failed' and (summary['passed'], summary['failed']) == (1, 1)
    error = summary['scenarios'][mismatch.name]['trials'][0]['error']
    assert 'Expected BUILD in the BandPlan' in error
    assert summary['scenarios'][stop.name]['passed'] == 1
