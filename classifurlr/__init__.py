from classifurlr.filters import *
from classifurlr.post_processors import *
from classifurlr.classifiers import *
from classifurlr.classification import ClassifyPipeline

# Expose the default pipeline config
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

