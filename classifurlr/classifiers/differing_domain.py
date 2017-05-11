import logging, difflib, ipaddress, urllib.parse

from classifurlr.classification import Classifier, NotEnoughDataError
from classifurlr.url_utils import extract_domain

class DifferingDomainClassifier(Classifier):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Differing domain'
        self.desc = ('Detects whether the requested domain and the final domain '
                'are significantly different')
        self.pad_domain_to = 50
        self.multiplier = 2.5 # Ratios of very different URLs were around 0.28
        self.use_dice = True

    def is_ip(self, url):
        netloc = urllib.parse.urlparse(url).netloc
        if ':' in netloc:
            netloc = netloc.split(':')[0]
        try:
            ipaddress.ip_address(netloc)
            return True
        except ValueError:
            return False

    # https://en.wikibooks.org/wiki/Algorithm_Implementation/Strings/Dice%27s_coefficient#Python
    def dice_coefficient(self, a, b):
        if not len(a) or not len(b): return 0.0
        """ quick case for true duplicates """
        if a == b: return 1.0
        """ if a != b, and a or b are single chars, then they can't possibly match """
        if len(a) == 1 or len(b) == 1: return 0.0

        """ use python list comprehension, preferred over list.append() """
        a_bigram_list = [a[i:i+2] for i in range(len(a)-1)]
        b_bigram_list = [b[i:i+2] for i in range(len(b)-1)]

        a_bigram_list.sort()
        b_bigram_list.sort()

        # assignments to save function calls
        lena = len(a_bigram_list)
        lenb = len(b_bigram_list)
        # initialize match counters
        matches = i = j = 0
        while (i < lena and j < lenb):
            if a_bigram_list[i] == b_bigram_list[j]:
                matches += 2
                i += 1
                j += 1
            elif a_bigram_list[i] < b_bigram_list[j]:
                i += 1
            else:
                j += 1

        score = float(matches)/float(lena + lenb)
        return score

    def get_diff_ratio(self, a, b):
        # return 1 if they are very different, 0 if identical
        if self.use_dice:
            return 1.0 - self.dice_coefficient(a, b)
        else:
            ratio = difflib.SequenceMatcher(None,
                    a.zfill(self.pad_domain_to), b.zfill(self.pad_domain_to)).ratio()
            return min(1.0, (1 - ratio) * self.multiplier)

    def requested_domain(self, page):
        return extract_domain(page.entries[0]['request']['url'])

    def final_domain(self, page):
        if page.actual_page is None:
            raise NotEnoughDataError('No final page found')
        try:
            return extract_domain(page.actual_page['request']['url'])
        except KeyError as e:
            raise NotEnoughDataError('Could not find request URL for '
                    'URL "{}"'.format(page.url)) from e

    def is_page_blocked(self, page, session, classification):
        block_page_urls = [
                'http://blocked.zajil.com/' # KW
                ]
        if page.actual_page and page.actual_page['request']['url'] in block_page_urls:
            return True
        return None

    def page_down_confidence(self, page, session):
        requested = self.requested_domain(page)
        final = self.final_domain(page)
        ratio = self.get_diff_ratio(requested, final)
        if ratio > 0:
            logging.debug('{} - Page: {} - Requested: {} - Final: {} - Diff: {}'.format(
                self.slug(), page.page_id, requested, final, round(ratio, 6)))
        return ratio

