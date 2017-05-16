import re, logging

from ..classification import Classifier
from ..url_utils import extract_domain
from ..har_utils import har_entry_response_content

class BlockpageSignatureClassifier(Classifier):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Block page signature'
        self.desc = ('Uses text patterns found in pre-identified block pages '
                ' to detect blocking')

        # Signatures from https://github.com/TheTorProject/ooni-pipeline/blob/master/pipeline/batch/sql_tasks.py

        #TODO: (I've attempted all these, but failed.)
        # Find blockpage for Azerbaijan
        # Develop metric for Ethiopia (looks like 403s from "nginx" server)
        # Myanmar block page - look at OONI report

        # These are the countries for which we identify blocking by looking for certain
        # fingerprint in the HTTP response body.
        self.body_fingerprints = [
                re.escape('that is considered illegal according to Belgian legislation'),        # BE
                re.escape('This web site has been blocked for violating regulations and laws of Kingdom of Bahrain.'),# BH
                'iframe [^>]* src=["\']https?:\/\/www\.anonymous\.com\.bh',                      # BH
                re.escape('nba.com.cy/Eas/eas.nsf/All/6F7F17A7790A55C8C2257B130055C86F'),        # CY
                re.escape('lagt at blokere for adgang til siden.'),                              # DK
                re.escape('xtpage = "page-blocage-terrorisme"'),                                 # FR
                re.escape('www.gamingcommission.gov.gr/index.php/forbidden-access-black-list/'), # GR
                re.escape('14. pontja, illetve 36/G'),                                           # HU
                re.escape('access to this page is blocked due to Communication and Informatics Ministerial Decree Number 19/2014 regarding Internet Safe'), # ID
                'iframe [^>]* src=["\']https?:\/\/www\.airtel\.in\/dot\/',                       # IN
                re.escape('The page you have requested has been blocked'),                       # IN
                re.escape('Your requested url has been blocked as per the directions received from Department of Telecommunications,Government of India.'), # IN
                re.escape('Your requested URL has been blocked as per the directions received from Department of Telecommunications, Government of India.'), # IN
                'iframe [^>]* src=["\']https?:\/\/10\.10',                                       # IR
                re.escape('http://peyvandha.ir'),                                                # IR
                re.escape('GdF Stop Page'),                                                      # IT
                re.escape('http://warning.or.kr'),                                               # KR
                re.escape('<meta name="kcsc" content="blocking" />'),                            # KR
                re.escape('قد حجب الموقع بناء لأمر القضاء اللبناني'),                             # LB
                re.escape('This website is not available in Malaysia as it violate'),            # MY
                'iframe [^>]* src=["\']https?:\/\/block\.om\/',                                  # OM
                re.escape('prohibited for viewership from within Pakistan'),                     # PK
                re.escape('http://eais.rkn.gov.ru/'),                                            # RU
                'iframe [^>]* src=["\']https?:\/\/128\.204\.240\.1',                             # SA
                re.escape('page should not be blocked please <a href="http://www.internet.gov.sa/'),# SA
                'iframe [^>]* src=["\']https?:\/\/196\.29\.164\.27\/ntc\/ntcblock\.html',        # SD
                re.escape('it contravenes the Broadcasting (Class Licence) Notification issued by the Info-communications Media Development Authority'),# SG
                re.escape('access is restricted by the Media Development Authority'),            # SG
                re.escape('iframe src="http://103.208.24.21'),                                   # TH
                re.escape('ถูกระงับโดยกระทรวงดิจิทัลเพื่อเศรษฐกิจและสังคม'),                              # TH
                re.escape('could have an affect on or be against the security of the Kingdom, public order or good morals.'), # TH
                re.escape('<title>Telekomünikasyon İletişim Başkanlığı</title>'),                # TR


                # From ICLab https://github.com/iclab/iclab-dmp/blob/master/primitives/block_page_detection.py
                "src=[\",\']http://(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?:\:[0-9]{2,5})?/webadmin/deny/", # NetSweeper
                "src=[\",\']http://(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?:\:[0-9]{2,5})?/blocked.html",
                re.escape("The url has been blocked")
                ]

        # These are countries for which we detect blocking by looking for certain
        # header values.
        self.header_fingerprints = [
                ('Location', re.escape('http://internet-positif.org')),            # ID
                ('Location', re.escape('http://www.warning.or.kr')),               # KR
                ('Location', re.escape('http://block-no.altibox.net/')),           # NO
                ('Location', re.escape('http://mobilegen.vodafone.pt/denied/dn')), # PT
                ('Location', re.escape('http://www.vodafone.qa/alu.cfm')),         # QA
                ('Location', re.escape('http://warning.rt.ru')),                   # RU
                ('Location', re.escape('https://www.atlex.ru/block.html')),        # RU
                ('Location', re.escape('http://block.acs-group.net.ru/block/?')),  # RU
                ('Location', 'http:\/\/blackhole\.beeline\.ru\/.*'),               # RU
                ('Server', 'Protected by WireFilter'),                             # SA
                ('Location', re.escape('http://196.1.211.6:8080/alert/')),         # SD
                ('Location', re.escape('http://www.starhub.com/mda-blocked/01.html')),# SG
                ('Location', re.escape('http://blocked.nb.sky.com')),              # UK
                ('Via', re.escape('1.1 C1102')),                                   # UZ
                ]

    def is_page_blocked(self, page, session, classification):
        if classification.is_down(): return True
        return None

    def page_down_confidence(self, page, session):
        requested_domain = session.get_domain()
        final_domain = extract_domain(page.actual_page['request']['url'])

        for entry in page.entries:
            for header in entry['response']['headers']:
                for fprint in self.header_fingerprints:
                    if (header['name'] == fprint[0] and
                            re.search(fprint[1], header['value'])):
                        blocked_domain = extract_domain(entry['request']['url'])
                        if blocked_domain not in [requested_domain, final_domain]:
                            logging.warning('{} - Saw different domain blocked! - '
                                    'Requested domain: {} - Blocked domain: {} - '
                                    'Header Pattern: "{}" - Header: "{}" - '
                                    'Value: "{}"'.format(self.slug(), requested_domain,
                                        blocked_domain, fprint[1], header['name'],
                                        header['value']))
                            continue
                        logging.debug('{} - Page: {} - Header Pattern: "{}" - '
                                'Header: "{}" - Value: "{}"'.format(self.slug(),
                                    page.page_id, fprint[1], header['name'],
                                    header['value']))
                        return 1.0

        body = har_entry_response_content(page.actual_page)
        for fprint in self.body_fingerprints:
            match = re.search(fprint, body)
            if match is not None:
                logging.debug('{} - Page: {} - Body Pattern: "{}" '
                        '- Matched: "{}"'.format(self.slug(), page.page_id,
                            fprint, match.group(0)))
                return 1.0
        return 0.0

