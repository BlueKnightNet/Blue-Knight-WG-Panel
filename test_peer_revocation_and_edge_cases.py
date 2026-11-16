"""Unit tests for peer revocation, concurrency lock handling, atomic validation, and edge cases."""
import json
import unittest
from unittest.mock import patch

import panel
import test_base


class PeerRevocationAndEdgeCasesTests(test_base.TestCase):
    def test_revocation_immediately_drops_peer_and_flushes_conntrack(self):
        """Client revocation immediately drops peer from interface and flushes conntrack."""
        response = self.request('/api/revoke', {'id': '0123456789abcdef'})
        self.assertEqual(response.status, 200)

        # Peer drop from kernel interface
        self.assertTrue(
            any(cmd[:5] == ('wg', 'set', 'wgauto0', 'peer', 'client-public') and cmd[5] == 'remove'
                for cmd in self.commands),
            "Expected kernel peer removal command to be executed immediately"
        )

        # Conntrack flush for source and destination addresses
        self.assertTrue(
            any(cmd == ('conntrack', '-D', '-s', '10.203.240.2') for cmd in self.commands),
            "Expected conntrack flush for client source address"
        )
        self.assertTrue(
            any(cmd == ('conntrack', '-D', '-d', '10.203.240.2') for cmd in self.commands),
            "Expected conntrack flush for client destination address"
        )

    def test_revocation_cleans_up_table_100_ip_rule(self):
        """Revocation cleans up table 100 ip rule for the revoked client."""
        response = self.request('/api/revoke', {'id': '0123456789abcdef'})
        self.assertEqual(response.status, 200)

        expected_rule_del = ('ip', 'rule', 'del', 'from', '10.203.240.2/32', 'table', '100', 'priority', '14500')
        self.assertTrue(
            any(cmd == expected_rule_del for cmd in self.commands),
            "Expected client-specific routing rule in table 100 to be deleted"
        )

    def test_revoking_non_existent_client_returns_404(self):
        """Revoking a non-existent client returns 404."""
        response = self.request('/api/revoke', {'id': 'deadbeef12345678'})
        self.assertEqual(response.status, 404)
        data = json.loads(response.read().decode())
        self.assertEqual(data.get('error'), 'Client not found.')

    def test_deletion_does_not_fail_or_rollback_if_wg_set_encounters_vanished_peer(self):
        """Deletion does not fail or rollback if wg set ... remove encounters an already removed peer."""
        normal_run = panel.run.side_effect

        def vanish_on_remove(*args, data=None):
            if args[:2] == ('wg', 'set') and args[-1] == 'remove':
                # Simulate ENXIO (No such device or address) error when peer vanished
                raise RuntimeError('ENXIO: Peer not found in interface')
            if args == ('wg', 'show', 'wgauto0', 'peers'):
                # Kernel reports that peer is no longer present
                return ''
            return normal_run(*args, data=data)

        with patch.object(panel, 'run', side_effect=vanish_on_remove):
            response = self.request('/api/revoke', {'id': '0123456789abcdef'})
            self.assertEqual(response.status, 200)

        state = panel.load()
        self.assertEqual(len(state['clients']), 0)
        self.assertNotIn('client-public', panel.CONFIG.read_text())

    def test_unauthenticated_post_rejected_with_401_without_acquiring_mutation_lock(self):
        """Unauthenticated POST is rejected with 401 without acquiring MUTATION_LOCK."""
        # Hold MUTATION_LOCK in the test runner
        panel.MUTATION_LOCK.acquire()
        try:
            # Unauthenticated POST request must be rejected with 401 immediately
            # without timing out (10s) or returning 409
            response = self.request('/api/clients', {'name': 'unauth_peer'}, auth=False)
            self.assertEqual(response.status, 401)
        finally:
            panel.MUTATION_LOCK.release()

    def test_updating_client_with_invalid_name_after_valid_quota_rejects_with_400_without_mutating_state(self):
        """Updating client with invalid name after valid quota rejects with 400 without mutating quota in state."""
        original_client = panel.load()['clients'][0]
        original_quota = original_client.get('quotaBytes', 0)

        body = {
            'quotaBytes': 1073741824,
            'name': 'invalid name with spaces!'
        }
        response = self.request('/api/clients/0123456789abcdef/details', body)
        self.assertEqual(response.status, 400)

        # Verify atomic validation preserved state
        current_client = panel.load()['clients'][0]
        self.assertEqual(current_client.get('quotaBytes', 0), original_quota)
        self.assertEqual(current_client['name'], original_client['name'])

    def test_details_and_tuning_for_non_existent_client_returns_404(self):
        """Details and tuning for non-existent client return 404."""
        response = self.request('/api/clients/deadbeef12345678/details', {'name': 'validname'})
        self.assertEqual(response.status, 404)
        data = json.loads(response.read().decode())
        self.assertEqual(data.get('error'), 'Client not found.')

        response_tuning = self.request('/api/clients/deadbeef12345678/tuning', {'profile': 'default'})
        self.assertEqual(response_tuning.status, 404)
        data_tuning = json.loads(response_tuning.read().decode())
        self.assertEqual(data_tuning.get('error'), 'Client not found.')

    def test_account_usage_does_not_zero_counters_or_double_count_when_peer_missing(self):
        """account_usage does not zero counters or double count when peer is missing from counters."""
        state = panel.load()
        client = state['clients'][0]
        client['lastRx'] = 1000
        client['lastTx'] = 2000
        client['usedBytes'] = 3000

        # Step 1: Peer missing from transfer report (e.g., interface reloaded or inactive)
        normal_run = panel.run.side_effect

        def empty_transfer(*args, data=None):
            if args == ('wg', 'show', 'wgauto0', 'transfer'):
                return ''
            return normal_run(*args, data=data)

        with patch.object(panel, 'run', side_effect=empty_transfer):
            panel.account_usage(state)

        # Verify counters were NOT reset to 0
        self.assertEqual(client['lastRx'], 1000)
        self.assertEqual(client['lastTx'], 2000)
        self.assertEqual(client['usedBytes'], 3000)

        # Step 2: Peer re-appears with updated transfer counters
        def updated_transfer(*args, data=None):
            if args == ('wg', 'show', 'wgauto0', 'transfer'):
                return f"{client['public']}\t1200\t2300"
            return normal_run(*args, data=data)

        with patch.object(panel, 'run', side_effect=updated_transfer):
            panel.account_usage(state)

        # Expected delta: (1200 - 1000) + (2300 - 2000) = 200 + 300 = 500
        # Expected total: 3000 + 500 = 3500 (NOT 3000 + 1200 + 2300 = 6500)
        self.assertEqual(client['lastRx'], 1200)
        self.assertEqual(client['lastTx'], 2300)
        self.assertEqual(client['usedBytes'], 3500)

    def test_sync_handles_inline_comments_in_public_key_lines(self):
        """sync handles inline comments in PublicKey lines without verification failure."""
        state = panel.load()
        config_text = (
            "[Interface]\n"
            "PrivateKey = server-private\n"
            "Address = 10.203.240.1/24\n"
            "ListenPort = 51820\n\n"
            "[Peer]\n"
            "PublicKey = client-public # Mobile peer\n"
            "PresharedKey = shared\n"
            "AllowedIPs = 10.203.240.2/32\n"
        )
        normal_run = panel.run.side_effect

        def mock_run(*args, data=None):
            if args == ('wg', 'show', 'wgauto0', 'peers'):
                return 'client-public'
            return normal_run(*args, data=data)

        with patch.object(panel, 'run', side_effect=mock_run):
            # This must succeed without raising RuntimeError('VPN peer removal could not be verified.')
            panel.sync(config_text, state)


if __name__ == '__main__':
    unittest.main()
