"""Unit tests for Cloudflare WARP registration, status API, actions, and policy routing."""
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.request

import panel
from test_base import PanelTestBase


class WarpTests(PanelTestBase):
    def test_warp_registration_and_config(self):
        mock_response = io.BytesIO(json.dumps({
            'id': 'test-warp-id',
            'token': 'test-warp-token',
            'config': {
                'peers': [{
                    'public_key': 'bmXOC+F1FxEMF9dyiK2H5/1SUtzH0JuVo51h2wPfgyo=',
                    'endpoint': {'v4': '162.159.192.1:2408'}
                }],
                'interface': {
                    'addresses': {'v4': '172.16.0.2/32', 'v6': '2606:4700:110:8::2/128'}
                }
            }
        }).encode())
        with patch.object(urllib.request, 'urlopen', return_value=mock_response):
            reg = panel.register_warp()
        self.assertEqual(reg['ipv4'], '172.16.0.2/32')
        self.assertTrue(panel.WARP_CONFIG.exists())
        conf_text = panel.WARP_CONFIG.read_text()
        self.assertIn('Table = off', conf_text)
        self.assertIn('Address = 172.16.0.2/32', conf_text)
        self.assertIn('PublicKey = bmXOC+F1FxEMF9dyiK2H5/1SUtzH0JuVo51h2wPfgyo=', conf_text)
        self.assertTrue((panel.ROOT / 'warp.json').exists())
        status = panel.warp_status()
        self.assertTrue(status['registered'])
        self.assertTrue(status['installed'])

    def test_warp_api_endpoints_and_actions(self):
        mock_response = io.BytesIO(json.dumps({
            'id': 'test-warp-id',
            'token': 'test-warp-token',
            'config': {
                'peers': [{
                    'public_key': 'bmXOC+F1FxEMF9dyiK2H5/1SUtzH0JuVo51h2wPfgyo=',
                    'endpoint': {'v4': '162.159.192.1:2408'}
                }],
                'interface': {'addresses': {'v4': '172.16.0.2/32'}}
            }
        }).encode())
        with patch.object(urllib.request, 'urlopen', return_value=mock_response):
            r = self.request('/api/warp', {'action': 'setup'})
            self.assertEqual(r.status, 200)
            data = json.load(r)
            self.assertTrue(data['registered'])
            self.assertFalse(data['workspaceWarp'])

            # Enable WARP
            r = self.request('/api/warp', {'action': 'enable'})
            self.assertEqual(r.status, 200)
            self.assertTrue(json.load(r)['workspaceWarp'])
            self.assertTrue(panel.load()['warpOutbound'])

            # Toggle WARP
            r = self.request('/api/warp', {'action': 'toggle'})
            self.assertEqual(r.status, 200)
            self.assertFalse(json.load(r)['workspaceWarp'])
            self.assertFalse(panel.load()['warpOutbound'])

            # GET status
            r = self.request('/api/warp')
            self.assertEqual(r.status, 200)
            status = json.load(r)
            self.assertTrue(status['registered'])

            # Reset WARP
            r = self.request('/api/warp', {'action': 'reset'})
            self.assertEqual(r.status, 200)
            self.assertFalse(json.load(r)['registered'])

    def test_settings_and_client_tuning_warp_support(self):
        # Settings with warpOutbound
        r = self.request('/api/settings', {'host': 'vpn.example.com', 'port': 51820, 'mtu': 1280, 'dns': '1.1.1.1', 'warpOutbound': True})
        self.assertEqual(r.status, 200)
        self.assertTrue(panel.load()['warpOutbound'])

        # Client tuning with warp override
        r = self.request('/api/clients/0123456789abcdef/tuning', {'profile': 'default', 'dns': '', 'warp': 'enabled'})
        self.assertEqual(r.status, 200)
        client = panel.load()['clients'][0]
        self.assertEqual(client['tuning']['warp'], 'enabled')

        # Check /api/status includes warp state
        r = self.request('/api/status')
        status = json.load(r)
        self.assertIn('warp', status)
        self.assertTrue(status['warpOutbound'])

    def test_warp_policy_routing_and_per_client_overrides(self):
        panel.WARP_CONFIG.write_text('[Interface]\nAddress = 172.16.0.2/32\nPrivateKey = priv\n')
        state = panel.load()
        state['warpOutbound'] = True
        state['clients'][0]['tuning'] = {'warp': 'enabled'}
        self.commands.clear()
        panel.sync_warp_routing(state)
        # Verify ip route and rule commands executed
        self.assertTrue(any(c[:5] == ('ip', 'route', 'replace', 'default', 'dev') for c in self.commands))
        self.assertTrue(any(c[:4] == ('ip', 'rule', 'add', 'from') and c[4] == state['network'] for c in self.commands))
        self.assertTrue(any(c[:4] == ('ip', 'rule', 'add', 'from') and c[4].startswith(state['clients'][0]['address']) for c in self.commands))


if __name__ == '__main__':
    unittest.main()
