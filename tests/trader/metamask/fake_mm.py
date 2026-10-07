"""A scripted stand-in for the ``mm`` CLI, used by the test suite.

Reads canned replies from the JSON file named by ``FAKE_MM_SCENARIO`` and
appends every invocation to the JSON-lines file named by ``FAKE_MM_LOG``.

Scenario format::

    {"responses": {"predict place": <reply or [reply, reply, ...]>,
                   "help:predict place": "help text"}}

A reply is a result envelope, optionally with ``__exit__`` (exit code),
``__raw__`` (print this text instead of JSON), ``__stderr__`` (print the
envelope on stderr) or ``__sleep__`` (seconds to sleep first). A list of
replies is consumed in order; the last one repeats.
"""

from __future__ import annotations

import json
import os
import sys
import time


def _key_tokens(argv: list[str]) -> list[str]:
    tokens = []
    for token in argv:
        if token.startswith('-'):
            break
        tokens.append(token)
    return tokens


def main() -> int:
    argv = sys.argv[1:]
    with open(os.environ['FAKE_MM_SCENARIO']) as fh:
        scenario = json.load(fh)
    with open(os.environ['FAKE_MM_LOG'], 'a') as fh:
        fh.write(
            json.dumps(
                {
                    'argv': argv,
                    'env': {
                        k: os.environ.get(k)
                        for k in ('MM_INTEGRATION_ID', 'MM_INTEGRATION_VERSION')
                    },
                }
            )
            + '\n'
        )

    responses = scenario.get('responses', {})
    tokens = _key_tokens(argv)

    if '--help' in argv:
        for n in range(len(tokens), 0, -1):
            text = responses.get('help:' + ' '.join(tokens[:n]))
            if text is not None:
                print(text)
                return 0
        print('Usage: mm ...')
        return 0

    reply = None
    key = None
    for n in range(len(tokens), 0, -1):
        candidate = ' '.join(tokens[:n])
        if candidate in responses:
            key, reply = candidate, responses[candidate]
            break
    if reply is None:
        print(
            json.dumps(
                {
                    'ok': False,
                    'error': {
                        'code': 'UNKNOWN_COMMAND',
                        'message': ' '.join(argv),
                        'hint': 'fake',
                    },
                }
            )
        )
        return 1

    if isinstance(reply, list):
        state_path = os.environ['FAKE_MM_LOG'] + '.state'
        state = {}
        if os.path.exists(state_path):
            with open(state_path) as fh:
                state = json.load(fh)
        index = state.get(key, 0)
        state[key] = index + 1
        with open(state_path, 'w') as fh:
            json.dump(state, fh)
        reply = reply[min(index, len(reply) - 1)]

    if '__sleep__' in reply:
        time.sleep(float(reply['__sleep__']))
    if '__raw__' in reply:
        print(reply['__raw__'])
        return int(reply.get('__exit__', 0))
    payload = {k: v for k, v in reply.items() if not k.startswith('__')}
    stream = sys.stderr if reply.get('__stderr__') else sys.stdout
    print(json.dumps(payload), file=stream)
    if '__exit__' in reply:
        return int(reply['__exit__'])
    return 0 if payload.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
