# Originally, this was supposed to classify a set of pages as up or down, with
# a confidence estimation for each. I decided that there were too much open to
# interpretation in that design, and that our use cases weren't necessarily
# trying to determine if something was up, so it is now an inaccessibility
# detector. You give it a set of pages, it gives you the probability that the
# set of pages is down.
#
# To reiterate, THIS SCRIPT WILL ONLY EVER RETURN A DOWN STATUS.

import random, json, sys, argparse, itertools, base64
from haralyzer import HarParser, HarPage
from bs4 import BeautifulSoup
from similarityMetrics import similarity_metrics
from pprint import pprint

# {
#   url: 'http://example.com',
#   baseline: false, // 'page_1',
#   pageDetail: {
#       'page_0': {
#           asn: 0,
#           screenshot: 'data:image/png;base64,',
#           errors: [''],
#       },
#       ...
#   },
#   har: {...}
# }

def parse_args():
    parser = argparse.ArgumentParser(description='Determine whether a collection of pages is inaccessible')
    parser.add_argument('sessions_file', type=open,
            help='file containing JSON detailing a number of HTTP sessions')
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
        raise KeyError('"text" field not found in entry content')
    text = content['text']
    if 'encoding' in content and content['encoding'] == 'base64':
        text = base64.b64decode(text)
    # BeautifulSoup takes care of the document encoding for us.
    return str(BeautifulSoup(text, 'html.parser'))

class Classification:
    DOWN = 'down'
    def __init__(self, classifier, direction=None, confidence=None):
        self.classifier = classifier
        self.direction = direction
        self.confidence = confidence

    def as_json(self):
        return json.dumps({
            'status': self.direction,
            'statusConfidence': self.confidence,
            'classifier': self.classifier.slug()
            }, indent=2)

class Classifier:
    def __init__(self):
        self.name = '__placeholder__'
        self.desc = '__placeholder__'

    def slug(self):
        return self.name.lower().replace(' ', '_')

    def classify(self, sessions):
        raise NotImplementedError('Classifier must implement classify')

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
                print(e, file=sys.stderr)
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
                print('Irrelevant page when looking for "{}": {}'.format(sessions['url'], page.url), file=sys.stderr)
                continue
            if 'baseline' in sessions and sessions['baseline'] == page.page_id:
                continue
            relevant_pages.append(page)
        return relevant_pages

    def classify(self, sessions):
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
                print('Not enough data for {}'.format(classifier.slug()), file=sys.stderr)
                print(e, file=sys.stderr)
                continue
        return self.tally_vote(self.classifications)

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

class StatusCodeClassifier(Classifier):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Status code classifier'
        self.desc = 'A simple classifier that says all non-2xx status codes are down'

    def is_page_down(self, page):
        entry = page.actual_page
        if 'response' not in entry or 'status' not in entry['response']:
            raise NotEnoughDataError('"response" or "status" not found in entry '
                    'for URL "{}"'.format(entry['rekwest']['url']))
        status = page.actual_page['response']['status']
        return status < 200 or status > 299

class ErrorClassifier(Classifier):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Error classifier'
        self.desc = 'Classifies all sessions that contain errors as down'

    def is_page_down(self, page):
        return (page.page_id in self.sessions['pageDetail'] and
                    len(self.sessions['pageDetail'][page.page_id]['errors']) > 0)

    def classify(self, sessions):
        self.sessions = sessions
        return super().classify(sessions)

class ThrottleClassifier(Classifier):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Throttle classifier'
        self.desc = 'Detects excessively long load times that might indicate throttling'
        self.threshold = 1 # byte per millisecond

    def is_page_down(self, page):
        bites = page.get_total_size(page.entries)
        mss = page.get_load_time(async=False)
        bytes_per_ms = bites/mss
        return bytes_per_ms <= self.threshold

class ClassifierWithBaseline(Classifier):
    def set_baseline(self, sessions):
        if hasattr(self, 'baseline'):
            return self.baseline
        try:
            baseline = sessions['baseline']
        except KeyError as e:
            raise NotEnoughDataError('Could not locate baseline for URL '
                    '"{}"'.format(sessions['url'])) from e
        self.baseline = HarPage(baseline, har_data=sessions['har'])
        return self.baseline

    def classify(self, sessions):
        self.set_baseline(sessions)
        return super().classify(sessions)

class BlockPageClassifier(ClassifierWithBaseline):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Block page classifier'
        self.desc = 'Detects whether a page is a block page given a baseline'
        self.page_length_threshold = 0.3019
        self.cosine_sim_threshold = 0.816
        self.dom_sim_threshold = 0.995

    def is_page_down(self, page):
        if not hasattr(self, 'baseline'):
            raise AttributeError('Baseline not set')
        try:
            baseline_content = har_entry_response_content(self.baseline.actual_page)
        except KeyError as e:
            raise NotEnoughDataError('Could not locate baseline '
                    'content for URL "{}"'.format(page.url)) from e
        try:
            this_content = har_entry_response_content(page.actual_page)
        except KeyError as e:
            raise NotEnoughDataError('Could not locate page '
                    'content for URL "{}"'.format(page.url)) from e
        metrics = similarity_metrics(baseline_content, this_content)
        return metrics['length ratio'] <= self.page_length_threshold

class PageLengthClassifier(ClassifierWithBaseline):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Page length classifier'
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
        return length_ratio >= self.page_length_threshold

def run(sessions):
    pipeline = [
            (StatusCodeClassifier(), 1.0),
            (ErrorClassifier(), 1.0),
            (PageLengthClassifier(), 1.0),
            (ThrottleClassifier(), 1.0),
            (BlockPageClassifier(), 1.0),
            ]
    classifier = ClassifyPipeline(pipeline)
    classification = classifier.classify(sessions)
    print(classification.as_json())
    for c in classifier.classifications:
        print(c.as_json())

if __name__ == '__main__':
    args = parse_args()
    run(json.load(args.sessions_file))
