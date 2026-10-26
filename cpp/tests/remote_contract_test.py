"""Regression guards for the external-client verifier, not backend fixtures."""
import unittest
from pathlib import Path
import subprocess
import sys
import tempfile
from remote_rtc_contract import Target, validate_observation


def fixture():
    value = dict(errors=[], result=dict(planned=40, scheduled=40, skipped=0,
        actualSent=40, sent=40, sentSeqs=list(range(40)), received=35, acked=35,
        samples=[dict(seq=n, rttMs=25) for n in range(35)], uploadReceived=38,
        uploadReceivedSeqs=list(range(38)), clientUploadActualSent=40,
        complete=True, final=True, invalidReasons=[],
        probeTransport=dict(ordered=False, maxRetransmits=0, zeroRetransmitHonored=True)))
    stats = dict(pc='connected', dtls='connected', sctp='connected',
        pairState='succeeded', bytesSent=1000, bytesReceived=1000,
        remote=dict(address='1.1.1.1', protocol='udp'))
    return value, stats


class RemoteVerifierTests(unittest.TestCase):
    def test_valid_packet_loss_is_preserved_not_protocol_failure(self):
        value, stats = fixture()
        result = validate_observation(value, stats, '1.1.1.1')
        self.assertTrue(result['protocol_complete'])
        self.assertTrue(result['scheduler_density_at_least_099'])
        self.assertEqual(result['missing_download_acks'], 5)
        self.assertEqual(result['missing_upload_probes'], 2)

    def test_density_uses_scheduling_not_delivery(self):
        value, stats = fixture()
        value['result'].update(scheduled=30, skipped=10, actualSent=30, sent=30,
            sentSeqs=list(range(30)), received=25, acked=25,
            samples=[dict(seq=n, rttMs=25) for n in range(25)])
        result = validate_observation(value, stats, '1.1.1.1')
        self.assertEqual(result['scheduler_density'], .75)
        self.assertFalse(result['scheduler_density_at_least_099'])
        self.assertTrue(result['protocol_complete'])

    def test_broken_accounting_rejected(self):
        value, stats = fixture()
        value['result']['skipped'] = 1
        with self.assertRaises(AssertionError):
            validate_observation(value, stats, '1.1.1.1')

    def test_duplicate_sent_ids_rejected(self):
        value, stats = fixture()
        value['result']['sentSeqs'][-1] = 0
        with self.assertRaises(AssertionError):
            validate_observation(value, stats, '1.1.1.1')

    def test_sample_count_mismatch_rejected(self):
        value, stats = fixture()
        value['result']['received'] = 36
        with self.assertRaises(AssertionError):
            validate_observation(value, stats, '1.1.1.1')

    def test_missing_reason_on_incomplete_rejected(self):
        value, stats = fixture()
        value['result'].update(complete=False, final=False)
        with self.assertRaises(AssertionError):
            validate_observation(value, stats, '1.1.1.1')

    def test_reliable_probe_transport_rejected(self):
        value, stats = fixture()
        value['result']['probeTransport']['maxRetransmits'] = None
        with self.assertRaises(AssertionError):
            validate_observation(value, stats, '1.1.1.1')

    def test_private_remote_candidate_rejected(self):
        value, stats = fixture()
        stats['remote']['address'] = '127.0.0.1'
        with self.assertRaises(AssertionError):
            validate_observation(value, stats, '127.0.0.1')

    def test_wrong_public_remote_candidate_rejected(self):
        value, stats = fixture()
        stats['remote']['address'] = '8.8.8.8'
        with self.assertRaises(AssertionError):
            validate_observation(value, stats, '1.1.1.1')

    def test_tcp_relay_not_accepted_as_direct_udp(self):
        value, stats = fixture()
        stats['remote']['protocol'] = 'tcp'
        with self.assertRaises(AssertionError):
            validate_observation(value, stats, '1.1.1.1')

    def test_missing_completion_keeps_upload_scheduler_unknown(self):
        value, stats = fixture()
        value['result'].update(complete=False, final=False, clientUploadActualSent=None,
                               invalidReasons=['UPLOAD_COMPLETE_MISSING'])
        result = validate_observation(value, stats, '1.1.1.1')
        self.assertFalse(result['protocol_complete'])
        self.assertFalse(result['scheduler_density_at_least_099'])
        self.assertIsNone(result['upload_scheduler_density'])
        self.assertIsNone(result['missing_upload_probes'])

    def test_null_upload_count_without_reason_rejected(self):
        value, stats = fixture()
        value['result'].update(complete=False, final=False, clientUploadActualSent=None,
                               invalidReasons=['UNRELATED'])
        with self.assertRaises(AssertionError):
            validate_observation(value, stats, '1.1.1.1')

    def test_duplicate_received_ids_rejected(self):
        value, stats = fixture()
        value['result']['uploadReceivedSeqs'].append(0)
        with self.assertRaises(AssertionError):
            validate_observation(value, stats, '1.1.1.1')

    def test_no_credentials_query_or_plaintext_url(self):
        for url in ['http://1.1.1.1', 'https://user:secret@1.1.1.1', 'https://1.1.1.1?secret=example']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                Target(url, '/unused')

    def test_cli_refuses_existing_evidence_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'failure.json'
            output.write_bytes(b'original failure evidence')
            child = subprocess.run([sys.executable, str(Path(__file__).with_name('remote_rtc_contract.py')),
                '--url', 'https://1.1.1.1:1', '--ca', '/unused', '--pin', 'unused',
                '--backend', 'cpp', '--role', 'agent', '--output', str(output)],
                capture_output=True, text=True, timeout=15)
            self.assertEqual(child.returncode, 2)
            self.assertIn('never overwrite a previous result', child.stderr)
            self.assertEqual(child.stdout, '')
            self.assertEqual(output.read_bytes(), b'original failure evidence')


if __name__ == '__main__':
    unittest.main()
