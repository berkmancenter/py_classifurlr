import random, json, sys, argparse
from haralyzer import HarParser, HarPage
from pprint import pprint
# {
#   url: 'http://example.com',
#   pageDetail: {
#       'page_0': {
#           asn: 0,
#           screenshot: 'data:image/png;base64,',
#           errors: ['']
#       },
#       ...
#   },
#   har: {...}
# }

def parse_args():
    parser = argparse.ArgumentParser(description='Classify a collection of pages as accessible or inaccessible with a certain confidence based on some number of factors')
    parser.add_argument('sessions_file', type=open,
            help='file containing JSON detailing a number of HTTP sessions')
    return parser.parse_args()

def normalize(weights):
    if min(weights) <= 0: raise ValueError('No weight can be zero or negative')
    total = sum(weights)
    return [weight / total for weight in weights]

class Classification:
    UP = 'up'
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

    def relevant_har_pages(self, url, pages):
        relevant_pages = []
        for page in pages:
            # page.url is the initial requested url
            if not page.url.startswith(url):
                print('Irrelevant page when looking for "{}": {}'.format(url, page.url), file=sys.stderr)
                continue
            relevant_pages.append(page)
        return relevant_pages

    def break_tie(self):
        #TODO Should this tie be randomly broken, or broken toward down?
        return random.choice([Classification.UP, Classification.DOWN])


class ClassifyPipeline(Classifier):
    def __init__(self, classifiers):
        Classifier.__init__(self)
        self.name = 'Classification Pipeline'
        self.desc = 'Classifies by passing data through multiple classifiers and weights their results'
        self.classifiers = [classifier for classifier, weight in classifiers]
        self.weights = self.normalize_weights(classifiers)

    def classify(self, sessions):
        self.classifications = []
        for classifier in self.classifiers:
            self.classifications.append(classifier.classify(sessions))
        return self.tally_vote(self.classifications)

    def max_confidence(self, classifications):
        return max([c.confidence for c in classifications])

    def tally_vote(self, classifications):
        votes = { Classification.UP: [], Classification.DOWN: [] }
        for c in classifications:
            votes[c.direction].append(c)
        # If all votes are in a single direction
        if len(votes[Classification.UP]) == 0:
            return Classification(self, Classification.DOWN,
                    self.max_confidence(votes[Classification.DOWN]))
        elif len(votes[Classification.DOWN]) == 0:
            return Classification(self, Classification.UP,
                    self.max_confidence(votes[Classification.UP]))
        else:
            # If votes are split, it's a weighted sum.
            tally = { Classification.UP: 0.0, Classification.DOWN: 0.0 }
            for c in classifications:
                tally[c.direction] = c.confidence * self.weights[c.classifier]

            if tally[Classification.UP] == tally[Classification.DOWN]:
                direction = self.break_tie()
            elif tally[Classification.UP] > tally[Classification.DOWN]:
                direction = Classification.UP
            else:
                direction = Classification.DOWN

            return Classification(self, direction, tally[direction])

    #TODO I don't think we can normalize until we get confidences. If there are
    # two classifiers and one has zero confidence, we shouldn't cut the other in
    # half.
    def normalize_weights(self, classifiers):
        normed = normalize([w for _, w in classifiers])
        weights = {}
        for i, classifier in enumerate(classifiers):
            weights[classifier[0]] = normed[i]
        return weights

class StatusCodeClassifier(Classifier):

    #TODO Do a manual review of pages with 200 codes to come up with a more
    # accurate estimation of confidence.
    BASE_CONFIDENCE = 0.95

    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Status code classifier'
        self.desc = 'A simple classifier that says all 2xx status codes are up with high confidence'

    def classify(self, sessions):
        har_parser = HarParser(sessions['har'])
        page_states = { Classification.UP: 0, Classification.DOWN: 0 }
        for page in self.relevant_har_pages(sessions['url'], har_parser.pages):
            status = page.actual_page['response']['status']
            if status >= 200 and status <= 299:
                page_states[Classification.UP] += 1
            else:
                page_states[Classification.DOWN] += 1

        # Are there more up pages or down pages?
        if page_states[Classification.UP] == page_states[Classification.DOWN]:
            direction = self.break_tie()
        elif page_states[Classification.UP] > page_states[Classification.DOWN]:
            direction = Classification.UP
        else:
            direction = Classification.DOWN

        victory_percent = page_states[direction] / sum(page_states.values())
        confidence = self.BASE_CONFIDENCE * victory_percent
        return Classification(self, direction, confidence)


class ErrorClassifier(Classifier):

    #TODO Do a manual review of pages with errors to come up with a more
    # accurate estimation of confidence.
    BASE_CONFIDENCE = 0.95

    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Error classifier'
        self.desc = 'Classifies all sessions that contain errors as down'

    def classify(self, sessions):
        # UP here means without errors, while DOWN means with errors
        scores = { Classification.UP: 0, Classification.DOWN: 0 }
        har_parser = HarParser(sessions['har'])
        for page in self.relevant_har_pages(sessions['url'], har_parser.pages):
            # If the page has errors, score one for team DOWN
            if (page.page_id in sessions['pageDetail'] and
                    len(sessions['pageDetail'][page.page_id]['errors']) > 0):
                scores[Classification.DOWN] += 1
            else:
                scores[Classification.UP] += 1

        # Are there any pages with errors?
        if scores[Classification.DOWN] > 0:
            victory_percent = scores[Classification.DOWN] / sum(scores.values())
            confidence = self.BASE_CONFIDENCE * victory_percent
            return Classification(self, Classification.DOWN, confidence)
        else:
            return Classification(self, Classification.DOWN, 0.0)

def run(sessions):
    pipeline = [
            (StatusCodeClassifier(), 1.0),
            (ErrorClassifier(), 1.0)
            ]
    classifier = ClassifyPipeline(pipeline)
    classification = classifier.classify(sessions)
    print(classification.as_json())
    for c in classifier.classifications:
        print(c.as_json())

if __name__ == '__main__':
    args = parse_args()
    run(json.load(args.sessions_file))
