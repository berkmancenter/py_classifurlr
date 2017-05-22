import unittest, json
from classifurlr import run
from classifurlr.classifiers import *

FIXTURE_DIR = 'tests/fixtures/'
def test_result(session_filename):
    with open(FIXTURE_DIR + session_filename, 'r') as f:
        result = run(json.load(f))
    return result

class DifferingDomainTest(unittest.TestCase):
    def test_is_ip(self):
        d = DifferingDomainClassifier()
        self.assertTrue(d.is_ip('https://192.128.0.1:80'))
        self.assertFalse(d.is_ip('https://www.google.com'))

class StatusCodeTest(unittest.TestCase):
    def test_non_200(self):
        filename = '403.json'
        result = test_result(filename)
        self.assertTrue(result.is_down())
        self.assertTrue(result.get_constituents()[0].get_constituent_from(StatusCodeClassifier).is_down())

class InconclusiveFilterTest(unittest.TestCase):
    def test_seized_domain(self):
        filename = 'kickass.json'
        result = test_result(filename)
        self.assertTrue(result.is_inconclusive())

class BlockpageSignatureTest(unittest.TestCase):
    def test_redirects_to_different_domain(self):
        filename = 'lesbiansubmission.json'
        result = test_result(filename)
        self.assertTrue(result.is_blocked())

    def test_blocked_embed(self):
        filename = 'samurpress.json'
        result = test_result(filename)
        self.assertFalse(result.is_blocked())

if __name__ == '__main__':
    unittest.main()
