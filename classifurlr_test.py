import unittest, classifurlr
from classifurlr import *

class DifferingDomainTest(unittest.TestCase):
    def test_is_ip(self):
        d = DifferingDomainClassifier()
        self.assertTrue(d.is_ip('https://192.128.0.1:80'))
        self.assertFalse(d.is_ip('https://www.google.com'))

if __name__ == '__main__':
    unittest.main()
