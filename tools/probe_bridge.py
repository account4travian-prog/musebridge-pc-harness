"""Exercise firmware hello/configure/keepalive without account credentials."""
import argparse
import threading
import time

from musebridge.serial_ble import SerialTransport


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', required=True)
    args = parser.parse_args()
    errors = []
    transport = SerialTransport(args.port, 'MuseGadgetA1B2C3', lambda _: None, lambda: None)

    def run():
        try:
            transport.run()
        except Exception as exc:
            errors.append(type(exc).__name__)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        ready = transport.ready.wait(15)
        if ready:
            time.sleep(4)  # Exercise the periodic keepalive as well as configuration.
        print({'firmware_ready': ready, 'reader_alive': thread.is_alive(), 'errors': errors})
        return 0 if ready and thread.is_alive() and not errors else 1
    finally:
        transport.stop()
        thread.join(timeout=3)


if __name__ == '__main__':
    raise SystemExit(main())
