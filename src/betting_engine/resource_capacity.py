"""Read-only macOS telemetry and new-worker admission; never process control.

No inference of OOM from RAM 'used'. File-backed/free/purgeable bytes overlap;
max(), not their sum, is an explicitly approximate reclaimable-memory proxy.
OS memory pressure and swap growth remain separate mandatory signals. Thermal
state is NSProcessInfo's advisory, not a temperature measurement; Apple returns
nominal when the platform cannot report a thermal state.
"""
from __future__ import annotations

import ctypes
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

VERSION = 'macos-admission-v1'
GIB = 1024 ** 3
MIB = 1024 ** 2
SAMPLE_SECONDS = 5.0
STALE_SECONDS = 30.0
_LOCK = threading.Lock()
_STATES: dict[str, dict] = {}


def _iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _command(args):
    # Fixed native commands, no shell, credentials, sudo, or unbounded streaming.
    result = subprocess.run(args, capture_output=True, timeout=1, check=True,
                            env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL': 'C'})
    if len(result.stdout) > 32768:
        raise ValueError('native_output_too_large')
    return result.stdout.decode('utf-8', errors='strict')


def _cpu_ticks():
    # SDK mach/host_info.h HOST_CPU_LOAD_INFO=3, natural_t[CPU_STATE_MAX=4].
    lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
    lib.mach_host_self.restype = ctypes.c_uint
    lib.host_statistics.argtypes = [ctypes.c_uint, ctypes.c_int,
                                   ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
    lib.host_statistics.restype = ctypes.c_int
    lib.mach_port_deallocate.argtypes = [ctypes.c_uint, ctypes.c_uint]
    lib.mach_port_deallocate.restype = ctypes.c_int
    host = lib.mach_host_self()
    ticks, count = (ctypes.c_uint * 4)(), ctypes.c_uint(4)
    try:
        if lib.host_statistics(host, 3, ticks, ctypes.byref(count)) or count.value != 4:
            raise ValueError('host_cpu_statistics_unavailable')
        return list(ticks)
    finally:
        task = ctypes.c_uint.in_dll(lib, 'mach_task_self_').value
        lib.mach_port_deallocate(task, host)


def _thermal_state():
    # Foundation NSProcessInfo.h: 0 nominal, 1 fair, 2 serious, 3 critical.
    ctypes.CDLL('/System/Library/Frameworks/Foundation.framework/Foundation')
    objc = ctypes.CDLL('/usr/lib/libobjc.A.dylib')
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    send_object = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(('objc_msgSend', objc))
    send_integer = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(('objc_msgSend', objc))
    cls = objc.objc_getClass(b'NSProcessInfo')
    if not cls:
        raise ValueError('thermal_class_unavailable')
    instance = send_object(cls, objc.sel_registerName(b'processInfo'))
    if not instance:
        raise ValueError('thermal_instance_unavailable')
    value = send_integer(instance, objc.sel_registerName(b'thermalState'))
    if value not in (0, 1, 2, 3):
        raise ValueError('thermal_state_invalid')
    return value


def parse_vm_stat(text):
    match = re.search(r'page size of (\d+) bytes', text)
    if not match:
        raise ValueError('page_size_missing')
    page = int(match[1])
    if page not in (4096, 16384):
        raise ValueError('page_size_invalid')
    entries = dict(re.findall(r'^([^:\n]+):\s*(\d+)\.$', text, re.M))
    mapping = {'free_bytes': 'Pages free', 'file_backed_bytes': 'File-backed pages',
               'purgeable_bytes': 'Pages purgeable', 'wired_bytes': 'Pages wired down',
               'compressor_bytes': 'Pages occupied by compressor'}
    result = {name: int(entries[key]) * page for name, key in mapping.items()}
    # These categories are NOT independently additive, nor is cache guaranteed free.
    result['memory_headroom_proxy_bytes'] = max(result[k] for k in
                                              ('free_bytes', 'file_backed_bytes', 'purgeable_bytes'))
    result['page_size'] = page
    return result


def parse_sysctl(text):
    entries = dict(line.split(': ', 1) for line in text.splitlines() if ': ' in line)
    # XNU exports dispatch/NOTE_MEMORYSTATUS_PRESSURE bits, NOT its internal enum.
    result = {'cpu_count': int(entries['hw.ncpu']), 'memory_bytes': int(entries['hw.memsize']),
              'memory_pressure': int(entries['kern.memorystatus_vm_pressure_level'])}
    match = re.search(r'\bused\s*=\s*(\d+(?:\.\d+)?)([KMG])\b', entries['vm.swapusage'])
    if not match:
        raise ValueError('swap_usage_missing')
    result['swap_used_bytes'] = round(float(match[1]) * {'K': 1024, 'M': MIB, 'G': GIB}[match[2]])
    return result


def sample_native(root):
    """One bounded, read-only sample. Unavailable measurements are explicit."""
    raw = {'sample_at': _iso(time.time()), 'wall_time': time.time(),
           'monotonic': time.monotonic(), 'errors': [], 'source': 'native_macos_read_only'}
    if sys.platform != 'darwin':
        raw['errors'].append('unsupported_platform')
        return raw
    for name, read in (
        ('cpu', lambda: {'cpu_ticks': _cpu_ticks()}),
        ('sysctl', lambda: parse_sysctl(_command(['/usr/sbin/sysctl', 'hw.ncpu', 'hw.memsize',
                                                'kern.memorystatus_vm_pressure_level', 'vm.swapusage']))),
        ('vm_stat', lambda: parse_vm_stat(_command(['/usr/bin/vm_stat']))),
        ('thermal', lambda: {'thermal_state': _thermal_state()}),
        ('disk', lambda: {'disk_free_bytes': (lambda s: s.f_bavail * s.f_frsize)(os.statvfs(root))}),
    ):
        try:
            raw.update(read())
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            raw['errors'].append(name + ':' + type(exc).__name__)
    raw['sample_duration_seconds'] = time.monotonic() - raw['monotonic']
    return raw


def derive_metrics(samples):
    """Aggregate CPU utilization across all cores (0..100), not process %CPU."""
    latest = samples[-1] if samples else {}
    metrics = {k: v for k, v in latest.items() if k not in ('cpu_ticks', 'errors')}
    metrics.update(cpu_percent=None, cpu_rolling_percent=None, swap_growth_mib_per_min=None)
    cpu = []
    for previous, current in zip(samples, samples[1:]):
        elapsed = current.get('monotonic', 0) - previous.get('monotonic', 0)
        a, b = previous.get('cpu_ticks'), current.get('cpu_ticks')
        if not (1 <= elapsed <= STALE_SECONDS and isinstance(a, list) and isinstance(b, list)
                and len(a) == len(b) == 4 and all(type(n) is int and 0 <= n <= 0xffffffff for n in a + b)):
            cpu.append(None)
            continue
        delta = [(y - x) % (2 ** 32) for x, y in zip(a, b)]
        total = sum(delta)
        # Reject resets/implausible wrap, while allowing a genuine uint32 rollover.
        if not (0 < total <= elapsed * current.get('cpu_count', 0) * 10000):
            cpu.append(None)
            continue
        cpu.append((100 * (total - delta[2]) / total, elapsed))
    if cpu and cpu[-1] is not None:
        metrics['cpu_percent'] = cpu[-1][0]
        valid = [item for item in cpu[-3:] if item is not None]
        metrics['cpu_rolling_percent'] = sum(p * dt for p, dt in valid) / sum(dt for _, dt in valid)
    if len(samples) > 1:
        previous = samples[-2]
        elapsed = latest.get('monotonic', 0) - previous.get('monotonic', 0)
        old, new = previous.get('swap_used_bytes'), latest.get('swap_used_bytes')
        if 1 <= elapsed <= STALE_SECONDS and _number(old) and _number(new):
            metrics['swap_growth_mib_per_min'] = max(0, new - old) / MIB * 60 / elapsed
    metrics['memory_headroom_interpretation'] = 'max(free,file_backed,purgeable); approximate reclaimable proxy, not guaranteed allocation or RAM-used'
    metrics['thermal_interpretation'] = 'NSProcessInfo advisory; nominal can mean platform unsupported; not temperature'
    return metrics


def _config(config):
    adaptive = config.get('resource_capacity', {})
    if not isinstance(adaptive, dict):
        raise ValueError('resource_capacity_invalid')
    enabled = adaptive.get('enabled', False)
    if type(enabled) is not bool:
        raise ValueError('enabled_invalid')
    maximum = config.get('max_parallel', 3)
    if type(maximum) is not int or maximum < 1:
        raise ValueError('max_parallel_invalid')
    if not enabled:
        return {'enabled': False, 'slots': min(3, maximum)}
    defaults = {'cpu_soft_target': 75, 'cpu_high_water': 80, 'max_workers': 6,
                'memory_reserve_gib': 4, 'disk_reserve_gib': 20, 'swap_growth_mib_per_min': 64}
    parsed = {key: adaptive.get(key, default) for key, default in defaults.items()}
    if not all(_number(value) and value > 0 for value in parsed.values()):
        raise ValueError('capacity_threshold_invalid')
    if not (50 <= parsed['cpu_soft_target'] < parsed['cpu_high_water'] <= 80):
        raise ValueError('cpu_threshold_invalid')
    if type(parsed['max_workers']) is not int or not 1 <= parsed['max_workers'] <= 6:
        raise ValueError('max_workers_invalid')
    parsed.update(enabled=True, slots=min(maximum, parsed['max_workers'], 6))
    return parsed


def assess_capacity(samples, config, active_count, state=None, *, now=None):
    """Pure decision + returned hysteresis state; does not alter running work.

    Three distinct consecutive healthy samples open ONE additional slot. Repeated
    calls on one cached sample cannot create additional permits or advance recovery.
    """
    now = time.time() if now is None else now
    previous = dict(state or {})
    out = {'version': VERSION, 'checked_at': _iso(now), 'sample_at': None,
           'allow_new': False, 'allowed_slots': 0, 'configured_slots': 0,
           'state': 'telemetry_unavailable', 'reasons': [], 'metrics': {}}
    next_state = {'healthy_samples': 0, 'last_sample': None, 'allowed_slots': 0}
    try:
        cfg = _config(config)
        if type(active_count) is not int or not 0 <= active_count <= 6:
            raise ValueError('active_count_invalid')
    except (ValueError, TypeError, AttributeError) as exc:
        out.update(state='configuration_invalid', reasons=[str(exc)])
        return out, next_state
    out['configured_slots'] = cfg['slots']
    if not cfg['enabled']:
        out.update(state='disabled', allowed_slots=cfg['slots'], allow_new=active_count < cfg['slots'])
        return out, next_state
    if not samples:
        out['reasons'] = ['no_native_sample']
        return out, next_state
    latest = samples[-1]
    try:
        metrics = derive_metrics(samples)
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError):
        out['reasons'] = ['malformed_native_sample']
        return out, next_state
    out.update(sample_at=latest.get('sample_at'), metrics=metrics)
    stamp = latest.get('wall_time')
    ticks = latest.get('cpu_ticks')
    delta_keys = ('cpu_percent', 'cpu_rolling_percent', 'swap_growth_mib_per_min')
    warming_up = (len(samples) == 1 and _number(stamp) and 0 <= now-stamp <= 10
                  and isinstance(ticks, list) and len(ticks) == 4
                  and all(type(n) is int and 0 <= n <= 0xffffffff for n in ticks)
                  and all(metrics.get(k) is None for k in delta_keys))
    needed = ('cpu_count', 'memory_bytes', 'memory_pressure', 'swap_used_bytes',
              'memory_headroom_proxy_bytes', 'thermal_state', 'disk_free_bytes', 'monotonic')
    if not warming_up:
        needed += delta_keys
    reasons = list(latest.get('errors', []))
    if not _number(stamp) or not 0 <= now - stamp <= STALE_SECONDS:
        reasons.append('sample_stale_or_future')
    if any(not _number(metrics.get(k)) for k in needed):
        reasons.append('required_metric_unavailable')
    elif not ((warming_up or (0 <= metrics['cpu_percent'] <= 100 and 0 <= metrics['cpu_rolling_percent'] <= 100))
              and metrics['cpu_count'] > 0 and metrics['memory_bytes'] > 0
              and metrics['memory_pressure'] in (1, 2, 4) and metrics['thermal_state'] in (0, 1, 2, 3)
              and all(metrics[k] >= 0 for k in ('swap_used_bytes', 'memory_headroom_proxy_bytes', 'disk_free_bytes'))
              and (warming_up or metrics['swap_growth_mib_per_min'] >= 0)
              and metrics['memory_headroom_proxy_bytes'] <= metrics['memory_bytes']):
        reasons.append('required_metric_invalid')
    if reasons:
        out['reasons'] = reasons
        return out, next_state
    if not warming_up and metrics['cpu_percent'] >= cfg['cpu_high_water']:
        reasons.append('cpu_high_water')
    if not warming_up and metrics['cpu_rolling_percent'] >= cfg['cpu_soft_target']:
        reasons.append('cpu_soft_target')
    if metrics['memory_pressure'] != 1:
        reasons.append('memory_pressure')
    if metrics['memory_headroom_proxy_bytes'] < cfg['memory_reserve_gib'] * GIB:
        reasons.append('memory_reserve')
    if metrics['disk_free_bytes'] < cfg['disk_reserve_gib'] * GIB:
        reasons.append('disk_reserve')
    if not warming_up and metrics['swap_growth_mib_per_min'] >= cfg['swap_growth_mib_per_min']:
        reasons.append('swap_growth')
    if metrics['thermal_state'] >= 1:
        reasons.append('thermal_advisory')
    if reasons:
        out.update(state='resource_limited', reasons=reasons)
        return out, next_state
    if warming_up:
        out.update(state='warming_up', reasons=['first_native_delta_pending'])
        return out, next_state
    sample_id = latest.get('monotonic')
    healthy = previous.get('healthy_samples', 0)
    slots = min(cfg['slots'], max(0, previous.get('allowed_slots', 0)))
    if sample_id != previous.get('last_sample'):
        healthy += 1
        if healthy >= 3:
            slots = min(cfg['slots'], max(active_count, slots) + 1)
            healthy = 0
    next_state = {'healthy_samples': healthy, 'last_sample': sample_id, 'allowed_slots': slots}
    out.update(allowed_slots=slots, allow_new=active_count < slots,
               state='ready' if active_count < slots else 'recovering' if slots < cfg['slots'] else 'at_capacity',
               reasons=[] if active_count < slots else ['healthy_sample_hysteresis'] if slots < cfg['slots'] else ['configured_capacity'])
    return out, next_state


def evaluate_admission(root, runner_config, active_count):
    """Controller API; at most one native sample per root per five seconds.

    This process-local ring is intentionally reset conservatively on restart.
    Caller may persist the returned status; this module never writes files.
    """
    key = str(Path(root).resolve())
    with _LOCK:
        # One controller root normally. Bound accidental many-root calls too.
        if key not in _STATES and len(_STATES) >= 8:
            _STATES.pop(next(iter(_STATES)))
        record = _STATES.setdefault(key, {'samples': [], 'state': {}, 'config': None})
        try:
            cfg = _config(runner_config)
        except (ValueError, TypeError, AttributeError):
            return assess_capacity([], runner_config, active_count)[0]
        if not cfg['enabled']:
            record.update(samples=[], state={}, config=None)
            return assess_capacity([], runner_config, active_count)[0]
        if cfg != record['config']:
            record.update(samples=[], state={}, config=cfg)
        samples = record['samples']
        if not samples or time.monotonic() - samples[-1]['monotonic'] >= SAMPLE_SECONDS:
            samples.append(sample_native(key))
            del samples[:-7]
        decision, state = assess_capacity(samples, runner_config, active_count, record['state'])
        record['state'] = state
        return decision
