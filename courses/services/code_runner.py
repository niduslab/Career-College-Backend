"""Docker-based code execution sandbox for coding-exercise submissions.

Ports the architecture described in docs/submission-flow.md with one material
change driven by docs/comparison.md §17: instead of running one container per
test case, we run ONE container per submission. The container's harness loops
over every test case internally, emitting per-test results between sentinel
markers. This eliminates (N-1) container startups (~300 ms each) and, for
C++/Java, (N-1) compile cycles per submission.

WARNING (echoed from CLAUDE.md): this runner is Docker-out-of-Docker; the
Docker daemon socket is shared with the host, so a sufficiently advanced
attacker can escape to the host daemon. Demo / single-tenant use only.

Public surface
--------------
    SingleTestResult      — frozen dataclass returned per test case
    DockerTransientError  — wrap-around for retryable docker.errors.APIError
    DockerUnavailableError — daemon unreachable; NOT retried by the task
    CodeRunner            — main entry point; .run_submission(...)

The Celery tasks in courses/tasks.py mock-patch this class in tests
(`patch('courses.services.code_runner.CodeRunner.run_submission')`).
"""

from __future__ import annotations

import logging
import re
import textwrap
import uuid
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses + exceptions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SingleTestResult:
    status: str            # 'passed' | 'failed' | 'error'
    actual_output: str
    stdout: str
    stderr: str
    runtime_ms: int
    exit_code: int


class DockerTransientError(Exception):
    """Wraps a Docker API error that's safe to retry (daemon timeout etc.)."""


class DockerUnavailableError(Exception):
    """Daemon is unreachable. Operator action — not auto-retried."""


# ---------------------------------------------------------------------------
# Language configuration
# ---------------------------------------------------------------------------

_SUPPORTED_LANGUAGES = {'python', 'javascript', 'cpp', 'java'}

# Per-test stdout/stderr cap inside a result row.
MAX_OUTPUT = 4000

# Parser for the sentinel emitted by every per-language harness.
_TEST_RESULT_RE = re.compile(
    rb'<<<TEST_RESULT idx=(\d+) status=(\w+) runtime_ms=(\d+) '
    rb'exit=(-?\d+) stdout_len=(\d+) stderr_len=(\d+)>>>\n'
)
_END_RE = re.compile(rb'<<<END idx=\d+>>>\n')


def _image_for(language: str) -> str:
    return {
        'python': settings.RUNNER_IMAGE_PYTHON,
        'javascript': settings.RUNNER_IMAGE_JAVASCRIPT,
        'cpp': settings.RUNNER_IMAGE_CPP,
        'java': settings.RUNNER_IMAGE_JAVA,
    }[language]


# ---------------------------------------------------------------------------
# Per-language harnesses. Each is concatenated to the learner's code; the
# combined text is base64-encoded and passed to the container via the CODE
# env var, then decoded + executed by the language-specific command.
#
# Convention learners must follow: define a `solve(input)` function/method
# that takes a single string and either returns the answer (preferred) or
# prints it. Multi-argument unpacking, if needed, is the learner's job.
#
# Each harness reads INPUT_COUNT + INPUT_{0..N-1} env vars, runs solve() per
# input with per-test exception handling, and emits one sentinel block per
# test on stdout. Format is length-prefixed (not base64-encoded) so binary
# stdout from the learner survives intact.
# ---------------------------------------------------------------------------

_PYTHON_HARNESS = textwrap.dedent('''

    # === harness start (injected) ===
    import os as _os, sys as _sys, io as _io, time as _time, traceback as _tb
    _count = int(_os.environ.get('INPUT_COUNT', '0'))
    _orig_out, _orig_err = _sys.stdout, _sys.stderr
    for _i in range(_count):
        _inp = _os.environ.get('INPUT_%d' % _i, '')
        _outbuf = _io.StringIO()
        _errbuf = _io.StringIO()
        _sys.stdout = _outbuf
        _sys.stderr = _errbuf
        _status = 'passed'
        _exit_code = 0
        _start = _time.perf_counter()
        try:
            _result = solve(_inp)
            if _result is not None:
                _outbuf.write(str(_result))
        except BaseException as _e:
            _status = 'error'
            _exit_code = 1
            _tb.print_exc(file=_errbuf)
        finally:
            _runtime_ms = int((_time.perf_counter() - _start) * 1000)
            _sys.stdout = _orig_out
            _sys.stderr = _orig_err
        _out_bytes = _outbuf.getvalue().encode('utf-8', errors='replace')
        _err_bytes = _errbuf.getvalue().encode('utf-8', errors='replace')
        _orig_out.write(
            '<<<TEST_RESULT idx=%d status=%s runtime_ms=%d exit=%d '
            'stdout_len=%d stderr_len=%d>>>\\n'
            % (_i, _status, _runtime_ms, _exit_code, len(_out_bytes), len(_err_bytes))
        )
        _orig_out.flush()
        _orig_out.buffer.write(_out_bytes)
        _orig_out.buffer.write(b'\\n')
        _orig_out.buffer.write(_err_bytes)
        _orig_out.buffer.write(b'\\n')
        _orig_out.write('<<<END idx=%d>>>\\n' % _i)
        _orig_out.flush()
''')


_JAVASCRIPT_HARNESS = textwrap.dedent('''

    // === harness start (injected) ===
    (async () => {
        const _count = parseInt(process.env.INPUT_COUNT || '0', 10);
        for (let _i = 0; _i < _count; _i++) {
            const _inp = process.env['INPUT_' + _i] || '';
            const _outChunks = [];
            const _errChunks = [];
            const _origOut = process.stdout.write.bind(process.stdout);
            const _origErr = process.stderr.write.bind(process.stderr);
            process.stdout.write = (data) => {
                _outChunks.push(Buffer.isBuffer(data) ? data : Buffer.from(String(data)));
                return true;
            };
            process.stderr.write = (data) => {
                _errChunks.push(Buffer.isBuffer(data) ? data : Buffer.from(String(data)));
                return true;
            };
            let _status = 'passed', _exit = 0;
            const _start = process.hrtime.bigint();
            try {
                const _r = await solve(_inp);
                if (_r !== undefined && _r !== null) {
                    _outChunks.push(Buffer.from(String(_r)));
                }
            } catch (e) {
                _status = 'error'; _exit = 1;
                _errChunks.push(Buffer.from(String(e && e.stack ? e.stack : e)));
            }
            const _runtime = Number((process.hrtime.bigint() - _start) / 1000000n);
            process.stdout.write = _origOut;
            process.stderr.write = _origErr;
            const _outBuf = Buffer.concat(_outChunks);
            const _errBuf = Buffer.concat(_errChunks);
            _origOut(
                '<<<TEST_RESULT idx=' + _i + ' status=' + _status +
                ' runtime_ms=' + _runtime + ' exit=' + _exit +
                ' stdout_len=' + _outBuf.length +
                ' stderr_len=' + _errBuf.length + '>>>\\n'
            );
            _origOut(_outBuf);
            _origOut('\\n');
            _origOut(_errBuf);
            _origOut('\\n');
            _origOut('<<<END idx=' + _i + '>>>\\n');
        }
    })();
''')


# C++ harness — prepended (includes + helpers) and appended (main()). The
# learner provides a function `void solve(const std::string& input)` (or
# equivalent that writes its answer to std::cout).
#
# We do NOT use <bits/stdc++.h>: pulling it under -O2 makes cc1plus exceed
# the 128 MB memory cap and the OOM-killer terminates the compile. Listing
# only the headers the harness actually uses keeps compile memory well under
# the cap; learners are free to add their own #include lines above solve().
_CPP_PROLOGUE = textwrap.dedent('''
    #include <iostream>
    #include <sstream>
    #include <string>
    #include <chrono>
    #include <cstdlib>
    #include <stdexcept>
    // === harness prologue ===
''')

_CPP_EPILOGUE = textwrap.dedent('''
    // === harness main (injected) ===
    int main() {
        const char* _count_s = std::getenv("INPUT_COUNT");
        int _count = _count_s ? std::atoi(_count_s) : 0;
        for (int _i = 0; _i < _count; _i++) {
            std::string _key = std::string("INPUT_") + std::to_string(_i);
            const char* _inp_c = std::getenv(_key.c_str());
            std::string _inp = _inp_c ? std::string(_inp_c) : "";
            std::stringstream _outbuf;
            std::stringstream _errbuf;
            std::streambuf* _oldOut = std::cout.rdbuf(_outbuf.rdbuf());
            std::streambuf* _oldErr = std::cerr.rdbuf(_errbuf.rdbuf());
            std::string _status = "passed";
            int _exit = 0;
            auto _start = std::chrono::steady_clock::now();
            try {
                solve(_inp);
            } catch (const std::exception& _e) {
                _status = "error"; _exit = 1;
                _errbuf << _e.what();
            } catch (...) {
                _status = "error"; _exit = 1;
                _errbuf << "unknown exception";
            }
            auto _end = std::chrono::steady_clock::now();
            long _runtime_ms = std::chrono::duration_cast<std::chrono::milliseconds>(_end - _start).count();
            std::cout.rdbuf(_oldOut);
            std::cerr.rdbuf(_oldErr);
            std::string _out_s = _outbuf.str();
            std::string _err_s = _errbuf.str();
            std::cout << "<<<TEST_RESULT idx=" << _i << " status=" << _status
                      << " runtime_ms=" << _runtime_ms << " exit=" << _exit
                      << " stdout_len=" << _out_s.size()
                      << " stderr_len=" << _err_s.size() << ">>>\\n";
            std::cout.write(_out_s.data(), _out_s.size());
            std::cout << "\\n";
            std::cout.write(_err_s.data(), _err_s.size());
            std::cout << "\\n";
            std::cout << "<<<END idx=" << _i << ">>>\\n";
            std::cout.flush();
        }
        return 0;
    }
''')


# Java harness — wraps the learner code inside a Solution class with a main()
# loop. Learner provides `static void solve(String input)`; multi-arg parsing
# is the learner's responsibility.
_JAVA_PROLOGUE = textwrap.dedent('''
    import java.io.*;
    import java.util.*;
    public class Solution {
''')

_JAVA_EPILOGUE = textwrap.dedent('''
        public static void main(String[] args) throws Exception {
            int _count = Integer.parseInt(System.getenv().getOrDefault("INPUT_COUNT", "0"));
            PrintStream _origOut = System.out;
            PrintStream _origErr = System.err;
            for (int _i = 0; _i < _count; _i++) {
                String _inp = System.getenv().getOrDefault("INPUT_" + _i, "");
                ByteArrayOutputStream _outBuf = new ByteArrayOutputStream();
                ByteArrayOutputStream _errBuf = new ByteArrayOutputStream();
                System.setOut(new PrintStream(_outBuf, true, "UTF-8"));
                System.setErr(new PrintStream(_errBuf, true, "UTF-8"));
                String _status = "passed";
                int _exit = 0;
                long _start = System.nanoTime();
                try {
                    solve(_inp);
                } catch (Throwable _t) {
                    _status = "error"; _exit = 1;
                    _t.printStackTrace(new PrintStream(_errBuf, true, "UTF-8"));
                }
                long _runtimeMs = (System.nanoTime() - _start) / 1_000_000L;
                System.setOut(_origOut);
                System.setErr(_origErr);
                byte[] _outBytes = _outBuf.toByteArray();
                byte[] _errBytes = _errBuf.toByteArray();
                _origOut.printf(
                    "<<<TEST_RESULT idx=%d status=%s runtime_ms=%d exit=%d "
                    + "stdout_len=%d stderr_len=%d>>>%n",
                    _i, _status, _runtimeMs, _exit, _outBytes.length, _errBytes.length
                );
                _origOut.write(_outBytes); _origOut.write('\\n');
                _origOut.write(_errBytes); _origOut.write('\\n');
                _origOut.printf("<<<END idx=%d>>>%n", _i);
                _origOut.flush();
            }
        }
    }
''')


def _assemble_source(language: str, learner_code: str) -> str:
    """Combine the learner's code with the per-language harness."""
    if language == 'python':
        return learner_code + '\n' + _PYTHON_HARNESS
    if language == 'javascript':
        return learner_code + '\n' + _JAVASCRIPT_HARNESS
    if language == 'cpp':
        return _CPP_PROLOGUE + '\n' + learner_code + '\n' + _CPP_EPILOGUE
    if language == 'java':
        return _JAVA_PROLOGUE + '\n' + learner_code + '\n' + _JAVA_EPILOGUE
    raise ValueError(f'Unsupported language: {language}')


def _command_for(language: str) -> list[str]:
    """Container command. CODE env var is the base64-encoded combined source.

    All four commands decode CODE -> a file or eval, then run. C++ and Java
    must compile inside the container's tmpfs (/tmp has 32 MB; gcc/javac fit).
    """
    if language == 'python':
        # `import base64,os; exec(base64.b64decode(os.environ["CODE"]).decode())`
        return [
            'python3', '-c',
            (
                'import base64, os; '
                'exec(compile(base64.b64decode(os.environ["CODE"]).decode(), '
                '"<learner>", "exec"))'
            ),
        ]
    if language == 'javascript':
        return [
            'node', '-e',
            (
                'eval(Buffer.from(process.env.CODE, "base64").toString())'
            ),
        ]
    if language == 'cpp':
        return [
            'sh', '-c',
            (
                'set -e; '
                'echo "$CODE" | base64 -d > /tmp/s.cpp && '
                'g++ -std=c++17 -O2 -pipe -o /tmp/s /tmp/s.cpp && '
                '/tmp/s'
            ),
        ]
    if language == 'java':
        return [
            'sh', '-c',
            (
                'set -e; '
                'mkdir -p /tmp/j && '
                'echo "$CODE" | base64 -d > /tmp/j/Solution.java && '
                'javac -d /tmp/j /tmp/j/Solution.java && '
                'cd /tmp/j && java Solution'
            ),
        ]
    raise ValueError(f'Unsupported language: {language}')


# ---------------------------------------------------------------------------
# Output normalization (mirrors docs/submission-flow.md "Output normalization")
# ---------------------------------------------------------------------------

_JSON_ISH = re.compile(r'^\s*[\[\{].*[\]\}]\s*$', re.DOTALL)


def _normalize(value: str) -> str:
    """Strip leading/trailing whitespace always; strip ALL whitespace ONLY
    when the value looks like a JSON array or object. Numbers and strings
    are otherwise compared literally."""
    if value is None:
        return ''
    stripped = value.strip()
    if _JSON_ISH.match(stripped):
        return re.sub(r'\s+', '', stripped)
    return stripped


# ---------------------------------------------------------------------------
# Sentinel parser
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _ParsedTest:
    idx: int
    harness_status: str   # 'passed' | 'error' (as reported by harness)
    runtime_ms: int
    exit_code: int
    actual_output: str
    stderr: str


def _parse_batch_output(stdout: bytes, n_expected: int) -> list[_ParsedTest | None]:
    """Walk the harness's sentinel stream and pull out per-test results.

    Returns a list of length n_expected. Missing entries (e.g. crash before
    a later test's sentinel) stay as None and are upgraded to 'error' by
    the caller.
    """
    results: list[_ParsedTest | None] = [None] * n_expected
    cursor = 0
    while cursor < len(stdout):
        m = _TEST_RESULT_RE.search(stdout, cursor)
        if not m:
            break
        idx = int(m.group(1))
        harness_status = m.group(2).decode()
        runtime_ms = int(m.group(3))
        exit_code = int(m.group(4))
        stdout_len = int(m.group(5))
        stderr_len = int(m.group(6))
        body_start = m.end()
        out_bytes = stdout[body_start:body_start + stdout_len]
        # +1 for the literal newline the harness emits after stdout bytes.
        err_start = body_start + stdout_len + 1
        err_bytes = stdout[err_start:err_start + stderr_len]
        cursor = err_start + stderr_len + 1
        end_m = _END_RE.match(stdout, cursor)
        if end_m:
            cursor = end_m.end()
        if 0 <= idx < n_expected:
            results[idx] = _ParsedTest(
                idx=idx,
                harness_status=harness_status,
                runtime_ms=runtime_ms,
                exit_code=exit_code,
                actual_output=out_bytes.decode('utf-8', errors='replace'),
                stderr=err_bytes.decode('utf-8', errors='replace'),
            )
    return results


# ---------------------------------------------------------------------------
# CodeRunner
# ---------------------------------------------------------------------------

class CodeRunner:
    """Execute a learner's code against a batch of test cases in ONE container.

    Public entry point: `run_submission(code, test_cases, time_limit_ms, language)`.
    """

    def run_submission(
        self,
        code: str,
        test_cases: list,
        time_limit_ms: int,
        language: str,
    ) -> list[SingleTestResult]:
        if language not in _SUPPORTED_LANGUAGES:
            raise ValueError(f'Unsupported language: {language}')

        if not test_cases:
            return []

        # Local import so the module can be imported in test envs that don't
        # have the docker SDK installed (tests mock run_submission directly).
        import base64

        try:
            import docker  # noqa: WPS433
            from docker.errors import (
                APIError as _DockerAPIError,
                ImageNotFound as _ImageNotFound,
            )
        except ImportError as exc:  # pragma: no cover
            raise DockerUnavailableError(
                'docker SDK not installed; cannot execute coding submissions.'
            ) from exc

        try:
            client = docker.from_env()
            client.ping()
        except Exception as exc:
            raise DockerUnavailableError(f'Docker daemon unreachable: {exc}') from exc

        source = _assemble_source(language, code)
        code_b64 = base64.b64encode(source.encode('utf-8')).decode('ascii')

        env = {'CODE': code_b64, 'INPUT_COUNT': str(len(test_cases))}
        for i, tc in enumerate(test_cases):
            env[f'INPUT_{i}'] = tc.input_data or ''

        image = _image_for(language)
        command = _command_for(language)
        # Per-test budget + overhead (startup + compile). One container runs
        # ALL tests, so total wall clock scales with N.
        timeout_s = max(
            5,
            int((time_limit_ms * len(test_cases)) / 1000) + 5,
        )

        container = None
        try:
            try:
                container = client.containers.run(
                    image=image,
                    command=command,
                    environment=env,
                    network_disabled=True,
                    mem_limit='128m',
                    memswap_limit='128m',
                    cpu_period=100_000,
                    cpu_quota=50_000,
                    read_only=True,
                    tmpfs={'/tmp': 'size=32m,exec'},
                    cap_drop=['ALL'],
                    security_opt=['no-new-privileges:true'],
                    detach=True,
                    remove=False,
                    name=f'cc-runner-{uuid.uuid4().hex[:12]}',
                )
            except _ImageNotFound:
                err = (
                    f'Runner image not found: {image}. '
                    f'Run `docker pull {image}` or set the RUNNER_IMAGE_* env var.'
                )
                return self._all_error(test_cases, err)
            except _DockerAPIError as exc:
                # Connection / timeout-style errors are retryable; others are
                # caller-fault and should not be retried.
                msg = str(exc)
                if 'connection' in msg.lower() or 'timeout' in msg.lower():
                    raise DockerTransientError(msg) from exc
                return self._all_error(test_cases, f'Docker error: {msg}')

            try:
                container.wait(timeout=timeout_s)
                timed_out = False
            except Exception:
                # docker.errors.ReadTimeout / requests timeouts — kill + mark timeout.
                try:
                    container.kill()
                except Exception:
                    pass
                timed_out = True

            try:
                raw_stdout = container.logs(stdout=True, stderr=False)
                raw_stderr = container.logs(stdout=False, stderr=True)
            except Exception as exc:
                logger.exception('Failed to read container logs: %s', exc)
                raw_stdout = b''
                raw_stderr = b''

            parsed = _parse_batch_output(raw_stdout, len(test_cases))
            return self._assemble_results(test_cases, parsed, raw_stderr, timed_out)
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    logger.exception('Failed to remove container; may need manual cleanup.')

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _all_error(self, test_cases: list, message: str) -> list[SingleTestResult]:
        return [
            SingleTestResult(
                status='error', actual_output='', stdout='',
                stderr=message[:MAX_OUTPUT], runtime_ms=0, exit_code=1,
            )
            for _ in test_cases
        ]

    def _assemble_results(
        self,
        test_cases: list,
        parsed: list[_ParsedTest | None],
        raw_stderr: bytes,
        timed_out: bool,
    ) -> list[SingleTestResult]:
        """Convert parsed harness output + expected outputs into final results.

        The harness reports `passed | error` (per-test exception capture);
        we resolve to `passed | failed | error` here by comparing
        normalized stdout against the test case's expected_output.
        """
        container_stderr_tail = raw_stderr.decode('utf-8', errors='replace')[-MAX_OUTPUT:]
        results: list[SingleTestResult] = []
        for i, tc in enumerate(test_cases):
            p = parsed[i]
            if p is None:
                # Container died before this test produced output (segfault,
                # OOM, or wall-clock timeout). Surface the tail of container
                # stderr for operator/learner debugging.
                msg = (
                    'Execution timed out.' if timed_out
                    else 'No output captured (container likely crashed).'
                )
                results.append(SingleTestResult(
                    status='error',
                    actual_output='',
                    stdout='',
                    stderr=(msg + '\n' + container_stderr_tail)[:MAX_OUTPUT],
                    runtime_ms=0,
                    exit_code=1,
                ))
                continue
            # Per-test exception in the harness — pre-empts equality check.
            if p.harness_status == 'error' or p.exit_code != 0:
                results.append(SingleTestResult(
                    status='error',
                    actual_output=p.actual_output[:MAX_OUTPUT],
                    stdout=p.actual_output[:MAX_OUTPUT],
                    stderr=p.stderr[:MAX_OUTPUT],
                    runtime_ms=p.runtime_ms,
                    exit_code=p.exit_code,
                ))
                continue
            # Equality check on normalized outputs.
            expected = tc.expected_output or ''
            passed = _normalize(p.actual_output) == _normalize(expected)
            results.append(SingleTestResult(
                status='passed' if passed else 'failed',
                actual_output=p.actual_output[:MAX_OUTPUT],
                stdout=p.actual_output[:MAX_OUTPUT],
                stderr=p.stderr[:MAX_OUTPUT],
                runtime_ms=p.runtime_ms,
                exit_code=p.exit_code,
            ))
        return results
