"""Experimental caller-controlled handoff for private worker restart journals.

No runtime wiring or default restart action. The caller supplies a trusted,
bounded callback; observations cannot select code, commands or a service. This
shares the engine adapter's commit-before-callback and nonblocking OS lock.
Every writer for this scratch journal must cooperate through this adapter.
"""
from functools import partial
from hashlib import sha256
from inspect import (getattr_static, isasyncgenfunction, iscoroutinefunction,
                     isgeneratorfunction, signature)
import os
from pathlib import Path
import stat
from types import FunctionType, MethodType

from . import development_restart_dispatch as dispatch
from . import development_restart_journal as journal
from . import development_worker_health as worker_health


VERSION = "offline-worker-restart-dispatch-v1"


def _source_sha256(source):
    """Hash a stable regular source without following its final path component.

    Nonblocking open keeps a replaced FIFO from stalling supervision. Reading
    only the initial size plus one byte bounds work even if a file keeps growing;
    chunks bound memory. The controller still owns the parent directories and
    must prevent changes after validation, including in-memory code replacement.
    """
    path = Path(source)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Worker source is not a regular file")
        digest = sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                raise ValueError("Worker source truncated during validation")
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise ValueError("Worker source grew during validation")
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        identity = tuple(getattr(before, field) for field in fields)
        for after in (os.fstat(fd), path.lstat()):
            if (not stat.S_ISREG(after.st_mode)
                    or tuple(getattr(after, field) for field in fields) != identity):
                raise ValueError("Worker source changed during validation")
        return digest.hexdigest()
    finally:
        os.close(fd)


def _validate_source_pins(expected_source_sha256):
    """Compare adapter source bytes with a complete, caller-owned review pin.

    No pins are discovered or generated here. Paths come only from the loaded
    modules, never evidence or mapping keys. This checks files on disk, not
    in-memory code, review authority or release status. The controller must own
    a stable source tree and restart imports after installing reviewed bytes.
    """
    if expected_source_sha256 is None:
        return
    sources = {
        "development_worker_dispatch.py": __file__,
        "development_restart_dispatch.py": dispatch.__file__,
        "development_restart_journal.py": journal.__file__,
        "development_worker_health.py": worker_health.__file__,
    }
    if not isinstance(expected_source_sha256, dict):
        raise ValueError("Worker source pins must be a complete filename/SHA256 mapping")
    pins = dict(expected_source_sha256)
    if (pins.keys() != sources.keys()
            or any(not isinstance(value, str) or len(value) != 64
                   or any(char not in "0123456789abcdef" for char in value)
                   for value in pins.values())):
        raise ValueError("Worker source pins must contain all four exact filenames and SHA256 hashes")
    for name, source in sources.items():
        try:
            actual = _source_sha256(source)
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError("Worker source unavailable: " + name
                             + "; no journal access or restart was requested") from exc
        if actual != pins[name]:
            raise ValueError("Worker source drift: " + name + "; expected "
                             + pins[name] + "; observed " + actual
                             + "; no journal access or restart was requested")


def _synchronous_signature(function):
    # Validate the actual callable path before asking inspect to bind it.
    # follow_wrapped=False still trusts signature metadata, including that on
    # targets hidden behind partials and bound methods. Do not execute arbitrary
    # __call__ descriptors (notably partialmethod) to discover their targets.
    # Unsupported descriptors require a trusted ordinary synchronous wrapper.
    target = function
    seen = set()
    while True:
        if id(target) in seen:
            raise ValueError("cyclic worker callable")
        seen.add(id(target))
        if any(getattr_static(target, name, None) is not None
               for name in ("__signature__", "__text_signature__")):
            raise ValueError("worker dependencies must not advertise custom signatures")
        if type(target) is partial:
            target = target.func
        elif type(target) is MethodType:
            target = target.__func__
        elif type(target) is FunctionType:
            # Accessing a partialmethod on the class can create a plain
            # function wrapper. Its generic signature is not the target's.
            if any(getattr_static(target, name, None) is not None
                   for name in ("_partialmethod", "__partialmethod__")):
                raise ValueError("unsupported worker partialmethod wrapper")
            if any(check(target) for check in
                   (iscoroutinefunction, isgeneratorfunction, isasyncgenfunction)):
                raise ValueError("worker dependencies must return synchronously")
            break
        else:
            # inspect also recognizes duck-typed functions via __code__ and
            # related metadata, bypassing the ordinary __call__ signature.
            # Such metadata need not describe the method actually invoked.
            # Reject it statically (including descriptors) instead of spending
            # an intent/cooldown on an argument mismatch. A trusted explicit
            # Python wrapper remains available for these adapters.
            if getattr_static(target, "__code__", None) is not None:
                raise ValueError("worker callable objects must not advertise function metadata")
            call = getattr_static(type(target), "__call__", None)
            if not callable(target) or type(call) is not FunctionType:
                raise ValueError("unsupported worker callable descriptor")
            target = call
    # Bind the callable that will actually run. functools.wraps metadata can
    # advertise a compatible inner function while the invoked wrapper requires
    # different arguments. Following it could consume a restart/cooldown before
    # discovering the caller error, or find an unusable outcome writer only
    # after restart. Preserve partial arguments and bound-method handling.
    return signature(function, follow_wrapped=False)


def validate_worker_dependencies(*, expected_source_sha256=None):
    """Reject incomplete candidate integration without opening a journal.

    The retained F0038 release failed with an engine-only journal beside the
    worker adapter. Check the loaded interfaces, not a receipt or version label
    alone. This is a compatibility check, not source authentication or release
    approval; the controller must still test and review the complete source set.
    Deferred functions are incompatible with this synchronous protocol. This
    static check cannot prove what an arbitrary synchronous wrapper returns.

    Optional controller-owned source pins additionally reject missing/changed
    adapter source bytes, even when interfaces still bind. Omitting pins retains
    the legacy compatibility-only check; it never implies accepted-source parity.
    """
    _validate_source_pins(expected_source_sha256)
    try:
        if (getattr(journal, "WORKER_VERSION", None) != "offline-worker-restart-journal-v1"
                or getattr(journal, "SERVICES", None) !=
                frozenset({"learning", "development", "release"})
                or getattr(dispatch, "WORKER_VERSION", None) != VERSION
                or getattr(dispatch, "journal", None) is not journal):
            raise ValueError("worker journal/dispatch identity mismatch")
        # Binding exercises no function and creates no scratch files. Require
        # explicit worker parameters, so a generic **kwargs shim is insufficient.
        for function, parameter in ((journal.create_journal, "service"),
                                    (dispatch._dispatch_observation, "service"),
                                    (dispatch._service_manifest, "service")):
            if parameter not in _synchronous_signature(function).parameters:
                raise ValueError("missing explicit service parameter")
        _synchronous_signature(journal.create_journal).bind(
            None, checkpoint_at=None, created_at=None, service="learning")
        _synchronous_signature(dispatch._dispatch_observation).bind(
            None, None, None, service="learning", request_restart=None, clock=None)
        _synchronous_signature(dispatch._service_manifest).bind(None, "learning")
        # Validate the persistence/reporting boundary before any observation or
        # restart. The reservation writer must execute synchronously: a deferred
        # replacement returns without committing, allowing the callback to run
        # with no durable dispatch reservation. Its exact positional call shape
        # must also bind before an observation can consume a restart intent.
        # In particular, an incompatible outcome writer must not be
        # discovered only AFTER the controller has received a restart request.
        # Bind the exact call shapes without opening or mutating a journal.
        for function, arguments, keywords in (
                # This shared assertion helper enforces service identity,
                # journal invariants and the callback's None acknowledgement.
                # A deferred guard silently skips those checks and can turn
                # an invalid callback result into a succeeded request.
                (journal._check, (False, "worker preflight"), {}),
                (dispatch._reserve, (None, None, None), {}),
                # A deferred formatter would pass a coroutine to persistence
                # after the callback has already failed. Reject integration
                # drift before spending an intent or opening the journal.
                (dispatch._request_failure_text, (None,), {}),
                # Public writers may remain synchronous while their shared
                # primitives drift. A deferred checkpoint silently skips the
                # durable miss; a deferred append cannot commit the reservation.
                # Validate these actual bindings without executing a write.
                (journal._checkpoint, (None, None, None, None), {}),
                # Both the checkpoint writer and read-only reports use this
                # predicate. A deferred result is truthy without evaluating
                # the deadline and can durably invent an early checkpoint miss.
                # Check its actual call shape before any journal access.
                (journal._missed, (None, None, None), {}),
                (journal._append, (None, None, None, None, None), {}),
                # Every read/write relies on this synchronous clock guard.
                # A deferred replacement is silently ignored by its callers,
                # allowing an outcome to precede its committed reservation.
                (journal._chronology, (None, None, None), {}),
                # Observation commits before adding new_intent to this report.
                # A deferred builder can therefore spend an intent/cooldown
                # before its non-mapping result fails, with no callback delivery.
                # Validate the shared builder before opening the journal.
                (journal._report, (None, None, None), {}),
                (journal.inspect_journal, (None,), {"as_of": None}),
                (journal.record_observation, (None, None, None),
                 {"observed_at": None, "reserve_dispatch_capacity": True}),
                (journal.record_outcome, (None, None, None),
                 {"error": None, "observed_at": None}),
                (dispatch._dispatch_report, (None,), {})):
            _synchronous_signature(function).bind(*arguments, **keywords)
        # The journal imports these functions by value. A partial integration
        # or reload can leave old health logic bound in an otherwise compatible
        # journal. Check those actual bindings without evaluating observations.
        if worker_health.SERVICES != journal.SERVICES:
            raise ValueError("worker health service inventory mismatch")
        for name, arguments in (
                ("worker_disabled", ("learning", None)),
                ("worker_problem", ("learning", None, None)),
                ("worker_progress_after", ("learning", None, None, None))):
            function = getattr(worker_health, name)
            if getattr(journal, name) is not function:
                raise ValueError("worker health binding mismatch: " + name)
            if "service" not in _synchronous_signature(function).parameters:
                raise ValueError("missing explicit health service parameter: " + name)
            _synchronous_signature(function).bind(*arguments)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(
            "Incompatible worker restart dependencies: integrate and validate "
            "development_restart_journal, development_worker_health and "
            "development_restart_dispatch together; no restart was requested"
        ) from exc


def _validate_worker_callbacks(request_restart, clock):
    # Caller configuration is not a failed restart request. Check both call
    # shapes before recording even the first observation, so a known-invalid
    # callback cannot consume an intent or its cooldown. Never invoke either
    # function to probe it: a restart probe itself would be a side effect.
    for name, function, arguments in (
            ("request_restart", request_restart, ("learning", "intent")),
            ("clock", clock, ())):
        try:
            _synchronous_signature(function).bind(*arguments)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Invalid worker " + name + ": expected an inspectable synchronous "
                "callable accepting " + ("(service, intent_id)" if arguments else "()")
                + "; no observation or restart was requested"
            ) from exc


def dispatch_worker_observation(handle, health, *, service, request_restart,
                                clock=dispatch._clock, expected_source_sha256=None):
    """Deliver only a NEW committed intent for the explicitly selected worker.

    service must be learning, development or release and match the pinned
    journal before any lock/database write or callback. Engine journals and
    engine publication evidence cannot use this entry point. Disabled workers
    remain disabled under the journal's existing health policy.

    request_restart(service, intent_id) returns None for request acknowledgement,
    or raises Exception for failure. All other returns, control interruptions,
    persistence/clock failures and caller death retain uncertainty; reopening
    never replays them. All intents retain the original 900-second cooldown.
    A later healthy heartbeat proves only heartbeat progress, not resumed work,
    completed tests/review, release safety or production recovery.

    Ordinary request failures retain their exception type and a bounded fallback
    if message rendering fails. Interruptions during rendering remain uncertain;
    a stored failed request never certifies the service's actual state.

    Capacity for reservation, outcome and a possible elapsed checkpoint is
    checked in the observation transaction before committing a new intent.
    Insufficient capacity leaves prior observations, intents and cooldowns
    unchanged; read-only inspection still exposes the fixed checkpoint status.

    Both callbacks must expose synchronous, inspectable call signatures without
    custom __signature__ or __text_signature__ metadata. Callable objects need
    an ordinary Python __call__ method and must not advertise __code__ metadata;
    other descriptors or function-like adapters need a trusted wrapper.
    Known incompatible signatures and deferred functions fail before journal
    access, observations or cooldown consumption. This cannot establish the
    return semantics of arbitrary wrappers; runtime failures retain the above
    outcome/uncertainty rules. Uninspectable callables need a trusted explicit
    synchronous wrapper supplied by the controller.

    Callback timeouts belong to the caller: this function cannot terminate
    arbitrary Python safely. A blocked callback retains the lock until exit.
    This is a cooperating offline protocol, not an execution sandbox.

    expected_source_sha256 optionally supplies the controller's complete source
    pin mapping for the four adapter files. Drift fails before journal access or
    callbacks; no pin or release receipt is created, updated or accepted here.
    Pinned paths must be stable regular files, with no final-component symlink.
    """
    validate_worker_dependencies(expected_source_sha256=expected_source_sha256)
    journal._check(isinstance(service, str) and service in journal.SERVICES,
                   "Unknown worker dispatch service")
    _validate_worker_callbacks(request_restart, clock)
    return dispatch._dispatch_observation(handle, health, None, service=service,
                                          request_restart=request_restart, clock=clock)


def inspect_worker_dispatch(handle, *, service, as_of=None, expected_source_sha256=None):
    """Read-only uncertain/failed/succeeded delivery and fixed checkpoint status."""
    validate_worker_dependencies(expected_source_sha256=expected_source_sha256)
    journal._check(isinstance(service, str) and service in journal.SERVICES,
                   "Unknown worker dispatch service")
    dispatch._service_manifest(handle, service)
    return dispatch._dispatch_report(journal.inspect_journal(handle, as_of=as_of))
