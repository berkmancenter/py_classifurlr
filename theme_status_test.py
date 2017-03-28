import unittest
from theme_status import ThemeInCountryStatus

class ThemeInCountryStatusTest(unittest.TestCase):
    def test_url_category(self):
        country = 'PK'
        t = ThemeInCountryStatus('Political Content', country, [])
        c = t.get_url_category('http://zougla.gr/')
        self.assertEqual('NEWS', c)

    def test_theme_categories(self):
        theme = 'Political Content'
        expected = ['DEV', 'ENV', 'FEXP', 'HAL', 'HATE', 'HUMR', 'MINR',
                'NEWS', 'POLT', 'REL', 'WOMR']
        t = ThemeInCountryStatus('Political Content', 'PK', [])
        self.assertEqual(expected, sorted(t.get_theme_categories()))

    def test_get_urls_categories(self):
        urls = [
                'http://www.alhijazonline.com/',
                'https://www.torproject.org/',
                'https://twitter.com']
        expected = {
                'http://www.alhijazonline.com/': 'POLT',
                'https://www.torproject.org/': 'ANON',
                'https://twitter.com': 'PLATFORM'}
        t = ThemeInCountryStatus('Political Content', 'SA', [])
        self.assertEqual(expected, t.get_urls_categories(urls))

if __name__ == '__main__':
    unittest.main()
