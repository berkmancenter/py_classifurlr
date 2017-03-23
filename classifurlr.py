import random, json, sys, argparse, itertools, base64, difflib, logging, re
import ipaddress, urllib.parse, concurrent.futures
import tldextract, dateutil.parser
from collections import defaultdict
from haralyzer import HarParser, HarPage
from bs4 import BeautifulSoup
from similarityMetrics import similarity_metrics

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
    content = entry['response']['content']
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
        self.desc = 'Filters out pages that look inconclusive (CDN captchas, etc.)'

    def is_filtered_out(self, page):
        entry = page.actual_page
        if entry is None:
            return False
        if 'response' not in entry or 'status' not in entry['response']:
            return False

        status = page.actual_page['response']['status']
        if status != 403:
            return False
        for header in entry['response']['headers']:
            if (header['name'].lower() == 'server' and
                    (header['value'] == 'cloudflare-nginx' or
                     header['value'] == 'AkamaiGHost')):
                        return True
        return False

class RelevanceFilter(Filter):
    def __init__(self):
        Filter.__init__(self)
        self.name = 'Relevance'
        self.desc = 'Filters out pages that are not relevant to the given URL'

    def is_filtered_out(self, page):
        # Don't consider the baseline when classifying
        if 'baseline' in self.session and self.session['baseline'] == page.page_id:
            logging.debug("Filtering out baseline {}".format(page.page_id))
            return True

        # page.url is the initial requested url
        if not page.url.startswith(self.session['url']):
            logging.warning('Possibly irrelevant page when looking for '
                    '"{}": {}'.format(self.session['url'], page.url))
            #return True
        return False

class BlockedFinder():
    pass

class Classification:
    DOWN = 'down'
    UP = 'up'
    INCONCLUSIVE = 'inconclusive'

    def __init__(self, subject, classifier, direction=None, confidence=None,
            constituents=None, error=None):
        self.subject = subject
        self.classifier = classifier
        self.direction = direction
        self.confidence = confidence
        self.constituents = constituents
        self.error = error

    def subject_id(self):
        if hasattr(self.subject, 'page_id'):
            return 'page-{}'.format(self.subject.page_id)
        if 'url' in self.subject:
            return 'session-{}'.format(self.subject['url'])
        return ''

    def mark_up(self, confidence=None):
        self.direction = self.UP
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

    def as_dict(self):
        d = {
                'subject': self.subject_id(),
                'status': self.direction,
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
        return classification

class ClassifyPipeline(Classifier):
    DOWN_VS_UP_WEIGHT = 3.0
    LOOK_BACK_DAYS = 90

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
        for page in pages:
            page_classifications.append(self.classify_page(page, session))
        return self.rollup_session(session, page_classifications)

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
        har_parser = HarParser(session['har'])
        pages = har_parser.pages
        logging.debug('Begin filtering: {} pages'.format(len(pages)))
        for filt in self.filters:
            logging.debug('Running filter {}'.format(filt.name))
            keep, toss = filt.filter(session, pages)
            self.filtered_out += zip(toss, [filt] * len(toss))
            pages = keep
        logging.debug('Finished filtering: {} pages'.format(len(pages)))
        logging.debug('Filtered out: {}'.format(list(map(
            lambda p: "{} by {} filter".format(p[0].page_id, p[1].name),
                self.filtered_out))))
        return pages

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
        down_vs_up_weight = 3.0
        look_back_days = 90
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

    def page_down_confidence(self, page, session):
        if page.page_id not in session['pageDetail']:
            raise NotEnoughDataError('No page details for page "{}"'.format(page.page_id))
        errors = session['pageDetail'][page.page_id]['errors']
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
        if hasattr(self, 'baseline'):
            return self.baseline
        if 'baseline' not in session or session['baseline'] == False:
            raise NotEnoughDataError('Could not locate baseline for URL '
                    '"{}"'.format(session['url']))
        self.baseline = HarPage(session['baseline'], har_data=session['har'])
        return self.baseline

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
        try:
            baseline_content = har_entry_response_content(baseline.actual_page)
        except NotEnoughDataError as e:
            raise NotEnoughDataError('Could not locate baseline '
                    'content for URL "{}"'.format(page.url)) from e
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

        # These are the countries for which we identify blocking by looking for certain
        # fingerprint in the HTTP response body.
        self.body_fingerprints = [
                re.escape('iframe src="http://10.10'),                                           # IR
                re.escape('iframe src="http://www.anonymous.com.bh/"'),                          # BH
                re.escape('This web site has been blocked for violating regulations and laws of Kingdom of Bahrain.'),# BH
                re.escape('<title>Telekomünikasyon İletişim Başkanlığı</title>'),                # TR
                re.escape('www.gamingcommission.gov.gr/index.php/forbidden-access-black-list/'), # GR
                re.escape('http://eais.rkn.gov.ru/'),                                            # RU
                re.escape('The page you have requested has been blocked'),                       # IN
                re.escape('that is considered illegal according to Belgian legislation'),        # BE
                re.escape('GdF Stop Page'),                                                      # IT
                re.escape('nba.com.cy/Eas/eas.nsf/All/6F7F17A7790A55C8C2257B130055C86F'),        # CY
                re.escape('lagt at blokere for adgang til siden.'),                              # DK
                re.escape('xtpage = "page-blocage-terrorisme"'),                                 # FR
                re.escape('http://warning.or.kr'),                                               # KR
                re.escape('prohibited for viewership from within Pakistan'),                     # PK

                # From ICLab https://github.com/iclab/iclab-dmp/blob/master/primitives/block_page_detection.py
                "src=[\",\']http://(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?:\:[0-9]{2,5})?/webadmin/deny/",
                "src=[\",\']http://(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?:\:[0-9]{2,5})?/blocked.html",
                re.escape("The url has been blocked")
                ]

        # These are countries for which we detect blocking by looking for certain
        # header values.
        self.header_fingerprints = [
                ('Server', 'Protected by WireFilter'),                             # SA
                ('Location', re.escape('http://internet-positif.org')),            # ID
                ('Location', re.escape('http://196.1.211.6:8080/alert/')),         # SD
                ('Location', re.escape('http://www.vodafone.qa/alu.cfm')),         # QA
                ('Location', re.escape('http://www.warning.or.kr')),               # KR
                ('Location', re.escape('http://mobilegen.vodafone.pt/denied/dn')), # PT
                ('Location', re.escape('http://block-no.altibox.net/')),           # NO
                ('Location', re.escape('http://blocked.nb.sky.com')),              # UK
                ('Location', re.escape('http://warning.rt.ru')),                   # RU
                ('Via', re.escape('1.1 C1102')),                                   # UZ
                ]

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

    def page_down_confidence(self, page, session):
        requested = self.requested_domain(page)
        final = self.final_domain(page)
        ratio = self.get_diff_ratio(requested, final)
        logging.debug('{} - Page: {} - Requested: {} - Final: {} - Ratio: {}'.format(
            self.slug(), page.page_id, requested, final, round(ratio, 6)))
        return ratio

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
    classification = pipeline.classify(session)
    return classification

def app(environ, start_response):
    session_json = environ['wsgi.input'].read().decode('utf-8')
    session = json.loads(session_json)
    status = '201 Created'
    headers = [('Content-Type', 'application/json')]
    start_response(status, headers)
    c = run(session)
    return [c.as_json().encode('utf-8')]

if __name__ == '__main__':
    args = parse_args()
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    c = run(json.load(args.session_file))
    print(c.as_json())
