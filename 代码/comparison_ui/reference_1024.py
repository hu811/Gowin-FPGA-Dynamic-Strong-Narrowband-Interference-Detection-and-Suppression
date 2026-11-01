"""Mixed-input-only, frozen 1024-point numerical reference.

No UART, GUI, labels, reference target, or actual FPGA output enters this API.
Software FFT/coefficient preparation waiting is zero; Python wall time is not
FPGA latency. The existing latest-deferred-bank scheduler is retained.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal, localcontext
from functools import lru_cache
from pathlib import Path
import ast
import hashlib
import json
import sys
import types

import numpy as np

MAX_SAMPLES = 1048575
MIN_SAMPLES = 32


class ReferenceCancelled(RuntimeError):
    """The caller cancelled an unfinished reference computation."""


def _check_cancel(cancelled):
    if cancelled is not None and cancelled():
        raise ReferenceCancelled('1024 software reference cancelled')


def _report(progress, cancelled, stage, completed, total):
    _check_cancel(cancelled)
    if progress is not None:
        progress(stage, completed, total)
    _check_cancel(cancelled)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load_module(name, path):
    # These three numerical files have no import-path manipulation or writes.
    # Compile source directly to avoid creating __pycache__ in the model tree.
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(path.read_text(encoding='utf-8'), str(path), 'exec'), module.__dict__)
    return module


def _reuse_functions(name, path, names, namespace, classes=()):
    """Compile original function/class ASTs; do not transform arithmetic."""
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    selected = [node for node in tree.body
                if (isinstance(node, ast.FunctionDef) and node.name in names)
                or (isinstance(node, ast.ClassDef) and node.name in classes)]
    assert {node.name for node in selected} == set(names) | set(classes)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module.__dict__.update(namespace)
    # Dataclass decoration needs its defining module registered. Names are
    # private to this engine, so it does not replace host application modules.
    sys.modules[name] = module
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), module.__dict__)
    return module


@lru_cache(maxsize=2)
def _model(directory, manifest_sha):
    source = Path(directory)/'frozen_source'
    suffix = hashlib.sha256((str(directory)+manifest_sha).encode()).hexdigest()[:12]
    prefix = '_mixed1024_'+suffix
    fft = _load_module(prefix+'_fft', source/'fft_reference.py')
    gate = _load_module(prefix+'_gate', source/'gate_reference.py')
    aux = _load_module(prefix+'_aux', source/'aux_reference.py')
    # The original functions are reused, omitting only legacy sys.path edits
    # and unrelated scoring-program imports from their enclosing modules.
    tree = ast.parse((source/'fixed_baseline.py').read_text(encoding='utf-8'))
    names = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    detector = _reuse_functions(prefix+'_detector', source/'fixed_baseline.py', names,
        dict(np=np, asdict=asdict, dataclass=dataclass, Decimal=Decimal,
             localcontext=localcontext, lru_cache=lru_cache,
             fft_reference=fft, gate_reference=gate, aux_reference=aux), classes=['Config'])
    fir = _load_module(prefix+'_fir', source/'fir_replay.py')
    cosine = _reuse_functions(prefix+'_cosine', source/'fixed_builder.py', ['cosq'],
                             dict(COSINE=fir.COSINE)).cosq
    basis = _reuse_functions(prefix+'_basis', source/'basis_probe.py',
        ['basis_q16', 'response_q20', 'build_bank_basis'],
        dict(np=np, lru_cache=lru_cache, cosq=cosine, PROTOTYPE=fir.PROTOTYPE,
             round_div_even=fir.round_div_even, quantized_frequencies=fir.quantized_frequencies))
    q15 = _reuse_functions(prefix+'_q15', source/'evaluate_q15.py', ['response_q15', 'build_q15'],
        dict(np=np, lru_cache=lru_cache, basis_probe=basis, cosq=cosine, ONE=32768,
             round_div_even=fir.round_div_even, quantized_frequencies=fir.quantized_frequencies))
    return types.SimpleNamespace(detector=detector, fir=fir, q15=q15, source=source, prefix=prefix)


class _ConvolutionBoundary:
    """Add cancellation checks around unchanged NumPy convolution calls."""
    def __init__(self, cancelled):
        self.cancelled = cancelled

    def __getattr__(self, name):
        return getattr(np, name)

    def convolve(self, *args, **kwargs):
        _check_cancel(self.cancelled)
        result = np.convolve(*args, **kwargs)
        _check_cancel(self.cancelled)
        return result


def run_reference(data_int16, model_directory, progress=None, cancelled=None):
    """Run the frozen 1024/21dB reference on arbitrary mixed int16 samples.

    Parameters
    ----------
    data_int16 : one-dimensional numpy int16 array, 32..1048575 samples
        A private copy is made before processing, so caller changes cannot mix
        input versions during background computation. Sampling rate is 32kHz.
    model_directory : path
        Existing ``validation/recorded_1024_reference`` directory, containing
        ``source_manifest.json`` and the original ``frozen_source`` files.
    progress : callable(stage: str, completed: int, total: int), optional
        Called on the computing thread, not on the GUI thread. Stages are
        ``verify``, ``detect``, and ``filter``. Zero total means no observations.
    cancelled : callable() -> bool, optional
        Checked at start, each observation, phase boundaries, and each backend
        convolution boundary. Raises ReferenceCancelled; no partial success.

    Returns a dict with output(int16 array), stats, source_hashes, banks_q16,
    trajectory, decisions, and backend_diagnostics. It never writes files.
    For fewer than 1024 samples there is no detector observation; the same
    zero-state 641-tap backend executes the 320-sample delayed bypass.
    """
    _check_cancel(cancelled)
    value = np.asarray(data_int16)
    if value.ndim != 1 or value.dtype != np.dtype('int16'):
        raise TypeError('Expected a one-dimensional int16 mixed sample array')
    if not MIN_SAMPLES <= len(value) <= MAX_SAMPLES:
        raise ValueError(f'Expected {MIN_SAMPLES}..{MAX_SAMPLES} samples')
    samples = np.array(value, dtype=np.int16, copy=True, order='C')
    samples.setflags(write=False)
    directory = Path(model_directory).resolve()
    _report(progress, cancelled, 'verify', 0, 1)
    manifest_file = directory/'source_manifest.json'
    manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
    source_hashes = {}
    for name, record in manifest['files'].items():
        _check_cancel(cancelled)
        path = (directory/'frozen_source'/name).resolve()
        if not path.is_relative_to(directory/'frozen_source'):
            raise ValueError('Frozen source path escapes model directory')
        actual = _sha(path)
        if actual != record['sha256']:
            raise ValueError('Frozen numerical source changed: '+name)
        source_hashes[name] = actual
    manifest_sha = _sha(manifest_file)
    source_hashes['source_manifest.json'] = manifest_sha
    model = _model(str(directory), manifest_sha)
    _report(progress, cancelled, 'verify', 1, 1)
    detector = model.detector
    cfg = detector.Config(max_centers=4, threshold_db=21)
    ends = range(1024, len(samples)+1, 64)
    total = len(ends)
    extracted = []
    _report(progress, cancelled, 'detect', 0, total)
    for observation, end in enumerate(ends):
        _check_cancel(cancelled)
        # Call the unmodified extractor on exactly one complete window.
        record, = detector.extract_observations(samples[end-1024:end], cfg.estimator)
        record.update(observation=observation, window_start_sample=end-1024, window_end_sample=end)
        extracted.append(record)
        _report(progress, cancelled, 'detect', observation+1, total)
    decisions = detector.detect_updates(samples, cfg, extracted)
    _report(progress, cancelled, 'filter', 0, len(samples))
    # A private function namespace makes cancellation state local to this run.
    replay = _reuse_functions(model.prefix+'_replay', model.source/'run_comparison.py', ['replay'],
        dict(np=_ConvolutionBoundary(cancelled), actual=model.q15,
             bench=types.SimpleNamespace(fir_replay=model.fir))).replay
    result, details = replay(samples.astype(float)[None, :], samples, decisions)
    _report(progress, cancelled, 'filter', len(samples), len(samples))
    return dict(output=result['output'], stats={
        **decisions['stats'], 'sample_count': len(samples), 'sample_rate_hz': 32000,
        'window_samples': 1024, 'hop_samples': 64, 'threshold_db': 21,
        'max_centers': 4, 'filter_taps': 641, 'coefficient_fraction_bits': 16,
        'fade_samples': 32, 'alignment_samples': 320,
        'coefficient_guard_failures': len(details['guard_failures']),
        'output_saturations': result['saturations'],
        'frontend_compute_waiting_samples': 0, 'coefficient_prepare_waiting_samples': 0,
        'layer': 'Finite-arithmetic software reference; not FPGA timing or board output',
        'input_information': 'Mixed samples only; no reference, labels, or actual output supplied',
    }, source_hashes=source_hashes, banks_q16=result['banks'], trajectory=result['trajectory'],
       decisions=decisions, backend_diagnostics=details)
