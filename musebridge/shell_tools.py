"""Conversation-owned shell commands with bounded inline waiting."""
from __future__ import annotations

import math
import time
import uuid

from .shells import default_shell
from .workspace import Workspace


INLINE_CAP_S = 25  # MB-25 P-1: 150 seconds did not survive the remote deadline.
ACTIVE = ('accepted', 'running')


def number(value, name, minimum, maximum):
    """Validate a finite numeric command limit without accepting booleans."""
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(name + ' must be a finite number')
    if not minimum <= value <= maximum:
        raise ValueError(f'{name} must be {minimum}..{maximum}')
    return float(value)


class ShellTools:
    """Adapt durable jobs without holding admission locks while waiting."""

    def __init__(self, router):
        self.router = router
        self.inline_cap_s = INLINE_CAP_S

    def run(self, cid, params, guard, run_id=None):
        """Launch once under the admission gate, then wait outside that gate."""
        command = params.get('command')
        if not isinstance(command, str) or not command.strip() or len(command.encode()) > 128000:
            raise ValueError('command must be a nonempty script under 128000 bytes')
        timeout = number(params.get('timeout_s', 120), 'timeout_s', 0.01, 3600)
        background = params.get('background', False)
        if type(background) is not bool:
            raise ValueError('background must be a boolean')
        request_id = params.get('request_id', uuid.uuid4().hex)
        if not isinstance(request_id, str) or not request_id or len(request_id) > 200:
            raise ValueError('request_id must be a nonempty string of at most 200 characters')
        context = self.router.catalog.context(cid)
        if not context['project']:
            raise ValueError('Choose a project before running a command')
        workspace = Workspace(context['project']['root'])
        cwd = workspace.resolve(params.get('cwd', '.'))
        with self.router.execution.lock:
            guard()
            snapshot = self.router.jobs.start(cid + ':' + request_id, command, cwd,
                                              timeout_s=timeout, shell=params.get('shell', default_shell()))
            if run_id:
                self.router.finish_line.watch_check(cid, run_id, snapshot['job_id'])
            with self.router.catalog.lock, self.router.catalog.db:
                self.router.catalog.db.execute('INSERT OR IGNORE INTO job_links VALUES(?,?)',
                                                (snapshot['job_id'], cid))
        if not background:
            self._wait(snapshot['job_id'], min(timeout, self.inline_cap_s))
        return self._result(snapshot['job_id'])

    def job(self, cid, params):
        """Inspect, wait for, list, or cancel only this conversation's jobs."""
        action = params.get('action', 'wait')
        if action not in ('wait', 'status', 'cancel', 'list'):
            raise ValueError('action must be wait, status, cancel, or list')
        if action == 'list':
            return self._list(cid)
        identifier = params.get('job_id')
        if not isinstance(identifier, str) or not identifier:
            raise ValueError('job_id is required for wait, status, or cancel')
        self._owned(cid, identifier)
        if action == 'cancel':
            self.router.jobs.cancel(identifier)
        elif action == 'wait':
            wait = number(params.get('wait_s', self.inline_cap_s), 'wait_s', 0, INLINE_CAP_S)
            self._wait(identifier, wait)
        if 'cursor' in params:
            return self._page(identifier, params['cursor'])
        return self._result(identifier)

    def _owned(self, cid, identifier):
        rows = self.router.catalog.rows('SELECT conversation_id FROM job_links WHERE job_id=?', (identifier,))
        if not rows or rows[0]['conversation_id'] != cid:
            raise ValueError('Job belongs to a different conversation')

    def _list(self, cid):
        rows = self.router.catalog.rows('SELECT job_id FROM job_links WHERE conversation_id=?', (cid,))
        identifiers = [row['job_id'] for row in rows]
        states = {}
        for offset in range(0, len(identifiers), 1000):
            states.update(self.router.jobs.states(identifiers[offset:offset + 1000]))
        return {'jobs': [{'job_id': key, 'state': value} for key, value in states.items() if value in ACTIVE]}

    def _wait(self, identifier, wait_s):
        deadline = time.monotonic() + wait_s
        snapshot = self.router.jobs.status(identifier)
        while snapshot['state'] in ACTIVE and time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            snapshot = self.router.jobs.wait(identifier, snapshot['output_end'], min(remaining, 1))

    def _result(self, identifier):
        from .shell_output import clean_output, shape_output
        preview = self.router.jobs.preview(identifier)
        result = shape_output(preview['head'], preview['tail'], preview['omitted_chars'])
        active = preview['state'] in ACTIVE
        result.update({'job_id': identifier, 'state': preview['state'],
                       'exit_code': preview['exit_code'], 'running': active})
        started = preview.get('started')
        result['duration_s'] = round(max(0, (preview.get('finished') or time.time()) - started), 3) if started else 0
        if not clean_output(preview['head'] + preview['tail']) and not preview['omitted_chars']:
            result['output'] = (f"Command produced no output (exit {preview['exit_code']})."
                                if preview['exit_code'] is not None else f"No output captured ({preview['state']}).")
        if active:
            result['output_so_far'] = result.pop('output')
            result['hint'] = f'shell.job job_id={identifier} action=wait; inspect exit_code before claiming success.'
        if result['truncated']:
            result['log'] = {'job_id': identifier, 'cursor': 0}
            result['hint'] = ('Use shell.job action=status with this job_id and cursor to page retained output. '
                              'Any discarded middle cannot be recovered; narrow the command or redirect future output to a project file.')
        return result

    def _page(self, identifier, cursor):
        from .shell_output import clean_output, shape_output
        page = self.router.jobs.status(identifier, cursor)
        start = page['cursor'] - len(page['output'])
        text = page['output'][:1200]
        next_cursor = start + len(text)
        shaped = shape_output('', text)
        if not clean_output(text):
            shaped['output'] = (f"Command produced no output (exit {page['exit_code']})."
                                if page['exit_code'] is not None else f"No output captured ({page['state']}).")
        return {**shaped, 'job_id': identifier, 'state': page['state'], 'exit_code': page['exit_code'],
                'cursor': next_cursor, 'output_end': page['output_end'],
                'retained_prefix_missing': page['truncated'],
                'more': next_cursor < page['output_end']}
