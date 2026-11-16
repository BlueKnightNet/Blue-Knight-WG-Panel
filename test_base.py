"""Shared base test case and utilities for panel tests."""
import base64
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import panel


class PanelTestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.paths = patch.multiple(
            panel,
            ROOT=root,
            CONFIG=root / 'server.conf',
            AWG_CONFIG=root / 'awgauto0.conf',
            WARP_CONFIG=root / 'warp0.conf',
            PEER_SAMPLES={},
            TRAFFIC_HISTORY=[],
            AWG_SAMPLES={},
            AWG_HISTORY=[]
        )
        self.paths.start()
        self.state = dict(
            header='[Interface]\nPrivateKey = server-private\nAddress = 10.203.240.1/24\nListenPort = 51820',
            unmanaged=['PublicKey = unmanaged\nAllowedIPs = 10.203.240.3/32'],
            clients=[dict(id='0123456789abcdef', name='phone', address='10.203.240.2',
                          private='client-private', public='client-public', psk='shared')],
            host='vpn.example.com', port=51820, dns='1.1.1.1', mtu=1280,
            network='10.203.240.0/24', serverPublic='server-public'
        )
        panel.atomic(root / 'state.json', json.dumps(self.state))
        panel.atomic(root / 'password', 'test-password\n')
        panel.atomic(panel.CONFIG, panel.render_server(self.state))
        self.commands = []

        def fake_run(*args, data=None):
            self.commands.append(args)
            if args[:2] == ('wg', 'genkey'):
                return 'new-private'
            if args[:2] == ('wg', 'genpsk'):
                return 'new-psk'
            if args[:2] == ('wg', 'pubkey'):
                return 'new-public'
            if args[:2] == ('wg-quick', 'strip'):
                return Path(args[2]).read_text()
            if args == ('wg', 'show', 'wgauto0', 'dump'):
                return 'private\tpublic\t51820\toff\nclient-public\tshared\t(none)\t10.203.240.2/32\t0\t123\t456\t25'
            if args[:2] == ('ip', 'rule'):
                return '0: from all lookup local\n32766: from all lookup main'
            return ''

        self.runner = patch.object(panel, 'run', side_effect=fake_run)
        self.runner.start()
        self.http = panel.HTTPServer(('127.0.0.1', 0), panel.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.runner.stop()
        self.paths.stop()
        self.temp.cleanup()

    def request(self, path, body=None, auth=True, csrf=True):
        headers = {}
        if auth:
            headers['Authorization'] = 'Basic ' + base64.b64encode(b'admin:test-password').decode()
        if body is not None:
            headers['Content-Type'] = 'application/json'
            if csrf:
                headers['X-WG-Panel'] = '1'
        request = Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            return urlopen(request, timeout=3)
        except HTTPError as e:
            return e

    def prepare_awg(self):
        state = json.loads(json.dumps(self.state))
        state.update(protocol='amneziawg', port=51821, network='10.204.240.0/24')
        state['header'] = state['header'].replace('10.203.240.1', '10.204.240.1').replace('51820', '51821')
        state['header'] += '\nJc = 4\nJmin = 40\nJmax = 700\nS1 = 20\nS2 = 50\nH1 = 100001\nH2 = 200001\nH3 = 300001\nH4 = 400001'
        state['clients'][0]['address'] = '10.204.240.2'
        state['unmanaged'] = []
        panel.atomic(panel.state_path(state), json.dumps(state))
        panel.atomic(panel.AWG_CONFIG, panel.render_server(state))
        original = panel.run.side_effect

        def awg_run(*args, data=None):
            if args[0] == panel.AWG_BIN + 'awg':
                self.commands.append(args)
                if args[1] == 'show' and args[-1] in ('peers', 'transfer'):
                    return ''
                if args[1] == 'show':
                    return 'private\tpublic\t51821\t4\t40\t700\toff\nclient-public\tshared\t(none)\t10.204.240.2/32\t0\t123\t456\t25'
                return original('wg', *args[1:], data=data)
            if args[0] == panel.AWG_BIN + 'awg-quick':
                self.commands.append(args)
                return Path(args[2]).read_text()
            return original(*args, data=data)

        panel.run.side_effect = awg_run
        return state


BasePanelTestCase = PanelTestBase
TestCase = PanelTestBase
