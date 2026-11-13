"""Compare two sanitized MB-25b evaluation reports without filling missing data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


METRICS=('success','capability_exercised','secondary_display_verified',
         'tools_calls','failed_call_rate','wall_time','result_bytes','preamble_bytes')


def _index_results(report: dict) -> dict[str,dict]:
    rows=report.get('results')
    if not isinstance(rows,list):
        raise ValueError('Report results must be a list')
    result={}
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('task_id'),str):
            raise ValueError('Report contains an invalid task result')
        if row['task_id'] in result:
            raise ValueError('Report contains a duplicate task ID')
        result[row['task_id']]=row
    return result


def _delta(old, new):
    if old is None or new is None:
        return None
    if isinstance(old, bool) and isinstance(new, bool):
        return ('yes' if old else 'no') + ' → ' + ('yes' if new else 'no')
    if isinstance(old,bool) or isinstance(new,bool):
        return None
    if isinstance(old,(int,float)) and isinstance(new,(int,float)):
        return round(new-old,6)
    return None


def compare_reports(before: dict, after: dict) -> dict:
    """Return per-task and aggregate deltas; missing observations stay None."""
    left = _index_results(before)
    right = _index_results(after)
    rows=[]
    for task_id in sorted(set(left)|set(right)):
        old = left.get(task_id)
        new = right.get(task_id)
        if old is None:
            rows.append({'task_id':task_id,'status':'missing-before','before':None,'after':new,
                         'deltas':{metric:None for metric in METRICS}})
        elif new is None:
            rows.append({'task_id':task_id,'status':'missing-after','before':old,'after':None,
                         'deltas':{metric:None for metric in METRICS}})
        else:
            rows.append({'task_id':task_id,'status':'compared','before':old,'after':new,
                         'deltas':{metric:_delta(old.get(metric),new.get(metric)) for metric in METRICS}})
    old_aggregate=before.get('aggregate') if isinstance(before.get('aggregate'),dict) else {}
    new_aggregate=after.get('aggregate') if isinstance(after.get('aggregate'),dict) else {}
    aggregate={metric:_delta(old_aggregate.get(metric),new_aggregate.get(metric))
               for metric in ('success_rate','tools_calls','failed_call_rate','wall_time','result_bytes','preamble_bytes')}
    return {'before_label':before.get('label'),'after_label':after.get('label'),
            'task_sets_match':set(left) == set(right),
            'tasks':rows,'aggregate_deltas':aggregate}


def _display(value):
    if value is None:return 'N/A'
    if isinstance(value,bool):return 'yes' if value else 'no'
    if isinstance(value,float):return f'{value:+.6f}'
    if isinstance(value,int):return f'{value:+d}'
    return str(value)


def format_comparison(result: dict) -> str:
    """Render comparison transitions and full-run aggregate caveats."""
    lines=[f"Compare {result['before_label']} → {result['after_label']}",'',
           'Deltas are after minus before; negative call, failure, time, and byte deltas are lower.',
           '| Task | Status | Success | Capability | Secondary display | Tool calls | Failed-call rate | Wall time (s) | Result bytes | Preamble bytes |',
           '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in result['tasks']:
        if row['status']!='compared':
            vals=[row['task_id'],row['status'],'N/A','N/A','N/A','N/A','N/A','N/A','N/A','N/A']
        else:
            vals=[row['task_id'],'compared',_display(row['deltas']['success']),
                  _display(row['deltas']['capability_exercised']),
                  _display(row['deltas']['secondary_display_verified']),
                  _display(row['deltas']['tools_calls']),_display(row['deltas']['failed_call_rate']),
                  _display(row['deltas']['wall_time']),_display(row['deltas']['result_bytes']),
                  _display(row['deltas']['preamble_bytes'])]
        lines.append('| '+' | '.join(value.replace('|','\\|') for value in vals)+' |')
    lines.extend(['','Overall deltas (full-run totals):'])
    if not result.get('task_sets_match', True):
        lines.append('Task sets differ; overall totals are not a like-for-like comparison.')
    for metric,value in result['aggregate_deltas'].items():
        lines.append(f'- {metric}: {_display(value)}')
    return '\n'.join(lines)+'\n'


def read_report(path: Path) -> dict:
    """Read a JSON score report and validate its task-result list."""
    try:
        report=json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError):
        raise ValueError('Could not read an evaluation report') from None
    if not isinstance(report,dict):
        raise ValueError('Evaluation report must be a JSON object')
    _index_results(report)
    return report


def main(argv=None) -> int:
    """Compare two report paths from the command line."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before',type=Path)
    parser.add_argument('after',type=Path)
    args=parser.parse_args(argv)
    try:
        result=compare_reports(read_report(args.before),read_report(args.after))
    except ValueError as exc:
        print(str(exc),file=sys.stderr)
        return 2
    # Keep the console path usable under Windows' default legacy code page.
    # The saved Markdown formatter retains Unicode arrows.
    print(format_comparison(result).replace('\u2192','->'),end='')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
