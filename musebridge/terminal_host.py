"""Private JobObject-owned ConPTY host; it never owns the gadget connection."""
import argparse
import json
import sqlite3
import subprocess
import sys
import time


def main():
    from winpty import PTY
    from winpty.enums import Backend
    parser=argparse.ArgumentParser();parser.add_argument('--state',required=True);parser.add_argument('--session',required=True);args=parser.parse_args()
    db=sqlite3.connect(args.state);db.row_factory=sqlite3.Row;db.execute('PRAGMA busy_timeout=5000')
    row=db.execute('SELECT * FROM sessions WHERE id=?',(args.session,)).fetchone()
    if not row or row['state']!='accepted':return 2
    argv=json.loads(row['argv_json']);pty=PTY(row['cols'],row['rows'],backend=Backend.ConPTY)
    pty.spawn(argv[0],cmdline=' '+subprocess.list2cmdline(argv[1:]) if len(argv)>1 else None,cwd=row['cwd'])
    import pyte
    class TerminalScreen(pyte.Screen):
        # Queries stay inside the owned pseudoterminal. This supplies DSR and
        # cursor replies needed by programs such as the modern Python REPL.
        def write_process_input(self, data):
            pty.write(data)
    screen = TerminalScreen(row['cols'],row['rows'])
    stream = pyte.Stream(screen)
    sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    with db:db.execute("UPDATE sessions SET state='running' WHERE id=?",(args.session,))
    try:
        while True:
            output=pty.read(blocking=False)
            if output:
                stream.feed(output)
                sys.stdout.write(output);sys.stdout.flush()
            commands=db.execute("SELECT * FROM inputs WHERE session_id=? AND state='pending' ORDER BY seq LIMIT 10",(args.session,)).fetchall()
            for command in commands:
                with db:
                    changed=db.execute("UPDATE inputs SET state='sending' WHERE seq=? AND state='pending'",(command['seq'],)).rowcount
                if not changed:continue
                payload=json.loads(command['payload'])
                try:
                    if command['kind']=='write':pty.write(payload['text'])
                    elif command['kind']=='resize':
                        pty.set_size(payload['cols'],payload['rows'])
                        screen.resize(lines=payload['rows'],columns=payload['cols'])
                    else:raise ValueError('Unknown terminal command')
                except Exception:
                    with db:db.execute("UPDATE inputs SET state='unknown' WHERE seq=?",(command['seq'],))
                    raise
                else:
                    with db:db.execute("UPDATE inputs SET state='done' WHERE seq=?",(command['seq'],))
            if not pty.isalive():
                output=pty.read(blocking=False)
                if output:sys.stdout.write(output);sys.stdout.flush()
                break
            time.sleep(.025)
        return pty.get_exitstatus() or 0
    except Exception:
        return 1
    finally:
        try:pty.cancel_io()
        except Exception:pass
        db.close()


if __name__=='__main__':raise SystemExit(main())
