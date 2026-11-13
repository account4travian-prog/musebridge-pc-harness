"""Muse v5 pairing, using upstream crypto and a serial BLE transport."""
import threading
import time
from musegadget import __version__, identity, muse_api, config
from musegadget.ble_setup import SetupController, ProvisionFailed
from musegadget.pairing import PairingSession
from .serial_ble import SerialTransport


class OnlineNetwork:
    def is_online(self):
        return True  # The credentials check below verifies actual Muse reachability.

    def current_connection_entry(self):
        return {'ssid': 'Windows PC current connection', 'rssi': -30, 'secure': False}


def get_identity(store):
    saved = store.load('identity')
    if not saved:
        saved = {'mac': identity.generate_mac()}
        store.save('identity', saved)
    return identity.Identity(saved['mac'])


def pair(store, port, sdk_token, status, stop, transport_factory=SerialTransport):
    if not sdk_token.startswith('mgst_') or any(c.isspace() for c in sdk_token):
        raise ValueError('Enter the SDK token from gadgets.muse.ai')
    ident = get_identity(store)
    done = threading.Event()
    controller = None
    transport = transport_factory(port, ident.ble_name,
                                  lambda p: controller.on_write(p),
                                  lambda: controller.on_disconnect() if controller else None)

    def provision(credentials, commit):
        # Do not persist any phone-supplied credentials until Muse accepts them.
        api = muse_api.api_root(credentials.api_url_v2)
        if not api.startswith('https://'):
            raise ProvisionFailed('auth_failed')
        vms, _ = muse_api.fetch_vms_with_status(credentials.access_token, api)
        if not vms:
            raise ProvisionFailed('auth_failed')
        record = {**vars(credentials), 'token_type': 'device',
                  'access_token_saved_at': int(time.time()),
                  'sdk_token_hint': config.sdk_token_hint(sdk_token)}

        def save():
            if stop.is_set():
                return False
            store.save('pairing', record)
            store.save('sdk', {'token': sdk_token})
            return True
        if not commit(save):
            raise ProvisionFailed('error_storage')

    controller = SetupController(pairing=PairingSession(
        node_id=ident.node_id, device_id=ident.device_id, mac=ident.mac,
        firmware_version=__version__, sdk_token=sdk_token), identity=ident,
        version=__version__, transport=transport, network=OnlineNetwork(),
        provision=provision, on_complete=done.set)
    controller.start()
    def watchdog():
        deadline = time.monotonic() + 600
        while not stop.wait(0.1):
            if done.is_set():
                time.sleep(1.5)  # Allow the final encrypted auth_ok notification.
                break
            if time.monotonic() > deadline:
                break
        transport.stop()
    watcher = threading.Thread(target=watchdog, daemon=True)
    watcher.start()
    status('Pairing ' + ident.ble_name + ' — add this device in the Muse phone app')
    try:
        transport.run()
    finally:
        stop.set()
        controller.stop()
        watcher.join(timeout=2)
    if not done.is_set():
        raise ConnectionError('Pairing did not complete')
    status('Paired. You can now connect from this PC.')
