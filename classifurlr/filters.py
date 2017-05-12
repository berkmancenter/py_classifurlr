import logging, re

from .har_utils import har_entry_response_content
from .classification import NotEnoughDataError

class Filter:
    def __init__(self):
        self.name = '__placeholder__'
        self.desc = '__placeholder__'
        self.version = '0.1'

    def slug(self):
        return self.name.lower().replace(' ', '_')

    def filter(self, session, pages):
        self.session = session
        keep, toss = [], []
        for page in pages:
            if self.is_filtered_out(page):
                toss.append(page)
            else:
                keep.append(page)
        return (keep, toss)

    def is_filtered_out(self, page):
        raise NotImplementedError('must implement #is_filtered_out')

class InconclusiveFilter(Filter):
    def __init__(self):
        Filter.__init__(self)
        self.name = 'Inconclusive'
        self.desc = 'Filters out pages that look inconclusive (CDN captchas, VPN timeouts, etc.)'

    def is_captcha_challenge(self, page):
        entry = page.actual_page
        if entry is None:
            return False
        if 'response' not in entry or 'status' not in entry['response']:
            return False

        status = page.actual_page['response']['status']
        if status != 403:
            return False
        for header in entry['response']['headers']:
            # Incapsula CDN
            if (header['name'].lower() == 'set-cookie' and
                header['value'].lower().startswith('incap_ses_')):
                return True

            # Cloudflare and Akamai CDNs
            if (header['name'].lower() == 'server' and
                    (header['value'] == 'cloudflare-nginx' or
                     header['value'] == 'AkamaiGHost')):
                        return True
        return False

    def is_isp_login(self, page):
        pass

    def is_seized_domain(self, page):
        body_patterns = [
                re.escape('This domain name has been seized by ICE - Homeland Security Investigations'),# US
                ]
        try:
            body = har_entry_response_content(page.actual_page)
        except NotEnoughDataError:
            return False
        for pattern in body_patterns:
            match = re.search(pattern, body)
            if match is not None:
                logging.debug('{} - Page: {} - Body Pattern: "{}" '
                        '- Matched: "{}"'.format(self.slug(), page.page_id,
                            pattern, match.group(0)))
                return True
        return False

    def is_vpn_timeout(self, page):
        errors = self.session.get_page_errors(page.page_id)
        if errors is None or len(errors) == 0:
            return False
        return any([
                e.startswith("(28, 'Resolving timed out") or
                e.startswith("(28, 'Operation timed out") or
                e.startswith("(28, 'Connection timed out") or
                e.startswith("(7, 'Failed to connect") for e in errors if
                e is not None])

    def is_filtered_out(self, page):
        return (
                self.is_captcha_challenge(page) or
                self.is_vpn_timeout(page) or
                self.is_isp_login(page) or
                self.is_seized_domain(page)
                )

class RelevanceFilter(Filter):
    def __init__(self):
        Filter.__init__(self)
        self.name = 'Relevance'
        self.desc = 'Filters out pages that are not relevant to the given URL'

    def is_filtered_out(self, page):
        # Don't consider the baseline when classifying
        if self.session.get_baseline_id() == page.page_id:
            logging.debug("Filtering out baseline {}".format(page.page_id))
            return True

        # page.url is the initial requested url
        if not page.url.startswith(self.session.url):
            logging.info('Possibly irrelevant page when looking for '
                    '"{}": {}'.format(self.session.url, page.url))
            #return True
        return False

