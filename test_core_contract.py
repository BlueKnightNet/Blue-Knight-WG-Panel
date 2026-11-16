"""Core functionality contract tests ensuring fundamental invariants never break."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

import panel


class CoreFunctionalityContractTests(unittest.TestCase):
    """Rigorous regression contract tests to ensure core panel functionality never breaks."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = patch.multiple(
            panel,
            ROOT=self.root,
            CONFIG=self.root / 'server.conf',
            AWG_CONFIG=self.root / 'awgauto0.conf',
            WARP_CONFIG=self.root / 'warp0.conf',
            PEER_SAMPLES={},
            TRAFFIC_HISTORY=[],
            AWG_SAMPLES={},
            AWG_HISTORY=[]
        )
        self.paths.start()
        self.state = dict(
            header='[Interface]\nPrivateKey = srv-priv\nAddress = 10.203.240.1/24\nListenPort = 51820',
            unmanaged=[],
            clients=[dict(id='client1111111111', name='alpha', address='10.203.240.2',
                          private='cli-priv', public='cli-pub', psk='cli-psk')],
            host='vpn.example.com', port=51820, dns='1.1.1.1, 1.0.0.1', mtu=1280,
            network='10.203.240.0/24', serverPublic='srv-pub'
        )
        panel.atomic(self.root / 'state.json', json.dumps(self.state))
        panel.atomic(self.root / 'password', 'pass\n')
        panel.atomic(panel.CONFIG, panel.render_server(self.state))
        self.commands = []

        def mock_run(*args, data=None):
            self.commands.append(args)
            if args[:2] == ('wg', 'genkey'):
                return 'generated-private-key'
            if args[:2] == ('wg', 'pubkey'):
                return 'generated-public-key'
            if args[:2] == ('wg', 'genpsk'):
                return 'generated-preshared-key'
            if args[:3] == ('wg', 'show', 'wgauto0'):
                if args[-1] == 'peers':
                    return 'cli-pub'
                if args[-1] == 'transfer':
                    return 'cli-pub\t1024\t2048'
                return 'srv-priv\tsrv-pub\t51820\toff\ncli-pub\tcli-psk\t1.2.3.4:51820\t10.203.240.2/32\t100\t1024\t2048\t25'
            if args[:2] == ('wg-quick', 'strip'):
                return Path(args[2]).read_text()
            if args[:2] == ('ip', 'rule'):
                return '0: from all lookup local\n32766: from all lookup main'
            return ''

        self.runner = patch.object(panel, 'run', side_effect=mock_run)
        self.runner.start()

    def tearDown(self):
        self.runner.stop()
        self.paths.stop()
        self.temp.cleanup()

    def test_wireguard_core_contract(self):
        state = panel.load('wireguard')
        client = state['clients'][0]
        config = panel.client_config(state, client)
        self.assertIn('[Interface]', config)
        self.assertIn('PrivateKey = cli-priv', config)
        self.assertIn('Address = 10.203.240.2/32', config)
        self.assertIn('DNS = 1.1.1.1, 1.0.0.1', config)
        self.assertIn('[Peer]', config)
        self.assertIn('PublicKey = srv-pub', config)
        self.assertIn('PresharedKey = cli-psk', config)
        self.assertIn('Endpoint = vpn.example.com:51820', config)

    def test_amneziawg_compatibility_contract(self):
        awg_state = dict(
            protocol='amneziawg',
            header='[Interface]\nPrivateKey = srv-priv\nAddress = 10.204.240.1/24\nListenPort = 51821\nJc = 4\nJmin = 40\nJmax = 700\nS1 = 20\nS2 = 50\nH1 = 1001\nH2 = 2001\nH3 = 3001\nH4 = 4001\nHeaderProtectionKey = legacy-bad-key\nContentPaddingAddition = 0-16',
            unmanaged=[],
            clients=[dict(id='awgclient111111', name='phone', address='10.204.240.2',
                          private='awg-priv', public='awg-pub', psk='awg-psk')],
            host='vpn.example.com', port=51821, dns='1.1.1.1', mtu=1280,
            network='10.204.240.0/24', serverPublic='srv-pub'
        )
        panel.atomic(panel.state_path(awg_state), json.dumps(awg_state))
        loaded = panel.load('amneziawg')
        for bad_key in ('HeaderProtectionKey', 'ContentPaddingAddition', 'S3', 'S4'):
            self.assertNotIn(bad_key, loaded['header'])
        conf = panel.client_config(loaded, loaded['clients'][0])
        for required_key in ('Jc = 4', 'Jmin = 40', 'Jmax = 700', 'S1 = 20', 'S2 = 50', 'H1 = 1001', 'H2 = 2001', 'H3 = 3001', 'H4 = 4001'):
            self.assertIn(required_key, conf)
        for bad_key in ('HeaderProtectionKey', 'ContentPaddingAddition', 'S3', 'S4'):
            self.assertNotIn(bad_key, conf)

    def test_cloudflare_warp_safety_contract(self):
        mock_response = io.BytesIO(json.dumps({
            'id': 'warp-dev-id',
            'token': 'warp-token',
            'config': {
                'peers': [{'public_key': 'bmXOC+F1FxEMF9dyiK2H5/1SUtzH0JuVo51h2wPfgyo=', 'endpoint': {'v4': '162.159.192.1:2408'}}],
                'interface': {'addresses': {'v4': '172.16.0.2/32'}}
            }
        }).encode())
        with patch.object(urllib.request, 'urlopen', return_value=mock_response):
            panel.register_warp()
        conf = panel.WARP_CONFIG.read_text()
        self.assertIn('Table = off', conf)
        self.assertIn('Address = 172.16.0.2/32', conf)

    def test_security_redaction_contract(self):
        state = panel.load('wireguard')
        metrics = panel.client_metrics(state, {})
        status_dump = json.dumps(metrics)
        self.assertNotIn('cli-priv', status_dump)
        self.assertNotIn('srv-priv', status_dump)

    def test_panel_html_bundle_integrity(self):
        html_file = Path(__file__).parent / 'panel.html'
        self.assertTrue(html_file.exists())
        content = html_file.read_text(encoding='utf-8')
        self.assertTrue(content.startswith('<!doctype html>'))
        self.assertNotIn('<script src="http', content)


if __name__ == '__main__':
    unittest.main()
