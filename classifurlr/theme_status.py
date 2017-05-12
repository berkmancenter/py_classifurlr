import sqlite3, csv, argparse, json, urllib.parse, sys, urllib.request
import logging
from pprint import pprint

from .categorization import Categorization, CATEGORY_CSV

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
    # We test the status in this order because e.g. pervasive blocking is also
    # substantial blocking, but substantial is not necessarily pervasive.
    TEST_ORDER = ['pervasive', 'substantial', 'selective', 'none', 'suspected']

    def __init__(self, theme, country, url_statuses):
        self.theme = theme.strip()
        self.country = country.strip().upper()
        self.status = None
        self.categories = None
        self.url_statuses = url_statuses

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
                if 'blocked' in status and status['blocked'] == True:
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
        return True

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
        self.categories = Categorization.get_theme_categories(self.theme)
        self._add_category_to_url_statuses()
        self._remove_uncategorized_statuses()
        self._set_status()
        return self.status

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
        logging.info('Categorizing URLs')
        statuses_to_categorize = [s for s in self.url_statuses
                if 'category' not in s or s['category'] is None]
        if len(statuses_to_categorize) == 0: return
        url_cats = Categorization.get_urls_categories([s['url'] for s in statuses_to_categorize], self.country)
        for s in statuses_to_categorize:
            s['category'] = url_cats[s['url']].strip().upper()
        logging.info('Finished categorizing URLs')

def run(theme, country, url_statuses):
    c = ThemeInCountryStatus(theme, country, url_statuses)
    c.classify()
    return c

if __name__ == '__main__':
    args = parse_args()
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    c = ThemeInCountryStatus(args.theme, args.country, json.load(args.statuses))
    c.classify()
    print(c.as_json())
