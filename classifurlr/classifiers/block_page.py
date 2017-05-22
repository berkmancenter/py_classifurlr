import re, logging

from ..classification import Classifier, NotEnoughDataError
from ..url_utils import extract_domain
from ..har_utils import har_entry_response_content

class BlockpageSignatureClassifier(Classifier):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Block page signature'
        self.desc = ('Uses text patterns found in pre-identified block pages '
                ' to detect blocking')

        # Some signatures from https://github.com/TheTorProject/ooni-pipeline/blob/master/pipeline/batch/sql_tasks.py
        # Others from ICLab https://github.com/iclab/iclab-dmp/blob/master/primitives/block_page_detection.py
        # And others we found ourselves

        #TODO: (I've attempted all these, but failed.)
        # Find blockpage for Azerbaijan
        # Develop metric for Ethiopia (looks like 403s from "nginx" server)
        # Myanmar block page - look at OONI report

        self.url_patterns = [
'https?:\/\/www\.anonymous\.com\.bh',                                                      # BH
'https?:\/\/nba\.com\.cy\/Eas\/eas\.nsf\/All\/6F7F17A7790A55C8C2257B130055C86F',           # CY
'https?:\/\/www\.gamingcommission\.gov\.gr\/index\.php\/forbidden\-access\-black\-list\/', # GR
'https?:\/\/internet\-positif\.org',                                                        # ID
'https?:\/\/www\.airtel\.in\/dot\/',                                                       # IN
'https?:\/\/10\.10',                                                                       # IR
'https?:\/\/peyvandha\.ir',                                                                # IR
'https?:\/\/warning\.or\.kr',                                                              # KR
'https?:\/\/block\-no\.altibox\.net\/',                                                     # NO
'https?:\/\/block\.om\/',                                                                  # OM
'https?:\/\/mobilegen\.vodafone\.pt\/denied\/dn',                                          # PT
'https?:\/\/www\.vodafone\.qa\/alu\.cfm',                                                  # QA
'https?:\/\/eais\.rkn\.gov\.ru\/',                                                         # RU
'https?:\/\/warning\.rt\.ru',                                                              # RU
'https?:\/\/www\.atlex\.ru\/block\.html',                                                  # RU
'https?:\/\/block\.acs\-group\.net\.ru\/block\/',                                           # RU
'https?:\/\/blackhole\.beeline\.ru\/.*',                                                   # RU
'https?:\/\/128\.204\.240\.1',                                                             # SA
'https?:\/\/196\.29\.164\.27\/ntc\/ntcblock\.html',                                        # SD
'https?:\/\/196\.1\.211\.6:8080\/alert\/',                                                 # SD
'https?:\/\/www\.starhub\.com\/mda\-blocked\/01\.html',                                     # SG
'https?:\/\/103\.208\.24\.21',                                                             # TH
'https?:\/\/blocked\.nb\.sky\.com',                                                        # UK

"https?:\/\/(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?:\:[0-9]{2,5})?\/webadmin\/deny\/",             # NetSweeper
"https?:\/\/(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?:\:[0-9]{2,5})?\/blocked\.html",
]


        self.body_fingerprints = [
re.escape('that is considered illegal according to Belgian legislation'),        # BE
re.escape('This web site has been blocked for violating regulations and '
    'laws of Kingdom of Bahrain.'),                                              # BH
re.escape('lagt at blokere for adgang til siden.'),                              # DK
re.escape('xtpage = "page-blocage-terrorisme"'),                                 # FR
re.escape('14. pontja, illetve 36/G'),                                           # HU
re.escape('access to this page is blocked due to Communication and '
    'Informatics Ministerial Decree Number 19/2014 regarding Internet Safe'),    # ID
re.escape('The page you have requested has been blocked'),                       # IN
re.escape('Your requested url has been blocked as per the directions '
    'received from Department of Telecommunications,Government of India.'),      # IN
re.escape('Your requested URL has been blocked as per the directions received '
    'from Department of Telecommunications, Government of India.'),              # IN
re.escape('GdF Stop Page'),                                                      # IT
re.escape('<meta name="kcsc" content="blocking" />'),                            # KR
re.escape('قد حجب الموقع بناء لأمر القضاء اللبناني'),                             # LB
re.escape('This website is not available in Malaysia as it violate'),            # MY
re.escape('prohibited for viewership from within Pakistan'),                     # PK
re.escape('page should not be blocked please '
    '<a href="http://www.internet.gov.sa/'),                                     # SA
re.escape('it contravenes the Broadcasting (Class Licence) Notification '
    'issued by the Info-communications Media Development Authority'),            # SG
re.escape('access is restricted by the Media Development Authority'),            # SG
re.escape('ถูกระงับโดยกระทรวงดิจิทัลเพื่อเศรษฐกิจและสังคม'),                              # TH
re.escape('could have an affect on or be against the security of the Kingdom, '
    'public order or good morals.'),                                             # TH
re.escape('<title>Telekomünikasyon İletişim Başkanlığı</title>'),                # TR
re.escape("The url has been blocked"),
]

        # These are countries for which we detect blocking by looking for certain
        # header values.
        self.header_fingerprints = [
                ('Server', 'Protected by WireFilter'), # SA
                ('Via', re.escape('1.1 C1102')),       # UZ
                ]


    def contains_bad_iframe(self, page, session):
        domains = self.get_domains_that_constitute_blocked(page, session)
        for e in page.entries:
            body = har_entry_response_content(e)
            entry_domain = extract_domain(e['request']['url'])
            for url in self.url_patterns:
                fprint = 'iframe [^>]* src=["\']{}'.format(url)
                match = re.search(fprint, body)
                if match is not None:
                    if entry_domain in domains:
                        logging.debug('{} - Page: {} - Body Pattern: "{}" '
                                '- Matched: "{}"'.format(self.slug(), page.page_id,
                                    fprint, match.group(0)))
                        return True
                    else:
                        logging.warning('{} - Saw different domain blocked! - '
                                'Requested domains: {} - Blocked domain: {} - '
                                'Body Pattern: "{}" - Matched: "{}"'.format(
                                    self.slug(), domains, entry_domain, fprint,
                                    match.group(0)))
        return False

    def contains_bad_redirect(self, page, session):
        domains = self.get_domains_that_constitute_blocked(page, session)
        for entry in page.entries:
            entry_domain = extract_domain(entry['request']['url'])
            for header in entry['response']['headers']:
                for url in self.url_patterns:
                    if (header['name'] == 'Location' and
                            re.search(url, header['value'])):
                        if entry_domain in domains:
                            logging.debug('{} - Page: {} - Header Pattern: "{}" - '
                                'Header: "{}" - Value: "{}"'.format(self.slug(),
                                    page.page_id, fprint[1], header['name'],
                                    header['value']))
                            return True
                        else:
                            logging.warning('{} - Saw different domain blocked! - '
                                'Requested domains: {} - Blocked domain: {} - '
                                    'Page: {} - Header Pattern: "{}" - '
                                'Header: "{}" - Value: "{}"'.format(self.slug(),
                                    domains, entry_domain, page.page_id,
                                    fprint[1], header['name'],
                                    header['value']))
        return False

    def contains_bad_body_text(self, page, session):
        domains = self.get_domains_that_constitute_blocked(page, session)
        for e in page.entries:
            body = har_entry_response_content(e)
            entry_domain = extract_domain(e['request']['url'])
            for fprint in self.body_fingerprints:
                match = re.search(fprint, body)
                if match is not None:
                    if entry_domain in domains:
                        logging.debug('{} - Page: {} - Body Pattern: "{}" '
                                '- Matched: "{}"'.format(self.slug(), page.page_id,
                                    fprint, match.group(0)))
                        return True
                    else:
                        logging.warning('{} - Saw different domain blocked! - '
                                'Requested domains: {} - Blocked domain: {} - '
                                'Body Pattern: "{}" - Matched: "{}"'.format(
                                    self.slug(), domains, entry_domain, fprint,
                                    match.group(0)))
        return False

    def contains_bad_header(self, page, session):
        domains = self.get_domains_that_constitute_blocked(page, session)
        for entry in page.entries:
            entry_domain = extract_domain(entry['request']['url'])
            for header in entry['response']['headers']:
                for fprint in self.header_fingerprints:
                    if (header['name'] == fprint[0] and
                            re.search(fprint[1], header['value'])):
                        if entry_domain in domains:
                            logging.debug('{} - Page: {} - Header Pattern: "{}" - '
                                'Header: "{}" - Value: "{}"'.format(self.slug(),
                                    page.page_id, fprint[1], header['name'],
                                    header['value']))
                            return True
                        else:
                            logging.warning('{} - Saw different domain blocked! - '
                                'Requested domains: {} - Blocked domain: {} - '
                                    'Page: {} - Header Pattern: "{}" - '
                                'Header: "{}" - Value: "{}"'.format(self.slug(),
                                    domains, entry_domain, page.page_id,
                                    fprint[1], header['name'],
                                    header['value']))
        return False

    def contains_request_for_bad_url(self, page, session):
        # We should only run this if we don't have any content as a last-ditch
        # effort to find blocked pages (where the bad URLs might be things like
        # iframes). Otherwise, this could give us a false positive in the case
        # of embedded content being blocked. This situation occurs with
        # header-only data.
        entry_contents = []
        for e in page.entries:
            try:
                entry_contents.append(har_entry_response_content(e))
            except NotEnoughDataError:
                entry_contents.append(None)
        if not all([content is None for content in entry_contents]):
            return False

        for e in page.entries:
            for url in self.url_patterns:
                match = re.search(url, e['request']['url'])
                if match is not None:
                    logging.debug('{} - Page: {} - Pattern: "{}" '
                            '- Matched: "{}"'.format(self.slug(), page.page_id,
                                url, match.group(0)))
                    return True
        return False

    # Sometimes a domain is blocked that is only embedded in a larger page. For
    # example, Google Maps looks blocked in Uzbekistan, so pages that embed
    # Google Maps look blocked, but aren't. If any of the domains that this
    # function returns look blocked, then the page is blocked - otherwise, it's
    # not.
    def get_domains_that_constitute_blocked(self, page, session):
        domains = [session.get_domain()]
        if (page.actual_page and
                page.actual_page['request'] and
                page.actual_page['request']['url']):
            domains.append(extract_domain(page.actual_page['request']['url']))
        return domains

    def is_page_blocked(self, page, session, classification):
        if classification.is_down(): return True
        return None

    def page_down_confidence(self, page, session):
        conditions = [
            self.contains_bad_iframe,
            self.contains_bad_redirect,
            self.contains_bad_body_text,
            self.contains_bad_header,
            self.contains_request_for_bad_url,
            ]
        for is_true_for in conditions:
            try:
                if is_true_for(page, session): return 1.0
            except NotEnoughDataError:
                continue

        return 0.0
