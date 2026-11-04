import base64
import unittest
from oss_mentor.datasets.publication_secrets import contains_secret, redact, require_publication_safe


class PublicationGateTests(unittest.TestCase):
    def test_hf_is_blocked_and_redacted_without_echoing_value(self):
        sample = b'hf_' + b'x' * 32
        with self.assertRaises(ValueError) as error:
            require_publication_safe(sample)
        self.assertNotIn(sample.decode(), str(error.exception))
        self.assertFalse(contains_secret(redact(sample)))

    def test_reference_token_is_blocked(self):
        sample = base64.b64encode(b'reftkn:' + b'x' * 40)
        self.assertTrue(contains_secret(sample))
        self.assertFalse(contains_secret(redact(sample)))

    def test_normal_code_is_preserved(self):
        sample = b'API_TOKEN = os.environ.get("API_TOKEN")\r\n'
        require_publication_safe(sample)
        self.assertEqual(redact(sample), sample)


if __name__ == '__main__':
    unittest.main()
