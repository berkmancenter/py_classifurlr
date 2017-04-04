import unittest
from .categorization import Categorization

class CategorizationTest(unittest.TestCase):
    def test_get_url_category(self):
        c = Categorization.get_url_category('http://zougla.gr/', 'PK')
        self.assertEqual('NEWS', c)

    def test_get_theme_categories(self):
        theme = 'Political Content'
        expected = ['DEV', 'ENV', 'FEXP', 'HAL', 'HATE', 'HUMR', 'MINR',
                'NEWS', 'POLT', 'REL', 'WOMR']
        self.assertEqual(expected, sorted(Categorization.get_theme_categories(theme)))

    def test_get_category_theme(self):
        category = 'FEXP'
        expected = 'Political Content'
        self.assertEqual(expected, Categorization.get_category_theme(category))

    def test_get_urls_categories(self):
        country = 'SA'
        urls = [
                'http://www.alhijazonline.com/',
                'https://www.torproject.org/',
                'https://twitter.com']
        expected = {
                'http://www.alhijazonline.com/': 'POLT',
                'https://www.torproject.org/': 'ANON',
                'https://twitter.com': 'PLATFORM'}
        self.assertEqual(expected, Categorization.get_urls_categories(urls, country))

if __name__ == '__main__':
    unittest.main()
