import random, json, sys, argparse, itertools, base64, difflib, logging, re
import ipaddress, urllib.parse
import tldextract
from haralyzer import HarParser, HarPage
from bs4 import BeautifulSoup
from similarityMetrics import similarity_metrics

def parse_args():
    parser = argparse.ArgumentParser(description='Determine whether a collection of pages is inaccessible')
    parser.add_argument('sessions_file', type=open,
            help='file containing JSON detailing a number of HTTP sessions')
    parser.add_argument('--debug', action='store_true',
            help='Log debugging info')
    return parser.parse_args()

def normalize(weights):
    if min(weights) <= 0: raise ValueError('No weight can be zero or negative')
    total = sum(weights)
    return [weight / total for weight in weights]

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

class Classification:
    DOWN = 'down'
    def __init__(self, classifier, direction=None, confidence=None, constituents=None):
        self.classifier = classifier
        self.direction = direction
        self.confidence = confidence
        self.constituents = constituents

    def as_dict(self):
        d = {
                'status': self.direction,
                'confidence': round(self.confidence, 6),
                'classifier': self.classifier.slug(),
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

    def is_page_down(self, page):
        raise NotImplementedError('Classifier may implement is_page_down')

    def percent_down_pages(self, sessions):
        down_count = 0.0
        total_count = 0.0
        for page in self.relevant_pages(sessions):
            try:
                total_count += 1
                if self.is_page_down(page):
                    down_count += 1
            except NotEnoughDataError as e:
                # Don't include pages if the classifier doesn't know anything.
                logging.info(e)
                continue

        percent_down = 0.0
        if total_count > 0:
            percent_down = down_count / total_count
        return percent_down

    def relevant_pages(self, sessions):
        har_parser = HarParser(sessions['har'])
        relevant_pages = []
        for page in har_parser.pages:
            # page.url is the initial requested url
            if not page.url.startswith(sessions['url']):
                logging.warning('Possibly irrelevant page when looking for '
                        '"{}": {}'.format(sessions['url'], page.url))
                #continue # Don't skip
            if 'baseline' in sessions and sessions['baseline'] == page.page_id:
                continue
            relevant_pages.append(page)
        return relevant_pages

    def classify(self, sessions):
        self.sessions = sessions
        confidence = self.percent_down_pages(sessions)
        return Classification(self, Classification.DOWN, confidence)

class ClassifyPipeline(Classifier):
    def __init__(self, classifiers):
        Classifier.__init__(self)
        self.name = 'Classification Pipeline'
        self.desc = 'Classifies by passing data through multiple classifiers and weights their results'
        self.classifiers = [classifier for classifier, _ in classifiers]
        self.weights = self.normalize_weights(classifiers)

    def classify(self, sessions):
        self.classifications = []
        for classifier in self.classifiers:
            try:
                self.classifications.append(classifier.classify(sessions))
            except NotEnoughDataError as e:
                logging.warning('Not enough data for {} - {}'.format(classifier.slug(), e))
                continue
        classification = self.tally_vote(self.classifications)
        classification.constituents = self.classifications
        return classification

    def tally_vote(self, ications):
        #TODO Learn math so I can make this correct.
        ications.sort(key=lambda c: c.confidence, reverse=True)
        confidence = 0.0
        iers = [ication.classifier for ication in ications]
        max_weight = max([w for c, w in self.weights.items() if c in iers])
        for ication in ications:
            weight = self.weights[ication.classifier] / max_weight
            confidence += (1 - confidence) * ication.confidence * weight
        return Classification(self, Classification.DOWN, confidence)

    def normalize_weights(self, classifiers):
        normed = normalize([w for _, w in classifiers])
        weights = {}
        for i, classifier in enumerate(classifiers):
            weights[classifier[0]] = normed[i]
        return weights

class EmptyPageClassifier(Classifier):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Empty page'
        self.desc = 'A classifier that says pages with very little content are down'
        self.size_cutoff = 300 # bytes

    def is_page_down(self, page):
        total_size = page.get_total_size(page.entries)
        return total_size <= self.size_cutoff

class StatusCodeClassifier(Classifier):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Status code'
        self.desc = 'A simple classifier that says all non-2xx status codes are down'

    def is_page_down(self, page):
        entry = page.actual_page
        if entry is None:
            raise NotEnoughDataError('No final page found')
        if 'response' not in entry or 'status' not in entry['response']:
            raise NotEnoughDataError('"response" or "status" not found in entry '
                    'for URL "{}"'.format(entry['rekwest']['url']))
        status = page.actual_page['response']['status']
        logging.debug("{} - Page: {} - Status: {}".format(self.slug(), page.page_id,
            status))
        return status < 200 or status > 299

class ErrorClassifier(Classifier):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Error'
        self.desc = 'Classifies all sessions that contain errors as down'

    def is_page_down(self, page):
        if page.page_id not in self.sessions['pageDetail']:
            raise NotEnoughDataError('No page details for page "{}"'.format(page.page_id))
        errors = self.sessions['pageDetail'][page.page_id]['errors']
        logging.debug("{} - Page: {} - Errors: {}".format(self.slug(), page.page_id,
            errors))
        return len(errors) > 0

class ThrottleClassifier(Classifier):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Throttle'
        self.desc = 'Detects excessively long load times that might indicate throttling'
        self.total_confidence_above_size = 600
        self.time_threshold = 5 * 60 * 1000 # 5 minutes
        self.bandwidth_threshold = 50 # kbps

    def page_down_confidence(self, page):
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

    def classify(self, sessions):
        # This classifier does things a little differently. Instead of each
        # page voting whether it's up or down, we take the average difference
        # ratio between the requested and final domains.
        self.sessions = sessions
        confidences = [self.page_down_confidence(page) for page in self.relevant_pages(sessions)]
        avg = sum(confidences) / len(confidences)
        return Classification(self, Classification.DOWN, avg)

class ClassifierWithBaseline(Classifier):
    def set_baseline(self, sessions):
        if hasattr(self, 'baseline'):
            return self.baseline
        if 'baseline' not in sessions or sessions['baseline'] == False:
            raise NotEnoughDataError('Could not locate baseline for URL '
                    '"{}"'.format(sessions['url']))
        self.baseline = HarPage(sessions['baseline'], har_data=sessions['har'])
        return self.baseline

    def classify(self, sessions):
        self.set_baseline(sessions)
        return super().classify(sessions)

class CosineSimilarityClassifier(ClassifierWithBaseline):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Cosine similarity'
        self.desc = ('Uses cosine similarity between a page and a baseline '
            'to determine whether a page is a block page')
        self.page_length_threshold = 0.3019
        self.cosine_sim_threshold = 0.816
        self.dom_sim_threshold = 0.995

    def is_page_down(self, page):
        if not hasattr(self, 'baseline'):
            raise AttributeError('Baseline not set')
        try:
            baseline_content = har_entry_response_content(self.baseline.actual_page)
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
        return metrics['cosine similarity'] <= self.cosine_sim_threshold

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

    def is_page_down(self, page):
        if not hasattr(self, 'baseline'):
            raise AttributeError('Baseline not set')
        baseline_len = self.response_len(self.baseline.actual_page)
        this_content_len = self.response_len(page.actual_page)
        length_ratio = abs(baseline_len - this_content_len) / max(baseline_len, this_content_len)
        logging.debug("{} - Page: {} - Baseline: {} - Content: {} - Diff Ratio: {}".format(
            self.slug(), page.page_id, baseline_len, this_content_len, round(length_ratio, 3)))
        return length_ratio >= self.page_length_threshold

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

    def is_page_down(self, page):
        for entry in page.entries:
            for header in entry['response']['headers']:
                for fprint in self.header_fingerprints:
                    if (header['name'] == fprint[0] and
                            re.search(fprint[1], header['value'])):
                        logging.debug('{} - Page: {} - Header Pattern: "{}" - '
                                'Header: "{}" - Value: "{}"'.format(self.slug(),
                                    page.page_id, fprint[1], header['name'],
                                    header['value']))
                        return True

            # Everything below here relates to the body, so if we can't extract
            # a body, move on to the next entry.
            try:
                body = har_entry_response_content(entry)
            except NotEnoughDataError:
                continue

            for fprint in self.body_fingerprints:
                match = re.search(fprint, body)
                if match is not None:
                    logging.debug('{} - Page: {} - Body Pattern: "{}" '
                            '- Matched: "{}"'.format(self.slug(), page.page_id,
                                fprint, match.group(0)))
                    return True

        return False

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

    def get_page_ratio(self, page):
        requested = self.requested_domain(page)
        final = self.final_domain(page)
        ratio = self.get_diff_ratio(requested, final)
        logging.debug('{} - Page: {} - Requested: {} - Final: {} - Ratio: {}'.format(
            self.slug(), page.page_id, requested, final, round(ratio, 6)))
        return ratio

    def classify(self, sessions):
        # This classifier does things a little differently. Instead of each
        # page voting whether it's up or down, we take the average difference
        # ratio between the requested and final domains.
        self.sessions = sessions
        ratios = [self.get_page_ratio(page) for page in self.relevant_pages(sessions)]
        avg = sum(ratios) / len(ratios)
        return Classification(self, Classification.DOWN, avg)

def run(sessions):
    pipeline_config = [
            (StatusCodeClassifier(), 1.0),
            (ErrorClassifier(), 1.0),
            (PageLengthClassifier(), 1.0),
            (ThrottleClassifier(), 1.0),
            (EmptyPageClassifier(), 1.0),
            (CosineSimilarityClassifier(), 1.0),
            (DifferingDomainClassifier(), 1.0),
            (BlockpageSignatureClassifier(), 1.0),
            ]
    pipeline = ClassifyPipeline(pipeline_config)
    classification = pipeline.classify(sessions)
    return classification

def app(environ, start_response):
    sessions_json = environ['wsgi.input'].read().decode('utf-8')
    sessions = json.loads(sessions_json)
    status = '201 Created'
    headers = [('Content-Type', 'application/json')]
    start_response(status, headers)
    c = run(sessions)
    return [c.as_json().encode('utf-8')]

if __name__ == '__main__':
    args = parse_args()
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    c = run(json.load(args.sessions_file))
    print(c.as_json())
