import json, logging, concurrent.futures

import dateutil.parser
from haralyzer import HarParser
from .url_utils import extract_domain

class NotEnoughDataError(LookupError):
    pass

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

class ClassifierWithBaseline(Classifier):
    def get_baseline(self, session):
        baseline = session.get_baseline()
        if baseline is None:
            raise NotEnoughDataError('Could not locate baseline for URL '
                    '"{}"'.format(session.url))
        return baseline

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

    def get_constituents(self):
        return self.constituents

    def get_constituent_from(self, classifier):
        return next((c for c in self.get_constituents()
            if isinstance(c.classifier, classifier)), None)

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
        session = Session(session)
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
        weight_from_age = self.interpolate(domain, rang, -1 * seconds_old)
        return weight_from_status * weight_from_age

    def normalize_weights(self, classifiers):
        normed = self.normalize([w for _, w in classifiers])
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

    @staticmethod
    def normalize(weights):
        if min(weights) <= 0: raise ValueError('No weight can be zero or negative')
        total = sum(weights)
        return [weight / total for weight in weights]

    @staticmethod
    def interpolate(domain, rang, x):
        if domain[1] - domain[0] == 0:
            raise ValueError('Domain must be wider than single value')
        # Simple linear interpolation
        slope = (rang[1] - rang[0]) / (domain[1] - domain[0])
        intercept = rang[0] - (slope * domain[0])
        # Clip to range
        return min([max([rang[0], x * slope + intercept]), rang[1]])

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

    def get_domain(self):
        return extract_domain(self.get_url())

    def get_baseline_id(self):
        if 'baseline' not in self: return None
        return self['baseline']

    def get_baseline(self):
        if self.baseline: return self.baseline
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
        return details['countryCode'].upper()

    def get_page_asn(self, page_id):
        details = self.get_page_details(page_id)
        if 'asn' not in details:
            return None
        return details['asn']
