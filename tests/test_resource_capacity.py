from copy import deepcopy
from datetime import datetime, timezone
import subprocess

import pytest

from betting_engine import resource_capacity as rc

CONFIG = {'max_parallel': 6, 'resource_capacity': {'enabled': True}}
BASE = 1790800000


def sample(t=0, busy=40, **changes):
    value = {'sample_at': datetime.fromtimestamp(BASE+t, timezone.utc).isoformat(),
             'wall_time': BASE+t, 'monotonic': t,
             'cpu_ticks': [int(t*busy), 0, int(t*(100-busy)), 0],
             'cpu_count': 12, 'memory_bytes': 48*rc.GIB,
             'memory_pressure': 1, 'swap_used_bytes': 0,
             'memory_headroom_proxy_bytes': 12*rc.GIB,
             'compressor_bytes': 11*rc.GIB, 'free_bytes': 100*rc.MIB,
             'file_backed_bytes': 12*rc.GIB, 'purgeable_bytes': rc.GIB,
             'thermal_state': 0, 'disk_free_bytes': 395*rc.GIB, 'errors': []}
    value.update(changes)
    return value


def assess(samples, active=3, state=None, config=None, now=None):
    return rc.assess_capacity(samples, config or CONFIG, active, state,
                              now=now if now is not None else samples[-1]['wall_time'])


def healthy_ready(active=3):
    state, samples = {}, [sample()]
    for t in (5, 10, 15):
        samples.append(sample(t))
        result, state = assess(samples, active, state)
    return result, state, samples


def test_cpu_deltas_are_system_capacity_percent_not_accumulated_or_load_average():
    samples = [sample(0), sample(5)]
    assert rc.derive_metrics(samples)['cpu_percent'] == 40
    assert rc.derive_metrics(samples)['swap_growth_mib_per_min'] == 0
    assert rc.derive_metrics([samples[0]])['cpu_percent'] is None
    assert rc.derive_metrics([samples[0], samples[0]])['cpu_percent'] is None


def test_real_uint32_rollover_but_counter_reset_is_not_false_idle():
    a = sample(0, cpu_ticks=[2**32-100, 0, 1000, 0])
    b = sample(5, cpu_ticks=[100, 0, 1300, 0])
    assert rc.derive_metrics([a, b])['cpu_percent'] == 40
    assert rc.derive_metrics([sample(0, cpu_ticks=[100000, 100000, 100000, 0]), sample(5)])['cpu_percent'] is None


def test_soft_rolling_target_and_immediate_high_water_both_stop_new_only():
    ready, state, samples = healthy_ready()
    assert ready['allowed_slots'] == 4 and ready['allow_new']
    # Sudden high CPU must not hide behind rolling mean.
    samples.append(sample(20, cpu_ticks=[1000, 0, 1000, 0]))  # last interval80%
    result, _ = assess(samples, 4, state)
    assert 'cpu_high_water' in result['reasons']
    assert not result['allow_new'] and result['allowed_slots'] == 0
    assert 'cancel' not in result and 'kill' not in result
    # A cooler last interval still holds while recent capacity use averages75%.
    rolling = [sample(0), sample(5, busy=80), sample(10, busy=80),
               sample(15, cpu_ticks=[1125, 0, 375, 0])]
    result, _ = assess(rolling)
    assert result['metrics']['cpu_percent'] == 65
    assert result['metrics']['cpu_rolling_percent'] == 75
    assert result['reasons'] == ['cpu_soft_target']


def test_three_distinct_healthy_samples_per_slot_cached_calls_cannot_ramp():
    ready, state, samples = healthy_ready()
    assert ready['allowed_slots'] == 4
    for _ in range(100):
        result, state = assess(samples, 4, state)
        assert not result['allow_new'] and result['allowed_slots'] == 4
    for t in (20, 25):
        samples.append(sample(t))
        result, state = assess(samples, 4, state)
        assert result['allowed_slots'] == 4
    samples.append(sample(30))
    result, state = assess(samples, 4, state)
    assert result['allowed_slots'] == 5
    for t in (35, 40, 45, 50, 55, 60):
        samples.append(sample(t))
        result, state = assess(samples, 6, state)
    assert result['allowed_slots'] == 6 and not result['allow_new']


def test_normal_pressure_with_nearly_all_ram_used_is_not_oom():
    ready, _, _ = healthy_ready()
    assert ready['allow_new']
    assert ready['metrics']['free_bytes'] == 100*rc.MIB
    assert ready['metrics']['compressor_bytes'] == 11*rc.GIB
    assert 'not guaranteed' in ready['metrics']['memory_headroom_interpretation']


@pytest.mark.parametrize('changes,reason', [
    ({'memory_pressure': 4}, 'memory_pressure'),
    ({'thermal_state': 1}, 'thermal_advisory'),
    ({'thermal_state': 2}, 'thermal_advisory'),
    ({'thermal_state': 3}, 'thermal_advisory'),
    ({'memory_headroom_proxy_bytes': 3*rc.GIB}, 'memory_reserve'),
    ({'disk_free_bytes': 19*rc.GIB}, 'disk_reserve'),
    ({'swap_used_bytes': 8*rc.MIB}, 'swap_growth'),
])
def test_resource_limits_are_independent_and_recovery_requires_new_good_samples(changes, reason):
    _, state, samples = healthy_ready()
    samples.append(sample(20, **changes))
    result, state = assess(samples, 4, state)
    assert reason in result['reasons'] and result['allowed_slots'] == 0
    for t in (25, 30):
        samples.append(sample(t))
        result, state = assess(samples, 4, state)
        assert not result['allow_new']
    samples.append(sample(35))
    result, state = assess(samples, 4, state)
    assert result['allow_new'] and result['allowed_slots'] == 5


@pytest.mark.parametrize('changes', [
    {'memory_pressure': 0}, {'memory_pressure': 3}, {'memory_pressure': True},
    {'thermal_state': None}, {'memory_bytes': float('nan')},
    {'cpu_ticks': [False, 0, 0, 0]}, {'cpu_ticks': [1]}, {'monotonic': 'unknown'},
    {'disk_free_bytes': -1}, {'memory_headroom_proxy_bytes': 49*rc.GIB},
    {'errors': ['sysctl:TimeoutExpired']},
])
def test_malformed_unknown_metrics_are_conservative(changes):
    result, _ = assess([sample(0), sample(5, **changes)])
    assert result['state'] == 'telemetry_unavailable'
    assert result['allowed_slots'] == 0 and not result['allow_new']


@pytest.mark.parametrize('age', [-0.01, 30.001])
def test_stale_and_future_sample_do_not_admit(age):
    result, _ = assess([sample(), sample(5)], now=BASE+5+age)
    assert 'sample_stale_or_future' in result['reasons'] and not result['allow_new']


def test_sleep_gap_requires_new_cpu_interval():
    result, _ = assess([sample(), sample(60)])
    assert result['state'] == 'telemetry_unavailable'


@pytest.mark.parametrize('config', [
    {'max_parallel': True}, {'resource_capacity': True},
    {'resource_capacity': {'enabled': 'yes'}},
    {'resource_capacity': {'enabled': True, 'cpu_high_water': 95}},
    {'resource_capacity': {'enabled': True, 'cpu_soft_target': 81}},
    {'resource_capacity': {'enabled': True, 'max_workers': 7}},
    {'resource_capacity': {'enabled': True, 'disk_reserve_gib': float('nan')}},
])
def test_invalid_optin_cannot_raise_capacity(config):
    result, _ = rc.assess_capacity([], config, 3, now=BASE)
    assert result['state'] == 'configuration_invalid'
    assert not result['allow_new'] and result['allowed_slots'] == 0


def test_legacy_disabled_never_samples_and_keeps_three_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, 'sample_native', lambda _: pytest.fail('disabled must not sample'))
    for config in ({}, {'max_parallel': 6}, {'resource_capacity': {'enabled': False}}):
        result = rc.evaluate_admission(tmp_path, config, 2)
        assert result['state'] == 'disabled' and result['allowed_slots'] == 3 and result['allow_new']


def test_wrapper_samples_once_each_five_seconds_and_never_writes(monkeypatch, tmp_path):
    now, calls = [0], []
    monkeypatch.setattr(rc.time, 'time', lambda: BASE+now[0])
    monkeypatch.setattr(rc.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(rc, 'sample_native', lambda _: (calls.append(now[0]) or sample(now[0])))
    rc._STATES.clear()
    for i in (0, 2, 4, 5, 7, 10, 15):
        now[0] = i
        result = rc.evaluate_admission(tmp_path, CONFIG, 3)
    assert calls == [0, 5, 10, 15]
    assert result['allow_new'] and result['allowed_slots'] == 4
    assert list(tmp_path.iterdir()) == []
    # Threshold changes cannot reuse a warmed approval window.
    changed = deepcopy(CONFIG); changed['resource_capacity']['cpu_soft_target'] = 70
    assert not rc.evaluate_admission(tmp_path, changed, 3)['allow_new']


def test_vm_parser_does_not_add_overlapping_categories():
    text = '''Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free: 2.
Pages purgeable: 5.
File-backed pages: 8.
Pages wired down: 9.
Pages occupied by compressor: 11.
'''
    parsed = rc.parse_vm_stat(text)
    assert parsed['memory_headroom_proxy_bytes'] == 8*16384
    assert parsed['compressor_bytes'] == 11*16384
    with pytest.raises(ValueError):
        rc.parse_vm_stat(text.replace('16384', '0'))
    with pytest.raises(KeyError):
        rc.parse_vm_stat(text.replace('Pages occupied', 'Unknown'))


def test_exported_pressure_bit_and_swap_units_parser():
    parsed = rc.parse_sysctl('hw.ncpu: 12\nhw.memsize: 51539607552\nkern.memorystatus_vm_pressure_level: 1\nvm.swapusage: total = 2.00G  used = 1.50G  free = 0.50G  (encrypted)\n')
    assert parsed['memory_pressure'] == 1
    assert parsed['swap_used_bytes'] == 1.5*rc.GIB
    with pytest.raises(ValueError):
        rc.parse_sysctl('hw.ncpu: 12\nhw.memsize: 51539607552\nkern.memorystatus_vm_pressure_level: 1\nvm.swapusage: unavailable\n')


def test_native_sampler_retains_failures_no_zero_substitutes(monkeypatch, tmp_path):
    monkeypatch.setattr(rc.sys, 'platform', 'darwin')
    monkeypatch.setattr(rc, '_cpu_ticks', lambda: [1, 2, 3, 4])
    monkeypatch.setattr(rc, '_thermal_state', lambda: 0)
    monkeypatch.setattr(rc, '_command', lambda args: (_ for _ in ()).throw(subprocess.TimeoutExpired(args, 1)))
    value = rc.sample_native(tmp_path)
    assert value['errors'] == ['sysctl:TimeoutExpired', 'vm_stat:TimeoutExpired']
    assert 'memory_pressure' not in value and 'swap_used_bytes' not in value


def test_sampler_unsupported_platform_is_explicit(monkeypatch, tmp_path):
    monkeypatch.setattr(rc.sys, 'platform', 'linux')
    value = rc.sample_native(tmp_path)
    assert value['errors'] == ['unsupported_platform'] and 'cpu_ticks' not in value


def test_only_complete_first_sample_has_bounded_nonfault_warming_status():
    result, _ = assess([sample()], now=BASE)
    assert result['state'] == 'warming_up'
    assert not result['allow_new'] and result['allowed_slots'] == 0
    assert result['metrics']['cpu_percent'] is None  # Never substitute zero CPU.
    assert assess([sample()], now=BASE+10)[0]['state'] == 'warming_up'
    assert assess([sample()], now=BASE+10.001)[0]['state'] == 'telemetry_unavailable'
    for changes in ({'thermal_state': None}, {'memory_pressure': None}, {'cpu_ticks': []},
                    {'errors': ['sysctl:TimeoutExpired']}, {'monotonic': float('nan')}):
        assert assess([sample(**changes)], now=BASE)[0]['state'] == 'telemetry_unavailable'
    assert assess([sample(memory_pressure=2)], now=BASE)[0]['state'] == 'resource_limited'
    assert assess([sample(thermal_state=2)], now=BASE)[0]['state'] == 'resource_limited'
    # Multiple samples without a usable interval are a measurement fault, not warmup.
    assert assess([sample(), sample()], now=BASE)[0]['state'] == 'telemetry_unavailable'


def test_warning_pressure_keeps_one_lane_without_overdispatch():
    samples=[sample(0,memory_pressure=2),sample(5,memory_pressure=2)]
    result,state=assess(samples,active=0)
    assert result['allow_new'] and result['allowed_slots']==1
    assert result['state']=='reduced_capacity'
    for active in (1,3):
        result,_=assess(samples,active=active,state=state)
        assert not result['allow_new'] and result['allowed_slots']==1
    samples.append(sample(10,memory_pressure=4))
    result,_=assess(samples,active=0,state=state)
    assert not result['allow_new'] and result['allowed_slots']==0


@pytest.mark.parametrize('changes', [dict(swap_used_bytes=8*rc.MIB),dict(memory_headroom_proxy_bytes=3*rc.GIB),dict(thermal_state=2)])
def test_warning_pressure_does_not_override_independent_limits(changes):
    result,_=assess([sample(0,memory_pressure=2),sample(5,memory_pressure=2,**changes)],active=0)
    assert not result['allow_new'] and result['allowed_slots']==0
