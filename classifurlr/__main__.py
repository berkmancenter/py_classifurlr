import argparse, json, logging

from classifurlr.filters import *
from classifurlr.post_processors import *
from classifurlr.classifiers import *
from classifurlr.classification import ClassifyPipeline

def parse_args():
    parser = argparse.ArgumentParser(description='Determine whether a collection of pages is inaccessible')
    parser.add_argument('session_file', type=open,
            help='file containing JSON detailing HTTP requests + responses')
    parser.add_argument('--debug', action='store_true',
            help='Log debugging info')
    return parser.parse_args()

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

if __name__ == '__main__':
    args = parse_args()
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    c = run(json.load(args.session_file))
    print(c.as_json())
