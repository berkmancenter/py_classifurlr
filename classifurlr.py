import json, argparse, itertools, base64, difflib, logging, re
import ipaddress, urllib.parse, concurrent.futures, pprint
import tldextract, dateutil.parser
from collections import defaultdict
from haralyzer import HarParser, HarPage
from bs4 import BeautifulSoup
from .similarityMetrics import similarity_metrics

def parse_args():
    parser = argparse.ArgumentParser(description='Determine whether a collection of pages is inaccessible')
    parser.add_argument('session_file', type=open,
            help='file containing JSON detailing HTTP requests + responses')
    parser.add_argument('--debug', action='store_true',
            help='Log debugging info')
    return parser.parse_args()

def normalize(weights):
    if min(weights) <= 0: raise ValueError('No weight can be zero or negative')
    total = sum(weights)
    return [weight / total for weight in weights]

def interpolate(domain, rang, x):
    if domain[1] - domain[0] == 0:
        raise ValueError('Domain must be wider than single value')
    # Simple linear interpolation
    slope = (rang[1] - rang[0]) / (domain[1] - domain[0])
    intercept = rang[0] - (slope * domain[0])
    # Clip to range
    return min([max([rang[0], x * slope + intercept]), rang[1]])

class NotEnoughDataError(LookupError):
    pass

def har_entry_response_content(entry):
    try:
        content = entry['response']['content']
    except Exception:
        raise NotEnoughDataError('Could not parse entry content')
    if 'text' not in content:
        raise NotEnoughDataError('"text" field not found in entry content')
    text = content['text']
    if 'encoding' in content and content['encoding'] == 'base64':
        text = base64.b64decode(text)
    # BeautifulSoup takes care of the document encoding for us.
    try:
        return str(BeautifulSoup(text, 'lxml'))
    except Exception as e:
        raise NotEnoughDataError('Could not parse entry content')

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

    def is_vpn_timeout(self, page):
        errors = self.session.get_page_errors(page.page_id)
        if errors is None or len(errors) == 0:
            return False
        return any([
                e.startswith("(28, 'Resolving timed out") or
                e.startswith("(28, 'Operation timed out") or
                e.startswith("(28, 'Connection timed out") or
                e.startswith("(7, 'Failed to connect") for e in errors])

    def is_filtered_out(self, page):
        return self.is_captcha_challenge(page) or self.is_vpn_timeout(page)

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

class BlockedFinder():
    def __init__(self):
        self.name = 'Blocked Finder'
        self.desc = 'Determines whether a session is blocked by looking for blocked pages'

    def process(self, session_classification):
        if not session_classification.is_down(): return session_classification
        for pc in session_classification.constituents:
            for pcc in pc.constituents:
                if pcc.is_blocked() or pc.is_blocked():
                    pc.mark_blocked()
                    session_classification.mark_blocked()

        return session_classification

class Classification:
    DOWN = 'down'
    UP = 'up'
    INCONCLUSIVE = 'inconclusive'

    def __init__(self, subject, classifier, direction=None, confidence=None,
            constituents=None, error=None, blocked=None):
        self.subject = subject
        self.classifier = classifier
        self.direction = direction
        self.confidence = confidence
        self.constituents = constituents
        self.error = error
        self.blocked = blocked

    def subject_id(self):
        if hasattr(self.subject, 'page_id'):
            return self.subject.page_id
        if 'url' in self.subject:
            return self.subject['url']
        return ''

    def mark_blocked(self):
        self.blocked = True
        self.mark_down(1.0)

    def mark_not_blocked(self):
        self.blocked = False

    def mark_up(self, confidence=None):
        self.direction = self.UP
        self.mark_not_blocked()
        if self.confidence is None:
            self.confidence = confidence

    def mark_down(self, confidence=None):
        self.direction = self.DOWN
        if self.confidence is None:
            self.confidence = confidence

    def mark_inconclusive(self, error=None):
        self.direction = self.INCONCLUSIVE
        if self.error is None:
            self.error = error

    def is_up(self):
        return self.direction == self.UP

    def is_down(self):
        return self.direction == self.DOWN

    def is_inconclusive(self):
        return self.direction == self.INCONCLUSIVE

    def is_blocked(self):
        return self.blocked

    def as_dict(self):
        d = {
                'subject': self.subject_id(),
                'status': self.direction,
                'blocked': self.blocked,
                'confidence': round(self.confidence, 6) if self.confidence else None,
                'classifier': self.classifier.slug(),
                'error': str(self.error) if self.error else None,
                'version': self.classifier.version
                }
        if self.constituents is not None:
            d['constituents'] = []
            for constituent in self.constituents:
                d['constituents'].append(constituent.as_dict())
        return d

    def as_json(self):
        return json.dumps(self.as_dict(), indent=2)

class Classifier:
    def __init__(self):
        self.name = '__placeholder__'
        self.desc = '__placeholder__'
        self.version = '0.1'

    def slug(self):
        return self.name.lower().replace(' ', '_')

    def page_down_confidence(self, page, session):
        raise NotImplementedError('Classifier may implement page_down_confidence')

    def is_page_blocked(self, page, session, classification):
        if classification.is_up(): return False
        return None

    def classify_page(self, page, session):
        classification = Classification(page, self)
        try:
            down_confidence = self.page_down_confidence(page, session)
            if down_confidence >= 0.5:
                classification.mark_down((down_confidence - 0.5) * 2.0)
            else:
                classification.mark_up((0.5 - down_confidence) * 2.0)
        except NotEnoughDataError as e:
            classification.mark_inconclusive(e)
        if self.is_page_blocked(page, session, classification):
            classification.mark_blocked()
        return classification

class ClassifyPipeline(Classifier):
    def __init__(self, filters, classifiers, post_processors):
        Classifier.__init__(self)
        self.name = 'Classification Pipeline'
        self.desc = 'Classifies by passing data through multiple classifiers and weighing their results'
        self.classifiers = [classifier for classifier, _ in classifiers]
        self.weights = self.normalize_weights(classifiers)
        self.filters = filters
        self.filtered_out = []
        self.post_processors = post_processors

    # A session is made of multiple pages, a page is made of multiple entries.
    # 1. Each page will first be run through filters that might eliminate it from
    #    further consideration.
    # 2. Each page will be run through a number of classifiers that determine
    #    information about the page's up/down status.
    # 3. Each page will have it's up/down classifications rolled up into
    #    a single status.
    # 4. Each page will be run through post-processing to compute any
    #    additional info about the page (i.e. blocked).
    # 5. The pages will be considered together to give a final
    #    up/down/blocked/inconclusive verdict for the session.
    def classify(self, session):
        pages = self.filtered_pages(session)
        page_classifications = []
        if len(pages) == 0:
            session_classification = Classification(session, self,
                    Classification.INCONCLUSIVE, 1.0)
        else:
            for page in pages:
                page_classifications.append(self.classify_page(page, session))
            session_classification = self.rollup_session(session, page_classifications)
        return self.process_session_classification(session_classification)

    def classify_async(self, session):
        pages = self.filtered_pages(session)
        page_classifications = []
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = []
            for page in pages:
                futures.append(executor.submit(self.classify_page, page, session))
            for future in concurrent.futures.as_completed(futures):
                page_classifications.append(future.result())

        return self.rollup_session(session, page_classifications)

    def filtered_pages(self, session):
        pages = session.get_pages()
        logging.debug('Begin filtering: {} pages'.format(len(pages)))
        for filt in self.filters:
            logging.debug('Running filter {}'.format(filt.name))
            keep, toss = filt.filter(session, pages)
            self.filtered_out += zip(toss, [filt] * len(toss))
            pages = keep
        logging.debug('Finished filtering: {} pages'.format(len(pages)))
        if len(self.filtered_out) > 0:
            logging.debug('Filtered out: {}'.format(list(map(
                lambda p: "{} by {} filter".format(p[0].page_id, p[1].name),
                    self.filtered_out))))
        return pages

    def process_session_classification(self, sc):
        for pp in self.post_processors:
            sc = pp.process(sc)
        return sc

    def classify_page(self, page, session):
        constituents = []
        for classifier in self.classifiers:
            constituents.append(classifier.classify_page(page, session))
        return self.rollup_single_page(page, constituents)

    # There are up, down, and inconclusive classifications for each page, each
    # with a different classifier. This eliminates the inconclusive, weighs the
    # up and down independently, weighs the various classifiers, and returns
    # down if there is any down confidence.
    def rollup_single_page(self, page, constituents):
        classification = Classification(page, self, constituents=constituents)
        conclusive = [c for c in constituents if not c.is_inconclusive()]
        if len(conclusive) == 0:
            classification.mark_inconclusive(NotEnoughDataError('No conclusive tests'))
            return classification
        ups, downs = [], []
        for c in conclusive:
            if c.is_up(): ups.append(c)
            if c.is_down(): downs.append(c)
        down_conf = self.tally_confidences(downs)
        # We're always biased towards down.
        if down_conf > 0.0:
            classification.mark_down(down_conf)
        else:
            up_conf = min([c.confidence for c in ups])
            classification.mark_up(up_conf)
        return classification

    def tally_confidences(self, classifications):
        if len(classifications) == 0: return 0.0
        #TODO Learn math so I can make this correct.
        classifications.sort(key=lambda c: c.confidence, reverse=True)
        confidence = 0.0
        iers = [ication.classifier for ication in classifications]
        max_weight = max([w for c, w in self.weights.items() if c in iers])
        for ication in classifications:
            weight = self.weights[ication.classifier] / max_weight
            confidence += (1 - confidence) * ication.confidence * weight
        return confidence

    # We need to pool page confidences into a single session classification.
    # Intuitively, more recent requests should count more than older requests,
    # and one down test should count more than one up test.
    def rollup_session(self, session, page_classifications):
        classification = Classification(session, self,
                constituents=page_classifications)
        total_conf = 0.0
        total_weight = 0.0
        most_recent = max([dateutil.parser.parse(p.subject.startedDateTime) for p in
            page_classifications])
        for c in page_classifications:
            weight = self.classification_weight(c, most_recent)
            conf = c.confidence * weight
            if c.is_down(): conf *= -1.0
            total_conf += conf
            total_weight += weight
        if total_conf < 0:
            classification.mark_down(total_conf * -1 / total_weight)
        else:
            classification.mark_up(total_conf / total_weight)
        return classification

    def classification_weight(self, c, now):
        down_vs_up_weight = 1.5
        look_back_days = 60
        weight_from_status = 1.0 if c.is_up() else down_vs_up_weight
        seconds_old = (now - dateutil.parser.parse(c.subject.startedDateTime)).total_seconds()
        domain = [look_back_days * 24 * 60 * 60 * -1, 0.0]
        rang = [0.0, 1.0]
        weight_from_age = interpolate(domain, rang, -1 * seconds_old)
        return weight_from_status * weight_from_age

    def normalize_weights(self, classifiers):
        normed = normalize([w for _, w in classifiers])
        weights = {}
        for i, classifier in enumerate(classifiers):
            weights[classifier[0]] = normed[i]
        return weights

    def percent_down_pages(self, pages):
        down_count = 0.0
        total_count = 0.0

        percent_down = 0.0
        if total_count > 0:
            percent_down = down_count / total_count
        return percent_down

    def page_statuses(self, session, pages):
        statuses = {}
        for page in pages:
            try:
                if self.is_page_down(page):
                    statuses[page.page_id] = Classification.DOWN
                else:
                    statuses[page.page_id] = Classification.UP
            except NotEnoughDataError as e:
                # Don't include pages if the classifier doesn't know anything.
                statuses[page.page_id] = e
                logging.info(e)
                continue
        return statuses

class EmptyPageClassifier(Classifier):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Empty page'
        self.desc = 'A classifier that says pages with very little content are down'
        self.size_cutoff = 300 # bytes

    def page_down_confidence(self, page, session):
        total_size = page.get_total_size(page.entries)
        return 1.0 if total_size <= self.size_cutoff else 0.0

class StatusCodeClassifier(Classifier):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Status code'
        self.desc = 'A simple classifier that says all non-2xx status codes are down'

    def page_down_confidence(self, page, session):
        entry = page.actual_page
        if entry is None:
            raise NotEnoughDataError('No final page found')
        if 'response' not in entry or 'status' not in entry['response']:
            raise NotEnoughDataError('"response" or "status" not found in entry '
                    'for URL "{}"'.format(entry['rekwest']['url']))
        status = page.actual_page['response']['status']
        logging.debug("{} - Page: {} - Status: {}".format(self.slug(), page.page_id,
            status))
        return 1.0 if status < 200 or status > 299 else 0.0

class ErrorClassifier(Classifier):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Error'
        self.desc = 'Classifies all session that contain errors as down'

    def is_blocked_in_china(self, page, session, classification):
        country = session.get_page_country_code(page.page_id)
        if country is None or country != 'CN':
            return None # None means we don't know if it's blocked or not
        errors = [session.get_page_errors(p.page_id) for p in session.get_pages()]
        # All pages must have the error, and there must be more than one page.
        if errors is None or None in errors or [] in errors or len(errors) <= 1: return None
        errors = [e[0] for e in errors]
        if all(['Operation canceled' in e for e in errors]):
            return True
        return None

    def is_blocked_in_kazakhstan(self, page, session, classification):
        country = session.get_page_country_code(page.page_id)
        if country is None or country != 'KZ':
            return None # None means we don't know if it's blocked or not
        errors = [session.get_page_errors(p.page_id) for p in session.get_pages()]
        # All pages must have the error, and there must be more than one page.
        if errors is None or None in errors or len(errors) <= 1: return None
        errors = [e[0] for e in errors]
        if all([('Operation canceled' in e) for e in errors]):
            return True
        return None

    def is_blocked_in_lebanon(self, page, session, classification):
        country = session.get_page_country_code(page.page_id)
        if country is None or country != 'LB':
            return None
        errors = [session.get_page_errors(p.page_id) for p in session.get_pages()]
        # All pages must have the error, and there must be more than one page.
        if errors is None or None in errors or len(errors) <= 1: return None
        errors = [e[0] for e in errors]
        if all([('Connection closed' in e) for e in errors]):
            return True
        return None

    def is_page_blocked(self, page, session, classification):
        if classification.is_up(): return False
        return (self.is_blocked_in_china(page, session, classification) or
                self.is_blocked_in_lebanon(page, session, classification) or
                self.is_blocked_in_kazakhstan(page, session, classification))

    def page_down_confidence(self, page, session):
        errors = session.get_page_errors(page.page_id)
        if errors is None or len(errors) == 0:
            raise NotEnoughDataError('No errors for page "{}"'.format(page.page_id))
        logging.debug("{} - Page: {} - Errors: {}".format(self.slug(), page.page_id,
            errors))
        return 1.0 if len(errors) > 0 else 0.0

class ThrottleClassifier(Classifier):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Throttle'
        self.desc = 'Detects excessively long load times that might indicate throttling'
        self.total_confidence_above_size = 600
        self.time_threshold = 5 * 60 * 1000 # 5 minutes
        self.bandwidth_threshold = 50 # kbps

    def page_down_confidence(self, page, session):
        bites = page.get_total_size(page.entries)
        kilobits = bites * 8 / 1000.0
        mss = page.get_load_time()
        seconds = mss / 1000.0
        kilobits_per_sec = kilobits/seconds
        logging.debug("{} - Page: {} - Bytes: {} - Time (ms): {} - kbps: {}".format(
            self.slug(), page.page_id, bites, mss, round(kilobits_per_sec, 3)))
        down = (mss >= self.time_threshold
                and kilobits_per_sec <= self.bandwidth_threshold)
        confidence = 0.0
        if down:
            # Simple linear intepolation between 0 and 100% confidence
            confidence = min(1.0, bites / self.total_confidence_above_size)
        return confidence

class ClassifierWithBaseline(Classifier):
    def get_baseline(self, session):
        baseline = session.get_baseline()
        if baseline is None:
            raise NotEnoughDataError('Could not locate baseline for URL '
                    '"{}"'.format(session.url))
        return baseline

class CosineSimilarityClassifier(ClassifierWithBaseline):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Cosine similarity'
        self.desc = ('Uses cosine similarity between a page and a baseline '
            'to determine whether a page is a block page')
        self.page_length_threshold = 0.3019
        self.cosine_sim_threshold = 0.816
        self.dom_sim_threshold = 0.995

    def page_down_confidence(self, page, session):
        baseline = self.get_baseline(session)
        baseline_content = har_entry_response_content(baseline.actual_page)
        try:
            this_content = har_entry_response_content(page.actual_page)
        except NotEnoughDataError as e:
            raise NotEnoughDataError('Could not locate page '
                    'content for URL "{}"'.format(page.url)) from e
        metrics = similarity_metrics(baseline_content, this_content)
        logging.debug("{} - Page: {} - Metric: {}".format(self.slug(), page.page_id,
            round(metrics['cosine similarity'], 3)))
        return 1.0 if metrics['cosine similarity'] <= self.cosine_sim_threshold else 0.0

class PageLengthClassifier(ClassifierWithBaseline):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Page length'
        self.desc = 'Detects whether a page is a block page by page length given a baseline'
        self.page_length_threshold = 0.3019

    def response_len(self, entry):
        try:
            return entry['response']['content']['size']
        except KeyError as e:
            raise NotEnoughDataError('Could not determine response '
                    'size for URL "{}"'.format(entry['request']['url'])) from e

    def page_down_confidence(self, page, session):
        baseline = self.get_baseline(session)
        baseline_len = self.response_len(baseline.actual_page)
        this_content_len = self.response_len(page.actual_page)
        length_ratio = abs(baseline_len - this_content_len) / max(baseline_len, this_content_len)
        logging.debug("{} - Page: {} - Baseline: {} - Content: {} - Diff Ratio: {}".format(
            self.slug(), page.page_id, baseline_len, this_content_len, round(length_ratio, 3)))
        return 1.0 if length_ratio >= self.page_length_threshold else 0.0

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
                re.escape('This web site has been blocked for violating regulations and laws of Kingdom of Bahrain.'),# BH
                re.escape('nba.com.cy/Eas/eas.nsf/All/6F7F17A7790A55C8C2257B130055C86F'),        # CY
                re.escape('lagt at blokere for adgang til siden.'),                              # DK
                re.escape('xtpage = "page-blocage-terrorisme"'),                                 # FR
                re.escape('www.gamingcommission.gov.gr/index.php/forbidden-access-black-list/'), # GR
                re.escape('14. pontja, illetve 36/G'),                                           # HU
                'iframe [^>]* src=["\']https?:\/\/www\.airtel\.in\/dot\/',                       # IN
                re.escape('The page you have requested has been blocked'),                       # IN
                re.escape('Your requested url has been blocked as per the directions received from Department of Telecommunications,Government of India.'), # IN
                re.escape('Your requested URL has been blocked as per the directions received from Department of Telecommunications, Government of India.'), # IN
                'iframe [^>]* src=["\']https?:\/\/10\.10',                                       # IR
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
                re.escape('iframe src="http://103.208.24.21'),                                   # TH
                re.escape('ถูกระงับโดยกระทรวงดิจิทัลเพื่อเศรษฐกิจและสังคม'),                              # TH
                re.escape('<title>Telekomünikasyon İletişim Başkanlığı</title>'),                # TR
                re.escape('This domain name has been seized by ICE - Homeland Security Investigations'),# US


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
                ('Server', 'Protected by WireFilter'),                             # SA
                ('Location', re.escape('http://196.1.211.6:8080/alert/')),         # SD
                ('Location', re.escape('http://blocked.nb.sky.com')),              # UK
                ('Via', re.escape('1.1 C1102')),                                   # UZ
                ]

    def is_page_blocked(self, page, session, classification):
        if classification.is_down(): return True
        return None

    def page_down_confidence(self, page, session):
        for entry in page.entries:
            for header in entry['response']['headers']:
                for fprint in self.header_fingerprints:
                    if (header['name'] == fprint[0] and
                            re.search(fprint[1], header['value'])):
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

    def extract_domain(self, url):
        if self.is_ip(url):
            return urllib.parse.urlparse(url).netloc # IP and port
        return tldextract.extract(url).registered_domain

    def requested_domain(self, page):
        return self.extract_domain(page.entries[0]['request']['url'])

    def final_domain(self, page):
        if page.actual_page is None:
            raise NotEnoughDataError('No final page found')
        try:
            return self.extract_domain(page.actual_page['request']['url'])
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

class Session:
    def __init__(self, data):
        self.data = data
        self.url = self['url']
        self.pages = None
        self.baseline = None

    def __iter__(self):
        return self.data.__iter__()

    def __next__(self):
        return self.data.__next__()

    def __getitem__(self, key):
        if hasattr(self, key):
            return getattr(self, key)
        return self.data.__getitem__(key)

    def get_url(self):
        return self.url

    def get_baseline_id(self):
        if 'baseline' not in self: return None
        return self['baseline']

    def get_baseline(self):
        if self.baseline: return baseline
        for page in self.get_pages():
            if page.page_id == self.get_baseline_id():
                self.baseline = page
                return self.baseline
        return None

    def get_pages(self):
        if self.pages: return self.pages
        try:
            if 'har' not in self: return []
            har_parser = HarParser(self['har'])
            self.pages = har_parser.pages
            return self.pages
        except Exception as e:
            pprint.pprint(e)
            logging.warning('Saw exception when parsing HAR: {}'.format(e))
            return []

    def get_page_details(self, page_id):
        if page_id not in self['pageDetail']:
            return None
        return self['pageDetail'][page_id]

    def get_page_errors(self, page_id):
        details = self.get_page_details(page_id)
        if 'errors' not in details:
            return None
        return details['errors']

    def get_page_country_code(self, page_id):
        details = self.get_page_details(page_id)
        if 'countryCode' not in details:
            return None
        return details['countryCode']

def run(session):
    filters = [
            RelevanceFilter(),
            InconclusiveFilter()
            ]
    classifiers = [
            (StatusCodeClassifier(), 1.0),
            (ErrorClassifier(), 1.0),
            (PageLengthClassifier(), 1.0),
            (ThrottleClassifier(), 1.0),
            (EmptyPageClassifier(), 1.0),
            (CosineSimilarityClassifier(), 1.0),
            (DifferingDomainClassifier(), 1.0),
            (BlockpageSignatureClassifier(), 1.0),
            ]
    post_processors = [
            BlockedFinder()
            ]
    pipeline = ClassifyPipeline(filters, classifiers, post_processors)
    session = Session(session)
    classification = pipeline.classify(session)
    return classification

if __name__ == '__main__':
    args = parse_args()
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    c = run(json.load(args.session_file))
    print(c.as_json())
