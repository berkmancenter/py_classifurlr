import sqlite3, csv, argparse, json, urllib.parse, sys, urllib.request
import logging
from pprint import pprint

CATEGORY_CSV = 'categories.csv'
CORE_ENDPOINT = 'http://localhost:3000/'

def parse_args():
    parser = argparse.ArgumentParser(description='Determine whether a content theme is inaccessible')
    parser.add_argument('theme',
            help='the theme in determine the status of')
    parser.add_argument('country',
            help='the two-letter ISO code of the country in question')
    parser.add_argument('statuses', type=open,
            help='file containing JSON detailing URL-in-country statuses')
    parser.add_argument('--category_csv', nargs='?', default=CATEGORY_CSV,
            help='file containing JSON detailing HTTP requests + responses')
    parser.add_argument('--debug', action='store_true',
            help='Log debugging info')
    return parser.parse_args()

class ThemeInCountryStatus:
    TEST_ORDER = ['pervasive', 'substantial', 'selective', 'suspected', 'none']

    def __init__(self, theme, country, url_statuses,
            category_csv_filename=CATEGORY_CSV):
        self.theme = theme.strip()
        self.country = country.strip().upper()
        self.status = None
        self.categories = None
        self.url_statuses = url_statuses
        self.category_csv_filename = category_csv_filename
        self.category_csv_file = open(self.category_csv_filename, 'r')


    def __del__(self):
        self.category_csv_file.close()

    def as_dict(self):
        return {
                'theme': self.theme,
                'country': self.country,
                'status': self.status,
                'categories': self.status_count_by_category(),
                }

    def as_json(self):
        return json.dumps(self.as_dict(), indent=2)

    def status_count_by_category(self):
        cat_counts = {}
        for category in self.categories:
            counts = { 'up': 0, 'down': 0, 'inconclusive': 0, 'blocked': 0,
                    'total': 0, }
            for status in self.url_statuses_for_category(category):
                counts['total'] += 1
                if status['status'].strip().lower() == 'down':
                    counts['down'] += 1
                if status['status'].strip().lower() == 'up':
                    counts['up'] += 1
                if status['status'].strip().lower() == 'inconclusive':
                    counts['inconclusive'] += 1
                if status['blocked'] == True:
                    counts['blocked'] += 1
                cat_counts[category] = counts
        return cat_counts

    def url_statuses_for_category(self, category):
        statuses = []
        for s in self.url_statuses:
            cat = s['category']
            if cat is None: continue
            if cat == category.strip().upper():
                statuses.append(s)
        return statuses

    def percent_blocked_by_category(self):
        cat_blocked = {}
        for category, status_counts in self.status_count_by_category().items():
            if status_counts['total'] > 0:
                cat_blocked[category] = (float(status_counts['blocked']) /
                        status_counts['total'])
            else:
                cat_blocked[category] = 0.0
        return cat_blocked

    # None: No category has any URLs marked as blocked.
    def is_none(self):
        return not any(s['blocked'] for s in self.url_statuses)

    # Suspected: we don't find filtering, but we're not giving up a clean bill
    # of health.
    # We're testing in reverse order, so this becomes the catch-all. If none of
    # the other tests are true, this must be (otherwise we end up with all
    # tests false).
    def is_suspected(self):
        return True

    # Selective: About one URL blocked in about one category. Effectively,
    # anything blocked that's not substantial or pervasive.
    def is_selective(self):
        return any([s['blocked'] > 0 for s in self.status_count_by_category().values()])

    # Substantial: One or more URLs blocked in a handful of categories, or
    # a bunch in one category.
    def is_substantial(self):
        if self.status is not None: return self.status == 'substantial'
        min_cats_pct_blocked = 0.25
        min_urls_blocked_in_one_cat_pct = 0.5
        by_cat = self.status_count_by_category()
        blocked_pcts = self.percent_blocked_by_category().values()
        if len(blocked_pcts) == 0:
            return False
        max_pct_blocked_in_one = max(blocked_pcts)
        blocked_cats = [cat for cat, counts in by_cat.items() if counts['blocked'] > 0]
        handful_of_cats_blocked = (float(len(blocked_cats)) / len(by_cat.keys()) >=
              min_cats_pct_blocked)
        bunch_blocked_in_one_cat = (max_pct_blocked_in_one >=
              min_urls_blocked_in_one_cat_pct)
        return handful_of_cats_blocked or bunch_blocked_in_one_cat

    # Pervasive: "handful" of blocked sites in "handful" of categories
    def is_pervasive(self):
        if self.status is not None: return self.status == 'pervasive'
        min_urls_blocked = 5
        min_pct_urls_blocked = 0.25
        min_cats_pct_blocked = 0.25
        by_cat = self.status_count_by_category()
        blocked_cats = []
        for cat, counts in by_cat.items():
            if counts['total'] == 0: continue
            pct_blocked = float(counts['blocked']) / counts['total']
            if counts['blocked'] >= min_urls_blocked or pct_blocked >= min_pct_urls_blocked:
                blocked_cats.append(cat)
        if len(by_cat.keys()) == 0:
            return False
        pct_cats_blocked = float(len(blocked_cats)) / len(by_cat.keys())
        return pct_cats_blocked >= min_cats_pct_blocked

    def classify(self):
        self._remove_irrelevant_statuses()
        self.categories = self.get_theme_categories()
        self._add_category_to_url_statuses()
        self._remove_uncategorized_statuses()
        self._set_status()
        return self.status

    def get_urls_categories(self, urls, country=None):
        endpoint = CORE_ENDPOINT + 'urls/categorize'
        country = self.country if country is None else country
        data = urllib.parse.urlencode({ 'url[]': urls, 'country': country },
                doseq=True)
        data = data.encode('ascii')
        with urllib.request.urlopen(endpoint, data) as f:
            try:
                return {url: c[country] for url, c in
                        json.loads(f.read().decode('utf-8')).items()}
            except KeyError as e:
                logging.warning('Failed to find category - URL: "{}", Country: '
                        '"{}"'.format(url, country))
                return None

    def get_url_category(self, url, country=None):
        # Rather not make this a network call, but the logic is pretty
        # complicated given multi-country lists (like global).
        endpoint = CORE_ENDPOINT + 'urls/categorize'
        country = self.country if country is None else country
        data = urllib.parse.urlencode({ 'url': url, 'country': country })
        data = data.encode('ascii')
        with urllib.request.urlopen(endpoint, data) as f:
            try:
                return json.loads(f.read().decode('utf-8'))[country]
            except KeyError as e:
                logging.warning('Failed to find category - URL: "{}", Country: '
                        '"{}"'.format(url, country))
                return None

    def get_theme_categories(self):
        categories = []
        for row in csv.DictReader(self.category_csv_file):
            if row['theme'].strip().upper() == self.theme.upper():
                categories.append(row['code'].strip().upper())
        self.category_csv_file.seek(0)
        return categories

    def _set_status(self):
        for status in self.TEST_ORDER:
            if getattr(self, 'is_{}'.format(status))():
                self.status = status
                return

    def _remove_irrelevant_statuses(self):
        to_delete = []
        for s in self.url_statuses:
            if s['country_code'] != self.country:
                to_delete.append(s)
        for s in to_delete:
            self.url_statuses.remove(s)

    def _remove_uncategorized_statuses(self):
        to_delete = []
        for s in self.url_statuses:
            if s['category'] is None:
                to_delete.append(s)
        for s in to_delete:
            self.url_statuses.remove(s)

    def _add_category_to_url_statuses(self):
        url_cats = self.get_urls_categories([s['url'] for s in self.url_statuses])
        for s in self.url_statuses:
            s['category'] = url_cats[s['url']].strip().upper()

def app(environ, start_response):
    statuses_json = environ['wsgi.input'].read().decode('utf-8')
    parsed_qs = urllib.parse.parse_qs(environ['QUERY_STRING'])
    theme = parsed_qs['theme'][0]
    country = parsed_qs['country'][0]
    statuses = json.loads(statuses_json)
    status = '201 Created'
    headers = [('Content-Type', 'application/json')]
    start_response(status, headers)
    c = ThemeInCountryStatus(theme, country, statuses)
    c.classify()
    return [c.as_json().encode('utf-8')]

if __name__ == '__main__':
    args = parse_args()
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    c = ThemeInCountryStatus(args.theme, args.country,
            json.load(args.statuses), args.category_csv)
    c.classify()
    print(c.as_json())
