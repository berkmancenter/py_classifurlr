import random, json, sys, argparse, itertools
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
        self.classifiers = [classifier for classifier, _ in classifiers]
        self.weights = { classifier: weight for classifier, weight in classifiers }
        self.total_weight = sum(self.weights.values())

    def classify(self, sessions):
        self.classifications = []
        for classifier in self.classifiers:
            self.classifications.append(classifier.classify(sessions))
        return self.tally_vote(self.classifications)

    def tally_confidence_for_direction(self, ications):
        #TODO Learn math so this can be correct.
        equal_to_first = map(lambda c: c.direction == ications[0].direction, ications)
        if not all(equal_to_first):
            raise ValueError('All directions must be equal')
        ications.sort(key=lambda c: c.confidence, reverse=True)
        confidence = 0.0
        iers = [ication.classifier for ication in ications]
        max_weight = max([w for c, w in self.weights.items() if c in iers])
        for c in ications:
            weight = self.weights[c.classifier] / max_weight
            confidence += (1 - confidence) * c.confidence * weight
        return confidence

    def tally_vote(self, ications):
        #TODO Learn math so this can be correct.
        dir_key = lambda c: c.direction
        ications.sort(key=dir_key)
        ication_groups = itertools.groupby(ications, key=dir_key)
        dir_conf = { Classification.UP: 0.0, Classification.DOWN: 0.0 }
        dir_weight = { Classification.UP: 0.0, Classification.DOWN: 0.0 }
        for direction, dir_ications in ication_groups:
            dir_ications = list(dir_ications)
            dir_conf[direction] = self.tally_confidence_for_direction(dir_ications)
            for ication in dir_ications:
                dir_weight[direction] += self.weights[ication.classifier]
        up_conf = dir_conf[Classification.UP] * (dir_weight[Classification.UP] / self.total_weight)
        down_conf = dir_conf[Classification.DOWN] * (dir_weight[Classification.DOWN] / self.total_weight)

        if up_conf == down_conf:
            return Classification(self, self.break_tie(), 0.0)

        total_conf = up_conf - down_conf
        if total_conf < 0:
            direction = Classification.DOWN
            output_conf = -1 * total_conf
        else:
            direction = Classification.UP
            output_conf = total_conf
        return Classification(self, direction, output_conf)

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
