"""Docker-based code execution sandbox for coding-exercise submissions.

Script-based evaluation (Udemy-style): the instructor authors an evaluation
script (`CodingExercise.evaluation_script`) that imports/calls the learner's
code and asserts on it. One container runs the
whole suite; an injected per-language micro-harness executes each test and
emits one sentinel block per test on stdout. There are no I/O test-case
pairs and no expected-output string comparison.

The micro-harnesses are zero-dependency by design (Python `unittest`, Node
`assert` + a tiny `test()` registry, Java reflection over `test*` methods,
a ~100-line C++ `TEST()` macro header) so the runner images need nothing
beyond the base language toolchain.

WARNING (echoed from CLAUDE.md): this runner is Docker-out-of-Docker; the
Docker daemon socket is shared with the host, so a sufficiently advanced
attacker can escape to the host daemon. Demo / single-tenant use only.

Public surface
--------------
    ScriptTestResult      — frozen dataclass returned per test
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
class ScriptTestResult:
    test_name: str
    status: str      # 'passed' | 'failed' | 'error'
    stdout: str      # print output captured while the test ran
    stderr: str      # assertion-failure message / traceback (learner-visible)
    runtime_ms: int


class DockerTransientError(Exception):
    """Wraps a Docker API error that's safe to retry (daemon timeout etc.)."""


class DockerUnavailableError(Exception):
    """Daemon is unreachable. Operator action — not auto-retried."""


# ---------------------------------------------------------------------------
# Language configuration
# ---------------------------------------------------------------------------

_SUPPORTED_LANGUAGES = {'python', 'javascript', 'cpp', 'java'}

# Per-test stdout/stderr cap inside a result row (also enforced in-harness).
MAX_OUTPUT = 4000

# Parsers for the sentinel emitted by every per-language harness. All body
# segments (name / stdout / stderr) are length-prefixed so arbitrary bytes —
# dots and spaces in unittest ids, multi-line tracebacks, binary output —
# survive intact.
_SCRIPT_RESULT_RE = re.compile(
    rb'<<<SCRIPT_RESULT idx=(\d+) status=(\w+) runtime_ms=(\d+) '
    rb'name_len=(\d+) stdout_len=(\d+) stderr_len=(\d+)>>>\n'
)
_SCRIPT_END_RE = re.compile(rb'<<<SCRIPT_END idx=\d+>>>\n')


def _image_for(language: str) -> str:
    return {
        'python': settings.RUNNER_IMAGE_PYTHON,
        'javascript': settings.RUNNER_IMAGE_JAVASCRIPT,
        'cpp': settings.RUNNER_IMAGE_CPP,
        'java': settings.RUNNER_IMAGE_JAVA,
    }[language]


# ---------------------------------------------------------------------------
# Per-language micro-harnesses.
#
# Contract shared by all four:
#   - env CODE  = base64 learner code   -> written to the learner file
#   - env EVAL  = base64 instructor evaluation script -> the test file
#   - env RUNNER (and TESTKIT for C++) carry the injected harness itself
#   - the harness runs every test, capturing per-test stdout/stderr, and
#     emits one sentinel block per test:
#         <<<SCRIPT_RESULT idx=N status=S runtime_ms=R name_len=A
#            stdout_len=B stderr_len=C>>>\n
#         <name bytes>\n<stdout bytes>\n<stderr bytes>\n
#         <<<SCRIPT_END idx=N>>>\n
#   - assertion failure -> status 'failed'; any other exception -> 'error'
#   - a load/compile crash before any test runs emits a single
#     'evaluate (load)' error block (or, for compiled languages, exits
#     before emitting anything — the Django side then synthesizes one).
# ---------------------------------------------------------------------------

# Python: learner file importable as `exercise`; evaluate.py is a stdlib
# unittest module (`from exercise import ...`).
_PYTHON_RUNNER = textwrap.dedent('''
    import base64, io, os, sys, time, traceback, unittest

    os.makedirs('/tmp/work', exist_ok=True)
    with open('/tmp/work/exercise.py', 'wb') as _f:
        _f.write(base64.b64decode(os.environ['CODE']))
    with open('/tmp/work/evaluate.py', 'wb') as _f:
        _f.write(base64.b64decode(os.environ['EVAL']))
    sys.path.insert(0, '/tmp/work')

    _CAP = 4000
    _orig_out = sys.stdout


    def _emit(idx, name, status, runtime_ms, out_text, err_text):
        # Everything goes through the raw byte buffer: the text layer would
        # translate \\n on some platforms and desync the length-prefixed
        # protocol.
        name_b = str(name).encode('utf-8', errors='replace')[:255]
        out_b = str(out_text).encode('utf-8', errors='replace')[:_CAP]
        err_b = str(err_text).encode('utf-8', errors='replace')[:_CAP]
        _orig_out.flush()
        buf = _orig_out.buffer
        buf.write(
            (
                '<<<SCRIPT_RESULT idx=%d status=%s runtime_ms=%d '
                'name_len=%d stdout_len=%d stderr_len=%d>>>\\n'
                % (idx, status, runtime_ms, len(name_b), len(out_b), len(err_b))
            ).encode('ascii')
        )
        buf.write(name_b)
        buf.write(b'\\n')
        buf.write(out_b)
        buf.write(b'\\n')
        buf.write(err_b)
        buf.write(b'\\n')
        buf.write(('<<<SCRIPT_END idx=%d>>>\\n' % idx).encode('ascii'))
        buf.flush()


    class _SentinelResult(unittest.TestResult):
        def __init__(self):
            super().__init__()
            self._idx = 0

        def startTest(self, test):
            super().startTest(test)
            self._start = time.perf_counter()
            self._status = 'passed'
            self._err_text = ''
            self._saved = (sys.stdout, sys.stderr)
            self._buf_out = io.StringIO()
            self._buf_err = io.StringIO()
            sys.stdout, sys.stderr = self._buf_out, self._buf_err

        def addFailure(self, test, err):
            super().addFailure(test, err)
            self._status = 'failed'
            self._err_text = ''.join(traceback.format_exception(*err))

        def addError(self, test, err):
            super().addError(test, err)
            self._status = 'error'
            self._err_text = ''.join(traceback.format_exception(*err))

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            self._status = 'passed'
            self._err_text = 'skipped: %s' % reason

        def stopTest(self, test):
            sys.stdout, sys.stderr = self._saved
            runtime_ms = int((time.perf_counter() - self._start) * 1000)
            err_text = self._err_text or self._buf_err.getvalue()
            _emit(self._idx, test.id(), self._status, runtime_ms,
                  self._buf_out.getvalue(), err_text)
            self._idx += 1
            super().stopTest(test)


    try:
        _suite = unittest.defaultTestLoader.loadTestsFromName('evaluate')
    except BaseException:
        _emit(0, 'evaluate (load)', 'error', 0, '', traceback.format_exc())
        sys.exit(1)

    if _suite.countTestCases() == 0:
        _emit(0, 'evaluate (load)', 'error', 0, '',
              'No tests found in the evaluation script.')
        sys.exit(1)

    _suite.run(_SentinelResult())
''')


# JavaScript: learner file at /tmp/work/exercise.js (CommonJS exports);
# evaluate.js does `const ex = require('./exercise')` and registers tests
# via the injected global `test(name, fn)` (async fns supported), asserting
# with `require('node:assert')`.
_JAVASCRIPT_RUNNER = textwrap.dedent('''
    const fs = require('fs');
    const assert = require('assert');

    fs.mkdirSync('/tmp/work', { recursive: true });
    fs.writeFileSync('/tmp/work/exercise.js', Buffer.from(process.env.CODE, 'base64'));
    fs.writeFileSync('/tmp/work/evaluate.js', Buffer.from(process.env.EVAL, 'base64'));

    const CAP = 4000;
    const origOut = process.stdout.write.bind(process.stdout);
    const origErr = process.stderr.write.bind(process.stderr);

    function emit(idx, name, status, runtimeMs, out, err) {
        const nameB = Buffer.from(String(name)).slice(0, 255);
        const outB = Buffer.from(String(out)).slice(0, CAP);
        const errB = Buffer.from(String(err)).slice(0, CAP);
        origOut(
            '<<<SCRIPT_RESULT idx=' + idx + ' status=' + status +
            ' runtime_ms=' + runtimeMs + ' name_len=' + nameB.length +
            ' stdout_len=' + outB.length + ' stderr_len=' + errB.length + '>>>\\n'
        );
        origOut(nameB); origOut('\\n');
        origOut(outB); origOut('\\n');
        origOut(errB); origOut('\\n');
        origOut('<<<SCRIPT_END idx=' + idx + '>>>\\n');
    }

    const tests = [];
    global.test = (name, fn) => { tests.push([String(name), fn]); };

    (async () => {
        try {
            require('/tmp/work/evaluate.js');
        } catch (e) {
            emit(0, 'evaluate (load)', 'error', 0, '',
                 e && e.stack ? e.stack : String(e));
            process.exit(1);
        }
        if (tests.length === 0) {
            emit(0, 'evaluate (load)', 'error', 0, '',
                 'No tests registered — the evaluation script must call test(name, fn).');
            process.exit(1);
        }
        let idx = 0;
        for (const [name, fn] of tests) {
            const outChunks = [];
            const errChunks = [];
            process.stdout.write = (d) => {
                outChunks.push(Buffer.isBuffer(d) ? d : Buffer.from(String(d)));
                return true;
            };
            process.stderr.write = (d) => {
                errChunks.push(Buffer.isBuffer(d) ? d : Buffer.from(String(d)));
                return true;
            };
            let status = 'passed';
            let errText = '';
            const start = process.hrtime.bigint();
            try {
                await fn();
            } catch (e) {
                status = (e instanceof assert.AssertionError) ? 'failed' : 'error';
                errText = e && e.stack ? e.stack : String(e);
            }
            const runtime = Number((process.hrtime.bigint() - start) / 1000000n);
            process.stdout.write = origOut;
            process.stderr.write = origErr;
            emit(idx, name, status, runtime,
                 Buffer.concat(outChunks).toString(),
                 errText || Buffer.concat(errChunks).toString());
            idx++;
        }
    })();
''')


# Java: learner file is `public class Exercise` (static methods); the
# evaluation script is `public class Evaluate` whose public no-arg `test*`
# methods each form one test — fail by throwing AssertionError (plain
# `if (...) throw new AssertionError("...")`). Tests run in NAME order
# (reflection does not preserve declaration order).
_JAVA_RUNNER = textwrap.dedent('''
    import java.io.*;
    import java.lang.reflect.*;
    import java.util.*;

    public class Runner {
        static PrintStream origOut;
        static final int CAP = 4000;

        static byte[] cap(byte[] b, int max) {
            return b.length > max ? Arrays.copyOf(b, max) : b;
        }

        static void emit(int idx, String name, String status, long runtimeMs,
                         byte[] out, byte[] err) throws IOException {
            byte[] nameB = cap(name.getBytes("UTF-8"), 255);
            out = cap(out, CAP);
            err = cap(err, CAP);
            // \\n literals (not %n) so the byte protocol is OS-independent.
            origOut.printf(
                "<<<SCRIPT_RESULT idx=%d status=%s runtime_ms=%d "
                + "name_len=%d stdout_len=%d stderr_len=%d>>>\\n",
                idx, status, runtimeMs, nameB.length, out.length, err.length);
            origOut.write(nameB); origOut.write('\\n');
            origOut.write(out); origOut.write('\\n');
            origOut.write(err); origOut.write('\\n');
            origOut.printf("<<<SCRIPT_END idx=%d>>>\\n", idx);
            origOut.flush();
        }

        static byte[] stackTrace(Throwable t) throws IOException {
            ByteArrayOutputStream b = new ByteArrayOutputStream();
            t.printStackTrace(new PrintStream(b, true, "UTF-8"));
            return b.toByteArray();
        }

        public static void main(String[] args) throws Exception {
            origOut = System.out;
            PrintStream origErrStream = System.err;
            Object evalObj;
            List<Method> tests = new ArrayList<>();
            try {
                Class<?> cls = Class.forName("Evaluate");
                evalObj = cls.getDeclaredConstructor().newInstance();
                for (Method m : cls.getDeclaredMethods()) {
                    if (m.getName().startsWith("test")
                            && m.getParameterCount() == 0
                            && Modifier.isPublic(m.getModifiers())) {
                        tests.add(m);
                    }
                }
            } catch (Throwable t) {
                emit(0, "evaluate (load)", "error", 0, new byte[0], stackTrace(t));
                System.exit(1);
                return;
            }
            tests.sort(Comparator.comparing(Method::getName));
            if (tests.isEmpty()) {
                emit(0, "evaluate (load)", "error", 0, new byte[0],
                     "No test methods (public void test*()) found in the evaluation script."
                         .getBytes("UTF-8"));
                System.exit(1);
            }
            int idx = 0;
            for (Method m : tests) {
                ByteArrayOutputStream outBuf = new ByteArrayOutputStream();
                ByteArrayOutputStream errBuf = new ByteArrayOutputStream();
                System.setOut(new PrintStream(outBuf, true, "UTF-8"));
                System.setErr(new PrintStream(errBuf, true, "UTF-8"));
                String status = "passed";
                byte[] errBytes = new byte[0];
                long start = System.nanoTime();
                try {
                    m.invoke(evalObj);
                    errBytes = errBuf.toByteArray();
                } catch (InvocationTargetException ite) {
                    Throwable cause = ite.getCause() == null ? ite : ite.getCause();
                    status = (cause instanceof AssertionError) ? "failed" : "error";
                    errBytes = stackTrace(cause);
                } catch (Throwable t) {
                    status = "error";
                    errBytes = stackTrace(t);
                }
                long runtimeMs = (System.nanoTime() - start) / 1_000_000L;
                System.setOut(origOut);
                System.setErr(origErrStream);
                emit(idx, "Evaluate." + m.getName(), status, runtimeMs,
                     outBuf.toByteArray(), errBytes);
                idx++;
            }
        }
    }
''')


# C++: learner code is written verbatim to exercise.h; evaluate.cpp does
# `#include "exercise.h"` + `#include "testkit.h"` and declares tests with
# TEST(name) { ASSERT_EQ(...); }. testkit.h + main.cpp are injected.
# No <bits/stdc++.h> anywhere — pulling it under -O2 blows the 128 MB cap.
_CPP_TESTKIT = textwrap.dedent('''
    #pragma once
    #include <functional>
    #include <ostream>
    #include <sstream>
    #include <string>
    #include <type_traits>
    #include <utility>
    #include <vector>

    namespace testkit {

    struct TestFailure {
        std::string message;
        explicit TestFailure(std::string m) : message(std::move(m)) {}
    };

    struct TestCase {
        std::string name;
        std::function<void()> fn;
    };

    inline std::vector<TestCase>& registry() {
        static std::vector<TestCase> r;
        return r;
    }

    struct Registrar {
        Registrar(const std::string& name, std::function<void()> fn) {
            registry().push_back({name, std::move(fn)});
        }
    };

    template <typename T, typename = void>
    struct is_streamable : std::false_type {};
    template <typename T>
    struct is_streamable<
        T, std::void_t<decltype(std::declval<std::ostream&>() << std::declval<const T&>())>>
        : std::true_type {};

    template <typename T>
    std::string describe(const T& v) {
        if constexpr (is_streamable<T>::value) {
            std::ostringstream o;
            o << v;
            return o.str();
        } else {
            return "<unprintable>";
        }
    }

    }  // namespace testkit

    #define TEST(name) \\
        static void _testkit_fn_##name(); \\
        static ::testkit::Registrar _testkit_reg_##name(#name, _testkit_fn_##name); \\
        static void _testkit_fn_##name()

    #define TESTKIT_FAIL(msg) throw ::testkit::TestFailure(msg)

    #define ASSERT_TRUE(cond) \\
        do { \\
            if (!(cond)) { \\
                std::ostringstream _o; \\
                _o << "ASSERT_TRUE failed: " #cond " (line " << __LINE__ << ")"; \\
                TESTKIT_FAIL(_o.str()); \\
            } \\
        } while (0)

    #define ASSERT_FALSE(cond) \\
        do { \\
            if ((cond)) { \\
                std::ostringstream _o; \\
                _o << "ASSERT_FALSE failed: " #cond " (line " << __LINE__ << ")"; \\
                TESTKIT_FAIL(_o.str()); \\
            } \\
        } while (0)

    #define ASSERT_EQ(a, b) \\
        do { \\
            const auto& _va = (a); \\
            const auto& _vb = (b); \\
            if (!(_va == _vb)) { \\
                std::ostringstream _o; \\
                _o << "ASSERT_EQ failed: " #a " == " #b " (got " \\
                   << ::testkit::describe(_va) << " vs " << ::testkit::describe(_vb) \\
                   << ", line " << __LINE__ << ")"; \\
                TESTKIT_FAIL(_o.str()); \\
            } \\
        } while (0)

    #define ASSERT_NE(a, b) \\
        do { \\
            const auto& _va = (a); \\
            const auto& _vb = (b); \\
            if (_va == _vb) { \\
                std::ostringstream _o; \\
                _o << "ASSERT_NE failed: " #a " != " #b " (both " \\
                   << ::testkit::describe(_va) << ", line " << __LINE__ << ")"; \\
                TESTKIT_FAIL(_o.str()); \\
            } \\
        } while (0)
''')

_CPP_RUNNER = textwrap.dedent('''
    #include "testkit.h"
    #include <chrono>
    #include <iostream>
    #include <sstream>
    #include <string>

    static const size_t CAP = 4000;

    static void emit(int idx, std::string name, const std::string& status,
                     long runtime_ms, std::string out, std::string err) {
        if (name.size() > 255) name.resize(255);
        if (out.size() > CAP) out.resize(CAP);
        if (err.size() > CAP) err.resize(CAP);
        std::cout << "<<<SCRIPT_RESULT idx=" << idx << " status=" << status
                  << " runtime_ms=" << runtime_ms
                  << " name_len=" << name.size()
                  << " stdout_len=" << out.size()
                  << " stderr_len=" << err.size() << ">>>\\n";
        std::cout.write(name.data(), name.size());
        std::cout << "\\n";
        std::cout.write(out.data(), out.size());
        std::cout << "\\n";
        std::cout.write(err.data(), err.size());
        std::cout << "\\n";
        std::cout << "<<<SCRIPT_END idx=" << idx << ">>>\\n";
        std::cout.flush();
    }

    int main() {
        auto& tests = ::testkit::registry();
        if (tests.empty()) {
            emit(0, "evaluate (load)", "error", 0, "",
                 "No TEST(...) cases found in the evaluation script.");
            return 1;
        }
        int idx = 0;
        for (auto& t : tests) {
            std::stringstream outbuf;
            std::stringstream errbuf;
            std::streambuf* oldOut = std::cout.rdbuf(outbuf.rdbuf());
            std::streambuf* oldErr = std::cerr.rdbuf(errbuf.rdbuf());
            std::string status = "passed";
            std::string errText;
            auto start = std::chrono::steady_clock::now();
            try {
                t.fn();
            } catch (const ::testkit::TestFailure& f) {
                status = "failed";
                errText = f.message;
            } catch (const std::exception& e) {
                status = "error";
                errText = e.what();
            } catch (...) {
                status = "error";
                errText = "unknown exception";
            }
            auto end = std::chrono::steady_clock::now();
            long runtime_ms =
                std::chrono::duration_cast<std::chrono::milliseconds>(end - start).count();
            std::cout.rdbuf(oldOut);
            std::cerr.rdbuf(oldErr);
            emit(idx, t.name, status, runtime_ms, outbuf.str(),
                 errText.empty() ? errbuf.str() : errText);
            idx++;
        }
        return 0;
    }
''')


# Synthetic one-test evaluation scripts used by the instructor "Run code"
# action: execute/compile the code standalone (no real tests) and surface its
# stdout/stderr. For python/js the module's top-level code runs inside the
# test so prints are captured; for java it invokes Exercise.main via
# reflection when present; for cpp it is effectively a compile check.
SMOKE_EVALUATION_SCRIPTS = {
    'python': textwrap.dedent('''
        import unittest

        class RunCode(unittest.TestCase):
            def test_run_code(self):
                import exercise  # noqa: F401 — executes the file; output is captured
    '''),
    'javascript': textwrap.dedent('''
        test('run code', () => { require('./exercise'); });
    '''),
    'java': textwrap.dedent('''
        public class Evaluate {
            public void testRunCode() throws Exception {
                try {
                    Exercise.class
                        .getDeclaredMethod("main", String[].class)
                        .invoke(null, (Object) new String[0]);
                } catch (NoSuchMethodException e) {
                    System.out.println("No main(String[]) found; compile check passed.");
                }
            }
        }
    '''),
    'cpp': textwrap.dedent('''
        #include "exercise.h"
        #include "testkit.h"

        TEST(run_code) {
            // Compile check: exercise.h compiled successfully.
        }
    '''),
}


def _env_for(language: str, code_b64: str, eval_b64: str) -> dict:
    """Build the container env: learner code + evaluation script + harness."""
    import base64

    def b64(text: str) -> str:
        return base64.b64encode(text.encode('utf-8')).decode('ascii')

    env = {'CODE': code_b64, 'EVAL': eval_b64}
    if language == 'python':
        env['RUNNER'] = b64(_PYTHON_RUNNER)
    elif language == 'javascript':
        env['RUNNER'] = b64(_JAVASCRIPT_RUNNER)
    elif language == 'java':
        env['RUNNER'] = b64(_JAVA_RUNNER)
    elif language == 'cpp':
        env['RUNNER'] = b64(_CPP_RUNNER)
        env['TESTKIT'] = b64(_CPP_TESTKIT)
    return env


def _command_for(language: str) -> list[str]:
    """Container command: decode env vars to files in /tmp/work, compile if
    needed, run the harness. C++/Java compile inside the container's tmpfs
    (/tmp has 32 MB; gcc/javac fit)."""
    if language == 'python':
        return [
            'python3', '-c',
            (
                'import base64, os; '
                'exec(compile(base64.b64decode(os.environ["RUNNER"]).decode(), '
                '"<runner>", "exec"))'
            ),
        ]
    if language == 'javascript':
        return [
            'node', '-e',
            'eval(Buffer.from(process.env.RUNNER, "base64").toString())',
        ]
    if language == 'cpp':
        return [
            'sh', '-c',
            (
                'set -e; '
                'mkdir -p /tmp/work && cd /tmp/work && '
                'echo "$CODE" | base64 -d > exercise.h && '
                'echo "$EVAL" | base64 -d > evaluate.cpp && '
                'echo "$TESTKIT" | base64 -d > testkit.h && '
                'echo "$RUNNER" | base64 -d > main.cpp && '
                'g++ -std=c++17 -O2 -pipe -I. -o runner main.cpp evaluate.cpp && '
                './runner'
            ),
        ]
    if language == 'java':
        return [
            'sh', '-c',
            (
                'set -e; '
                'mkdir -p /tmp/work && cd /tmp/work && '
                'echo "$CODE" | base64 -d > Exercise.java && '
                'echo "$EVAL" | base64 -d > Evaluate.java && '
                'echo "$RUNNER" | base64 -d > Runner.java && '
                'javac Exercise.java Evaluate.java Runner.java && '
                'java Runner'
            ),
        ]
    raise ValueError(f'Unsupported language: {language}')


# ---------------------------------------------------------------------------
# Sentinel parser
# ---------------------------------------------------------------------------

def _parse_script_output(stdout: bytes) -> list[ScriptTestResult]:
    """Walk the harness's sentinel stream and pull out per-test results.

    Appending parser: the evaluation script decides how many tests exist, so
    (unlike the old fixed-count I/O protocol) the total is discovered from
    the stream itself. A truncated tail (crash mid-suite) simply yields the
    results emitted so far.
    """
    results: list[ScriptTestResult] = []
    cursor = 0
    while cursor < len(stdout):
        m = _SCRIPT_RESULT_RE.search(stdout, cursor)
        if not m:
            break
        status = m.group(2).decode()
        runtime_ms = int(m.group(3))
        name_len = int(m.group(4))
        stdout_len = int(m.group(5))
        stderr_len = int(m.group(6))
        p = m.end()
        name_b = stdout[p:p + name_len]
        p += name_len + 1  # +1 for the literal newline after each segment
        out_b = stdout[p:p + stdout_len]
        p += stdout_len + 1
        err_b = stdout[p:p + stderr_len]
        p += stderr_len + 1
        cursor = p
        end_m = _SCRIPT_END_RE.match(stdout, cursor)
        if end_m:
            cursor = end_m.end()
        if status not in ('passed', 'failed', 'error'):
            status = 'error'
        results.append(ScriptTestResult(
            test_name=name_b.decode('utf-8', errors='replace'),
            status=status,
            stdout=out_b.decode('utf-8', errors='replace'),
            stderr=err_b.decode('utf-8', errors='replace'),
            runtime_ms=runtime_ms,
        ))
    return results


# ---------------------------------------------------------------------------
# CodeRunner
# ---------------------------------------------------------------------------

class CodeRunner:
    """Execute the instructor's evaluation script against a learner's code
    in ONE sandboxed container.

    Public entry point:
        run_submission(code, evaluation_script, time_limit_ms, language)
        -> list[ScriptTestResult]
    """

    def run_submission(
        self,
        code: str,
        evaluation_script: str,
        time_limit_ms: int,
        language: str,
    ) -> list[ScriptTestResult]:
        if language not in _SUPPORTED_LANGUAGES:
            raise ValueError(f'Unsupported language: {language}')

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

        code_b64 = base64.b64encode(code.encode('utf-8')).decode('ascii')
        eval_b64 = base64.b64encode(evaluation_script.encode('utf-8')).decode('ascii')
        env = _env_for(language, code_b64, eval_b64)

        image = _image_for(language)
        command = _command_for(language)
        # time_limit_ms is the WHOLE-SUITE budget (the script decides the
        # test count, so a per-test budget can't be known upfront). The +10 s
        # headroom covers container startup and, for C++/Java, the compile.
        timeout_s = max(10, time_limit_ms // 1000 + 10)

        container = None
        try:
            try:
                container = client.containers.run(
                    image=image,
                    command=command,
                    environment=env,
                    detach=True,
                    remove=False,
                    name=f'cc-runner-{uuid.uuid4().hex[:12]}',
                    **self._container_security_kwargs(docker),
                )
            except _ImageNotFound:
                err = (
                    f'Runner image not found: {image}. '
                    f'Run `docker pull {image}` or set the RUNNER_IMAGE_* env var.'
                )
                return self._error_result(err)
            except _DockerAPIError as exc:
                # Connection / timeout-style errors are retryable; others are
                # caller-fault and should not be retried.
                msg = str(exc)
                if 'connection' in msg.lower() or 'timeout' in msg.lower():
                    raise DockerTransientError(msg) from exc
                return self._error_result(f'Docker error: {msg}')

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

            results = _parse_script_output(raw_stdout)
            if not results:
                # Nothing emitted: compile error, OOM, crash before the first
                # sentinel, or wall-clock timeout. Surface the container's
                # stderr tail (e.g. the compiler output) for debugging.
                msg = (
                    'Execution timed out.' if timed_out
                    else 'No output captured (evaluation crashed before producing results).'
                )
                stderr_tail = raw_stderr.decode('utf-8', errors='replace')[-MAX_OUTPUT:]
                return self._error_result((msg + '\n' + stderr_tail).strip())
            return results
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    logger.exception('Failed to remove container; may need manual cleanup.')

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _container_security_kwargs(self, docker_mod) -> dict:
        """Sandbox limits shared by every run — identical to the pre-script-
        mode posture: no network, 128 MB, 0.5 CPU, read-only root, tmpfs /tmp,
        all capabilities dropped."""
        return {
            'runtime': settings.RUNNER_RUNTIME,
            'network_disabled': True,
            'mem_limit': '128m',
            'memswap_limit': '128m',
            'nano_cpus': 500_000_000,  # 0.5 CPU
            'pids_limit': 64,
            'ulimits': [
                docker_mod.types.Ulimit(name='fsize', soft=10 * 1024 * 1024, hard=10 * 1024 * 1024),
                docker_mod.types.Ulimit(name='nproc', soft=64, hard=64),
                docker_mod.types.Ulimit(name='nofile', soft=128, hard=128),
                docker_mod.types.Ulimit(name='cpu', soft=10, hard=10),
            ],
            'read_only': True,
            'tmpfs': {'/tmp': 'size=32m,exec'},
            'cap_drop': ['ALL'],
            'security_opt': ['no-new-privileges:true'],
        }

    def _error_result(self, message: str) -> list[ScriptTestResult]:
        return [ScriptTestResult(
            test_name='evaluation',
            status='error',
            stdout='',
            stderr=message[:MAX_OUTPUT],
            runtime_ms=0,
        )]
